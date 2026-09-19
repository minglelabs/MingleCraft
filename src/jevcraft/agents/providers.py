import json
import random
from typing import Protocol

import httpx

from jevcraft.models import ChoiceAnswer, DecisionRequest, ProviderResult


class DecisionProvider(Protocol):
    name: str
    model: str
    remote: bool

    async def decide(self, request: DecisionRequest) -> ProviderResult: ...


class RandomProvider:
    name, model, remote = "random", "uniform-hierarchical", False

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        answers = {}
        for key, question in request.questions.items():
            options = list(question.criteria)
            answers[key] = ChoiceAnswer(
                choice=self.rng.choice(options),
                probabilities={k: 1 / len(options) for k in options},
            )
        return ProviderResult(model=self.model, answers=answers)


class RuleBasedProvider:
    name, model, remote = "rule", "terran-economy-v1", False

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        return ProviderResult(
            model=self.model,
            answers={
                node: ChoiceAnswer(choice=max(scores, key=lambda key: (scores[key], key)))
                for node, scores in request.priorities.items()
            },
        )


class JevProvider:
    name, remote = "jev", True

    def __init__(self, api_key: str, model: str = "jev-latest", transport=None):
        if not api_key:
            raise ValueError("Set TYPESAFE_API_KEY before using the Jev provider")
        self.api_key, self.model, self.transport = api_key, model, transport

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
            response = await client.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "state": request.state,
                    "questions": {k: v.model_dump() for k, v in request.questions.items()},
                },
            )
            response.raise_for_status()
            data = response.json()
        result = ProviderResult.model_validate(
            {
                "model": data["model"],
                "answers": data["answers"],
                "usage": data.get("usage", {}),
            }
        )
        if any(a.confidence is None or a.probabilities is None for a in result.answers.values()):
            raise ValueError("Jev Choice responses must include confidence and probabilities")
        return result


class OpenAIProvider:
    name, remote = "openai", True

    def __init__(
        self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", transport=None
    ):
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY before using the OpenAI provider")
        self.api_key, self.model = api_key, model
        self.base_url, self.transport = base_url.rstrip("/"), transport

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        answer_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                k: {"type": "string", "enum": list(q.criteria)}
                for k, q in request.questions.items()
            },
            "required": list(request.questions),
        }
        async with httpx.AsyncClient(timeout=5, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Answer each finite choice question independently using the supplied game state. Return only the specified JSON object.",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "state": request.state,
                                    "questions": {
                                        k: v.model_dump() for k, v in request.questions.items()
                                    },
                                }
                            ),
                        },
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "rts_choices",
                            "strict": True,
                            "schema": answer_schema,
                        },
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
        answers = json.loads(data["choices"][0]["message"]["content"])
        usage = data.get("usage", {})
        return ProviderResult(
            model=data.get("model", self.model),
            answers={k: ChoiceAnswer(choice=v) for k, v in answers.items()},
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )


class LocalModelProvider(OpenAIProvider):
    name, remote = "local", False

    def __init__(self, model: str, base_url: str, transport=None):
        # Requires an OpenAI-compatible server supporting strict JSON schemas.
        super().__init__("local", model, base_url, transport)
