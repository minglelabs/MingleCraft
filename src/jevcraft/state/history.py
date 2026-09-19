"""Lossless, model-facing match history for sequential Jev decisions."""

from copy import deepcopy

from jevcraft.models import Action, Observation


class MatchHistory:
    def __init__(self):
        self.records: list[dict] = []
        self._receipt_ids: set[str] = set()
        self._last_observation: dict | None = None

    def observe(self, observation: Observation) -> None:
        value = observation.model_dump()
        value["enemies"] = [enemy for enemy in value["enemies"] if enemy["visible"]]
        receipts = value.pop("receipts", [])
        for receipt in receipts:
            if receipt["decision_id"] in self._receipt_ids:
                continue
            self._receipt_ids.add(receipt["decision_id"])
            self.records.append(
                {
                    "event": "command_receipt",
                    "timestamp": {"frame": receipt["frame"], "game_seconds": receipt["frame"] / 24},
                    "observed_at": {
                        "frame": observation.frame,
                        "game_seconds": observation.frame / 24,
                    },
                    "decision_id": receipt["decision_id"],
                    "acceptance": {
                        "attempted": receipt["attempted"],
                        "accepted": receipt["accepted"],
                        "reason": receipt["reason"],
                    },
                    "interpretation": "acceptance, not completion",
                }
            )
        delta = (
            value
            if self._last_observation is None
            else {
                key: current
                for key, current in value.items()
                if current != self._last_observation.get(key)
            }
        )
        self._last_observation = value
        self.records.append(
            {
                "event": "observation",
                "timestamp": {"frame": observation.frame, "game_seconds": observation.frame / 24},
                "observation_delta": delta,
            }
        )

    def decision(self, observation: Observation, action: Action, decision_id: str) -> None:
        self.records.append(
            {
                "event": "issued_action",
                "timestamp": {"frame": observation.frame, "game_seconds": observation.frame / 24},
                "decision_id": decision_id,
                "action": action.model_dump(exclude={"priority"}),
                "interpretation": "issued intent, not completion",
            }
        )

    def value_estimate(self, observation: Observation, probability: float) -> None:
        self.records.append(
            {
                "event": "value_estimate",
                "timestamp": {"frame": observation.frame, "game_seconds": observation.frame / 24},
                "win_probability": probability,
            }
        )

    def snapshot(self) -> list[dict]:
        return deepcopy(self.records)
