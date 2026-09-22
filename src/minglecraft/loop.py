import asyncio
import json
import math
import time
from pathlib import Path

import httpx

from minglecraft import __version__
from minglecraft.actions.executor import envelope
from minglecraft.actions.generator import ActionGenerator
from minglecraft.actions.hierarchy import PROBABILITY_SUM_TOLERANCE, ChoiceTree
from minglecraft.actions.pruner import prune
from minglecraft.actions.spatial import (
    build_refinement_question,
    build_region_question,
    child_bounds,
    get_spatial_grid_spec,
    parse_refinement_key,
    parse_region_key,
)
from minglecraft.agents.encoding import compact_request_payload, estimate_tokens_conservative
from minglecraft.agents.providers import DecisionProvider
from minglecraft.evaluation.logger import MatchLogger
from minglecraft.models import (
    Action,
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


class ContextBudgetGuard(ValueError):
    """A request cannot fit even after all safe choice-node partitions."""

    def __init__(self, diagnostic: str):
        super().__init__("context_budget_exceeded")
        self.diagnostic = diagnostic


def live_policy_instructions(map_name: str | None = None) -> str:
    map_info = f" on map '{map_name}'" if map_name else ""
    return (
        f"You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher){map_info}. "
        "Your objective is to win the current match. "
        "Wire format: state uses 'jev/compact-v1'. Choice questions follow a command kind hierarchy: "
        "choose the command kind, then the acting unit or unit group, then the specific command target or parameters. "
        "Each criteria key is the exact option ID for that node. "
        "Use observation.self_race and enemy_race when interpreting the game and map state. "
        "Positions are pixel coordinates; build and land tile fields are build-tile coordinates. "
        "Answer the one requested Choice question with an existing option ID. "
        "Ground-coordinate candidates with null position or tile require subsequent coordinate choices; "
        "they do not target the origin. Return only the requested Choice answers."
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
        byte_budget: int = 96_000,
        token_budget: int = 24_000,
        single_stage: bool = False,
        spatial_precision_px: int = 8,
    ):
        if deadline_ms <= 0 or ttl_frames <= 0 or not 8 <= limit <= 50:
            raise ValueError("Invalid deadline, TTL or candidate limit")
        if request_size_limit <= 0 or byte_budget <= 0 or token_budget <= 0:
            raise ValueError("Budgets and limits must be positive")
        if spatial_precision_px <= 0:
            raise ValueError("Spatial precision must be positive")
        if pricing is not None and any(p < 0 for p in pricing):
            raise ValueError("Token pricing cannot be negative")
        self.provider, self.output = provider, output
        self.deadline_ms, self.ttl_frames, self.limit = deadline_ms, ttl_frames, limit
        self.seed, self.mode, self.pricing = seed, mode, pricing
        self.strategy, self.request_size_limit = strategy, request_size_limit
        self.byte_budget, self.token_budget = byte_budget, token_budget
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
        self,
        state: dict,
        questions: dict,
        choice_tree: dict | None = None,
        priorities: dict | None = None,
    ) -> DecisionRequest:
        request = DecisionRequest(
            state=state,
            questions=questions,
            choice_tree=choice_tree,
            priorities=priorities or {},
        )
        is_wire_model = self.staged or hasattr(self.provider, "endpoint")
        if is_wire_model:
            payload_dict = compact_request_payload(
                request,
                self.provider.model,
                choice_tree,
            )
            payload_bytes = json.dumps(
                payload_dict,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()

            if len(payload_bytes) > self.request_size_limit:
                raise ValueError("request_size_guard")
            if len(payload_bytes) > self.byte_budget:
                raise ValueError("context_budget_exceeded")

            estimated_tokens = estimate_tokens_conservative(payload_bytes.decode(errors="replace"))
            if estimated_tokens > self.token_budget:
                raise ValueError("context_budget_exceeded")
        else:
            payload_bytes = json.dumps(
                {
                    "model": self.provider.model,
                    "state": state,
                    "questions": {k: v.model_dump() for k, v in request.questions.items()},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            if len(payload_bytes) > self.request_size_limit:
                raise ValueError("request_size_guard")

        return request

    def _wire_bytes(self, request: DecisionRequest, choice_tree: dict | None = None) -> int | None:
        if not (self.staged or hasattr(self.provider, "endpoint")):
            return None
        return len(
            json.dumps(
                compact_request_payload(
                    request,
                    self.provider.model,
                    choice_tree,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )

    def _wire_metrics(self, request: DecisionRequest) -> dict[str, int] | None:
        """Measure the exact compact payload that the HTTP provider will send."""
        if not (self.staged or hasattr(self.provider, "endpoint")):
            return None
        payload = compact_request_payload(request, self.provider.model, request.choice_tree)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return {
            "request_bytes": len(encoded),
            "estimated_tokens": estimate_tokens_conservative(encoded.decode(errors="replace")),
        }

    @staticmethod
    def _range_label(labels: list[str], start: int, end: int) -> str:
        first, last = labels[start], labels[end - 1]
        return f"options {start + 1}-{end}: {first} through {last}"

    def _split_budget_node(self, tree: ChoiceTree, node: str) -> None:
        """Split a node in deterministic order while preserving every leaf reference."""
        options = list(tree.nodes[node].items())
        if len(options) <= 2:
            raise ContextBudgetGuard(f"node={node}; options={len(options)}; state_or_question_too_large")
        midpoint = len(options) // 2
        ranges = (options[:midpoint], options[midpoint:])
        parent_options: dict[str, str] = {}
        parent_priorities: dict[str, float] = {}
        parent_question = tree.request.questions[node]
        for index, branch in enumerate(ranges, 1):
            child = f"{node}_budget_{index}"
            branch_options = dict(branch)
            tree.nodes[child] = branch_options
            tree.priorities[child] = {
                key: tree.priorities[node][key] for key in branch_options
            }
            tree.request.questions[child] = ChoiceQuestion(
                instructions=parent_question.instructions,
                criteria={
                    key: tree.request.questions[node].criteria[key] for key in branch_options
                },
            )
            partition_key = f"budget_range_{index}"
            parent_options[partition_key] = child
            parent_priorities[partition_key] = max(
                tree.priorities[child].values(), default=0.0
            )
        tree.nodes[node] = parent_options
        tree.priorities[node] = parent_priorities
        tree.request.questions[node] = ChoiceQuestion(
            instructions=parent_question.instructions,
            criteria={
                f"budget_range_{index}": self._range_label(
                    [tree.request.questions[node].criteria[key] for key, _ in options],
                    0 if index == 1 else midpoint,
                    midpoint if index == 1 else len(options),
                )
                for index in (1, 2)
            },
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
            if not math.isclose(
                sum(answer.probabilities.values()),
                1,
                rel_tol=0.0,
                abs_tol=PROBABILITY_SUM_TOLERANCE,
            ):
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
            spatial_state = self._context(state, [action])
            spatial_questions = {"spatial": question}
            spatial_candidate = DecisionRequest(
                state=spatial_state,
                questions=spatial_questions,
                priorities={},
            )
            spatial_metrics = self._wire_metrics(spatial_candidate) or {}
            try:
                request = self._request(spatial_state, spatial_questions)
            except ValueError as request_error:
                calls.append(
                    {
                        "stage": "spatial",
                        "node": "spatial",
                        "latency_ms": 0,
                        "usage": {},
                        "sent": False,
                        "attempted": False,
                        "error": type(request_error).__name__,
                        **spatial_metrics,
                    }
                )
                if str(request_error) in {"context_budget_exceeded", "request_size_guard"}:
                    raise ContextBudgetGuard(
                        "node=spatial; options="
                        f"{len(question.criteria)}; spatial_question_too_large"
                    ) from request_error
                raise
            if deadline - time.monotonic() <= 0:
                raise asyncio.TimeoutError()
            started = time.perf_counter()
            call = {
                "stage": "spatial",
                "node": "spatial",
                "latency_ms": 0,
                "usage": {},
                "sent": True,
                "attempted": True,
                **(self._wire_metrics(request) or {}),
            }
            calls.append(call)
            try:
                result, latency = await self._call(request, deadline)
            except Exception as exc:
                call.update({"latency_ms": (time.perf_counter() - started) * 1000, "error": type(exc).__name__})
                raise
            call.update({"latency_ms": latency, "usage": result.usage})
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
                    "byte_budget": self.byte_budget,
                    "token_budget": self.token_budget,
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
            hierarchical=staged or self.single_stage,
        )

        wait_action = next(
            (a for a in actions if a.id == "wait"),
            Action(id="wait", category="wait", group="wait", label="Keep current orders"),
        )
        selected, path, reason, error = wait_action, [], None, None
        provider_http_status = None
        value_result = policy_result = None
        value_request = policy_request = None
        calls = []
        attempted_stage = None
        call_started = None
        diagnostic = None

        try:
            if time.monotonic() < self.retry_after:
                reason = "provider_cooldown"
            else:
                # 1. Staged value estimation
                if staged:
                    value_state = self._context(
                        state,
                        [],
                        self.latest_value,
                    )
                    try:
                        value_questions = {
                            "win_probability": NoulQuestion(instructions=LIVE_VALUE_INSTRUCTIONS)
                        }
                        value_candidate = DecisionRequest(
                            state=value_state,
                            questions=value_questions,
                            priorities={},
                        )
                        value_metrics = self._wire_metrics(value_candidate) or {}
                        value_request = self._request(value_state, value_questions)
                        if deadline - time.monotonic() <= 0:
                            raise asyncio.TimeoutError()
                        attempted_stage, call_started = "value", time.perf_counter()
                        value_call = {
                            "stage": "value",
                            "node": "win_probability",
                            "latency_ms": 0,
                            "usage": {},
                            "sent": True,
                            "attempted": True,
                            **value_metrics,
                        }
                        calls.append(value_call)
                        try:
                            value_result, latency = await self._call(value_request, deadline)
                        except Exception as exc:
                            value_call.update(
                                {
                                    "latency_ms": (time.perf_counter() - call_started) * 1000,
                                    "error": type(exc).__name__,
                                }
                            )
                            raise
                        value_call.update({"latency_ms": latency, "usage": value_result.usage})
                        answer = value_result.answers.get("win_probability")
                        if answer is None or answer.type != "noul":
                            raise ValueError("invalid_value_answer")
                        self.latest_value = {"frame": obs.frame, "win_probability": answer.noul}
                        self.history.value_estimate(obs, answer.noul)
                    except Exception as value_exc:
                        self.latest_value = None
                        value_call_record = next(
                            (c for c in reversed(calls) if c["stage"] == "value"), None
                        )
                        if value_call_record is None:
                            calls.append(
                                {
                                    "stage": "value",
                                    "node": "win_probability",
                                    "latency_ms": (
                                        (time.perf_counter() - call_started) * 1000
                                        if attempted_stage == "value" and call_started
                                        else 0
                                    ),
                                    "usage": {},
                                    "error": type(value_exc).__name__,
                                    "sent": False,
                                    "attempted": False,
                                    **locals().get("value_metrics", {}),
                                }
                            )
                        elif attempted_stage == "value":
                            value_call_record.update(
                                {
                                    "latency_ms": (
                                        (time.perf_counter() - call_started) * 1000
                                        if call_started
                                        else 0
                                    ),
                                    "error": type(value_exc).__name__,
                                }
                            )
                        attempted_stage = None

                # 2. Policy traversal: sequential single-node queries along chosen path
                if not tree.request.questions:
                    default_action = tree.actions.get("wait") or next(iter(tree.actions.values()))
                    root = "command_kind" if tree.is_hierarchical else "action"
                    choice = next(iter(tree.nodes[root]))
                    selected, path = default_action, [{"node": root, "choice": choice, "answer": None}]
                else:
                    root = "command_kind" if tree.is_hierarchical else "action"
                    curr_node = root
                    selected = wait_action

                    while True:
                        options = tree.nodes[curr_node]
                        if len(options) == 1:
                            choice = next(iter(options))
                            path.append({"node": curr_node, "choice": choice, "answer": None})
                            child = options[choice]
                            if child.startswith("leaf:"):
                                selected = tree.actions[child[5:]]
                                break
                            curr_node = child
                            continue

                        # Only include direct leaves for this specific node
                        direct_leaves = [
                            tree.actions[child[5:]] for child in options.values() if child.startswith("leaf:")
                        ]
                        node_question = tree.request.questions[curr_node]
                        node_state = self._context(state, direct_leaves, self.latest_value)

                        node_priorities = (
                            {curr_node: dict(tree.priorities[curr_node])}
                            if curr_node in tree.priorities
                            else {}
                        )
                        node_questions = {curr_node: node_question}
                        node_choice_tree = {curr_node: dict(options)}
                        node_candidate = DecisionRequest(
                            state=node_state,
                            questions=node_questions,
                            choice_tree=node_choice_tree,
                            priorities=node_priorities,
                        )
                        node_metrics = self._wire_metrics(node_candidate) or {}
                        try:
                            node_request = self._request(
                                node_state,
                                node_questions,
                                choice_tree=node_choice_tree,
                                priorities=node_priorities,
                            )
                        except ValueError as request_error:
                            if str(request_error) in {
                                "context_budget_exceeded",
                                "request_size_guard",
                            }:
                                calls.append(
                                    {
                                        "stage": "policy",
                                        "node": curr_node,
                                        "latency_ms": 0,
                                        "usage": {},
                                        "sent": False,
                                        "attempted": False,
                                        "error": type(request_error).__name__,
                                        **node_metrics,
                                    }
                                )
                                if len(options) > 2:
                                    self._split_budget_node(tree, curr_node)
                                    continue
                                raise ContextBudgetGuard(
                                    f"node={curr_node}; options={len(options)}; "
                                    "state_or_question_too_large"
                                ) from request_error
                            raise
                        if policy_request is None:
                            policy_request = node_request

                        if deadline - time.monotonic() <= 0:
                            raise asyncio.TimeoutError()
                        attempted_stage, call_started = "policy", time.perf_counter()
                        policy_call = {
                            "stage": "policy",
                            "node": curr_node,
                            "latency_ms": 0,
                            "usage": {},
                            "sent": True,
                            "attempted": True,
                            **(self._wire_metrics(node_request) or {}),
                        }
                        calls.append(policy_call)
                        try:
                            node_result, latency = await self._call(node_request, deadline)
                        except Exception as exc:
                            policy_call.update(
                                {
                                    "latency_ms": (time.perf_counter() - call_started) * 1000,
                                    "error": type(exc).__name__,
                                }
                            )
                            raise
                        policy_call.update({"latency_ms": latency, "usage": node_result.usage})
                        policy_result = node_result

                        answer = node_result.answers.get(curr_node)
                        if answer is None:
                            raise ValueError(f"Provider answer missing for node: {curr_node}")
                        ChoiceTree._validate_answer(answer, node_question)
                        choice = answer.choice
                        path.append({"node": curr_node, "choice": choice, "answer": answer.model_dump()})

                        child = options[choice]
                        if child.startswith("leaf:"):
                            selected = tree.actions[child[5:]]
                            break
                        curr_node = child

                if selected.commands:
                    selected, spatial_path = await self._resolve_spatial_action(
                        obs, state, selected, deadline, calls
                    )
                    path.extend(spatial_path)

        except Exception as exc:
            selected = wait_action
            error = type(exc).__name__
            diagnostic = getattr(exc, "diagnostic", None)
            if isinstance(exc, httpx.HTTPStatusError):
                provider_http_status = exc.response.status_code
                print(f"MingleCraft provider HTTP error: {provider_http_status}", flush=True)

            msg = str(exc)
            if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                reason = "deadline"
            elif msg == "request_size_guard":
                reason = "request_size_guard"
            elif msg == "context_budget_exceeded":
                reason = "context_budget_exceeded"
            else:
                reason = "provider_error"

            if reason not in {"request_size_guard", "context_budget_exceeded", "provider_cooldown"}:
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
                "called": any(call.get("sent", False) for call in calls),
                "calls": calls,
                "query_count": sum(call.get("sent", False) for call in calls),
                "latency_ms": (time.monotonic() - step_started) * 1000,
                "value_request": value_request.model_dump() if value_request is not None else None,
                "value_request_bytes": self._wire_bytes(value_request)
                if value_request is not None
                else None,
                "policy_request": policy_request.model_dump()
                if policy_request is not None
                else None,
                "policy_request_bytes": self._wire_bytes(policy_request)
                if policy_request is not None
                else None,
                "error": error,
                "diagnostic": diagnostic,
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
