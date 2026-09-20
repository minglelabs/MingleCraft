import asyncio
import json
import time
from pathlib import Path

import httpx

from jevcraft import __version__
from jevcraft.actions.executor import envelope
from jevcraft.actions.generator import ActionGenerator
from jevcraft.actions.hierarchy import ChoiceTree
from jevcraft.actions.pruner import prune
from jevcraft.agents.providers import DecisionProvider
from jevcraft.evaluation.logger import MatchLogger
from jevcraft.models import DecisionRequest, NoulQuestion, Observation, ProviderResult
from jevcraft.state import StateBuilder
from jevcraft.state.history import MatchHistory
from jevcraft.strategy.policy import SHARED_POLICY, VALUE_INSTRUCTIONS
from jevcraft.strategy.scheduler import Scheduler


class AgentLoop:
    # Keep the full history in local evaluation logs, but send a bounded
    # chronological window to Jev so the request stays within its context
    # budget during long live games.
    model_history_limit = 32

    def __init__(
        self,
        provider: DecisionProvider,
        output: Path,
        *,
        deadline_ms: int = 200,
        ttl_frames: int = 24,
        limit: int = 50,
        seed: int = 0,
        mode: str = "live",
        pricing: tuple[float, float] | None = None,
        strategy: str = SHARED_POLICY,
        request_size_limit: int = 1_500_000,
    ):
        if deadline_ms <= 0 or ttl_frames <= 0 or not 8 <= limit <= 50:
            raise ValueError("Invalid deadline, TTL or candidate limit")
        if request_size_limit <= 0:
            raise ValueError("Request size limit must be positive")
        if pricing is not None and any(p < 0 for p in pricing):
            raise ValueError("Token pricing cannot be negative")
        self.provider, self.output = provider, output
        self.deadline_ms, self.ttl_frames, self.limit = deadline_ms, ttl_frames, limit
        self.seed, self.mode, self.pricing = seed, mode, pricing
        self.strategy, self.request_size_limit = strategy, request_size_limit
        self.state_builder, self.generator, self.scheduler = (
            StateBuilder(),
            ActionGenerator(),
            Scheduler(),
        )
        self.history = MatchHistory()
        self.logger = None
        self.match_id = self.map_hash = None
        self.last_frame, self.sequence = -1, 0
        self.retry_after = 0.0
        self.finished = False
        self.latest_value: dict | None = None

    @property
    def staged(self) -> bool:
        return bool(getattr(self.provider, "supports_value", False))

    def _request(self, state: dict, questions: dict) -> DecisionRequest:
        request = DecisionRequest(state=state, questions=questions, priorities={})
        # Measure the actual wire payload (state + questions), not the full
        # model_dump which includes priorities never sent to the provider.
        payload = json.dumps(
            {
                "model": self.provider.model,
                "state": state,
                "questions": {k: v.model_dump() for k, v in request.questions.items()},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload.encode()) > self.request_size_limit:
            raise ValueError("request_size_guard")
        return request

    def _context(self, state: dict, actions: list, history: list[dict], latest_value=None) -> dict:
        history_window = history[-self.model_history_limit :]
        return {
            **state,
            "observation": state.get("observation", state),
            "candidate_actions": [a.model_dump(exclude={"priority"}) for a in actions],
            "match_history": history_window,
            "match_history_truncated": len(history_window) < len(history),
            "strategy_policy": self.strategy,
            "latest_value": latest_value,
        }

    async def _call(self, request: DecisionRequest, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        started = time.perf_counter()
        result = await asyncio.wait_for(self.provider.decide(request), remaining)
        return result, (time.perf_counter() - started) * 1000

    async def step(self, obs: Observation):
        if self.finished:
            raise ValueError("Match already ended")
        if self.match_id is None:
            self.match_id, self.map_hash = obs.match_id, obs.map_hash
            self.logger = MatchLogger(
                self.output / obs.match_id,
                {
                    "version": __version__,
                    "protocol_version": 1,
                    "provider": self.provider.name,
                    "model": self.provider.model,
                    "remote": self.provider.remote,
                    "seed": self.seed,
                    "mode": self.mode,
                    "pricing": self.pricing,
                    "deadline_ms": self.deadline_ms,
                    "ttl_frames": self.ttl_frames,
                    "request_size_limit": self.request_size_limit,
                    "candidate_limit": None if self.staged else self.limit,
                    "candidate_exhaustive": self.staged,
                    "scheduling": "all_categories" if self.staged else "scheduler_due",
                    "map_name": obs.map_name,
                    "map_hash": obs.map_hash,
                    "cadence_frames": self.scheduler.periods,
                },
            )
        if obs.match_id != self.match_id or obs.map_hash != self.map_hash:
            raise ValueError("Match identity changed")
        if obs.frame <= self.last_frame:
            raise ValueError("Observation frame must advance")
        step_started = time.monotonic()
        deadline = step_started + self.deadline_ms / 1000
        self.last_frame, self.sequence = obs.frame, self.sequence + 1
        self.history.observe(obs)
        state = {
            **self.state_builder.build(obs),
            "strategy_policy": self.strategy,
            "observation": {
                **obs.model_dump(),
                "enemies": [e for e in obs.model_dump()["enemies"] if e["visible"]],
            },
        }
        staged = bool(getattr(self.provider, "supports_value", False))
        due = set(Scheduler.periods) if staged else self.scheduler.due(obs.frame)
        actions = (
            self.generator.generate(obs, due, exhaustive=True)
            if staged
            else prune(self.generator.generate(obs, due), self.limit)
        )
        tree = ChoiceTree(state, actions)
        selected, path, reason, error = actions[0], [], None, None
        provider_http_status = None
        value_result = policy_result = None
        value_request = policy_request = None
        calls = []
        attempted_stage = None
        call_started = None
        try:
            if time.monotonic() < self.retry_after:
                reason = "provider_cooldown"
            elif staged:
                value_state = self._context(
                    state, actions, self.history.snapshot(), self.latest_value
                )
                value_request = self._request(
                    value_state, {"win_probability": NoulQuestion(instructions=VALUE_INSTRUCTIONS)}
                )
                if deadline - time.monotonic() <= 0:
                    raise asyncio.TimeoutError()
                attempted_stage, call_started = "value", time.perf_counter()
                value_result, latency = await self._call(value_request, deadline)
                calls.append({"stage": "value", "latency_ms": latency, "usage": value_result.usage})
                answer = value_result.answers.get("win_probability")
                if answer is None or answer.type != "noul":
                    raise ValueError("invalid_value_answer")
                self.latest_value = {"frame": obs.frame, "win_probability": answer.noul}
                self.history.value_estimate(obs, answer.noul)
                policy_questions = tree.request.questions
                if policy_questions:
                    policy_state = self._context(
                        state, actions, self.history.snapshot(), self.latest_value
                    )
                    policy_request = self._request(policy_state, policy_questions)
                    if deadline - time.monotonic() <= 0:
                        raise asyncio.TimeoutError()
                    attempted_stage, call_started = "policy", time.perf_counter()
                    policy_result, latency = await self._call(policy_request, deadline)
                    calls.append(
                        {"stage": "policy", "latency_ms": latency, "usage": policy_result.usage}
                    )
                    selected, path = tree.resolve(policy_result)
                else:
                    selected, path = tree.resolve(
                        ProviderResult(model=self.provider.model, answers={})
                    )
            elif tree.request.questions:
                policy_request = tree.request
                if deadline - time.monotonic() <= 0:
                    raise asyncio.TimeoutError()
                attempted_stage, call_started = "policy", time.perf_counter()
                policy_result, latency = await self._call(tree.request, deadline)
                calls.append(
                    {"stage": "policy", "latency_ms": latency, "usage": policy_result.usage}
                )
                selected, path = tree.resolve(policy_result)
            else:
                selected, path = tree.resolve(ProviderResult(model=self.provider.model, answers={}))
        except Exception as exc:
            if attempted_stage and not any(c["stage"] == attempted_stage for c in calls):
                calls.append(
                    {
                        "stage": attempted_stage,
                        "latency_ms": (time.perf_counter() - call_started) * 1000,
                        "usage": {},
                    }
                )
            error = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                provider_http_status = exc.response.status_code
                # Do not log headers, credentials, or provider response bodies.
                print(f"JevCraft provider HTTP error: {provider_http_status}", flush=True)
            reason = (
                "deadline"
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                else (
                    "request_size_guard" if str(exc) == "request_size_guard" else "provider_error"
                )
            )
            if reason not in {"request_size_guard", "provider_cooldown"}:
                self.retry_after = time.monotonic() + 2
        decision = envelope(obs, selected, self.sequence, self.ttl_frames, reason)
        self.scheduler.selected(selected.category, obs.frame)
        self.history.decision(obs, selected, decision.decision_id)
        self.logger.write(
            {
                "observation": obs.model_dump(),
                "state": state,
                "actions": [a.model_dump() for a in actions],
                "questions": {k: q.model_dump() for k, q in tree.request.questions.items()},
                "called": bool(calls),
                "calls": calls,
                "latency_ms": (time.monotonic() - step_started) * 1000,
                "value_request": value_request.model_dump() if value_request is not None else None,
                "policy_request": policy_request.model_dump()
                if policy_request is not None
                else None,
                "error": error,
                "provider_http_status": provider_http_status,
                "provider_result": policy_result.model_dump()
                if policy_result is not None
                else None,
                "value_result": value_result.model_dump() if value_result is not None else None,
                "value_estimate": self.latest_value,
                "path": path,
                "envelope": decision.model_dump(),
            }
        )
        return decision

    def finish(self, obs: Observation, completed: bool = True):
        if self.logger is None or obs.match_id != self.match_id or obs.map_hash != self.map_hash:
            raise ValueError("Cannot finish an unknown match")
        if self.finished or obs.frame < self.last_frame:
            raise ValueError("Duplicate or stale match end")
        self.history.observe(obs)
        self.finished = True
        self.logger.trace.write(
            json.dumps(
                {
                    "event": "match_end",
                    "observation": obs.model_dump(),
                    "match_history": self.history.snapshot(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return self.logger.finish(obs, completed)
