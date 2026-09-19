import asyncio
import json

import httpx
import pytest

from jevcraft.actions.generator import ActionGenerator
from jevcraft.actions.hierarchy import ChoiceTree
from jevcraft.agents import JevProvider, OpenAIProvider, RuleBasedProvider
from jevcraft.bwapi.synthetic import SyntheticGame
from jevcraft.loop import AgentLoop
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
