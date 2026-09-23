import asyncio
import json

import httpx

from minglecraft.actions.generator import ActionGenerator
from minglecraft.agents import JevProvider
from minglecraft.loop import AgentLoop
from minglecraft.models import Mineral, Observation, Position, Unit


def _zerg_observation():
    drones = tuple(
        Unit(
            id=10 + index,
            type="Zerg_Drone",
            position=Position(x=110 + index, y=100),
            hit_points=40,
            can_move=True,
            can_gather=(100, 200, 101) if index == 3 else (100, 200),
        )
        for index in range(4)
    )
    refinery = Unit(
        id=200,
        type="Zerg_Extractor",
        position=Position(x=120, y=100),
        hit_points=500,
        completed=True,
    )
    obs = Observation(
        match_id="zerg_group_gather",
        frame=0,
        map_name="Destination.scx",
        map_hash="hash1",
        map_width=4096,
        map_height=4096,
        self_race="Zerg",
        minerals=500,
        gas=0,
        supply_used=4,
        supply_total=9,
        home=Position(x=100, y=100),
        units=(*drones, refinery),
        mineral_patches=(
            Mineral(id=100, position=Position(x=120, y=100)),
            Mineral(id=101, position=Position(x=130, y=100)),
        ),
    )

    return obs


def test_exhaustive_group_gather_uses_common_zerg_drone_targets():
    actions = ActionGenerator().generate(_zerg_observation(), {"economy"}, exhaustive=True)
    by_id = {action.id: action for action in actions}

    group_ids = {
        action.id for action in actions if action.id.startswith("gather_group_all_workers_")
    }
    assert group_ids == {
        "gather_group_all_workers_100",
        "gather_group_all_workers_200",
    }
    for target_id in (100, 200):
        command = by_id[f"gather_group_all_workers_{target_id}"].commands[0]
        assert command.kind == "gather"
        assert command.unit_ids == (10, 11, 12, 13)
        assert command.target_id == target_id

    assert all(
        f"gather_{worker_id}_101" in by_id for worker_id in range(10, 14)
    )
    assert all(action.commands[0].kind == "gather" for action in actions if action.id != "wait")


def test_four_drones_can_gather_from_one_parallel_jev_request(tmp_path):
    payloads = []

    def respond(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        answers = {}
        for question_id, question in payload["questions"].items():
            if question["type"] == "noul":
                answers[question_id] = {"type": "noul", "noul": 0.5}
                continue
            criteria = question["criteria"]
            if question_id == "command_kind":
                choice = "gather"
            elif question_id.startswith("actors_") and "gather" in question_id:
                choice = next(key for key in criteria if key.startswith("group_all_workers_"))
            elif question_id.startswith("commands_") and "group_all_workers" in question_id:
                choice = "gather_group_all_workers_100"
            else:
                choice = next(iter(criteria))
            answers[question_id] = {
                "type": "choice",
                "choice": choice,
                "confidence": 1,
                "probabilities": {key: float(key == choice) for key in criteria},
            }
        return httpx.Response(200, json={"model": "jev-latest", "answers": answers})

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    decision = asyncio.run(AgentLoop(provider, tmp_path).step(_zerg_observation()))
    assert len(payloads) == 1
    assert decision.action_id == "gather_group_all_workers_100"
    assert decision.commands[0].unit_ids == (10, 11, 12, 13)
    assert decision.commands[0].target_id == 100
    payload = payloads[0]
    assert "win_probability" in payload["questions"]
    assert any("units=10,11,12,13" in label for question in payload["questions"].values() if question["type"] == "choice" for label in question["criteria"].values())
    assert "gather_group_all_workers_100" in json.dumps(payload["questions"])
    assert len(payload["state"]["candidate_actions"]["rows"]) == 0
