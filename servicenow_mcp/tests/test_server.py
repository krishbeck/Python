import asyncio
import json

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from servicenow_mcp import server
from servicenow_mcp.client import ServiceNowClient, ServiceNowConfig, ServiceNowError

BASE = "https://example.service-now.com/api/now"
SYS_ID = "a" * 32


@pytest.fixture
def mock_sn():
    config = ServiceNowConfig(instance_url="https://example.service-now.com", username="u", password="p")
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        server.set_client(ServiceNowClient(config))
        yield router
    server.set_client(None)


def call_error(name, **args):
    with pytest.raises(ToolError) as exc_info:
        run(server.mcp.call_tool(name, args))
    return str(exc_info.value)


def run(coro):
    return asyncio.run(coro)


def call(name, **args):
    result = run(server.mcp.call_tool(name, args))
    assert not result.is_error, result.content
    content = result.structured_content
    # Lists are wrapped as {"result": [...]}; dicts are returned as-is.
    return content["result"] if set(content) == {"result"} else content


def test_tools_registered():
    names = {t.name for t in run(server.mcp.list_tools())}
    assert names == {
        "query_table",
        "get_record",
        "list_incidents",
        "get_incident",
        "create_incident",
        "update_incident",
        "add_incident_comment",
        "resolve_incident",
    }


def test_list_incidents_builds_query(mock_sn):
    route = mock_sn.get("/table/incident").respond(json={"result": [{"number": "INC001"}]})
    assert call("list_incidents", priority=1, text="email") == [{"number": "INC001"}]
    params = route.calls.last.request.url.params
    assert params["sysparm_query"] == "active=true^priority=1^short_descriptionLIKEemail^ORDERBYDESCsys_created_on"
    assert route.calls.last.request.headers["Authorization"].startswith("Basic ")


def test_list_incidents_rejects_query_injection(mock_sn):
    assert "Invalid character" in call_error("list_incidents", text="x^active=false")


def test_create_incident(mock_sn):
    route = mock_sn.post("/table/incident").respond(json={"result": {"number": "INC002", "sys_id": SYS_ID}})
    record = call("create_incident", short_description="Printer down", urgency=2)
    assert record["number"] == "INC002"
    assert json.loads(route.calls.last.request.content) == {
        "short_description": "Printer down",
        "urgency": 2,
        "impact": 3,
    }


def test_update_incident_translates_state(mock_sn):
    mock_sn.get("/table/incident").respond(json={"result": [{"sys_id": SYS_ID, "number": "INC003"}]})
    patch = mock_sn.patch(f"/table/incident/{SYS_ID}").respond(json={"result": {"state": "In Progress"}})
    call("update_incident", number="inc003", fields={"state": "in_progress"})
    assert json.loads(patch.calls.last.request.content) == {"state": 2}


def test_add_work_note(mock_sn):
    mock_sn.get("/table/incident").respond(json={"result": [{"sys_id": SYS_ID}]})
    patch = mock_sn.patch(f"/table/incident/{SYS_ID}").respond(json={"result": {}})
    call("add_incident_comment", number="INC004", text="Looking into it", internal=True)
    assert json.loads(patch.calls.last.request.content) == {"work_notes": "Looking into it"}


def test_get_incident_not_found(mock_sn):
    mock_sn.get("/table/incident").respond(json={"result": []})
    assert "No incident record found" in call_error("get_incident", number="INC999")


def test_http_error_is_reported(mock_sn):
    mock_sn.get(f"/table/incident/{SYS_ID}").respond(
        401, json={"error": {"message": "User Not Authenticated", "detail": "Required to provide Auth information"}}
    )
    assert "HTTP 401: User Not Authenticated" in call_error("get_record", table="incident", sys_id=SYS_ID)


def test_invalid_table_rejected(mock_sn):
    assert "Invalid table name" in call_error("query_table", table="../../api/other")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "dev1.service-now.com/")
    monkeypatch.setenv("SERVICENOW_TOKEN", "tok")
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)
    config = ServiceNowConfig.from_env()
    assert config.instance_url == "https://dev1.service-now.com"
    assert config.token == "tok"


def test_config_requires_credentials(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev1.service-now.com")
    for var in ("SERVICENOW_TOKEN", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ServiceNowError):
        ServiceNowConfig.from_env()


def test_bearer_token_header():
    config = ServiceNowConfig(instance_url="https://example.service-now.com", token="tok")
    with respx.mock(base_url=BASE) as router:
        route = router.get("/table/sys_user").respond(json={"result": []})
        run(ServiceNowClient(config).query("sys_user"))
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"
