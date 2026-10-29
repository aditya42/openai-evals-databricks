"""Publish to hosted MLflow and Unity Catalog Delta tables using unified authentication."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from .core import read_json, safe_error

TABLES = {
    "eval_runs": {
        "batch_id": "STRING", "event_time": "TIMESTAMP", "model": "STRING", "framework": "STRING",
        "metric": "STRING", "dataset_sha256": "STRING", "config_sha256": "STRING", "mode": "STRING",
        "judge_model": "STRING", "mean_score": "DOUBLE", "pass_rate": "DOUBLE", "coverage": "DOUBLE",
        "passed": "BIGINT", "failed": "BIGINT", "errors": "BIGINT", "skipped": "BIGINT", "eligible": "BIGINT",
        "judge_requests": "BIGINT", "judge_input_tokens": "BIGINT", "judge_output_tokens": "BIGINT",
        "mlflow_run_id": "STRING", "commit_sha": "STRING", "git_branch": "STRING", "ci_url": "STRING",
    },
    "eval_cases": {
        "batch_id": "STRING", "event_time": "TIMESTAMP", "model": "STRING", "framework": "STRING",
        "case_id": "STRING", "category": "STRING", "metric": "STRING", "status": "STRING",
        "score": "DOUBLE", "threshold": "DOUBLE", "duration_seconds": "DOUBLE", "reason": "STRING",
        "dataset_sha256": "STRING", "config_sha256": "STRING", "mode": "STRING", "mlflow_run_id": "STRING",
    },
    "eval_generations": {
        "batch_id": "STRING", "event_time": "TIMESTAMP", "model": "STRING", "case_id": "STRING",
        "status": "STRING", "resolved_model": "STRING", "duration_seconds": "DOUBLE",
        "input_tokens": "BIGINT", "output_tokens": "BIGINT", "reasoning_tokens": "BIGINT",
        "usage_known": "BOOLEAN", "error": "STRING", "dataset_sha256": "STRING", "config_sha256": "STRING", "mode": "STRING",
    },
}
KEYS = {
    "eval_runs": ("batch_id", "model", "framework", "dataset_sha256", "config_sha256"),
    "eval_cases": ("batch_id", "model", "framework", "case_id", "dataset_sha256", "config_sha256"),
    "eval_generations": ("batch_id", "model", "case_id", "dataset_sha256", "config_sha256"),
}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Catalog/schema names must contain only letters, numbers, and underscores")
    return "`" + value + "`"


def namespace() -> str:
    return identifier(os.getenv("DATABRICKS_CATALOG", "workspace")) + "." + identifier(os.getenv("DATABRICKS_SCHEMA", "default"))


def ddl_statements(ns: str) -> list[str]:
    return [f"CREATE SCHEMA IF NOT EXISTS {ns}"] + [
        f"CREATE TABLE IF NOT EXISTS {ns}.{identifier(name)} (" + ", ".join(identifier(c) + " " + t for c, t in columns.items()) + ") USING DELTA"
        for name, columns in TABLES.items()
    ]


def merge_statement(ns: str, table: str) -> str:
    if table not in TABLES:
        raise ValueError("Unknown result table")
    schema = "ARRAY<STRUCT<" + ",".join(c + ":" + t for c, t in TABLES[table].items()) + ">>"
    match = " AND ".join(f"t.{identifier(c)} = s.{identifier(c)}" for c in KEYS[table])
    return (f"MERGE INTO {ns}.{identifier(table)} t USING "
            f"(SELECT r.* FROM (SELECT explode(from_json(:payload, '{schema}')) AS r)) s "
            f"ON {match} WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")


class Warehouse:
    def __init__(self, workspace=None, warehouse_id=None):
        if workspace is None:
            from databricks.sdk import WorkspaceClient
            workspace = WorkspaceClient()
        self.workspace = workspace
        self.warehouse_id = warehouse_id or os.environ["DATABRICKS_WAREHOUSE_ID"]

    def execute(self, statement: str, payload=None):
        for attempt in range(4):
            try:
                return self._execute(statement, payload)
            except ConcurrentDeltaWrite:
                if attempt == 3:
                    raise
                time.sleep(0.5 * 2 ** attempt)

    def _execute(self, statement: str, payload=None):
        from databricks.sdk.service.sql import StatementParameterListItem
        parameters = [StatementParameterListItem(name="payload", value=json.dumps(payload, allow_nan=False), type="STRING")] if payload is not None else None
        response = self.workspace.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=statement, parameters=parameters, wait_timeout="30s")
        deadline = time.monotonic() + 600
        while True:
            state = response.status.state.value
            if state == "SUCCEEDED":
                return response
            if state in ("FAILED", "CANCELED", "CLOSED"):
                error = getattr(response.status, "error", None)
                code = getattr(error, "error_code", "unknown")
                message = getattr(error, "message", "") or ""
                if state == "FAILED" and any(marker in message for marker in (
                    "DELTA_CONCURRENT_APPEND", "DELTA_CONCURRENT_WRITE", "DELTA_CONCURRENT_DELETE_READ",
                    "DELTA_CONCURRENT_DELETE_DELETE", "ConcurrentAppendException", "ConcurrentWriteException",
                )):
                    raise ConcurrentDeltaWrite("Concurrent Delta write; safe to retry the idempotent merge")
                raise RuntimeError(f"SQL statement {state}: {code}")
            if time.monotonic() >= deadline:
                raise TimeoutError("SQL warehouse statement timed out")
            time.sleep(1)
            response = self.workspace.statement_execution.get_statement(response.statement_id)

    def merge(self, table: str, rows: list[dict]):
        if rows:
            expected = set(TABLES[table])
            for row in rows:
                if set(row) != expected:
                    raise ValueError("Delta result row differs from the declared schema")
            self.execute(merge_statement(namespace(), table), rows)


class ConcurrentDeltaWrite(RuntimeError):
    pass


def require_live(report: dict):
    if report.get("mode") != "live" or report.get("schema_version") != 1:
        raise ValueError("Only live evaluation data may be published; fixtures are rejected")


def generation_rows(manifest: dict) -> list[dict]:
    require_live(manifest)
    shared = {"batch_id": manifest["run_id"], "event_time": manifest["created_at"], "model": manifest["model"],
              "dataset_sha256": manifest["dataset_sha256"], "config_sha256": manifest["config_sha256"], "mode": manifest["mode"]}
    rows = []
    for case_id, response in manifest["responses"].items():
        rows.append({**shared, "case_id": case_id, "status": response["status"], "resolved_model": response.get("resolved_model", ""),
                     "duration_seconds": response["duration_seconds"], "input_tokens": response["input_tokens"],
                     "output_tokens": response["output_tokens"], "reasoning_tokens": response["reasoning_tokens"],
                     "usage_known": response["usage_known"], "error": response.get("error", "")})
    return rows


def result_rows(report: dict, mlflow_run_id: str):
    require_live(report)
    shared = {"batch_id": report["batch_id"], "event_time": report["created_at"], "model": report["model"],
              "framework": report["framework"], "dataset_sha256": report["dataset_sha256"], "config_sha256": report["config_sha256"],
              "mode": report["mode"], "mlflow_run_id": mlflow_run_id}
    cases = [{**shared, **{k: row[k] for k in ("case_id", "category", "metric", "status", "score", "threshold", "duration_seconds", "reason")}}
             for row in report["results"]]
    s = report["summary"]
    run = {**shared, "metric": report["metric"], "judge_model": report["judge_model"],
           **{k: s[k] for k in ("mean_score", "pass_rate", "coverage", "passed", "failed", "skipped", "eligible")}, "errors": s["error"],
           "judge_requests": report["judge_usage"]["requests"], "judge_input_tokens": report["judge_usage"]["input_tokens"],
           "judge_output_tokens": report["judge_usage"]["output_tokens"],
           **{k: report[k] for k in ("commit_sha", "git_branch", "ci_url")}}
    return cases, [run]


def log_mlflow(report: dict, directory: Path) -> str:
    import mlflow
    from mlflow import MlflowClient

    require_live(report)
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "databricks"))
    experiment = mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "/Shared/openai-llm-evals"))
    client = MlflowClient()
    # Hashes are safe filter values and avoid re-creating a run on an upload retry.
    from .core import digest
    key = digest({k: report[k] for k in ("batch_id", "model", "framework", "dataset_sha256", "config_sha256")})
    existing = client.search_runs([experiment.experiment_id], filter_string=f"tags.upload_key = '{key}'", max_results=1)
    if existing:
        mlflow_run_id = existing[0].info.run_id
    else:
        mlflow_run_id = client.create_run(experiment.experiment_id, run_name=f'{report["framework"]} / {report["model"]}', tags={
            "upload_key": key, "batch_id": report["batch_id"], "framework": report["framework"], "model": report["model"],
            "dataset_sha256": report["dataset_sha256"], "config_sha256": report["config_sha256"],
            "git_commit": report["commit_sha"], "git_branch": report["git_branch"], "ci_url": report["ci_url"], "mode": "live",
        }).info.run_id
    for k in ("model", "framework", "metric", "judge_model", "dataset_sha256", "config_sha256"):
        client.log_param(mlflow_run_id, k, report[k])
    metrics = {k: v for k, v in report["summary"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    metrics.update({"judge_" + k: v for k, v in report["judge_usage"].items()})
    for k, value in metrics.items():
        client.log_metric(mlflow_run_id, k, float(value), timestamp=report["timestamp"] * 1000)
    for name in ("results.json", "junit.xml"):
        client.log_artifact(mlflow_run_id, str(directory / name), artifact_path="evaluation")
    client.set_terminated(mlflow_run_id, status="FINISHED" if report["summary"]["gate_passed"] else "FAILED")
    return mlflow_run_id


def publish_results(path: Path, warehouse=None):
    report = read_json(path)
    require_live(report)
    mlflow_id = log_mlflow(report, path.parent)
    cases, runs = result_rows(report, mlflow_id)
    warehouse = warehouse or Warehouse()
    # Summary is the completion marker; write case details first.
    warehouse.merge("eval_cases", cases)
    warehouse.merge("eval_runs", runs)
    return mlflow_id


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap")
    gen = commands.add_parser("publish-generation")
    gen.add_argument("file", type=Path)
    results = commands.add_parser("publish-results")
    results.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            warehouse = Warehouse()
            for statement in ddl_statements(namespace()):
                warehouse.execute(statement)
            print("Delta result tables are ready")
        elif args.command == "publish-generation":
            Warehouse().merge("eval_generations", generation_rows(read_json(args.file)))
            print("Generation metadata published")
        else:
            print("MLflow run: " + publish_results(args.file))
        return 0
    except Exception as exc:
        print("Databricks publication failed: " + safe_error(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
