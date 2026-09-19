import asyncio
import json

from jevcraft.loop import AgentLoop
from jevcraft.models import Action, ChoiceAnswer, NoulAnswer, ProviderResult, Receipt
from jevcraft.state.history import MatchHistory


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

    assert len(provider.requests) == 4
    value, policy, value2, policy2 = provider.requests
    assert list(value.questions) == ["win_probability"]
    assert "latest_value" not in value.state or value.state["latest_value"] is None
    assert policy.state["latest_value"] == {"frame": 0, "win_probability": 0.63}
    assert any(record["event"] == "issued_action" for record in policy2.state["match_history"])
    assert all(
        enemy["visible"]
        for record in policy2.state["match_history"]
        if record["event"] == "observation" and "enemies" in record["observation_delta"]
        for enemy in record["observation_delta"]["enemies"]
    )
    assert "strategy_policy" in policy.state


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
    assert len(provider.requests[0].state["candidate_actions"]) == 61
    assert len(provider.requests[1].state["candidate_actions"]) == 61


def test_request_size_guard_sends_nothing_and_keeps_history_intact(observation, tmp_path):
    provider = StagedProvider()
    loop = AgentLoop(provider, tmp_path, request_size_limit=128)

    result = asyncio.run(loop.step(observation))

    assert result.fallback_reason == "request_size_guard"
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
