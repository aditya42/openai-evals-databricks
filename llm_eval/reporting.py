from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

from .core import Result, write_json


def summarize(results: list[Result]) -> dict:
    counts = {s: sum(r.status == s for r in results) for s in ("passed", "failed", "error", "skipped")}
    eligible = len(results) - counts["skipped"]
    scores = [r.score for r in results if r.score is not None]
    return {**counts, "total": len(results), "eligible": eligible, "scored": len(scores),
            "mean_score": statistics.mean(scores) if scores else None,
            "pass_rate": counts["passed"] / eligible if eligible else None,
            "coverage": len(scores) / eligible if eligible else None,
            "gate_passed": bool(eligible) and counts["failed"] == 0 and counts["error"] == 0 and len(scores) == eligible}


def write_reports(output_dir: Path, results: list[Result], metadata: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    report = {**metadata, "summary": summary, "results": [r.to_dict() for r in results]}
    write_json(output_dir / "results.json", report)
    suite = ET.Element("testsuite", name=f'{metadata["framework"]}.{metadata["model"]}', tests=str(len(results)),
                       failures=str(summary["failed"]), errors=str(summary["error"]), skipped=str(summary["skipped"]),
                       time=f'{sum(r.duration_seconds for r in results):.6f}')
    for row in results:
        test = ET.SubElement(suite, "testcase", name=row.case_id, classname=f'{row.framework}.{row.model}', time=f'{row.duration_seconds:.6f}')
        props = ET.SubElement(test, "properties")
        for key, value in {"framework": row.framework, "model": row.model, "metric": row.metric,
                           "category": row.category, "mode": metadata["mode"], "score": row.score, "threshold": row.threshold}.items():
            if value is not None:
                ET.SubElement(props, "property", name=key, value=str(value))
        if row.status in ("failed", "error"):
            tag = "failure" if row.status == "failed" else "error"
            ET.SubElement(test, tag, message=f'{row.metric}: {row.status}; score={row.score}; threshold={row.threshold}').text = row.reason
        elif row.status == "skipped":
            ET.SubElement(test, "skipped", message=row.reason)
    ET.indent(suite)
    ET.ElementTree(suite).write(output_dir / "junit.xml", encoding="utf-8", xml_declaration=True)
    return report
