import asyncio
import json

import httpx

from minglecraft.agents import JevProvider
from minglecraft.agents.encoding import compact_request_payload
from minglecraft.loop import AgentLoop
from minglecraft.models import Action, ChoiceAnswer, NoulAnswer, ProviderResult, Receipt
from minglecraft.state.history import MatchHistory


class CombinedProvider:
    name, model, remote, supports_value = "jev", "jev-test", True, True

    def __init__(self, fail=False, delay=0):
        self.requests = []
        self.fail = fail
        self.delay = delay

    async def decide(self, request):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("private response body")
        return ProviderResult(
            model=self.model,
            answers={
                key: NoulAnswer(noul=0.63) if question.type == "noul"
                else ChoiceAnswer(choice=next(iter(question.criteria)))
                for key, question in request.questions.items()
            },
            usage={"input_tokens": 11, "output_tokens": 2},
        )


def test_value_and_policy_share_one_request_per_step(observation, tmp_path):
    provider = CombinedProvider()
    loop = AgentLoop(provider, tmp_path, deadline_ms=5000)
    asyncio.run(loop.step(observation))
    next_observation = observation.model_copy(update={
        "frame": 6,
        "enemies": tuple(enemy.model_copy(update={"visible": False}) for enemy in observation.enemies),
    })
    asyncio.run(loop.step(next_observation))

    assert len(provider.requests) == 2
    assert all("win_probability" in r.questions for r in provider.requests)
    assert all(any(q.type == "choice" for q in r.questions.values()) for r in provider.requests)
    assert provider.requests[0].state["latest_value"] is None
    assert provider.requests[1].state["latest_value"] == {"frame": 0, "win_probability": 0.63}
    assert all(len(r.state["candidate_actions"]) == 0 for r in provider.requests)
    assert all("match_history" not in r.state for r in provider.requests)
    assert all("choice_tree" not in compact_request_payload(r, provider.model)["state"] for r in provider.requests)
    assert any(record["event"] == "issued_action" for record in loop.history.snapshot())
    records = [json.loads(line) for line in (tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()]
    assert all(record["query_count"] == 1 for record in records)


def test_combined_failure_sanitizes_error_and_counts_one_call(observation, tmp_path):
    provider = CombinedProvider(fail=True)
    loop = AgentLoop(provider, tmp_path, deadline_ms=500)
    result = asyncio.run(loop.step(observation))
    summary = loop.finish(observation)
    trace = (tmp_path / "test_match" / "decisions.jsonl").read_text()
    event = json.loads(trace.splitlines()[0])
    assert result.fallback_reason == "provider_error"
    assert len(provider.requests) == 1
    assert event["query_count"] == 1
    assert event["calls"][0]["stage"] == "combined"
    assert event["calls"][0]["error"] == "RuntimeError"
    assert "private response body" not in trace
    assert summary["provider_calls"] == 1
    assert summary["value_calls"] == 1
    assert summary["policy_calls"] == 1
    assert summary["last_win_probability"] is None


def test_all_exhaustive_candidates_remain_in_question_tree(observation, tmp_path):
    provider = CombinedProvider()
    loop = AgentLoop(provider, tmp_path)

    def generate(obs, due, exhaustive=False):
        assert exhaustive is True
        return [Action(id="wait", category="wait", group="wait", label="wait")] + [
            Action(id=f"attack_{i}", category="attack", group=f"g{i}", label=f"candidate {i}")
            for i in range(60)
        ]

    loop.generator.generate = generate
    asyncio.run(loop.step(observation))
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert len(request.state["candidate_actions"]) == 0
    leaves = {child[5:] for options in request.choice_tree.values() for child in options.values() if child.startswith("leaf:")}
    assert leaves == {"wait", *(f"attack_{i}" for i in range(60))}
    assert set(request.questions).issuperset({"win_probability"})
    assert all(len(q.criteria) <= 200 for q in request.questions.values() if q.type == "choice")


def test_request_size_guard_sends_nothing_and_keeps_history_intact(observation, tmp_path):
    provider = CombinedProvider()
    loop = AgentLoop(provider, tmp_path, request_size_limit=128)
    result = asyncio.run(loop.step(observation))
    assert result.fallback_reason == "request_size_guard"
    assert provider.requests == []
    assert len(loop.history.snapshot()) == 2
    loop.finish(observation)


def test_combined_timeout_is_logged_as_one_failed_call(observation, tmp_path):
    provider = CombinedProvider(delay=1)
    loop = AgentLoop(provider, tmp_path, deadline_ms=20)
    result = asyncio.run(loop.step(observation))
    loop.finish(observation)
    record = json.loads((tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()[0])
    assert result.fallback_reason == "deadline"
    assert len(provider.requests) == 1
    assert record["query_count"] == 1
    assert [call["stage"] for call in record["calls"]] == ["combined"]
    assert record["value_result"] is None


def test_http_failure_is_one_attempted_call(observation, tmp_path):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(503, json={"error": "unavailable"})

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    loop = AgentLoop(provider, tmp_path)
    decision = asyncio.run(loop.step(observation))
    record = json.loads((tmp_path / "test_match" / "decisions.jsonl").read_text().splitlines()[0])
    assert decision.fallback_reason == "provider_error"
    assert len(payloads) == 1
    assert "win_probability" in payloads[0]["questions"]
    assert record["query_count"] == 1
    assert record["calls"][0]["stage"] == "combined"
    assert record["calls"][0]["error"] == "HTTPStatusError"


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
