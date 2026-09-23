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
    build_digit_question,
    get_spatial_grid_spec,
    parse_digit_key,
    spatial_digit_depth,
    synthesize_radix_coordinate,
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
    """One complete request exceeds its context budget without dropping choices."""

    def __init__(self, diagnostic: str):
        super().__init__("context_budget_exceeded")
        self.diagnostic = diagnostic


def live_policy_instructions(map_name: str | None = None) -> str:
    map_info = f" on map '{map_name}'" if map_name else ""
    return (
        f"You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher){map_info}. "
        "Your objective is to win the current match. "
        "Wire format: state uses 'jev/compact-v1'. All Choice questions are answered "
        "independently in one request. For each tree node, assume the branch named "
        "in that question is being considered. The program follows only the answers "
        "on the chosen command kind, actor, and target path. Criteria keys are option IDs. "
        "Use observation.self_race and enemy_race when interpreting the game and map state. "
        "Positions are pixel coordinates; build and land tile fields are build-tile coordinates. "
        "Answer every requested Choice with an existing option ID. "
        "Ground-coordinate actions use spatial digit questions in this same request; "
        "null position or tile does not target the origin."
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
        token_budget: int = 48_000,
        question_token_budget: int = 24_000,
        single_stage: bool = False,
        spatial_precision_px: int = 8,
    ):
        if deadline_ms <= 0 or ttl_frames <= 0 or not 8 <= limit <= 50:
            raise ValueError("Invalid deadline, TTL or candidate limit")
        if request_size_limit <= 0 or byte_budget <= 0 or token_budget <= 0 or question_token_budget <= 0:
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
        self.question_token_budget = question_token_budget
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
                raise ContextBudgetGuard(
                    f"wire_bytes={len(payload_bytes)}; byte_budget={self.byte_budget}"
                )

            estimated_tokens = estimate_tokens_conservative(payload_bytes.decode(errors="replace"))
            if estimated_tokens > self.token_budget:
                raise ContextBudgetGuard(
                    f"estimated_tokens={estimated_tokens}; total_budget={self.token_budget}"
                )
            for question_id, question in payload_dict["questions"].items():
                single_question = json.dumps(
                    {
                        "model": self.provider.model,
                        "state": payload_dict["state"],
                        "questions": {question_id: question},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                question_tokens = estimate_tokens_conservative(single_question)
                if question_tokens > self.question_token_budget:
                    raise ContextBudgetGuard(
                        f"question={question_id}; estimated_tokens={question_tokens}; "
                        f"question_budget={self.question_token_budget}"
                    )
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

    def _spatial_questions(self, obs, actions):
        questions = {}
        if obs.map_width < 1 or obs.map_height < 1:
            return questions
        spec = get_spatial_grid_spec(obs, self.spatial_precision_px)
        depth = spatial_digit_depth(spec)
        spatial_kinds = {"move", "attack", "patrol", "build", "land", "unload_all", "use_tech"}
        for action in actions:
            if not action.category.startswith("spatial_") or not action.commands:
                continue
            command = action.commands[0]
            if command.kind not in spatial_kinds or command.position is not None or command.tile is not None:
                continue
            actor = action.id
            for digit in range(1, depth + 1):
                questions[f"spatial_{action.id}_{digit}"] = build_digit_question(
                    actor, command.kind, spec, digit, depth
                )
        return questions

    def _resolve_spatial_action(self, obs, action, result):
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
        depth = spatial_digit_depth(spec)
        digits = []
        path = []
        for digit in range(1, depth + 1):
            key = f"spatial_{action.id}_{digit}"
            answer = result.answers.get(key)
            if answer is None or answer.type != "choice":
                raise ValueError(f"Provider answer missing for spatial digit: {key}")
            question = build_digit_question(
                action.id, command.kind, spec, digit, depth
            )
            ChoiceTree._validate_answer(answer, question)
            dx, dy = parse_digit_key(answer.choice)
            digits.append((dx, dy))
            path.append({"node": key, "choice": answer.choice, "answer": answer.model_dump()})
        x, y = synthesize_radix_coordinate(spec, digits)
        position = {"x": x, "y": y}
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
                    "question_token_budget": self.question_token_budget,
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
        state["common_policy"] = live_policy_instructions(obs.map_name)
        tree = ChoiceTree(
            state,
            actions,
            instructions="Follow state.common_policy."
            if (staged or self.single_stage)
            else POLICY_INSTRUCTIONS,
            hierarchical=staged or self.single_stage,
        )
        if obs.map_width > 0 and obs.map_height > 0:
            state["coordinate_rule"] = (
                "For an action's one absolute pixel destination, each independent "
                "digit question gives one base-4 x/y digit. All digits represent "
                "the same intended destination; no question sees other answers. "
                "With digits i=1..depth (most significant first), "
                "x=floor(map_width*sum(x_digit_i*4^(depth-i))/4^depth); "
                "use the same formula for y. x increases rightward, y downward. "
                f"map_width={obs.map_width}; map_height={obs.map_height}."
            )
        state["spatial_actors"] = {
            action.id: {
                "label": action.label,
                "unit_ids": list(action.commands[0].unit_ids),
                "kind": action.commands[0].kind,
            }
            for action in actions
            if action.category.startswith("spatial_")
            and action.commands
            and action.commands[0].position is None
            and action.commands[0].tile is None
        }
        all_questions = dict(tree.request.questions)
        if staged:
            all_questions["win_probability"] = NoulQuestion(instructions=LIVE_VALUE_INSTRUCTIONS)
        all_questions.update(self._spatial_questions(obs, actions))
        # Every legal leaf is already represented by its Choice criterion. Sending
        # the full action table again nearly doubles the model-facing context.
        combined_state = self._context(state, [], self.latest_value, tree.nodes)
        combined_request = DecisionRequest(
            state=combined_state,
            questions=all_questions,
            choice_tree=tree.nodes,
            priorities=tree.priorities,
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
        diagnostic = None

        try:
            if time.monotonic() < self.retry_after:
                reason = "provider_cooldown"
            else:
                combined_request = self._request(
                    combined_state,
                    all_questions,
                    choice_tree=tree.nodes,
                    priorities=tree.priorities,
                )
                policy_request = combined_request
                if deadline - time.monotonic() <= 0:
                    raise asyncio.TimeoutError()
                started = time.perf_counter()
                call = {
                    "stage": "combined",
                    "node": "all_questions",
                    "includes_value": staged,
                    "latency_ms": 0,
                    "usage": {},
                    "sent": True,
                    "attempted": True,
                    **(self._wire_metrics(combined_request) or {}),
                }
                calls.append(call)
                try:
                    combined_result, latency = await self._call(combined_request, deadline)
                except Exception as exc:
                    call.update({"latency_ms": (time.perf_counter() - started) * 1000, "error": type(exc).__name__})
                    raise
                call.update({"latency_ms": latency, "usage": combined_result.usage})
                policy_result = combined_result
                if staged:
                    answer = combined_result.answers.get("win_probability")
                    if answer is None or answer.type != "noul":
                        raise ValueError("invalid_value_answer")
                    value_result = ProviderResult(
                        model=combined_result.model,
                        answers={"win_probability": answer},
                        usage=combined_result.usage,
                    )
                    self.latest_value = {"frame": obs.frame, "win_probability": answer.noul}
                    self.history.value_estimate(obs, answer.noul)

                if not tree.request.questions:
                    default_action = tree.actions.get("wait") or next(iter(tree.actions.values()))
                    root = "command_kind" if tree.is_hierarchical else "action"
                    choice = next(iter(tree.nodes[root]))
                    selected, path = default_action, [{"node": root, "choice": choice, "answer": None}]
                else:
                    root = "command_kind" if tree.is_hierarchical else "action"
                    selected, path = tree.resolve(combined_result)

                if selected.commands:
                    selected, spatial_path = self._resolve_spatial_action(obs, selected, combined_result)
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
