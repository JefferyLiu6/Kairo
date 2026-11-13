"""The tool registry: one spec per tool, and a domain that is incomplete cannot be registered."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict

from kairo.core.domain.types import PMAction
from kairo.domains.catalog import DOMAINS
from kairo.domains.registry import DomainSpec, Registry, ToolContext, ToolSpec

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))
CTX = ToolContext(user_id="u", thread_id="pm-t", data_dir="/nowhere", now=NOW, message="water the plants")


class Water(BaseModel):
    """Propose watering a plant."""

    model_config = ConfigDict(extra="forbid")
    plant: str


class ListPlants(BaseModel):
    """Show the plants."""

    model_config = ConfigDict(extra="forbid")


def propose_tool(**overrides) -> ToolSpec:
    spec = dict(name="water_plant", kind="propose", args=Water, examples=("water the fern",),
                propose=lambda args, ctx: [PMAction("todo_add", {"title": f"water {args.plant}"})])
    return ToolSpec(**{**spec, **overrides})


def read_tool(**overrides) -> ToolSpec:
    spec = dict(name="list_plants", kind="read", args=ListPlants, read=lambda args, ctx: "fern, cactus")
    return ToolSpec(**{**spec, **overrides})


def garden(*tools, **overrides) -> DomainSpec:
    return DomainSpec(**{"name": "garden", "summary": "Plants and watering.", "tools": tools, **overrides})


def test_a_tool_becomes_a_strict_function_definition_named_for_the_model():
    schema = propose_tool().schema()["function"]
    assert schema["name"] == "water_plant" and schema["description"] == "Propose watering a plant."
    assert schema["strict"] is True and schema["parameters"]["required"] == ["plant"]
    assert schema["parameters"]["additionalProperties"] is False


def test_registered_tools_are_found_by_name_and_grouped_by_how_the_domain_runs():
    registry = Registry()
    registry.register(garden(propose_tool(), read_tool()))
    registry.register(DomainSpec(name="assistant", summary="Everything else.", mode="agent"))

    assert registry.tool("water_plant").kind == "propose"
    assert [tool.name for tool in registry.inline_tools()] == ["water_plant", "list_plants"]
    assert [domain.name for domain in registry.agent_domains()] == ["assistant"]
    with pytest.raises(ValueError, match="unknown tool"):
        registry.tool("prune_plant")


@pytest.mark.parametrize("domain, clue", [
    (garden(propose_tool(propose=None)), "propose"),                 # nothing to turn the call into a command
    (garden(propose_tool(examples=())), "example"),                  # no example message
    (garden(read_tool(read=None)), "read"),                          # a read tool that reads nothing
    (garden(propose_tool(), propose_tool()), "water_plant"),         # the same tool twice
    (garden(propose_tool(name="Water Plant")), "name"),              # not a name a model can call
    (garden(), "no tools"),                                          # an inline domain with nothing to call
])
def test_an_incomplete_domain_cannot_be_registered(domain, clue):
    with pytest.raises(ValueError, match=clue):
        Registry().register(domain)


def test_a_tool_name_must_be_unique_across_domains():
    registry = Registry()
    registry.register(garden(propose_tool()))
    with pytest.raises(ValueError, match="water_plant"):
        registry.register(DomainSpec(name="greenhouse", summary="More plants.", tools=(propose_tool(),)))
    with pytest.raises(ValueError, match="garden"):
        registry.register(garden(read_tool()))


def test_the_fingerprint_changes_when_any_tool_the_model_sees_changes():
    class WaterMore(Water):
        """Propose watering a plant, generously."""

    one, other = Registry(), Registry()
    one.register(garden(propose_tool()))
    other.register(garden(propose_tool(args=WaterMore)))
    assert one.fingerprint() != other.fingerprint()
    assert one.fingerprint() == one.fingerprint() and len(one.fingerprint()) == 8


def test_the_routing_lines_describe_each_domain_once():
    registry = Registry()
    registry.register(garden(propose_tool()))
    registry.register(DomainSpec(name="assistant", summary="Everything else.", mode="agent"))
    assert registry.routing_lines(mode="agent") == "- assistant: Everything else."


def test_the_app_catalog_has_tasks_inline_and_the_current_assistant_as_a_hand_off():
    assert {tool.name for tool in DOMAINS.inline_tools()} == {"add_todos", "complete_todo", "remove_todo", "list_todos"}
    assert [domain.name for domain in DOMAINS.agent_domains()] == ["assistant"]
    assert all(tool.examples for tool in DOMAINS.inline_tools() if tool.kind == "propose")
    assert CTX.message == "water the plants"
