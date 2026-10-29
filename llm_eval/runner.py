from __future__ import annotations

import os
import time
from pathlib import Path

from .adapters import get_adapter
from .core import METRICS, Result, digest, eligible, load_cases, read_json, safe_error, scored, validate_manifest
from .judge import ResponsesJudge
from .reporting import write_reports


def evaluate(config: dict, framework: str, model: str, response_path: Path, output_dir: Path, judge_factory=ResponsesJudge):
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # Do not leave a previous run's report available after an invalid new artifact.
    for name in ("results.json", "junit.xml"):
        (output_dir / name).unlink(missing_ok=True)
    cases = load_cases(config)
    manifest = read_json(response_path)
    validate_manifest(manifest, cases, config, model)
    threshold = config["thresholds"][framework]
    results = []
    judge = None
    init_error = None
    batch = {}
    active = [c for c in cases if eligible(framework, c) and manifest["responses"][c["id"]]["status"] == "ok"]
    try:
        adapter = get_adapter(framework)
        if framework in ("deepeval", "ragas", "trulens") and active:
            judge = judge_factory(config)
        if framework == "promptfoo" and active:
            batch = adapter.score_batch(active, response_path, output_dir)
    except Exception as exc:
        init_error = safe_error(exc)
    try:
        for case in cases:
            common = dict(framework=framework, model=model, case_id=case["id"], category=case["category"], metric=METRICS[framework], threshold=threshold)
            if not eligible(framework, case):
                results.append(Result(**common, status="skipped", reason="Outside this metric's applicability"))
                continue
            response = manifest["responses"][case["id"]]
            if response["status"] != "ok":
                results.append(Result(**common, status="error", reason="GenerationError: " + response.get("error", "unknown")))
                continue
            if init_error:
                results.append(Result(**common, status="error", reason="FrameworkInitializationError: " + init_error))
                continue
            start = time.perf_counter()
            try:
                if framework == "promptfoo":
                    row = batch[case["id"]]
                    if "error" in row:
                        raise RuntimeError(row["error"])
                    value, reason = row["score"], row["reason"]
                    duration = row["duration_seconds"]
                else:
                    value, reason = adapter.score_case(case, response["output"], judge, threshold)
                    duration = time.perf_counter() - start
                results.append(scored(framework, model, case, value, threshold, duration, reason))
            except Exception as exc:
                results.append(Result(**common, status="error", duration_seconds=time.perf_counter() - start, reason=safe_error(exc)))
    finally:
        if judge is not None:
            judge.close()
    metadata = {"schema_version": 1, "mode": manifest["mode"], "batch_id": manifest["run_id"], "model": model,
                "framework": framework, "metric": METRICS[framework], "created_at": manifest["created_at"],
                "timestamp": manifest["timestamp"], "dataset_sha256": digest(cases), "config_sha256": digest(config),
                "judge_model": config["judge_model"] if framework in ("deepeval", "ragas", "trulens") else "none",
                "judge_usage": judge.usage if judge is not None else {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "commit_sha": os.getenv("GITHUB_SHA", "local"), "git_branch": os.getenv("GITHUB_REF_NAME", "local"),
                "ci_url": f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}' if os.getenv("GITHUB_REPOSITORY") and os.getenv("GITHUB_RUN_ID") else ""}
    return write_reports(output_dir, results, metadata)
