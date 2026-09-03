"""Contract test: the MCP server exposes the expected tools with usable schemas."""

from __future__ import annotations

from ni_assembly_mcp.server import build_server

EXPECTED_TOOLS = {
    # Phase 2 — reference & list domains
    "get_departments",
    "get_parties",
    "list_all_party_groups",
    "list_organisations",
    "list_all_committees",
    "get_constituencies",
    "search_members",
    # Phase 3 — member detail, roles, register, party standing
    "get_detailed_member_information",
    "get_registered_interests",
    "list_ministerial_roles",
    "get_state_of_the_parties",
    # Phase 4 — parliamentary questions
    "search_parliamentary_questions",
    "get_question_details",
}


async def test_server_registers_expected_tools():
    server = build_server()
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_every_tool_has_a_description():
    server = build_server()
    for tool in await server.list_tools():
        assert tool.description and len(tool.description) > 20


async def test_search_members_schema_params():
    server = build_server()
    tool = next(t for t in await server.list_tools() if t.name == "search_members")
    props = tool.input_schema["properties"]
    assert {"name", "constituency_id", "party_id", "as_of_date", "current_only", "max_results"} <= props.keys()
    assert props["constituency_id"]["description"]
    assert tool.input_schema.get("required", []) == []


async def test_list_all_committees_enum():
    server = build_server()
    tool = next(t for t in await server.list_tools() if t.name == "list_all_committees")
    schema = tool.input_schema["properties"]["committee_type"]
    # Literal -> enum, possibly nested under allOf/$ref-free inline
    enum = schema.get("enum") or next(iter(schema.get("allOf", [{}])), {}).get("enum")
    assert set(enum) == {"all", "standing", "statutory", "adhoc", "other"}
