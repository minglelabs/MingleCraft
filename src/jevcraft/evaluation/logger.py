import json
from pathlib import Path

from jevcraft.models import Observation


class MatchLogger:
    def __init__(self, directory: Path, metadata: dict):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.trace = (directory / "decisions.jsonl").open("w", encoding="utf-8")
        self.metadata = metadata
        self.calls = self.errors = self.fallbacks = self.steps = 0
        self.latencies: list[float] = []
        self.confidences: list[float] = []
        self.input_tokens = self.output_tokens = 0
        (directory / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")

    def write(self, event: dict):
        self.trace.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.trace.flush()
        self.steps += 1
        if event.get("called"):
            self.calls += 1
            self.latencies.append(event["latency_ms"])
        self.errors += int(bool(event.get("error")))
        self.fallbacks += int(bool(event.get("envelope", {}).get("fallback_reason")))
        result = event.get("provider_result") or {}
        usage = result.get("usage", {})
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        for step in event.get("path", []):
            confidence = (step.get("answer") or {}).get("confidence")
            if confidence is not None:
                self.confidences.append(confidence)

    def finish(self, obs: Observation, completed: bool = True) -> dict:
        seconds = obs.frame / 24
        c = obs.counters
        rates = self.metadata.get("pricing")
        known_cost = None
        if not self.metadata["remote"]:
            known_cost = 0
        elif rates is not None:
            known_cost = (self.input_tokens * rates[0] + self.output_tokens * rates[1]) / 1_000_000
        summary = {
            "match_id": obs.match_id,
            "mode": self.metadata["mode"],
            "provider": self.metadata["provider"],
            "model": self.metadata["model"],
            "map_name": obs.map_name,
            "map_hash": obs.map_hash,
            "completed": completed,
            "result": obs.result if completed else "unknown",
            "game_seconds": seconds,
            "decision_steps": self.steps,
            "provider_calls": self.calls,
            "provider_errors": self.errors,
            "fallbacks": self.fallbacks,
            "mean_latency_ms": sum(self.latencies) / len(self.latencies)
            if self.latencies
            else None,
            "max_latency_ms": max(self.latencies) if self.latencies else None,
            "mean_path_confidence": sum(self.confidences) / len(self.confidences)
            if self.confidences
            else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_api_cost_usd": known_cost if not self.errors else None,
            "known_usage_cost_usd": known_cost,
            "cost_complete": not self.errors and known_cost is not None,
            "command_apm": c.commands_attempted * 60 / seconds if seconds else 0,
            "effective_apm": c.commands_effective * 60 / seconds if seconds else 0,
            "accepted_commands": c.commands_accepted,
            "resource_spend_fraction": (c.spent_minerals + c.spent_gas)
            / max(1, 50 + c.gathered_minerals + c.gathered_gas),
            "unit_exchange_ratio": c.units_killed / c.units_lost if c.units_lost else None,
            "counters": c.model_dump(),
        }
        (self.directory / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n"
        )
        self.trace.close()
        return summary
