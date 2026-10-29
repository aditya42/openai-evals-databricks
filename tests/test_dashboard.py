from pathlib import Path

from databricks.labs.lsql.lakeview import Dashboard
from databricks.sdk.service.sql import StatementParameterListItem, StatementResponse, StatementStatus, StatementState, ServiceError
import sqlglot

from llm_eval.backend import Warehouse, ddl_statements, merge_statement, namespace
from llm_eval.core import FRAMEWORKS, load_config
from llm_eval.dashboard import DASHBOARD_NAME,build,dashboard_queries,deploy

import pytest
from types import SimpleNamespace
from databricks.sdk.service.dashboards import Dashboard as SDKDashboard


def test_dashboard_roundtrip_and_all_query_references():
    dashboard=build(load_config()).as_dict()
    assert Dashboard.from_dict(dashboard).as_dict()==dashboard
    names={d["name"] for d in dashboard["datasets"]}
    assert len(dashboard["pages"])==2 and len(names)==11
    for page in dashboard["pages"]:
        for layout in page["layout"]:
            for query in layout["widget"].get("queries",[]):
                assert query["query"]["datasetName"] in names
    sql=dashboard_queries(load_config())
    assert "PENDING" in sql["comparison"] and "INCOMPLETE" in sql["overview"]
    assert "eval_generations" in sql["generation"] and "eval_cases" not in sql["generation"]
    assert all("mode='live'" in sql["history_"+f] for f in FRAMEWORKS)


def test_sql_parses_as_databricks_dialect():
    statements=list(dashboard_queries(load_config()).values())+ddl_statements("`main`.`llm_evals`")
    statements += [merge_statement("`main`.`llm_evals`",t) for t in ("eval_runs","eval_cases","eval_generations")]
    for statement in statements:
        assert sqlglot.parse_one(statement,read="databricks") is not None


def test_sql_payload_uses_sdk_parameter_binding():
    calls=[]
    class API:
        def execute_statement(self,**kwargs):
            calls.append(kwargs)
            return StatementResponse(statement_id="test-id",status=StatementStatus(state=StatementState.SUCCEEDED))
    class Workspace:
        statement_execution=API()
    Warehouse(workspace=Workspace(),warehouse_id="test-warehouse").execute("SELECT :payload",[{"reason":"a quote ' and ;"}])
    assert isinstance(calls[0]["parameters"][0],StatementParameterListItem)
    assert calls[0]["statement"]=="SELECT :payload"


def test_concurrent_delta_write_retries_same_parameterized_statement(monkeypatch):
    calls=[]
    class API:
        def execute_statement(self,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                return StatementResponse(statement_id="conflict",status=StatementStatus(state=StatementState.FAILED,
                    error=ServiceError(message="[DELTA_CONCURRENT_APPEND] Conflict")))
            return StatementResponse(statement_id="success",status=StatementStatus(state=StatementState.SUCCEEDED))
    class Workspace:
        statement_execution=API()
    monkeypatch.setattr("llm_eval.backend.time.sleep",lambda seconds:None)
    Warehouse(workspace=Workspace(),warehouse_id="test").execute("SELECT :payload",[{"test":1}])
    assert len(calls)==2
    assert calls[0]["statement"]==calls[1]["statement"]
    assert calls[0]["parameters"][0].value==calls[1]["parameters"][0].value


def test_namespace_defaults_match_confirmed_workspace_and_allow_overrides(monkeypatch):
    monkeypatch.delenv("DATABRICKS_CATALOG",raising=False)
    monkeypatch.delenv("DATABRICKS_SCHEMA",raising=False)
    assert namespace()=="`workspace`.`default`"
    monkeypatch.setenv("DATABRICKS_CATALOG","sandbox")
    monkeypatch.setenv("DATABRICKS_SCHEMA","evaluations")
    assert namespace()=="`sandbox`.`evaluations`"


class FakeLakeview:
    def __init__(self,existing=()):
        self.existing=list(existing)
        self.created=[];self.updated=[];self.published=[]
    def list(self):return iter(self.existing)
    def get(self,dashboard_id):return next(d for d in self.existing if d.dashboard_id==dashboard_id)
    def create(self,dashboard):
        dashboard.dashboard_id="new-dashboard"
        dashboard.path=dashboard.parent_path+"/"+DASHBOARD_NAME+".lvdash.json"
        dashboard.etag="version-1"
        self.created.append(dashboard);self.existing.append(dashboard)
        return dashboard
    def update(self,dashboard_id,dashboard):
        self.updated.append((dashboard_id,dashboard))
        dashboard.dashboard_id=dashboard_id
        return dashboard
    def publish(self,dashboard_id,**kwargs):self.published.append((dashboard_id,kwargs))


def test_dashboard_repeated_deployment_reuses_identity_and_checks_version():
    api=FakeLakeview()
    workspace=SimpleNamespace(lakeview=api,config=SimpleNamespace(host="https://example.cloud.databricks.com"))
    first=deploy(workspace,{"datasets":[],"pages":[]},"warehouse-1")
    second=deploy(workspace,{"datasets":[],"pages":[]},"warehouse-2")
    assert first["dashboard_id"]==second["dashboard_id"]
    assert len(api.created)==1 and len(api.updated)==1
    assert api.updated[0][1].etag=="version-1"
    assert api.updated[0][1].warehouse_id=="warehouse-2"
    assert first["action"]=="created" and second["action"]=="updated"
    assert second["url"].endswith("/sql/dashboardsv3/new-dashboard/published")
    assert all(not kwargs["embed_credentials"] for _,kwargs in api.published)


def test_duplicate_dashboard_names_require_an_explicit_id():
    dashboards=[SDKDashboard(dashboard_id=str(i),display_name=DASHBOARD_NAME,path="/Shared/"+str(i)+".lvdash.json",etag="version-1") for i in range(2)]
    api=FakeLakeview(dashboards)
    workspace=SimpleNamespace(lakeview=api,config=SimpleNamespace(host="https://example.cloud.databricks.com"))
    with pytest.raises(ValueError,match="Multiple matching"):
        deploy(workspace,{},"warehouse")
    assert not api.created and not api.updated and not api.published
    result=deploy(workspace,{},"warehouse",dashboard_id="1")
    assert result["dashboard_id"]=="1" and not api.created


def test_dashboard_without_version_tag_is_not_overwritten():
    api=FakeLakeview([SDKDashboard(dashboard_id="existing",display_name=DASHBOARD_NAME,path="/Shared/dashboard.lvdash.json")])
    workspace=SimpleNamespace(lakeview=api,config=SimpleNamespace(host="https://example.cloud.databricks.com"))
    with pytest.raises(ValueError,match="version tag"):
        deploy(workspace,{},"warehouse")
    assert not api.created and not api.updated and not api.published
