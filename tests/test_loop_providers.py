import asyncio
import json

import httpx
import pytest

from jevcraft.actions.generator import ActionGenerator
from jevcraft.actions.hierarchy import ChoiceTree
from jevcraft.agents import (
    JevProvider,
    OpenAIProvider,
    OpenRouterJevProvider,
    RuleBasedProvider,
)
from jevcraft.agents.encoding import compact_request_payload
from jevcraft.bwapi.synthetic import SyntheticGame
from jevcraft.loop import AgentLoop
from jevcraft.models import (
    Action,
    BuildSite,
    ChoiceAnswer,
    ChoiceQuestion,
    Command,
    DecisionRequest,
    Enemy,
    Position,
    ProviderResult,
    Receipt,
)
from jevcraft.strategy.scheduler import Scheduler


def test_synthetic_loop_runs_economy_production_and_logging(tmp_path):
    game = SyntheticGame("full_test")
    loop = AgentLoop(RuleBasedProvider(), tmp_path, mode="synthetic")

    async def run():
        for _ in range(160):
            decision = await loop.step(game.observe())
            game.execute(decision)
            game.advance()
        return loop.finish(game.observe(ended=True))

    summary = asyncio.run(run())
    assert summary["provider_errors"] == 0
    assert summary["mode"] == "synthetic" and summary["result"] == "unknown"
    assert summary["counters"]["gathered_minerals"] > 0
    assert summary["counters"]["spent_minerals"] > 0
    assert any(u["type"] == "Terran_Marine" for u in game.units)
    records = [
        json.loads(line)
        for line in (tmp_path / "full_test/decisions.jsonl").read_text().splitlines()
    ]
    assert len(records) == 161
    assert records[-1]["event"] == "match_end"
    assert all(len(r["actions"]) <= 50 for r in records[:-1])


def test_timeout_cancels_provider_and_enters_cooldown(observation, tmp_path):
    class Slow(RuleBasedProvider):
        cancelled = False

        async def decide(self, request):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    provider = Slow()
    loop = AgentLoop(provider, tmp_path, deadline_ms=10)
    first = asyncio.run(loop.step(observation))
    assert first.action_id == "wait" and first.fallback_reason == "deadline"
    assert provider.cancelled
    next_obs = observation.model_copy(update={"frame": 6})
    second = asyncio.run(loop.step(next_obs))
    assert second.fallback_reason == "provider_cooldown"
    summary = loop.finish(next_obs)
    assert summary["provider_calls"] == 1
    assert summary["provider_errors"] == 1


def test_provider_failure_does_not_log_secrets(observation, tmp_path):
    class Failure(RuleBasedProvider):
        async def decide(self, request):
            raise RuntimeError("Bearer secret-do-not-log")

    loop = AgentLoop(Failure(), tmp_path)
    result = asyncio.run(loop.step(observation))
    loop.finish(observation)
    assert result.commands == ()
    assert "secret-do-not-log" not in (tmp_path / "test_match/decisions.jsonl").read_text()


def test_stale_frames_and_cross_match_observations_rejected(observation, tmp_path):
    loop = AgentLoop(RuleBasedProvider(), tmp_path)
    asyncio.run(loop.step(observation))
    with pytest.raises(ValueError, match="advance"):
        asyncio.run(loop.step(observation))
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(loop.step(observation.model_copy(update={"match_id": "another", "frame": 6})))
    loop.finish(observation)
    with pytest.raises(ValueError, match="ended"):
        asyncio.run(loop.step(observation.model_copy(update={"frame": 12})))


def test_jev_exact_http_contract_and_probabilities(observation):
    request = ChoiceTree(
        {}, ActionGenerator().generate(observation, set(Scheduler.periods))
    ).request

    def respond(http_request):
        assert str(http_request.url) == "https://api.typesafe.ai/v1/systemone"
        assert http_request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(http_request.content)
        assert set(payload) == {"model", "state", "questions"}
        assert "priorities" not in payload
        answers = {}
        for node, q in payload["questions"].items():
            keys = list(q["criteria"])
            answers[node] = {
                "type": "choice",
                "choice": keys[0],
                "confidence": 0.9,
                "probabilities": {k: float(k == keys[0]) for k in keys},
            }
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    result = asyncio.run(
        JevProvider("test-key", transport=httpx.MockTransport(respond)).decide(request)
    )
    assert result.usage["input_tokens"] == 100
    assert all(a.probabilities is not None for a in result.answers.values())


def test_compact_jev_payload_preserves_ids_and_uses_references(observation):
    actions = ActionGenerator().generate(observation, set(Scheduler.periods), exhaustive=True)
    tree = ChoiceTree(
        {"candidate_actions": [a.model_dump(exclude={"priority"}) for a in actions]}, actions
    )
    payload = compact_request_payload(tree.request, "jev-test")
    baseline = json.dumps(
        {
            "model": "jev-test",
            "state": tree.request.state,
            "questions": {
                key: question.model_dump() for key, question in tree.request.questions.items()
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(compact) < len(baseline)
    rows = payload["state"]["candidate_actions"]["rows"]
    assert {row[0] for row in rows} == {action.id for action in actions}
    assert "priority" not in payload["state"]["candidate_actions"]["columns"]
    assert all(
        isinstance(value, str)
        for question in payload["questions"].values()
        for value in question["criteria"].values()
    )
    assert all(
        value.startswith(("node:", "leaf:"))
        for question in payload["questions"].values()
        for value in question["criteria"].values()
    )
    assert all(
        action.label not in question["criteria"].values()
        for action in actions
        for question in payload["questions"].values()
    )
    assert set(payload["state"]["choice_tree"]) == set(tree.nodes) - set(tree.request.questions)


def test_compact_payload_without_tree_keeps_external_criteria():
    request = DecisionRequest(
        state={},
        questions={"q": ChoiceQuestion(instructions="choose", criteria={"a": "Alpha"})},
        priorities={},
    )
    payload = compact_request_payload(request, "jev-test")
    assert payload["questions"]["q"]["criteria"] == {"a": "Alpha"}


def test_compact_payload_round_trips_facts_commands_and_every_leaf(observation):
    enemy = Enemy(
        id=900, type="Terran_Marine", position=Position(x=30, y=31), hit_points=40, visible=True
    )
    unit = observation.units[0].model_copy(
        update={
            "build_sites": (BuildSite(unit_type="Terran_Barracks", tile=Position(x=12, y=13)),),
        }
    )
    receipt = Receipt(decision_id="d1", frame=6, attempted=2, accepted=2, effective=1, reason="ok")
    obs = observation.model_copy(
        update={"units": (unit,), "enemies": (enemy,), "receipts": (receipt,)}
    )
    action = Action(
        id="combo",
        category="attack",
        group="squad",
        label="Move and attack",
        commands=(
            Command(kind="move", unit_ids=(unit.id,), position=Position(x=20, y=21)),
            Command(kind="attack", unit_ids=(unit.id,), target_id=enemy.id),
        ),
    )
    actions = [Action(id="wait", category="wait", group="wait", label="Wait"), action]
    state = {
        "observation": obs.model_dump(),
        "candidate_actions": [a.model_dump() for a in actions],
    }
    tree = ChoiceTree(state, actions)
    payload = compact_request_payload(tree.request, "jev-test")

    def table(table):
        return [dict(zip(table["columns"], row)) for row in table["rows"]]

    def pos(value):
        return None if value is None else {"x": value[0], "y": value[1]}

    wire_obs = payload["state"]["observation"]
    restored_obs = dict(wire_obs)
    restored_obs["home"] = pos(wire_obs["home"])
    restored_obs["units"] = table(wire_obs["units"])
    for restored in restored_obs["units"]:
        restored["position"] = pos(restored["position"])
        restored["build_sites"] = [
            {"unit_type": site[0], "tile": pos(site[1])} for site in restored["build_sites"]
        ]
    restored_obs["enemies"] = table(wire_obs["enemies"])
    for restored in restored_obs["enemies"]:
        restored["position"] = pos(restored["position"])
    restored_obs["mineral_patches"] = table(wire_obs["mineral_patches"])
    for restored in restored_obs["mineral_patches"]:
        restored["position"] = pos(restored["position"])
    restored_obs["locations"] = table(wire_obs["locations"])
    for restored in restored_obs["locations"]:
        restored["position"] = pos(restored["position"])
    restored_obs["receipts"] = table(wire_obs["receipts"])
    assert json.dumps(restored_obs, sort_keys=True) == json.dumps(obs.model_dump(), sort_keys=True)

    restored_actions = []
    for row in table(payload["state"]["candidate_actions"]):
        restored_actions.append(
            {
                **{key: row[key] for key in ("id", "category", "group", "label")},
                "commands": [
                    {
                        "kind": command[0],
                        "unit_ids": command[1],
                        "unit_type": command[2],
                        "target_id": command[3],
                        "position": pos(command[4]),
                        "tile": pos(command[5]),
                    }
                    for command in row["commands"]
                ],
            }
        )
    assert json.dumps(restored_actions, sort_keys=True) == json.dumps(
        [a.model_dump(exclude={"priority"}) for a in actions], sort_keys=True
    )

    def path(node, target):
        if node.startswith("leaf:"):
            return [] if node[5:] == target else None
        for option, child in tree.nodes[node].items():
            suffix = path(child, target)
            if suffix is not None:
                return [(node, option), *suffix]
        return None

    for target in (a.id for a in actions):
        answers = {
            node: ChoiceAnswer(choice=next(iter(question.criteria)))
            for node, question in tree.request.questions.items()
        }
        for node, choice in path("domain", target):
            if node in answers:
                answers[node] = ChoiceAnswer(choice=choice)
        selected, _ = tree.resolve(ProviderResult(model="jev-test", answers=answers))
        assert selected.id == target

        wire_questions = payload["questions"]
        wire_tree = payload["state"]["choice_tree"]
        wire_actions = {row["id"]: row for row in table(payload["state"]["candidate_actions"])}
        wire_command_rows = None
        for node, choice in path("domain", target):
            options = (
                wire_questions[node]["criteria"] if node in wire_questions else wire_tree[node]
            )
            wire_ref = options[choice]
            if wire_ref.startswith("leaf:"):
                assert wire_ref[5:] == target
                wire_command_rows = wire_actions[target]["commands"]
                break
        assert wire_command_rows == wire_actions[target]["commands"]


def test_openai_schema_and_no_invented_confidence(observation):
    request = ChoiceTree(
        {}, ActionGenerator().generate(observation, set(Scheduler.periods))
    ).request

    def respond(http_request):
        payload = json.loads(http_request.content)
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        answers = {k: v["enum"][0] for k, v in schema["schema"]["properties"].items()}
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": json.dumps(answers)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    result = asyncio.run(
        OpenAIProvider("test-key", "test-model", transport=httpx.MockTransport(respond)).decide(
            request
        )
    )
    assert all(a.confidence is None and a.probabilities is None for a in result.answers.values())


def test_openrouter_jev_uses_decisions_endpoint(observation):
    request = ChoiceTree(
        {}, ActionGenerator().generate(observation, set(Scheduler.periods))
    ).request

    def respond(http_request):
        assert str(http_request.url) == "https://openrouter.ai/api/alpha/decisions"
        assert http_request.headers["authorization"] == "Bearer router-key"
        payload = json.loads(http_request.content)
        assert payload["model"] == "~typesafe/jev-latest"
        answers = {}
        for node, question in payload["questions"].items():
            options = list(question["criteria"])
            answers[node] = {
                "type": "choice",
                "choice": options[0],
                "confidence": 0.91,
                "probabilities": {key: float(key == options[0]) for key in options},
            }
        return httpx.Response(
            200,
            json={
                "model": "~typesafe/jev-latest",
                "answers": answers,
                "usage": {"input_tokens": 120, "output_tokens": 14},
            },
        )

    result = asyncio.run(
        OpenRouterJevProvider("router-key", transport=httpx.MockTransport(respond)).decide(request)
    )
    assert result.model == "~typesafe/jev-latest"
    assert result.usage["output_tokens"] == 14
    assert all(answer.confidence == 0.91 for answer in result.answers.values())


def test_cost_unknown_without_configured_prices(observation, tmp_path):
    provider = RuleBasedProvider()
    provider.remote = True
    loop = AgentLoop(provider, tmp_path)
    asyncio.run(loop.step(observation))
    assert loop.finish(observation)["estimated_api_cost_usd"] is None


def test_expired_envelope_never_executes(tmp_path):
    game = SyntheticGame("expired")
    loop = AgentLoop(RuleBasedProvider(), tmp_path, ttl_frames=1)
    decision = asyncio.run(loop.step(game.observe()))
    game.advance()
    game.execute(decision)
    assert game.counters["commands_accepted"] == 0
    assert game.receipts[-1]["reason"] == "stale_or_wrong_match"
    loop.finish(game.observe())
