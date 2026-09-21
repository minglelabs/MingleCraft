import asyncio
import json
import math
import time
from pathlib import Path

import httpx

from minglecraft import __version__
from minglecraft.actions.executor import envelope
from minglecraft.actions.generator import ActionGenerator
from minglecraft.actions.hierarchy import ChoiceTree
from minglecraft.actions.pruner import prune
from minglecraft.actions.spatial import (
    build_refinement_question,
    build_region_question,
    child_bounds,
    get_spatial_grid_spec,
    parse_refinement_key,
    parse_region_key,
)
from minglecraft.agents.encoding import compact_request_payload
from minglecraft.agents.providers import DecisionProvider
from minglecraft.evaluation.logger import MatchLogger
from minglecraft.models import (
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    Observation,
    Position,
    ProviderResult,
)
from minglecraft.state import StateBuilder
from minglecraft.state.history import MatchHistory
from minglecraft.strategy.policy import POLICY_INSTRUCTIONS, SHARED_POLICY
from minglecraft.strategy.scheduler import Scheduler

LIVE_VALUE_INSTRUCTIONS = "Estimate the chance of ultimately winning from the supplied game state. Return only the requested Noul probability."


def live_policy_instructions(map_name: str | None = None) -> str:
    map_info = f" on map '{map_name}'" if map_name else ""
    return (
        f"You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher){map_info}. "
        "Wire format: state uses 'jev/compact-v1'. state.candidate_actions contains available candidates with "
        "columns [id, category, group, label, commands]. Each criteria key is an action id mapped to leaf:action_id. "
        "Ground-coordinate candidates with null position or tile require subsequent coordinate choices; "
        "they do not target the origin. Pick the single best action id from criteria to advance victory. "
        "Return only the requested Choice answer."
    )


class AgentLoop:
    def __init__(
        self,
        provider: DecisionProvider,
        output: Path,
        *,
        deadline_ms: int = 10_000,
        ttl_frames: int = 480,
        limit: int = 50,
        seed: int = 0,
        mode: str = "live",
        pricing: tuple[float, float] | None = None,
        strategy: str = SHARED_POLICY,
        request_size_limit: int = 1_500_000,
        single_stage: bool = False,
        spatial_precision_px: int = 8,
    ):
        if deadline_ms <= 0 or ttl_frames <= 0 or not 8 <= limit <= 50:
            raise ValueError("Invalid deadline, TTL or candidate limit")
        if request_size_limit <= 0:
            raise ValueError("Request size limit must be positive")
        if spatial_precision_px <= 0:
            raise ValueError("Spatial precision must be positive")
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
        self.single_stage = single_stage
        self.spatial_precision_px = spatial_precision_px

    @property
    def staged(self) -> bool:
        return bool(getattr(self.provider, "supports_value", False)) and not self.single_stage

    def _request(
        self, state: dict, questions: dict, choice_tree: dict | None = None
    ) -> DecisionRequest:
        request = DecisionRequest(
            state=state, questions=questions, choice_tree=choice_tree, priorities={}
        )
        # Measure the actual wire payload (state + questions), not the full
        # model_dump which includes priorities never sent to the provider.
        payload = json.dumps(
            compact_request_payload(request, self.provider.model, choice_tree)
            if self.staged or hasattr(self.provider, "endpoint")
            else {
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

    def _wire_bytes(self, request: DecisionRequest, choice_tree: dict | None = None) -> int | None:
        if not (self.staged or hasattr(self.provider, "endpoint")):
            return None
        return len(
            json.dumps(
                compact_request_payload(request, self.provider.model, choice_tree),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )

    @staticmethod
    def _resolve_spatial(result: ProviderResult, question: ChoiceQuestion) -> str:
        if set(result.answers) != {"spatial"}:
            raise ValueError("invalid_spatial_answer_ids")
        answer = result.answers["spatial"]
        if answer.type != "choice" or answer.choice not in question.criteria:
            raise ValueError("invalid_spatial_choice")
        if answer.probabilities is not None:
            if set(answer.probabilities) != set(question.criteria):
                raise ValueError("invalid_spatial_probabilities")
            if any(not math.isfinite(v) or not 0 <= v <= 1 for v in answer.probabilities.values()):
                raise ValueError("invalid_spatial_probability")
            if not math.isclose(sum(answer.probabilities.values()), 1, abs_tol=0.01):
                raise ValueError("invalid_spatial_probability_sum")
        return answer.choice

    async def _resolve_spatial_action(self, obs, state, action, deadline, calls):
        command = action.commands[0]
        spatial_kinds = {"move", "attack", "patrol", "build", "land", "unload_all", "use_tech"}
        if not action.category.startswith("spatial_"):
            return action, []
        if (
            command.position is not None
            or command.tile is not None
            or command.kind not in spatial_kinds
        ):
            return action, []
        spec = get_spatial_grid_spec(obs, self.spatial_precision_px)
        actor = f"unit(s) {','.join(map(str, command.unit_ids))}"
        bounds = (0, 0, spec.map_width, spec.map_height)
        path = []

        async def ask(question):
            # Spatial criteria carry geometric bounds in their descriptions.
            # Do not provide a choice tree: compact encoding would replace
            # those descriptions with leaf references before reaching Jev.
            request = self._request(self._context(state, [action]), {"spatial": question})
            if deadline - time.monotonic() <= 0:
                raise asyncio.TimeoutError()
            result, latency = await self._call(request, deadline)
            calls.append({"stage": "spatial", "latency_ms": latency, "usage": result.usage})
            choice = self._resolve_spatial(result, question)
            path.append(
                {
                    "node": "spatial",
                    "choice": choice,
                    "answer": result.answers["spatial"].model_dump(),
                }
            )
            return choice

        region = parse_region_key(await ask(build_region_question(actor, command.kind, spec)))
        bounds = spec.region_bounds(*region)
        level = 1

        # Budget reserve: estimate remaining latency per step to stop refinement
        # early before the deadline expires, preventing a fallback to wait / actions[0].
        latencies = [c["latency_ms"] for c in calls if c.get("latency_ms", 0) > 0]
        typical_latency_sec = (sum(latencies) / len(latencies) / 1000.0) if latencies else 0.05
        safety_margin_sec = max(0.015, typical_latency_sec * 1.25)

        while max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > spec.precision_px:
            if deadline - time.monotonic() < safety_margin_sec:
                break
            try:
                x, y = parse_refinement_key(
                    await ask(build_refinement_question(actor, command.kind, bounds, level))
                )
            except asyncio.TimeoutError:
                # A completed parent region is already a valid ground target. Keep
                # that resolution when the next refinement cannot fit the budget.
                break
            bounds = child_bounds(bounds, x, y)
            level += 1
            if calls and calls[-1].get("latency_ms", 0) > 0:
                latencies.append(calls[-1]["latency_ms"])
                typical_latency_sec = sum(latencies) / len(latencies) / 1000.0
                safety_margin_sec = max(0.015, typical_latency_sec * 1.25)
        position = {
            "x": min(spec.map_width - 1, (bounds[0] + bounds[2] - 1) // 2),
            "y": min(spec.map_height - 1, (bounds[1] + bounds[3] - 1) // 2),
        }
        if command.kind in {"build", "land"}:
            resolved = command.model_copy(
                update={"tile": Position(x=position["x"] // 32, y=position["y"] // 32)}
            )
        else:
            resolved = command.model_copy(update={"position": Position(**position)})
        return action.model_copy(update={"commands": (resolved,)}), path

    def _context(
        self,
        state: dict,
        actions: list,
        latest_value=None,
        choice_tree: dict | None = None,
    ) -> dict:
        # Match history remains in local traces. The current observation,
        # StateBuilder's enemy memory, and the complete candidate list are
        # sent to Jev; repeating the lossless event log exhausts its context.
        context = {
            **state,
            "observation": state.get("observation", state),
            "candidate_actions": [a.model_dump(exclude={"priority"}) for a in actions],
            "latest_value": latest_value,
        }
        if choice_tree is not None:
            context["choice_tree"] = choice_tree
        return context

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
                    "candidate_limit": None if (self.staged or self.single_stage) else self.limit,
                    "candidate_exhaustive": self.staged or self.single_stage,
                    "scheduling": "all_categories"
                    if (self.staged or self.single_stage)
                    else "scheduler_due",
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
            "observation": {
                **obs.model_dump(),
                "enemies": [e for e in obs.model_dump()["enemies"] if e["visible"]],
            },
        }
        staged = bool(getattr(self.provider, "supports_value", False))
        if self.single_stage:
            staged = False
        due = (
            set(Scheduler.periods)
            if (staged or self.single_stage)
            else self.scheduler.due(obs.frame)
        )
        actions = (
            self.generator.generate(obs, due, exhaustive=True)
            if (staged or self.single_stage)
            else prune(self.generator.generate(obs, due), self.limit)
        )
        tree = ChoiceTree(
            state,
            actions,
            instructions=live_policy_instructions(obs.map_name)
            if (staged or self.single_stage)
            else POLICY_INSTRUCTIONS,
        )
        choice_tree = {node: dict(options) for node, options in tree.nodes.items()}
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
                    state,
                    actions,
                    self.latest_value,
                )
                value_request = self._request(
                    value_state,
                    {"win_probability": NoulQuestion(instructions=LIVE_VALUE_INSTRUCTIONS)},
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
                        state,
                        actions,
                        self.latest_value,
                        choice_tree,
                    )
                    policy_request = self._request(policy_state, policy_questions, choice_tree)
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
                if hasattr(self.provider, "endpoint"):
                    # Staged-like compact payload for direct policy call
                    choice_tree = {node: dict(options) for node, options in tree.nodes.items()}
                    policy_state = self._context(state, actions, None, choice_tree)
                    policy_request = self._request(
                        policy_state, tree.request.questions, choice_tree
                    )
                else:
                    policy_request = tree.request
                if deadline - time.monotonic() <= 0:
                    raise asyncio.TimeoutError()
                attempted_stage, call_started = "policy", time.perf_counter()
                policy_result, latency = await self._call(policy_request, deadline)
                calls.append(
                    {"stage": "policy", "latency_ms": latency, "usage": policy_result.usage}
                )
                selected, path = tree.resolve(policy_result)
            else:
                selected, path = tree.resolve(ProviderResult(model=self.provider.model, answers={}))
            if selected.commands:
                selected, spatial_path = await self._resolve_spatial_action(
                    obs, state, selected, deadline, calls
                )
                path.extend(spatial_path)
        except Exception as exc:
            # A selected spatial action is only executable after every coordinate
            # choice resolves. Never let its placeholder command cross the bridge.
            selected = actions[0]
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
                print(f"MingleCraft provider HTTP error: {provider_http_status}", flush=True)
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
                "value_request_bytes": self._wire_bytes(value_request)
                if value_request is not None
                else None,
                "policy_request": policy_request.model_dump()
                if policy_request is not None
                else None,
                "policy_request_bytes": self._wire_bytes(policy_request, choice_tree)
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
