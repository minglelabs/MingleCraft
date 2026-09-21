import json
import random
from typing import Protocol

import httpx

from jevcraft.agents.encoding import compact_request_payload
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
    name, remote, supports_value = "jev", True, True
    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, api_key: str, model: str = "jev-latest", transport=None):
        if not api_key:
            raise ValueError("Set TYPESAFE_API_KEY before using the Jev provider")
        self.api_key, self.model, self.transport = api_key, model, transport
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=5, transport=self.transport)
        return self._client

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        import asyncio

        client = self._get_client()
        response = await asyncio.to_thread(
            client.post,
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=compact_request_payload(request, self.model),
        )
        if response.is_error:
            detail = response.text[:800].replace(self.api_key, "[REDACTED]")
            print(
                f"JevCraft provider response {response.status_code}: {detail}",
                flush=True,
            )
        response.raise_for_status()
        data = response.json()
        result = ProviderResult.model_validate(
            {
                "model": data.get("model", self.model),
                "answers": data["answers"],
                "usage": data.get("usage", {}),
            }
        )
        if set(result.answers) != set(request.questions):
            raise ValueError("Jev answer IDs do not match questions")
        for key, answer in result.answers.items():
            if answer.type != request.questions[key].type:
                raise ValueError("Jev answer type does not match question")
            if answer.type == "choice" and (
                answer.confidence is None or answer.probabilities is None
            ):
                raise ValueError("Jev Choice responses must include confidence and probabilities")
        return result


class OpenRouterJevProvider(JevProvider):
    name = "openrouter-jev"
    endpoint = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, api_key: str, model: str = "~typesafe/jev-latest", transport=None):
        if not api_key:
            raise ValueError("Set OPENROUTER_API_KEY before using the OpenRouter Jev provider")
        super().__init__(api_key, model, transport)


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
