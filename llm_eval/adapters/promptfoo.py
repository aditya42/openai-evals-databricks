import json
import os
import subprocess
from pathlib import Path

from ..core import ROOT, write_json


def score_batch(cases, response_path: Path, output_dir: Path):
    config_file = output_dir / "promptfoo-config.json"
    native_file = output_dir / "promptfoo-native.json"
    native_file.unlink(missing_ok=True)
    provider = Path(__file__).with_name("replay_provider.cjs")
    config = {
        "description": "Native Promptfoo assertions on shared Responses API output",
        "prompts": ["{{case_id}}"],
        "providers": [{"id": "file://" + str(provider), "config": {"responses": str(response_path.resolve())}}],
        "tests": [{"description": c["id"], "vars": {"case_id": c["id"]}, "assert": c["assertions"]} for c in cases],
        "evaluateOptions": {"maxConcurrency": 1}, "writeLatestResults": False,
    }
    write_json(config_file, config)
    # Cached assertions do not need target, tracking, or CI credentials.
    local_env = {k: v for k, v in os.environ.items()
                 if not k.startswith(("OPENAI_", "DATABRICKS_", "MLFLOW_"))
                 and k not in ("PROMPTFOO_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")}
    env = {**local_env, "PROMPTFOO_DISABLE_TELEMETRY": "true", "PROMPTFOO_DISABLE_UPDATE": "true",
           "PROMPTFOO_DISABLE_REMOTE_GENERATION": "true", "PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION": "true",
           "PROMPTFOO_CONFIG_DIR": str(output_dir / ".promptfoo")}
    package_dir = ROOT / "node_modules/promptfoo"
    binary = json.loads((package_dir / "package.json").read_text())["bin"]["promptfoo"]
    executable = package_dir / binary
    guard = Path(__file__).with_name("deny_network.cjs")
    result = subprocess.run(["node", "--require", str(guard), str(executable), "eval", "--config", str(config_file.resolve()),
                             "--output", str(native_file.resolve()), "--no-cache", "--no-progress-bar"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    (output_dir / "promptfoo-cli.log").write_text(result.stdout + "\n" + result.stderr)
    # A valid quality failure gives a nonzero CLI exit code; parse its report.
    if not native_file.is_file():
        raise RuntimeError("Promptfoo produced no report; see promptfoo-cli.log")
    report = json.loads(native_file.read_text())
    by_case = {}
    for row in report["results"]["results"]:
        case_id = row["testCase"]["vars"]["case_id"]
        if case_id in by_case:
            raise ValueError("Promptfoo returned duplicate cases")
        # Promptfoo also sets `error` on assertion failures (failureReason=1).
        # A scored assertion failure must remain a quality failure, not an outage.
        if row.get("failureReason") == 2 or not row.get("gradingResult"):
            by_case[case_id] = {"error": "PromptfooEvaluationError"}
        else:
            grade = row["gradingResult"]
            by_case[case_id] = {"score": float(grade["score"]), "reason": grade.get("reason", ""),
                                "duration_seconds": row.get("latencyMs", 0) / 1000}
    if set(by_case) != {c["id"] for c in cases}:
        raise ValueError("Promptfoo report omitted or added cases")
    return by_case
