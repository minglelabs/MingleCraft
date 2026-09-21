import asyncio
import json

import httpx
import pytest

from jevcraft.actions.spatial import (
    SpatialGridSpec,
    build_refinement_question,
    build_region_question,
    child_bounds,
    parse_refinement_key,
    parse_region_key,
)
from jevcraft.agents import JevProvider
from jevcraft.bwapi.synthetic import SyntheticGame
from jevcraft.loop import AgentLoop
from jevcraft.models import ChoiceAnswer, NoulAnswer, ProviderResult


def test_spatial_grid_coverage():
    spec = SpatialGridSpec(map_width=8192, map_height=8192)
    assert spec.map_width == 8192
    assert spec.map_height == 8192
    assert spec.region_divisions == 8
    assert spec.cell_divisions == 8

    # Ensure coverage of all 64 regions
    q_region = build_region_question("SCV 1", "move", spec)
    assert len(q_region.criteria) == 64
    for ry in range(8):
        for rx in range(8):
            key = f"region_{rx}_{ry}"
            assert key in q_region.criteria
            parsed_rx, parsed_ry = parse_region_key(key)
            assert (parsed_rx, parsed_ry) == (rx, ry)

    assert spec.region_bounds(7, 7) == (7168, 7168, 8192, 8192)
    uneven = SpatialGridSpec(map_width=1000, map_height=997)
    assert uneven.region_bounds(7, 7) == (875, 872, 1000, 997)


def test_parse_invalid_keys():
    with pytest.raises(ValueError):
        parse_region_key("invalid_key")


class SpatialProvider:
    name, model, remote, supports_value = "fake", "fake-spatial", False, False

    def __init__(self, invalid=False):
        self.requests = []
        self.invalid = invalid

    async def decide(self, request):
        self.requests.append(request)
        if "action" in request.questions:
            action = next(
                key
                for key in request.questions["action"].criteria
                if key.startswith("spatial_move_unit_")
            )
            return ProviderResult(model=self.model, answers={"action": ChoiceAnswer(choice=action)})
        if self.invalid:
            return ProviderResult(
                model=self.model, answers={"spatial": ChoiceAnswer(choice="outside_the_grid")}
            )
        key = (
            "region_7_6" if "region_7_6" in request.questions["spatial"].criteria else "refine_3_4"
        )
        return ProviderResult(model=self.model, answers={"spatial": ChoiceAnswer(choice=key)})


def test_agent_loop_resolves_uniform_ground_coordinate_end_to_end(tmp_path):
    provider = SpatialProvider()
    loop = AgentLoop(provider, tmp_path, single_stage=True, deadline_ms=5000)
    decision = asyncio.run(loop.step(SyntheticGame("spatial_e2e").observe()))

    assert decision.action_id.startswith("spatial_move_unit_")
    assert decision.commands[0].position.model_dump() == {"x": 3803, "y": 3363}
    assert [set(request.questions) for request in provider.requests] == [
        {"action"},
        {"spatial"},
        {"spatial"},
        {"spatial"},
    ]


def test_invalid_spatial_choice_waits_without_placeholder_command(tmp_path):
    provider = SpatialProvider(invalid=True)
    loop = AgentLoop(provider, tmp_path, single_stage=True, deadline_ms=5000)
    decision = asyncio.run(loop.step(SyntheticGame("spatial_invalid").observe()))

    assert decision.action_id == "wait"
    assert decision.commands == ()
    assert decision.fallback_reason == "provider_error"


def test_missing_map_dimensions_waits_without_assumed_8192_grid(tmp_path):
    provider = SpatialProvider()
    loop = AgentLoop(provider, tmp_path, single_stage=True, deadline_ms=5000)
    observation = (
        SyntheticGame("spatial_missing_dimensions")
        .observe()
        .model_copy(update={"map_width": 0, "map_height": 0})
    )
    decision = asyncio.run(loop.step(observation))

    assert decision.action_id == "wait"
    assert decision.commands == ()


def test_staged_jev_wire_keeps_spatial_bounds_and_map_dimensions(tmp_path):
    payloads = []

    def respond(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        questions = payload["questions"]
        if "win_probability" in questions:
            answers = {"win_probability": {"type": "noul", "noul": 0.6}}
        elif "action" in questions:
            action = next(
                key
                for key in questions["action"]["criteria"]
                if key.startswith("spatial_move_unit_")
            )
            choices = questions["action"]["criteria"]
            answers = {
                "action": {
                    "type": "choice",
                    "choice": action,
                    "confidence": 1,
                    "probabilities": {key: float(key == action) for key in choices},
                }
            }
        else:
            choices = questions["spatial"]["criteria"]
            choice = "region_7_6" if "region_7_6" in choices else "refine_3_4"
            answers = {
                "spatial": {
                    "type": "choice",
                    "choice": choice,
                    "confidence": 1,
                    "probabilities": {key: float(key == choice) for key in choices},
                }
            }
        return httpx.Response(200, json={"model": "jev-latest", "answers": answers})

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    loop = AgentLoop(provider, tmp_path, deadline_ms=5000)
    decision = asyncio.run(loop.step(SyntheticGame("spatial_wire").observe()))

    assert decision.commands[0].position.model_dump() == {"x": 3803, "y": 3363}
    spatial_payload = next(payload for payload in payloads if "spatial" in payload["questions"])
    criteria = spatial_payload["questions"]["spatial"]["criteria"]
    assert "Map Region" in criteria.get("region_7_6", "") or "Uniform cell" in next(
        iter(criteria.values())
    )
    assert spatial_payload["state"]["observation"]["map_width"] == 4096
    assert spatial_payload["state"]["observation"]["map_height"] == 4096
    assert not any(value.startswith("leaf:") for value in criteria.values())


def test_staged_spatial_timeout_waits_without_executing_placeholder(tmp_path):
    class SlowSpatial(SpatialProvider):
        supports_value = True

        async def decide(self, request):
            self.requests.append(request)
            if "win_probability" in request.questions:
                return ProviderResult(
                    model=self.model, answers={"win_probability": NoulAnswer(noul=0.5)}
                )
            if "action" in request.questions:
                action = next(
                    key
                    for key in request.questions["action"].criteria
                    if key.startswith("spatial_move_unit_")
                )
                return ProviderResult(
                    model=self.model, answers={"action": ChoiceAnswer(choice=action)}
                )
            await asyncio.sleep(1)
            raise AssertionError("spatial timeout should cancel this request")

    provider = SlowSpatial()
    loop = AgentLoop(provider, tmp_path, deadline_ms=20)
    decision = asyncio.run(loop.step(SyntheticGame("spatial_timeout").observe()))

    assert decision.action_id == "wait"
    assert decision.commands == ()
    assert decision.fallback_reason == "deadline"


def test_rectangular_refinement_has_no_empty_cells_or_lost_pixels():
    bounds = (19, 23, 22, 42)
    question = build_refinement_question("unit 1", "move", bounds, 1)
    covered = set()
    for key in question.criteria:
        child = child_bounds(bounds, *parse_refinement_key(key))
        assert child[0] < child[2] and child[1] < child[3]
        points = {(x, y) for x in range(child[0], child[2]) for y in range(child[1], child[3])}
        assert not covered.intersection(points)
        covered.update(points)
    assert covered == {(x, y) for x in range(19, 22) for y in range(23, 42)}


def test_tiny_map_regions_only_include_nonempty_cells():
    spec = SpatialGridSpec(map_width=3, map_height=2, precision_px=1)
    regions = build_region_question("unit 1", "move", spec)
    assert len(regions.criteria) == 6
    assert {spec.region_bounds(*parse_region_key(key)) for key in regions.criteria} == {
        (x, y, x + 1, y + 1) for x in range(3) for y in range(2)
    }
