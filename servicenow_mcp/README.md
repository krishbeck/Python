# ServiceNow MCP server (Python)

An [MCP](https://modelcontextprotocol.io) server that lets Claude Code, Claude Desktop, or any MCP client
work with a ServiceNow instance through the REST Table API.

## Tools

| Tool | What it does |
| --- | --- |
| `list_incidents` | List incidents (filter by active, priority, group, assignee, text) |
| `get_incident` | Full details of an incident by number (`INC0010001`) |
| `create_incident` | Open a new incident |
| `update_incident` | Update any fields; `state` accepts names like `in_progress` |
| `add_incident_comment` | Add a customer-visible comment or an internal work note |
| `resolve_incident` | Resolve with a close code and notes |
| `query_table` | Encoded-query search on any table (`change_request`, `sys_user`, `cmdb_ci`, ...) |
| `get_record` | Fetch any record by table + `sys_id` |

## Setup

Requires Python 3.10+.

```bash
cd servicenow_mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Configure with environment variables (see `.env.example`):

| Variable | Required | Notes |
| --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | yes | e.g. `https://dev12345.service-now.com` |
| `SERVICENOW_USERNAME` / `SERVICENOW_PASSWORD` | one of | Basic auth; use an integration user with the `itil` role |
| `SERVICENOW_TOKEN` | one of | OAuth bearer token; takes precedence over basic auth |
| `SERVICENOW_TIMEOUT` | no | Request timeout in seconds (default 30) |

No instance? Get a free Personal Developer Instance at <https://developer.servicenow.com>.

## Connect it to Claude Code

```bash
claude mcp add servicenow \
  -e SERVICENOW_INSTANCE_URL=https://dev12345.service-now.com \
  -e SERVICENOW_USERNAME=admin \
  -e SERVICENOW_PASSWORD='your-password' \
  -- /path/to/servicenow_mcp/.venv/bin/servicenow-mcp
```

Then ask Claude things like *"show me open P1 incidents"* or *"add a work note to INC0010001 saying the patch is deployed"*.

## Connect it to Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "/path/to/servicenow_mcp/.venv/bin/servicenow-mcp",
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://dev12345.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

## Tests

```bash
pip install -e ".[test]"
pytest
```

Tests mock ServiceNow with `respx`, so no instance is needed.
