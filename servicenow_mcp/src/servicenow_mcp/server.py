"""MCP server exposing ServiceNow incidents and tables as tools.

Run with:  python -m servicenow_mcp   (stdio transport, for Claude Code / Claude Desktop)
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.mcpserver import MCPServer

from servicenow_mcp.client import ServiceNowClient, ServiceNowConfig, ServiceNowError

mcp = MCPServer(
    name="servicenow",
    instructions=(
        "Tools for reading and updating a ServiceNow instance. Incidents can be referenced "
        "by number (e.g. INC0010001). Encoded queries use ServiceNow syntax, e.g. "
        "'active=true^priority=1^ORDERBYDESCsys_created_on'. Agile stories (rm_story, "
        "numbers like STRY0010001) need the Agile Development plugin on the instance."
    ),
)

INCIDENT_FIELDS = [
    "sys_id",
    "number",
    "short_description",
    "description",
    "state",
    "priority",
    "urgency",
    "impact",
    "category",
    "assignment_group",
    "assigned_to",
    "caller_id",
    "opened_at",
    "sys_updated_on",
]
INCIDENT_STATES = {"new": 1, "in_progress": 2, "on_hold": 3, "resolved": 6, "closed": 7, "canceled": 8}

STORY_FIELDS = [
    "sys_id",
    "number",
    "short_description",
    "description",
    "acceptance_criteria",
    "state",
    "priority",
    "story_points",
    "product",
    "epic",
    "sprint",
    "assignment_group",
    "assigned_to",
    "sys_updated_on",
]
# rm_story state values (Agile Development 2.0).
STORY_STATES = {
    "draft": -6,
    "ready": 1,
    "work_in_progress": 2,
    "ready_for_testing": -7,
    "testing": -8,
    "complete": 3,
    "cancelled": 4,
}

_TABLE_RE = re.compile(r"^[a-z0-9_]+$")
_SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_client: ServiceNowClient | None = None


def get_client() -> ServiceNowClient:
    global _client
    if _client is None:
        _client = ServiceNowClient(ServiceNowConfig.from_env())
    return _client


def set_client(client: ServiceNowClient | None) -> None:
    """Override the client (used by tests)."""
    global _client
    _client = client


def _check_table(table: str) -> str:
    if not _TABLE_RE.match(table):
        raise ServiceNowError(f"Invalid table name: {table!r}")
    return table


def _check_sys_id(sys_id: str) -> str:
    if not _SYS_ID_RE.match(sys_id):
        raise ServiceNowError(f"Invalid sys_id: {sys_id!r} (expected 32 hex characters)")
    return sys_id


def _q(value: str) -> str:
    """Guard a value interpolated into an encoded query against injecting extra clauses."""
    if "^" in value or "\n" in value:
        raise ServiceNowError(f"Invalid character in query value: {value!r}")
    return value


def _split_fields(fields: str) -> list[str] | None:
    return [f.strip() for f in fields.split(",") if f.strip()] or None


async def _sys_id_for(table: str, number: str) -> str:
    record = await get_client().get_by_number(table, number.strip().upper())
    return record["sys_id"]


async def _incident_sys_id(number: str) -> str:
    return await _sys_id_for("incident", number)


async def _reference(table: str, value: str) -> str:
    """Resolve a record number (e.g. EPIC0001001) to a sys_id; pass sys_ids through."""
    value = value.strip()
    return value if _SYS_ID_RE.match(value) else await _sys_id_for(table, value)


def _with_state(data: dict[str, Any], states: dict[str, int]) -> dict[str, Any]:
    """Translate a "state" given by name into its numeric value."""
    data = dict(data)
    state = data.get("state")
    if isinstance(state, str) and state.lower() in states:
        data["state"] = states[state.lower()]
    return data


# --- Generic table tools ---------------------------------------------------


@mcp.tool()
async def query_table(
    table: str, query: str = "", fields: str = "", limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Query any ServiceNow table (incident, change_request, sys_user, cmdb_ci, ...).

    Args:
        table: Table name, e.g. "incident" or "sys_user".
        query: Encoded query, e.g. "active=true^priority=1". Empty returns all rows.
        fields: Comma-separated fields to return. Empty returns every field.
        limit: Maximum rows to return (1-1000).
        offset: Rows to skip, for paging.
    """
    limit = max(1, min(limit, 1000))
    return await get_client().query(
        _check_table(table), query=query, fields=_split_fields(fields), limit=limit, offset=max(0, offset)
    )


@mcp.tool()
async def get_record(table: str, sys_id: str, fields: str = "") -> dict[str, Any]:
    """Fetch a single record from any table by its sys_id."""
    return await get_client().get(_check_table(table), _check_sys_id(sys_id), fields=_split_fields(fields))


# --- Incident tools ---------------------------------------------------------


@mcp.tool()
async def list_incidents(
    active_only: bool = True,
    priority: int | None = None,
    assignment_group: str = "",
    assigned_to: str = "",
    text: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List incidents, newest first.

    Args:
        active_only: Only return open incidents.
        priority: Filter by priority (1 = Critical ... 5 = Planning).
        assignment_group: Filter by assignment group name.
        assigned_to: Filter by assignee name or user ID.
        text: Match text in the short description.
        limit: Maximum incidents to return (1-100).
    """
    clauses = []
    if active_only:
        clauses.append("active=true")
    if priority is not None:
        clauses.append(f"priority={int(priority)}")
    if assignment_group:
        clauses.append(f"assignment_group.name={_q(assignment_group)}")
    if assigned_to:
        who = _q(assigned_to)
        clauses.append(f"assigned_to.name={who}^ORassigned_to.user_name={who}")
    if text:
        clauses.append(f"short_descriptionLIKE{_q(text)}")
    clauses.append("ORDERBYDESCsys_created_on")

    return await get_client().query(
        "incident", query="^".join(clauses), fields=INCIDENT_FIELDS, limit=max(1, min(limit, 100))
    )


@mcp.tool()
async def get_incident(number: str) -> dict[str, Any]:
    """Get the full details of an incident by its number, e.g. INC0010001."""
    return await get_client().get_by_number("incident", number.strip().upper())


@mcp.tool()
async def create_incident(
    short_description: str,
    description: str = "",
    urgency: int = 3,
    impact: int = 3,
    category: str = "",
    caller_id: str = "",
    assignment_group: str = "",
) -> dict[str, Any]:
    """Create a new incident. Returns the created record, including its number.

    Args:
        short_description: One-line summary.
        description: Full description.
        urgency: 1 = High, 2 = Medium, 3 = Low.
        impact: 1 = High, 2 = Medium, 3 = Low.
        category: e.g. "software", "hardware", "network", "inquiry".
        caller_id: sys_id or user name of the caller.
        assignment_group: sys_id or name of the assignment group.
    """
    data: dict[str, Any] = {"short_description": short_description, "urgency": urgency, "impact": impact}
    optional = {
        "description": description,
        "category": category,
        "caller_id": caller_id,
        "assignment_group": assignment_group,
    }
    data.update({k: v for k, v in optional.items() if v})
    return await get_client().create("incident", data)


@mcp.tool()
async def update_incident(number: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update fields on an incident, e.g. {"urgency": 1, "assigned_to": "<sys_id>"}.

    A "state" given by name (new, in_progress, on_hold, resolved, closed, canceled)
    is translated to its numeric value.
    """
    if not fields:
        raise ServiceNowError("No fields to update")
    data = _with_state(fields, INCIDENT_STATES)
    return await get_client().update("incident", await _incident_sys_id(number), data)


@mcp.tool()
async def add_incident_comment(number: str, text: str, internal: bool = False) -> dict[str, Any]:
    """Add a comment to an incident.

    Args:
        number: Incident number.
        text: Comment text.
        internal: True for a work note (agents only), False for a customer-visible comment.
    """
    field = "work_notes" if internal else "comments"
    return await get_client().update("incident", await _incident_sys_id(number), {field: text})


@mcp.tool()
async def resolve_incident(
    number: str, close_notes: str, close_code: str = "Solution provided"
) -> dict[str, Any]:
    """Resolve an incident with resolution notes.

    Args:
        number: Incident number.
        close_notes: What was done to resolve it.
        close_code: Resolution code; must match a choice on your instance
            (e.g. "Solution provided", "Workaround provided", "Duplicate").
    """
    data = {"state": INCIDENT_STATES["resolved"], "close_code": close_code, "close_notes": close_notes}
    return await get_client().update("incident", await _incident_sys_id(number), data)


# --- Agile story tools (rm_story) -------------------------------------------


@mcp.tool()
async def create_story(
    short_description: str,
    description: str = "",
    acceptance_criteria: str = "",
    story_points: int | None = None,
    priority: int | None = None,
    product: str = "",
    epic: str = "",
    sprint: str = "",
    assignment_group: str = "",
    assigned_to: str = "",
    state: str = "draft",
) -> dict[str, Any]:
    """Create an Agile user story. Returns the created record, including its number (STRY...).

    Args:
        short_description: Story title, e.g. "As a user, I want to reset my password".
        description: Full description.
        acceptance_criteria: Conditions that must be met for the story to be accepted.
        story_points: Estimate, e.g. 1, 2, 3, 5, 8.
        priority: 1 = Critical ... 4 = Low.
        product: sys_id or name of the product.
        epic: Epic number (EPIC0001001) or sys_id.
        sprint: Sprint number (SPNT0001001) or sys_id.
        assignment_group: sys_id or name of the assignment group.
        assigned_to: sys_id or user name of the assignee.
        state: draft, ready, work_in_progress, ready_for_testing, testing, complete, cancelled.
    """
    data: dict[str, Any] = {"short_description": short_description}
    optional: dict[str, Any] = {
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "story_points": story_points,
        "priority": priority,
        "product": product,
        "assignment_group": assignment_group,
        "assigned_to": assigned_to,
        "state": state,
    }
    data.update({k: v for k, v in optional.items() if v not in ("", None)})
    if epic:
        data["epic"] = await _reference("rm_epic", epic)
    if sprint:
        data["sprint"] = await _reference("rm_sprint", sprint)
    return await get_client().create("rm_story", _with_state(data, STORY_STATES))


@mcp.tool()
async def get_story(number: str) -> dict[str, Any]:
    """Get the full details of a story by its number, e.g. STRY0010001."""
    return await get_client().get_by_number("rm_story", number.strip().upper())


@mcp.tool()
async def list_stories(
    active_only: bool = True,
    epic: str = "",
    sprint: str = "",
    assigned_to: str = "",
    text: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List stories, newest first.

    Args:
        active_only: Only return open stories.
        epic: Filter by epic number (EPIC...).
        sprint: Filter by sprint number (SPNT...).
        assigned_to: Filter by assignee name or user ID.
        text: Match text in the short description.
        limit: Maximum stories to return (1-100).
    """
    clauses = []
    if active_only:
        clauses.append("active=true")
    if epic:
        clauses.append(f"epic.number={_q(epic.strip().upper())}")
    if sprint:
        clauses.append(f"sprint.number={_q(sprint.strip().upper())}")
    if assigned_to:
        who = _q(assigned_to)
        clauses.append(f"assigned_to.name={who}^ORassigned_to.user_name={who}")
    if text:
        clauses.append(f"short_descriptionLIKE{_q(text)}")
    clauses.append("ORDERBYDESCsys_created_on")

    return await get_client().query(
        "rm_story", query="^".join(clauses), fields=STORY_FIELDS, limit=max(1, min(limit, 100))
    )


@mcp.tool()
async def update_story(number: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update fields on a story, e.g. {"story_points": 5, "state": "ready"}.

    A "state" given by name (see create_story) is translated to its numeric value,
    and "epic" / "sprint" may be given as record numbers.
    """
    if not fields:
        raise ServiceNowError("No fields to update")
    data = _with_state(fields, STORY_STATES)
    for field, table in (("epic", "rm_epic"), ("sprint", "rm_sprint")):
        if data.get(field):
            data[field] = await _reference(table, str(data[field]))
    return await get_client().update("rm_story", await _sys_id_for("rm_story", number), data)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
