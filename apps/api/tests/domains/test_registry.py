"""The tool registry: one spec per tool, and a domain that is incomplete cannot be registered."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict

from kairo.core.domain.types import PMAction
from kairo.core.prompt import PromptTemplate
from kairo.domains.catalog import DOMAINS
from kairo.domains.registry import DomainSpec, NotThisDomain, Proposed, Registry, ToolContext, ToolSpec

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


GARDENER = PromptTemplate("test_gardener", "You look after the garden.\n$context")
EVERYTHING_ELSE = DomainSpec(name="assistant", summary="Everything else.", mode="legacy")


def garden_agent(*tools, **overrides) -> DomainSpec:
    return garden(*tools, **{"mode": "agent", "prompt": GARDENER, "context": lambda ctx: "fern, cactus", **overrides})


def test_a_tool_becomes_a_strict_function_definition_named_for_the_model():
    schema = propose_tool().schema()["function"]
    assert schema["name"] == "water_plant" and schema["description"] == "Propose watering a plant."
    assert schema["strict"] is True and schema["parameters"]["required"] == ["plant"]
    assert schema["parameters"]["additionalProperties"] is False


def test_registered_tools_are_found_by_name_and_grouped_by_how_the_domain_runs():
    registry = Registry()
    registry.register(garden(propose_tool(), read_tool()))
    registry.register(garden_agent(propose_tool(name="prune_plant"), name="orchard", summary="Fruit trees."))
    registry.register(EVERYTHING_ELSE)

    assert registry.tool("water_plant").kind == "propose"
    assert [tool.name for tool in registry.inline_tools()] == ["water_plant", "list_plants"]
    assert [domain.name for domain in registry.agent_domains()] == ["orchard"]
    assert [domain.name for domain in registry.hand_off_domains()] == ["orchard", "assistant"]
    assert registry.fallback().name == "assistant"
    assert registry.domain("orchard").mode == "agent" and registry.domain("nowhere") is None
    assert registry.domain_of("prune_plant").name == "orchard" and registry.domain_of("list_plants").name == "garden"
    with pytest.raises(ValueError, match="unknown tool"):
        registry.tool("fell_tree")


@pytest.mark.parametrize("domain, clue", [
    (garden(propose_tool(propose=None)), "propose"),                 # nothing to turn the call into a command
    (garden(propose_tool(examples=())), "example"),                  # no example message
    (garden(read_tool(read=None)), "read"),                          # a read tool that reads nothing
    (garden(propose_tool(), propose_tool()), "water_plant"),         # the same tool twice
    (garden(propose_tool(name="Water Plant")), "name"),              # not a name a model can call
    (garden(), "no tools"),                                          # an inline domain with nothing to call
    (garden_agent(), "no tools"),                                    # an agent with nothing to call
    (garden_agent(propose_tool(), prompt=None), "prompt"),           # an agent that is told nothing
    (garden(propose_tool(), mode="legacy"), "legacy"),               # a legacy domain owns no tools here
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
    registry.register(garden_agent(propose_tool(name="prune_plant"), name="orchard", summary="Fruit trees."))
    registry.register(EVERYTHING_ELSE)
    assert registry.routing_lines() == "- orchard: Fruit trees.\n- assistant: Everything else."


def test_there_is_at_most_one_legacy_domain():
    registry = Registry()
    registry.register(EVERYTHING_ELSE)
    with pytest.raises(ValueError, match="legacy"):
        registry.register(DomainSpec(name="other", summary="More.", mode="legacy"))


def test_an_agents_bundle_version_covers_its_prompt_and_its_tools():
    class WaterMore(Water):
        """Propose watering a plant, generously."""

    plain = garden_agent(propose_tool())
    assert len(plain.bundle()) == 8 and plain.bundle() == garden_agent(propose_tool()).bundle()
    assert plain.bundle() != garden_agent(propose_tool(args=WaterMore)).bundle()
    assert plain.bundle() != garden_agent(propose_tool(), prompt=PromptTemplate("test_gardener_2", "Tend it.")).bundle()


def test_a_proposal_can_carry_notes_and_a_verdict_can_name_where_a_request_belongs():
    proposed = Proposed([PMAction("todo_add", {"title": "water fern"})], notes=("The fern was watered yesterday.",))
    assert proposed.notes == ("The fern was watered yesterday.",) and len(proposed.actions) == 1
    verdict = NotThisDomain("a tree is not a plant in the garden", to="orchard")
    assert verdict.to == "orchard" and "tree" in verdict and NotThisDomain("no").to == ""


def test_the_app_catalog_has_tasks_inline_the_calendar_as_an_agent_and_the_old_assistant_as_the_fallback():
    assert {tool.name for tool in DOMAINS.inline_tools()} == {"add_todos", "complete_todo", "remove_todo", "list_todos"}
    assert [domain.name for domain in DOMAINS.agent_domains()] == ["schedule"]
    assert DOMAINS.fallback().name == "assistant"
    assert all(tool.examples for domain in (DOMAINS.domain("tasks"), DOMAINS.domain("schedule"))
               for tool in domain.tools if tool.kind == "propose")
    assert all(tool.examples for tool in DOMAINS.inline_tools() if tool.kind == "propose")
    assert CTX.message == "water the plants"
