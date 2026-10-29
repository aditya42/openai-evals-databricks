"""Build and deploy a native Databricks AI/BI dashboard with typed Lakeview definitions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from databricks.labs.lsql import lakeview as lv

from .backend import ddl_statements, namespace
from .core import FRAMEWORKS, METRICS, load_cases, load_config, safe_error, write_json

DASHBOARD_NAME = "OpenAI evaluations — five frameworks"


def deploy(workspace, payload: dict, warehouse_id: str, parent_path: str = "/Shared", dashboard_id: str | None = None) -> dict:
    from databricks.sdk.service.dashboards import Dashboard

    parent_path = parent_path.rstrip("/") or "/"
    if dashboard_id:
        current = workspace.lakeview.get(dashboard_id)
    else:
        matches = [d for d in workspace.lakeview.list()
                   if d.display_name == DASHBOARD_NAME and d.path
                   and (d.path.rsplit("/", 1)[0] or "/") == parent_path]
        if len(matches) > 1:
            raise ValueError("Multiple matching dashboards; set DATABRICKS_DASHBOARD_ID to choose one")
        current = workspace.lakeview.get(matches[0].dashboard_id) if matches else None
    definition = Dashboard(display_name=DASHBOARD_NAME, serialized_dashboard=json.dumps(payload), warehouse_id=warehouse_id)
    if current:
        if not current.etag:
            raise ValueError("Existing dashboard has no version tag; cannot safely update it")
        definition.etag = current.etag
        dashboard = workspace.lakeview.update(current.dashboard_id, definition)
        action = "updated"
    else:
        definition.parent_path = parent_path
        dashboard = workspace.lakeview.create(definition)
        action = "created"
    workspace.lakeview.publish(dashboard.dashboard_id, embed_credentials=False, warehouse_id=warehouse_id)
    return {"dashboard_id": dashboard.dashboard_id, "action": action,
            "url": workspace.config.host.rstrip("/") + "/sql/dashboardsv3/" + dashboard.dashboard_id + "/published"}


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dashboard_queries(config: dict) -> dict[str, str]:
    ns = namespace()
    runs, cases, generations = [f"{ns}.`{name}`" for name in ("eval_runs", "eval_cases", "eval_generations")]
    latest = f"latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM {runs} WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1)"
    join = "JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256"
    model_values = ",".join("(" + literal(m) + ")" for m in config["models"])
    framework_values = ",".join("(" + literal(f) + "," + literal(METRICS[f]) + ")" for f in FRAMEWORKS)
    total = len(FRAMEWORKS) * len(config["models"])
    target_generations = len(config["models"]) * len(load_cases(config))
    queries = {
        "overview": f"""WITH {latest}, suites AS (
 SELECT max(r.batch_id) AS batch_id, max(r.event_time) AS event_time,
        count(*) AS received_suites, {total} AS expected_suites,
        sum(r.failed) AS failed_cases, sum(r.errors) AS error_cases
 FROM {runs} r {join}
), gen AS (
 SELECT count(*) AS received_generations, {target_generations} AS expected_generations,
        count_if(r.status='error') AS generation_errors
 FROM {generations} r {join}
)
SELECT s.*,g.received_generations,g.expected_generations,
       CASE WHEN s.received_suites < {total} OR g.received_generations < {target_generations} THEN 'INCOMPLETE'
            WHEN s.failed_cases+s.error_cases+g.generation_errors > 0 THEN 'FAILED' ELSE 'PASSED' END AS batch_status
FROM suites s CROSS JOIN gen g""",
        "comparison": f"""WITH {latest},
models AS (SELECT * FROM VALUES {model_values} AS m(model)),
frameworks AS (SELECT * FROM VALUES {framework_values} AS f(framework, metric))
SELECT m.model, f.framework, f.metric, r.mean_score, round(100*r.pass_rate,2) AS pass_rate_pct,
       r.eligible, r.skipped, r.failed, r.errors, round(100*r.coverage,2) AS coverage_pct,
       CASE WHEN r.framework IS NULL THEN 'PENDING'
            WHEN r.errors > 0 THEN 'ERROR' WHEN r.failed > 0 THEN 'FAILED' ELSE 'PASSED' END AS gate_status
FROM models m CROSS JOIN frameworks f CROSS JOIN latest b
LEFT JOIN {runs} r ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256
  AND r.config_sha256=b.config_sha256 AND r.model=m.model AND r.framework=f.framework
ORDER BY f.framework,m.model""",
        "failures": f"""WITH {latest}
SELECT r.model, r.framework, r.case_id, r.category, r.status, r.score, r.threshold, r.reason
FROM {cases} r {join} WHERE r.status IN ('failed','error') ORDER BY r.framework,r.model,r.case_id""",
        "generation": f"""WITH {latest}
SELECT r.model, count(*) AS generation_attempts,
       count_if(r.status='error') AS generation_errors,
       round(avg(CASE WHEN r.status='ok' THEN r.duration_seconds END),3) AS avg_latency_s,
       round(percentile_approx(CASE WHEN r.status='ok' THEN r.duration_seconds END,0.95),3) AS p95_latency_s,
       sum(r.input_tokens) AS input_tokens, sum(r.output_tokens) AS output_tokens,
       sum(r.reasoning_tokens) AS reasoning_tokens, count_if(NOT r.usage_known) AS unknown_usage_cases
FROM {generations} r {join} GROUP BY r.model ORDER BY r.model""",
        "judge_usage": f"""WITH {latest}
SELECT r.model, r.framework, r.judge_model, r.judge_requests,
       r.judge_input_tokens, r.judge_output_tokens, r.mlflow_run_id, r.git_branch, r.commit_sha, r.ci_url
FROM {runs} r {join} ORDER BY r.framework,r.model""",
        "all_cases": f"""WITH {latest}
SELECT r.model,r.framework,r.case_id,r.category,r.status,r.score,r.threshold,r.duration_seconds,r.reason
FROM {cases} r {join} ORDER BY r.framework,r.model,r.case_id""",
    }
    for framework in FRAMEWORKS:
        queries["history_" + framework] = f"""SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM {runs} WHERE mode='live' AND framework={literal(framework)}
 AND dataset_sha256=(SELECT dataset_sha256 FROM {runs} WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM {runs} WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time"""
    return queries


def table_spec(title: str, fields: list[str]):
    numeric = {"mean_score", "pass_rate_pct", "eligible", "skipped", "failed", "errors", "coverage_pct", "score", "threshold", "duration_seconds",
               "received_suites", "expected_suites", "received_generations", "expected_generations", "failed_cases", "error_cases", "generation_attempts", "generation_errors",
               "avg_latency_s", "p95_latency_s", "input_tokens", "output_tokens", "reasoning_tokens", "unknown_usage_cases",
               "judge_requests", "judge_input_tokens", "judge_output_tokens"}
    columns = [lv.TableV1ColumnEncoding(boolean_values=[], display_as=lv.DisplayType.NUMBER if f in numeric else lv.DisplayType.STRING,
                field_name=f, title=f.replace("_", " ").title(), type=lv.ColumnType.FLOAT if f in numeric else lv.ColumnType.STRING,
                visible=True, allow_search=True, order=i) for i, f in enumerate(fields)]
    return lv.TableV1Spec(allow_html_by_default=False, condensed=True, encodings=lv.TableV1EncodingMap(columns=columns),
                         invisible_columns=[], items_per_page=20, with_row_number=False,
                         frame=lv.WidgetFrameSpec(title=title, show_title=True))


def build(config: dict) -> lv.Dashboard:
    queries = dashboard_queries(config)
    datasets = [lv.Dataset(name=name, display_name=name.replace("_", " ").title(), query=sql) for name, sql in queries.items()]

    def widget(name, dataset, fields, spec, x, y, width=6, height=7):
        query = lv.Query(dataset_name=dataset, fields=[lv.Field(name=f, expression="`" + f + "`") for f in fields], disaggregated=True)
        return lv.Layout(widget=lv.Widget(name=name, queries=[lv.NamedQuery(name="main_query", query=query)], spec=spec),
                         position=lv.Position(x=x, y=y, width=width, height=height))

    overview_fields = ["batch_id", "event_time", "received_suites", "expected_suites", "received_generations", "expected_generations", "failed_cases", "error_cases", "batch_status"]
    comparison_fields = ["model", "framework", "metric", "mean_score", "pass_rate_pct", "eligible", "skipped", "failed", "errors", "coverage_pct", "gate_status"]
    generation_fields = ["model", "generation_attempts", "generation_errors", "avg_latency_s", "p95_latency_s", "input_tokens", "output_tokens", "reasoning_tokens", "unknown_usage_cases"]
    failure_fields = ["model", "framework", "case_id", "category", "status", "score", "threshold", "reason"]
    layouts = [
        lv.Layout(widget=lv.Widget(name="readme", textbox_spec="# OpenAI model evaluations\nCompare models within each framework. Scores measure different properties and are never combined.\nPENDING means an upload is missing; INCOMPLETE is never a passing batch. This starter dataset contains 12 synthetic cases per model."), position=lv.Position(x=0,y=0,width=6,height=3)),
        widget("batch", "overview", overview_fields, table_spec("Latest received batch", overview_fields), 0, 3, height=3),
        widget("matrix", "comparison", comparison_fields, table_spec("Five frameworks × three models", comparison_fields), 0, 6, height=9),
        widget("latency", "generation", generation_fields, table_spec("Generation latency and tokens — counted once", generation_fields), 0, 15, height=5),
        widget("failures", "failures", failure_fields, table_spec("Failed cases and evaluation errors", failure_fields), 0, 20, height=8),
    ]
    history = [lv.Layout(widget=lv.Widget(name="history_readme",textbox_spec="# Scores over time\nThirty-day trends use the latest dataset and configuration hashes. Judge calls and tokens are listed separately from model generation."), position=lv.Position(x=0,y=0,width=6,height=2))]
    for i, framework in enumerate(FRAMEWORKS):
        spec = lv.LineSpec(encodings=lv.ChartEncodingMapWithSingleXy(
            x=lv.SingleFieldAxisEncoding(field_name="event_time", scale=lv.TemporalScale()),
            y=lv.SingleFieldAxisEncoding(field_name="mean_score", scale=lv.QuantitativeScale()),
            color=lv.ColorFieldEncoding(field_name="model", scale=lv.CategoricalScale())),
            frame=lv.WidgetFrameSpec(title=framework.title() + " — " + METRICS[framework], show_title=True))
        history.append(widget("trend_"+framework, "history_"+framework, ["event_time","model","mean_score"], spec, (i%2)*3, 2+(i//2)*6, width=3,height=6))
    usage_fields = ["model", "framework", "judge_model", "judge_requests", "judge_input_tokens", "judge_output_tokens", "mlflow_run_id", "git_branch", "commit_sha", "ci_url"]
    history.append(widget("judge", "judge_usage", usage_fields, table_spec("Judge usage and run links",usage_fields),0,20,height=8))
    all_fields = failure_fields+["duration_seconds"]
    history.append(widget("all", "all_cases", all_fields, table_spec("Every case, including skips",all_fields),0,28,height=10))
    return lv.Dashboard(datasets=datasets, pages=[lv.Page(name="overview",display_name="Model comparison",layout=layouts),lv.Page(name="details",display_name="Trends and details",layout=history)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "deploy"])
    parser.add_argument("--out", type=Path, default=Path("databricks/openai-evals.lvdash.json"))
    parser.add_argument("--deployment-out", type=Path, default=Path("reports/dashboard.json"))
    args = parser.parse_args()
    try:
        config = load_config()
        payload = build(config).as_dict()
        write_json(args.out, payload)
        args.out.with_suffix(".sql").write_text("\n\n".join("-- " + name + "\n" + sql + ";" for name,sql in dashboard_queries(config).items()) + "\n")
        (args.out.parent / "bootstrap.sql").write_text(";\n\n".join(ddl_statements(namespace())) + ";\n")
        if args.command == "build":
            print("Dashboard definition built: " + str(args.out))
            return 0
        from databricks.sdk import WorkspaceClient
        workspace = WorkspaceClient()
        result = deploy(workspace, payload, os.environ["DATABRICKS_WAREHOUSE_ID"],
                        parent_path=os.getenv("DATABRICKS_DASHBOARD_PARENT", "/Shared"),
                        dashboard_id=os.getenv("DATABRICKS_DASHBOARD_ID") or None)
        write_json(args.deployment_out, result)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(f'## Databricks dashboard\n\n[Open evaluation results]({result["url"]})\n\n'
                              f'Data destination: `{namespace()}`. Dashboard {result["action"]}.\n')
        print(result["url"])
        return 0
    except Exception as exc:
        print("Dashboard operation failed: " + safe_error(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
