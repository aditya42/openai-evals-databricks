"""Offline plumbing tests. Scripted judges are never model benchmark results."""
import copy
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from llm_eval import generation
from llm_eval.backend import TABLES, generation_rows, identifier, merge_statement, require_live, result_rows
from llm_eval.core import FRAMEWORKS, digest, eligible, load_cases, load_config, scored, validate_manifest, write_json
from llm_eval.reporting import summarize
from llm_eval.runner import evaluate


class ScriptedJudge:
    """Supply canned schema payloads to exercise the native frameworks without network calls."""
    name = "gpt-6-astra"

    def __init__(self, config=None):
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def respond(self, prompt, schema=None):
        self.usage["requests"] += 1
        if schema is None:
            return "Criteria: Test fixture only.\nSupporting Evidence: Test fixture only.\nScore: 3"
        if "deepeval" in schema.__module__:
            return schema.model_validate({"score": 10, "reason": "Test fixture only"})
        if "ragas" in schema.__module__:
            if schema.__name__ == "StatementGeneratorOutput":
                return schema.model_validate({"statements": ["The policy allows a return within 30 days."]})
            return schema.model_validate({"statements": [{"statement": "The policy allows a return within 30 days.", "reason": "Test fixture only", "verdict": 1}]})
        return schema.model_validate({"criteria": "Test fixture only", "supporting_evidence": "Test fixture only", "score": 3})

    async def arespond(self, prompt, schema=None):
        return self.respond(prompt, schema)

    def close(self):
        pass


def fixture_manifest(config, model=None):
    cases = load_cases(config)
    return {"schema_version": 1, "mode": "fixture", "model": model or config["models"][0], "run_id": "offline-test-only",
            "created_at": "2026-10-28T00:00:00+00:00", "timestamp": 1793145600,
            "dataset_sha256": digest(cases), "config_sha256": digest(config), "reasoning_effort": "low",
            "responses": {c["id"]: {"status": "ok", "output": c["reference"], "duration_seconds": 0.01,
                                        "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "usage_known": False} for c in cases}}


@pytest.mark.parametrize("framework,expected", [("deepeval",12),("ragas",4),("promptfoo",12),("trulens",10),("inspect",6)])
def test_native_framework_and_report_pipeline(framework, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    config = load_config()
    manifest = fixture_manifest(config)
    path = tmp_path / "fixture.json"
    write_json(path, manifest)
    report = evaluate(config,framework,manifest["model"],path,tmp_path/framework,judge_factory=ScriptedJudge)
    assert report["mode"] == "fixture"
    assert report["summary"]["eligible"] == expected
    assert report["summary"]["passed"] == expected
    assert report["summary"]["gate_passed"]
    assert len(ET.parse(tmp_path/framework/"junit.xml").findall("testcase")) == 12
    with pytest.raises(ValueError,match="fixtures"):
        require_live(report)


@pytest.mark.parametrize("framework", ["promptfoo","inspect"])
def test_wrong_answer_is_quality_failure_not_infrastructure_error(framework,tmp_path):
    config=load_config();manifest=fixture_manifest(config)
    manifest["responses"]["math_total"]["output"]="wrong"
    path=tmp_path/"fixture.json";write_json(path,manifest)
    report=evaluate(config,framework,manifest["model"],path,tmp_path/framework)
    row=next(r for r in report["results"] if r["case_id"]=="math_total")
    assert row["status"]=="failed" and row["score"]==0
    assert report["summary"]["error"]==0
    assert not report["summary"]["gate_passed"]


def test_generation_error_keeps_gate_red_and_preserves_null_score(tmp_path):
    config=load_config();manifest=fixture_manifest(config)
    manifest["responses"]["math_total"].update(status="error",error="RateLimitError",output="")
    path=tmp_path/"fixture.json";write_json(path,manifest)
    report=evaluate(config,"inspect",manifest["model"],path,tmp_path/"out")
    row=next(r for r in report["results"] if r["case_id"]=="math_total")
    assert row["status"]=="error" and row["score"] is None
    assert report["summary"]["coverage"]==pytest.approx(5/6)
    assert report["summary"]["pass_rate"]==pytest.approx(5/6)
    assert not report["summary"]["gate_passed"]
    assert len(ET.parse(tmp_path/"out/junit.xml").findall("testcase/error"))==1


@pytest.mark.parametrize("mutation",["model","dataset","config","missing"])
def test_mismatched_response_artifacts_are_rejected(mutation):
    config=load_config();cases=load_cases(config);manifest=fixture_manifest(config)
    if mutation=="model": manifest["model"]="wrong-model"
    elif mutation=="dataset": manifest["dataset_sha256"]="wrong-dataset"
    elif mutation=="config": manifest["config_sha256"]="wrong-config"
    else: manifest["responses"].pop(cases[0]["id"])
    with pytest.raises(ValueError):validate_manifest(manifest,cases,config,config["models"][0])


@pytest.mark.parametrize("value",[float("nan"),float("inf"),-0.1,1.1])
def test_invalid_scores_are_errors_not_passing_results(value):
    with pytest.raises(ValueError):scored("ragas","test",load_cases(load_config())[0],value,.9)


def test_empty_test_run_cannot_pass():
    assert not summarize([])["gate_passed"]


def test_delta_rows_and_idempotent_parameterized_merge(tmp_path):
    config=load_config();manifest=fixture_manifest(config)
    path=tmp_path/"fixture.json";write_json(path,manifest)
    report=evaluate(config,"inspect",manifest["model"],path,tmp_path/"out")
    report["mode"]="live";manifest["mode"]="live"
    cases,runs=result_rows(report,"mlflow-test-run")
    for table,rows in (("eval_runs",runs),("eval_cases",cases),("eval_generations",generation_rows(manifest))):
        assert rows and all(set(row)==set(TABLES[table]) for row in rows)
        statement=merge_statement("`main`.`llm_evals`",table)
        assert ":payload" in statement and "WHEN MATCHED THEN UPDATE" in statement
        assert "offline-test-only" not in statement
    assert len(generation_rows(manifest))==12
    assert all("output" not in row for row in generation_rows(manifest))


@pytest.mark.parametrize("value",["main; DROP TABLE x","a.b","a`b",""])
def test_catalog_and_schema_identifiers_are_validated(value):
    with pytest.raises(ValueError):identifier(value)


def test_incomplete_generations_are_not_successes_and_usage_is_preserved(tmp_path,monkeypatch):
    config=load_config();monkeypatch.setenv("OPENAI_API_KEY","unit-test-placeholder")
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):self.responses=self
        def create(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text="unfinished",status="incomplete",model=kwargs["model"],id="test-response",
                 usage=SimpleNamespace(input_tokens=8,output_tokens=12,output_tokens_details=SimpleNamespace(reasoning_tokens=9)))
        def close(self):pass
    monkeypatch.setattr(generation,"OpenAI",FakeClient)
    report=generation.generate(config,config["models"][0],tmp_path/"responses.json")
    assert len(calls)==12
    assert all("temperature" not in c and "logprobs" not in c for c in calls)
    assert all(r["status"]=="error" and r["output_tokens"]==12 and r["usage_known"] for r in report["responses"].values())


def test_fixture_generation_cannot_be_published():
    with pytest.raises(ValueError):generation_rows(fixture_manifest(load_config()))


def test_rejected_artifact_cannot_leave_stale_success_report(tmp_path):
    config=load_config();manifest=fixture_manifest(config)
    path=tmp_path/"fixture.json";write_json(path,manifest)
    output=tmp_path/"out"
    evaluate(config,"inspect",manifest["model"],path,output)
    manifest["dataset_sha256"]="wrong";write_json(path,manifest)
    with pytest.raises(ValueError):evaluate(config,"inspect",manifest["model"],path,output)
    assert not (output/"results.json").exists()
    assert not (output/"junit.xml").exists()
