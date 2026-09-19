import asyncio
import json
import time
from pathlib import Path

from jevcraft import __version__
from jevcraft.actions.executor import envelope
from jevcraft.actions.generator import ActionGenerator
from jevcraft.actions.hierarchy import ChoiceTree
from jevcraft.actions.pruner import prune
from jevcraft.agents.providers import DecisionProvider
from jevcraft.evaluation.logger import MatchLogger
from jevcraft.models import Observation, ProviderResult
from jevcraft.state import StateBuilder
from jevcraft.strategy.scheduler import Scheduler


class AgentLoop:
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
    ):
        if deadline_ms <= 0 or ttl_frames <= 0 or not 8 <= limit <= 50:
            raise ValueError("Invalid deadline, TTL or candidate limit")
        if pricing is not None and any(p < 0 for p in pricing):
            raise ValueError("Token pricing cannot be negative")
        self.provider, self.output = provider, output
        self.deadline_ms, self.ttl_frames, self.limit = deadline_ms, ttl_frames, limit
        self.seed, self.mode, self.pricing = seed, mode, pricing
        self.state_builder, self.generator, self.scheduler = (
            StateBuilder(),
            ActionGenerator(),
            Scheduler(),
        )
        self.logger = None
        self.match_id = None
        self.map_hash = None
        self.last_frame = -1
        self.sequence = 0
        self.retry_after = 0.0
        self.finished = False

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
                    "candidate_limit": self.limit,
                    "map_name": obs.map_name,
                    "map_hash": obs.map_hash,
                    "cadence_frames": self.scheduler.periods,
                },
            )
        if obs.match_id != self.match_id or obs.map_hash != self.map_hash:
            raise ValueError("Match identity changed")
        if obs.frame <= self.last_frame:
            raise ValueError("Observation frame must advance")
        self.last_frame = obs.frame
        self.sequence += 1
        state = self.state_builder.build(obs)
        actions = prune(self.generator.generate(obs, self.scheduler.due(obs.frame)), self.limit)
        tree = ChoiceTree(state, actions)
        selected, path, result, reason, error, called = actions[0], [], None, None, None, False
        start = time.perf_counter()
        if not tree.request.questions:
            selected, path = tree.resolve(ProviderResult(model=self.provider.model, answers={}))
        elif time.monotonic() < self.retry_after:
            reason = "provider_cooldown"
        else:
            called = True
            try:
                result = await asyncio.wait_for(
                    self.provider.decide(tree.request), self.deadline_ms / 1000
                )
                selected, path = tree.resolve(result)
            except Exception as exc:
                # Never persist exception text: it may contain credentials or HTTP bodies.
                error = type(exc).__name__
                reason = (
                    "deadline"
                    if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                    else "provider_error"
                )
                self.retry_after = time.monotonic() + 2
        latency = (time.perf_counter() - start) * 1000
        decision = envelope(obs, selected, self.sequence, self.ttl_frames, reason)
        self.scheduler.selected(selected.category, obs.frame)
        self.logger.write(
            {
                "observation": obs.model_dump(),
                "state": state,
                "actions": [a.model_dump() for a in actions],
                "questions": {k: q.model_dump() for k, q in tree.request.questions.items()},
                "called": called,
                "latency_ms": latency,
                "error": error,
                "provider_result": result.model_dump() if result else None,
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
        self.finished = True
        self.logger.trace.write(
            json.dumps({"event": "match_end", "observation": obs.model_dump()}) + "\n"
        )
        return self.logger.finish(obs, completed)
