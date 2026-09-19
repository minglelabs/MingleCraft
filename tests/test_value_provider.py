"""Validate the actual typed value API boundary, including malformed answers."""

import asyncio
import json

import httpx
import pytest

from jevcraft.agents.providers import JevProvider, OpenRouterJevProvider
from jevcraft.models import DecisionRequest, NoulQuestion
from jevcraft.strategy.policy import SHARED_POLICY, VALUE_INSTRUCTIONS


@pytest.mark.parametrize("provider_class", [JevProvider, OpenRouterJevProvider])
def test_value_contract(provider_class):
    request = DecisionRequest(
        state={"strategy_policy": SHARED_POLICY, "match_history": [], "latest_value": None},
        questions={"win_probability": NoulQuestion(instructions=VALUE_INSTRUCTIONS)},
        priorities={},
    )

    def respond(http_request):
        payload = json.loads(http_request.content)
        assert payload["questions"]["win_probability"] == {
            "type": "noul",
            "instructions": VALUE_INSTRUCTIONS,
        }
        assert payload["state"]["latest_value"] is None
        assert "priorities" not in payload
        return httpx.Response(
            200,
            json={
                "model": "test",
                "answers": {"win_probability": {"type": "noul", "noul": 0.42}},
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    result = asyncio.run(
        provider_class("test", transport=httpx.MockTransport(respond)).decide(request)
    )
    assert result.answers["win_probability"].noul == 0.42
    assert "confidence" not in result.answers["win_probability"].model_dump()


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "noul", "noul": -1},
        {"type": "noul", "noul": 1.1},
        {"type": "noul", "noul": float("nan")},
        {"type": "choice", "choice": "yes", "confidence": 1, "probabilities": {"yes": 1}},
    ],
)
def test_invalid_value_rejected(answer):
    request = DecisionRequest(
        state={}, questions={"win_probability": NoulQuestion(instructions="Win?")}, priorities={}
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            content=json.dumps(
                {
                    "model": "test",
                    "answers": {"win_probability": answer},
                }
            ).encode(),
        )
    )
    with pytest.raises(ValueError):
        asyncio.run(JevProvider("test", transport=transport).decide(request))
