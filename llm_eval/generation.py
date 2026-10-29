from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from .core import digest, load_cases, run_id, safe_error, system_prompt, user_prompt, write_json


def generate(config: dict, model: str, output: Path) -> dict:
    if model not in config["models"]:
        raise ValueError("Choose a configured model")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY in the environment or repository secrets")
    cases = load_cases(config)
    manifest = {"schema_version": 1, "mode": "live", "model": model, "run_id": run_id(),
                "created_at": datetime.now(timezone.utc).isoformat(), "timestamp": int(time.time()),
                "dataset_sha256": digest(cases), "config_sha256": digest(config),
                "reasoning_effort": config["reasoning_effort"], "responses": {}}
    client = OpenAI(timeout=config["timeout_seconds"], max_retries=2)
    try:
        for case in cases:
            start = time.perf_counter()
            row = {"status": "error", "output": "", "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "usage_known": False}
            try:
                response = client.responses.create(
                    model=model, instructions=system_prompt(case), input=user_prompt(case),
                    reasoning={"effort": config["reasoning_effort"]},
                    max_output_tokens=config["max_output_tokens"], store=False,
                )
                row.update(output=response.output_text, resolved_model=response.model, response_id=response.id)
                if response.usage is not None:
                    details = getattr(response.usage, "output_tokens_details", None)
                    row.update(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                               reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0, usage_known=True)
                if response.status != "completed":
                    row["error"] = "response_" + str(response.status)
                elif not response.output_text.strip():
                    row["error"] = "empty_output"
                else:
                    row["status"] = "ok"
            except Exception as exc:
                row["error"] = safe_error(exc)
            row["duration_seconds"] = time.perf_counter() - start
            manifest["responses"][case["id"]] = row
            write_json(output, manifest)
    finally:
        client.close()
    return manifest
