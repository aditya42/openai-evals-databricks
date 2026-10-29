from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORKS = ("deepeval", "ragas", "promptfoo", "trulens", "inspect")
METRICS = {"deepeval": "correctness", "ragas": "faithfulness", "promptfoo": "assertions", "trulens": "answer_relevance", "inspect": "exact_match"}
DEFAULT_SYSTEM = "Follow the user's instructions precisely. Use supplied context when present. Do not invent missing facts."


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temp.replace(path)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_config(path: Path | None = None) -> dict:
    value = read_json(path or ROOT / "config.json")
    if len(value["models"]) != len(set(value["models"])):
        raise ValueError("Duplicate models in config")
    if set(value["thresholds"]) != set(FRAMEWORKS):
        raise ValueError("Configure exactly five framework thresholds")
    for threshold in value["thresholds"].values():
        if not 0 <= threshold <= 1:
            raise ValueError("Thresholds must be between 0 and 1")
    return value


def load_cases(config: dict) -> list[dict]:
    cases = read_json(ROOT / config["dataset"])
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Dataset must have unique nonempty cases")
    for case in cases:
        for key in ("id", "category", "prompt", "contexts", "reference", "assertions", "inspect_target"):
            if key not in case:
                raise ValueError(f"Missing case field {key}")
        if not case["assertions"]:
            raise ValueError("Each case needs at least one Promptfoo assertion")
    return cases


def run_id() -> str:
    if os.getenv("LLM_EVAL_BATCH_ID"):
        return os.environ["LLM_EVAL_BATCH_ID"]
    if os.getenv("GITHUB_RUN_ID"):
        return f'{os.environ["GITHUB_RUN_ID"]}-{os.getenv("GITHUB_RUN_ATTEMPT", "1")}'
    return str(uuid.uuid4())


def system_prompt(case: dict) -> str:
    return case.get("system", DEFAULT_SYSTEM)


def user_prompt(case: dict) -> str:
    if not case["contexts"]:
        return case["prompt"]
    return "CONTEXT:\n" + "\n".join(case["contexts"]) + "\n\nQUESTION:\n" + case["prompt"]


def evaluation_input(case: dict) -> str:
    return "SYSTEM:\n" + system_prompt(case) + "\n\nUSER:\n" + user_prompt(case)


def eligible(framework: str, case: dict) -> bool:
    if framework == "ragas":
        return bool(case["contexts"])
    if framework == "trulens":
        return case["category"] != "safety"
    if framework == "inspect":
        return case["inspect_target"] is not None
    return True


def safe_error(exc: Exception) -> str:
    """Only error class/status; provider errors can echo prompts and credentials."""
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status else "")


@dataclass
class Result:
    framework: str
    model: str
    case_id: str
    category: str
    metric: str
    status: str
    score: float | None = None
    threshold: float | None = None
    duration_seconds: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def scored(framework: str, model: str, case: dict, score: float, threshold: float, duration: float = 0, reason: str = "") -> Result:
    score = float(score)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Invalid metric score; expected a finite number in [0,1]")
    return Result(framework, model, case["id"], case["category"], METRICS[framework], "passed" if score >= threshold else "failed", score, threshold, duration, reason)


def validate_manifest(manifest: dict, cases: list[dict], config: dict, model: str) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("mode") not in ("live", "fixture"):
        raise ValueError("Invalid generation artifact")
    if manifest["model"] != model or manifest["dataset_sha256"] != digest(cases) or manifest["config_sha256"] != digest(config):
        raise ValueError("Model, dataset, or configuration differs from generation")
    if set(manifest["responses"]) != {c["id"] for c in cases}:
        raise ValueError("Missing or extra generation responses")
    for case in cases:
        row = manifest["responses"][case["id"]]
        if row["status"] not in ("ok", "error"):
            raise ValueError("Invalid generation response status")
        if row["status"] == "ok" and not str(row.get("output", "")).strip():
            raise ValueError("Successful generation has empty output")
