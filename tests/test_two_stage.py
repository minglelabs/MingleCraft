import asyncio
import json

import httpx

from minglecraft.agents import JevProvider
from minglecraft.loop import AgentLoop
from minglecraft.models import Action, ChoiceAnswer, NoulAnswer, ProviderResult, Receipt
from minglecraft.state.history import MatchHistory


class StagedProvider:
    name, model, remote, supports_value = "jev", "jev-test", True, True

    def __init__(self, fail_policy=False):
        self.requests = []
        self.fail_policy = fail_policy

    async def decide(self, request):
        self.requests.append(request)
        if "win_probability" in request.questions:
            return ProviderResult(
                model=self.model,
                answers={"win_probability": NoulAnswer(noul=0.63)},
                usage={"input_tokens": 11, "output_tokens": 2},
            )
        if self.fail_policy:
            raise RuntimeError("private response body")
        answers = {
            key: ChoiceAnswer(choice=next(iter(question.criteria)))
            for key, question in request.questions.items()
        }
        return ProviderResult(
            model=self.model, answers=answers, usage={"input_tokens": 13, "output_tokens": 3}
        )


def test_jev_value_then_policy_has_fresh_value_and_history(observation, tmp_path):
    provider = StagedProvider()
    loop = AgentLoop(provider, tmp_path, deadline_ms=5000)
    asyncio.run(loop.step(observation))
    next_observation = observation.model_copy(
        update={
            "frame": 6,
            "enemies": tuple(
                enemy.model_copy(update={"visible": False}) for enemy in observation.enemies
            ),
        }
    )
    asyncio.run(loop.step(next_observation))

    values = [r for r in provider.requests if "win_probability" in r.questions]
    policies = [r for r in provider.requests if "command_kind" in r.questions]
    assert len(values) == 2
    assert len(policies) == 2
    assert "latest_value" not in values[0].state or values[0].state["latest_value"] is None
    assert policies[0].state["latest_value"] == {"frame": 0, "win_probability": 0.63}
    assert "choice_tree" not in values[0].state
    # The model-facing compact wire encoding carries the selected node criteria;
    # it does not dump the complete tree into state.
    assert "choice_tree" not in policies[0].state
    assert policies[0].choice_tree is not None
    assert len(policies[0].choice_tree) <= 1
    assert "match_history" not in values[0].state
    assert "match_history" not in policies[1].state
    history = loop.history.snapshot()
    assert any(record["event"] == "issued_action" for record in history)
    assert all(
        enemy["visible"]
        for record in history
        if record["event"] == "observation" and "enemies" in record["observation_delta"]
        for enemy in record["observation_delta"]["enemies"]
    )
    assert "strategy_policy" not in policies[0].state
    assert "strategy_policy" not in values[0].state


def test_policy_failure_keeps_value_usage_and_sanitizes_error(observation, tmp_path):
    provider = StagedProvider(fail_policy=True)
    loop = AgentLoop(provider, tmp_path, deadline_ms=500)
    result = asyncio.run(loop.step(observation))
    summary = loop.finish(observation)
    trace = (tmp_path / "test_match" / "decisions.jsonl").read_text()
    event = json.loads(trace.splitlines()[0])
    assert result.fallback_reason == "provider_error"
    assert len(event["calls"]) == 2
    assert event["value_result"]["usage"]["input_tokens"] == 11
    assert "private response body" not in trace
    assert summary["value_calls"] == 1
    assert summary["policy_calls"] == 1
    assert summary["last_win_probability"] == 0.63


def test_jev_sends_all_exhaustive_candidates(observation, tmp_path):
    provider = StagedProvider()
    loop = AgentLoop(provider, tmp_path, deadline_ms=500)

    def generate(obs, due, exhaustive=False):
        assert exhaustive is True
        return [Action(id="wait", category="wait", group="wait", label="wait")] + [
            Action(id=f"attack_{i}", category="attack", group=f"g{i}", label=f"candidate {i}")
            for i in range(60)
        ]

    loop.generator.generate = generate
    asyncio.run(loop.step(observation))
    # Value request strips candidate_actions to prevent context overflow
    assert len(provider.requests[0].state["candidate_actions"]) == 0
    policy = provider.requests[1]
    assert policy.choice_tree is not None
    assert policy.questions
    assert set(policy.questions).issubset(policy.choice_tree or {})
    # Only the selected node's direct leaves are exposed; descendants of other
    # branches are never dumped into this request.
    assert len(policy.state["candidate_actions"]) == 61
    assert all(len(question.criteria) <= 200 for question in policy.questions.values())


def test_request_size_guard_sends_nothing_and_keeps_history_intact(observation, tmp_path):
    provider = StagedProvider()
    loop = AgentLoop(provider, tmp_path, request_size_limit=128)

    result = asyncio.run(loop.step(observation))

    assert result.fallback_reason == "context_budget_exceeded"
    assert provider.requests == []
    assert len(loop.history.snapshot()) == 2
    loop.finish(observation)


def test_policy_timeout_is_logged_after_value_success(observation, tmp_path):
    class SlowPolicy(StagedProvider):
        async def decide(self, request):
            self.requests.append(request)
            if "win_probability" in request.questions:
                return ProviderResult(
                    model=self.model,
                    answers={"win_probability": NoulAnswer(noul=0.63)},
                )
            await asyncio.sleep(1)
            raise AssertionError("timeout should cancel this request")

    provider = SlowPolicy()
    loop = AgentLoop(provider, tmp_path, deadline_ms=20)
    result = asyncio.run(loop.step(observation))
    loop.finish(observation)

    record = json.loads((tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()[0])
    assert result.fallback_reason == "deadline"
    assert [call["stage"] for call in record["calls"]] == ["value", "policy"]
    assert record["value_result"]["answers"]["win_probability"]["noul"] == 0.63


def test_http_value_failure_is_one_attempted_call_and_policy_continues(observation, tmp_path):
    def respond(request):
        payload = json.loads(request.content)
        if "win_probability" in payload["questions"]:
            return httpx.Response(503, json={"error": "value unavailable"})
        question_id, question = next(iter(payload["questions"].items()))
        choice = next(iter(question["criteria"]))
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    question_id: {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 1,
                        "probabilities": {key: float(key == choice) for key in question["criteria"]},
                    }
                },
            },
        )

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    loop = AgentLoop(provider, tmp_path, deadline_ms=5000)
    asyncio.run(loop.step(observation))
    record = json.loads((tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()[0])
    assert record["calls"][0]["stage"] == "value"
    assert record["calls"][0]["sent"] is True
    assert record["calls"][0]["error"] == "HTTPStatusError"
    assert record["calls"][1]["stage"] == "policy"


def test_http_second_policy_failure_is_logged_separately(observation, tmp_path):
    policy_calls = 0

    def respond(request):
        nonlocal policy_calls
        payload = json.loads(request.content)
        if "win_probability" in payload["questions"]:
            return httpx.Response(200, json={"model": "jev-latest", "answers": {"win_probability": {"type": "noul", "noul": 0.5}}})
        policy_calls += 1
        if policy_calls == 2:
            return httpx.Response(502, json={"error": "policy unavailable"})
        question_id, question = next(iter(payload["questions"].items()))
        choice = next(iter(question["criteria"]))
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    question_id: {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 1,
                        "probabilities": {key: float(key == choice) for key in question["criteria"]},
                    }
                },
            },
        )

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    loop = AgentLoop(provider, tmp_path, deadline_ms=5000)
    asyncio.run(loop.step(observation))
    record = json.loads((tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()[0])
    policy_records = [call for call in record["calls"] if call["stage"] == "policy"]
    assert len(policy_records) == 2
    assert all(call["sent"] is True for call in policy_records)
    assert policy_records[1]["error"] == "HTTPStatusError"
    assert record["query_count"] == 3


def test_history_deduplicates_receipts_and_preserves_timestamped_deltas(observation):
    history = MatchHistory()
    receipt = Receipt(decision_id="d1", frame=6, attempted=1, accepted=1, effective=1, reason="ok")
    first = observation.model_copy(update={"frame": 6, "receipts": (receipt,)})
    second = observation.model_copy(update={"frame": 12, "receipts": (receipt,)})

    history.observe(first)
    history.observe(second)
    records = history.snapshot()

    assert sum(record["event"] == "command_receipt" for record in records) == 1
    assert records[0]["event"] == "command_receipt"
    assert records[0]["timestamp"]["frame"] == 6
    assert records[1]["event"] == "observation"
    assert records[2]["event"] == "observation"
    assert records[2]["observation_delta"] == {"frame": 12}
