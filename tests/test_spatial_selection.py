import asyncio
import json
import re

import httpx
import pytest

from minglecraft.actions.spatial import (
    SpatialGridSpec,
    build_digit_question,
    build_region_question,
    parse_digit_key,
    parse_region_key,
    spatial_digit_depth,
    synthesize_radix_coordinate,
)
from minglecraft.agents import JevProvider
from minglecraft.bwapi.synthetic import SyntheticGame
from minglecraft.loop import AgentLoop
from minglecraft.models import ChoiceAnswer, NoulAnswer, ProviderResult, Unit


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


def test_spatial_rounded_probability_distribution_is_accepted():
    question = build_region_question(
        "unit 1", "move", SpatialGridSpec(map_width=8192, map_height=8192)
    )
    keys = list(question.criteria)
    probabilities = {key: 0.0 for key in keys}
    probabilities[keys[0]] = 0.49
    probabilities[keys[1]] = 0.50
    result = ProviderResult(
        model="test",
        answers={
            "spatial": ChoiceAnswer(choice=keys[0], probabilities=probabilities),
        },
    )
    assert AgentLoop._resolve_spatial(result, question) == keys[0]



TARGET = (3803, 3363)


def _choices(request, target_action, *, invalid=False):
    tree = request.choice_tree or {}

    def reaches(child):
        if child.startswith("leaf:"):
            return child[5:] == target_action
        return any(reaches(next_child) for next_child in tree[child].values())

    width = request.state["observation"]["map_width"]
    height = request.state["observation"]["map_height"]
    answers = {}
    for node, question in request.questions.items():
        if question.type == "noul":
            answers[node] = NoulAnswer(noul=0.6)
        elif node.startswith("spatial_"):
            action_id = node.removeprefix("spatial_").rsplit("_", 1)[0]
            choice = next(iter(question.criteria))
            if action_id == target_action and width and height:
                level, depth = map(int, re.search(r"Choose digit (\d+)/(\d+)", question.instructions).groups())
                scale = 4**depth
                x_index = min(scale - 1, TARGET[0] * scale // width)
                y_index = min(scale - 1, TARGET[1] * scale // height)
                place = 4 ** (depth - level)
                choice = f"digit_{x_index // place % 4}_{y_index // place % 4}"
                if invalid:
                    choice = "outside_the_grid"
            answers[node] = ChoiceAnswer(choice=choice)
        else:
            choice = next((key for key, child in tree[node].items() if reaches(child)), next(iter(question.criteria)))
            answers[node] = ChoiceAnswer(choice=choice)
    return answers


def _assert_precise(position):
    assert abs(position.x - TARGET[0]) <= 8
    assert abs(position.y - TARGET[1]) <= 8


class SpatialProvider:
    name, model, remote, supports_value = "fake", "fake-spatial", False, False

    def __init__(self, target="spatial_move_unit_2", invalid=False):
        self.requests = []
        self.target = target
        self.invalid = invalid

    async def decide(self, request):
        self.requests.append(request)
        return ProviderResult(model=self.model, answers=_choices(request, self.target, invalid=self.invalid))


def test_radix_coordinate_on_rectangular_map():
    spec = SpatialGridSpec(map_width=4096, map_height=2304, precision_px=8)
    depth = spatial_digit_depth(spec)
    assert depth == 5
    assert all(len(build_digit_question("move_1", "move", spec, level, depth).criteria) == 16 for level in range(1, depth + 1))
    assert parse_digit_key("digit_3_2") == (3, 2)
    with pytest.raises(ValueError):
        parse_digit_key("digit_4_0")
    assert synthesize_radix_coordinate(spec, [(0, 0)] * depth) == (0, 0)
    assert synthesize_radix_coordinate(spec, [(3, 3)] * depth) == (4092, 2301)


def test_agent_loop_resolves_uniform_ground_coordinate_end_to_end(tmp_path):
    provider = SpatialProvider()
    decision = asyncio.run(AgentLoop(provider, tmp_path, single_stage=True).step(SyntheticGame("spatial_e2e").observe()))
    assert decision.action_id == provider.target
    _assert_precise(decision.commands[0].position)
    assert len(provider.requests) == 1
    questions = provider.requests[0].questions
    assert "command_kind" in questions
    assert sum(key.startswith(f"spatial_{provider.target}_") for key in questions) == 5
    assert all(len(q.criteria) == 16 for key, q in questions.items() if key.startswith("spatial_"))


def test_invalid_spatial_choice_waits_without_placeholder_command(tmp_path):
    provider = SpatialProvider(invalid=True)
    decision = asyncio.run(AgentLoop(provider, tmp_path, single_stage=True).step(SyntheticGame("spatial_invalid").observe()))
    assert decision.action_id == "wait"
    assert decision.commands == ()
    assert decision.fallback_reason == "provider_error"
    assert len(provider.requests) == 1


def test_missing_map_dimensions_waits_without_assumed_grid(tmp_path):
    provider = SpatialProvider()
    obs = SyntheticGame("spatial_missing_dimensions").observe().model_copy(update={"map_width": 0, "map_height": 0})
    decision = asyncio.run(AgentLoop(provider, tmp_path, single_stage=True).step(obs))
    assert decision.action_id == "wait"
    assert decision.commands == ()


def test_staged_jev_wire_sends_all_coordinate_digits_in_one_request(tmp_path):
    payloads = []
    fake = SpatialProvider()
    obs = SyntheticGame("spatial_wire").observe()
    asyncio.run(AgentLoop(fake, tmp_path / "fake", single_stage=True).step(obs))
    local_answers = _choices(fake.requests[0], fake.target)

    def respond(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        wire_answers = {}
        for key, question in payload["questions"].items():
            answer = local_answers.get(key, NoulAnswer(noul=0.6))
            if answer.type == "noul":
                wire_answers[key] = answer.model_dump()
            else:
                wire_answers[key] = {
                    "type": "choice", "choice": answer.choice, "confidence": 1,
                    "probabilities": {option: float(option == answer.choice) for option in question["criteria"]},
                }
        return httpx.Response(200, json={"model": "jev-latest", "answers": wire_answers})

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    decision = asyncio.run(AgentLoop(provider, tmp_path / "wire").step(obs))
    assert decision.action_id == fake.target
    _assert_precise(decision.commands[0].position)
    assert len(payloads) == 1
    payload = payloads[0]
    assert "win_probability" in payload["questions"]
    assert "command_kind" in payload["questions"]
    assert "choice_tree" not in payload["state"]
    assert payload["state"]["observation"]["map_width"] == 4096
    assert "4^(depth-i)" in payload["state"]["coordinate_rule"]
    assert len(payload["state"]["candidate_actions"]["rows"]) == 0


def test_staged_spatial_timeout_waits_without_executing_placeholder(tmp_path):
    class SlowSpatial(SpatialProvider):
        supports_value = True

        async def decide(self, request):
            self.requests.append(request)
            await asyncio.sleep(0.5)
            return ProviderResult(model=self.model, answers=_choices(request, self.target))

    provider = SlowSpatial()
    decision = asyncio.run(AgentLoop(provider, tmp_path, deadline_ms=250).step(SyntheticGame("spatial_timeout").observe()))
    assert decision.action_id == "wait"
    assert decision.commands == ()
    assert decision.fallback_reason == "deadline"
    assert len(provider.requests) == 1


def test_agent_loop_resolves_group_spatial_action_end_to_end(tmp_path):
    target = "spatial_attack_group_all_combat"
    provider = SpatialProvider(target=target)
    obs = SyntheticGame("spatial_group").observe()
    obs = obs.model_copy(update={"units": (*obs.units, Unit(id=20, type="Terran_Marine", position=obs.home, hit_points=40, can_move=True, can_attack=True), Unit(id=21, type="Terran_Marine", position=obs.home, hit_points=40, can_move=True, can_attack=True))})
    decision = asyncio.run(AgentLoop(provider, tmp_path, single_stage=True).step(obs))
    assert decision.action_id == target
    assert len(decision.commands[0].unit_ids) > 1
    assert decision.commands[0].kind == "attack"
    _assert_precise(decision.commands[0].position)
    assert len(provider.requests) == 1


def test_spatial_decision_uses_one_call_under_short_deadline(tmp_path):
    class TimedSpatialProvider(SpatialProvider):
        async def decide(self, request):
            self.requests.append(request)
            await asyncio.sleep(0.02)
            return ProviderResult(model=self.model, answers=_choices(request, self.target))

    provider = TimedSpatialProvider()
    decision = asyncio.run(AgentLoop(provider, tmp_path, single_stage=True, deadline_ms=55).step(SyntheticGame("spatial_budget").observe()))
    assert decision.action_id == provider.target
    assert decision.fallback_reason is None
    _assert_precise(decision.commands[0].position)
    assert len(provider.requests) == 1
