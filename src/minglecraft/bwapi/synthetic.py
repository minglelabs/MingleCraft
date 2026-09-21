"""Deterministic contract fixture, NOT a Brood War simulator or win-rate benchmark."""

from minglecraft.actions.generator import ActionGenerator
from minglecraft.models import DecisionEnvelope, Observation
from minglecraft.strategy.scheduler import Scheduler


class SyntheticGame:
    def __init__(self, match_id: str):
        self.match_id, self.frame = match_id, 0
        self.minerals, self.supply_used, self.supply_total = 50, 4, 10
        self.units = [
            {
                "id": 1,
                "type": "Terran_Command_Center",
                "position": {"x": 320, "y": 320},
                "hit_points": 1500,
            }
        ]
        self.units += [
            {
                "id": i,
                "type": "Terran_SCV",
                "position": {"x": 320 + i * 8, "y": 360},
                "hit_points": 60,
            }
            for i in range(2, 6)
        ]
        self.next_id = 6
        self.orders: dict[int, str] = {}
        self.jobs: list[tuple[int, str, int, dict]] = []
        self.counters = {
            k: 0
            for k in (
                "gathered_minerals",
                "spent_minerals",
                "commands_attempted",
                "commands_accepted",
                "commands_effective",
            )
        }
        self.receipts = []

    def observe(self, ended: bool = False) -> Observation:
        units = []
        for source in self.units:
            unit = dict(source)
            unit_id, kind = unit["id"], unit["type"]
            job = next((j for j in self.jobs if j[2] == unit_id), None)
            unit.update(
                idle=unit_id not in self.orders,
                training=bool(job and job[1] == "train"),
                constructing=bool(job and job[1] == "build"),
                completed=unit.get("completed", True),
            )
            unit["can_move"] = (
                kind in ("Terran_SCV", "Terran_Marine")
                and not unit["constructing"]
                and unit["completed"]
            )
            unit["can_attack"] = kind == "Terran_Marine" and unit["completed"]
            if (
                kind in ("Terran_Command_Center", "Terran_Barracks")
                and not job
                and unit["completed"]
                and self.minerals >= 50
                and self.supply_used < self.supply_total
            ):
                unit["can_train"] = [
                    "Terran_SCV" if kind == "Terran_Command_Center" else "Terran_Marine"
                ]
            if kind == "Terran_SCV" and not job:
                unit["can_gather"] = [100]
                unit["build_sites"] = []
                for building, cost, offset in [
                    ("Terran_Supply_Depot", 100, 0),
                    ("Terran_Barracks", 150, 3),
                ]:
                    if self.minerals >= cost:
                        count = sum(u["type"] == building for u in self.units)
                        unit["build_sites"].append(
                            {"unit_type": building, "tile": {"x": 16 + count * 4, "y": 12 + offset}}
                        )
            units.append(unit)
        return Observation.model_validate(
            {
                "match_id": self.match_id,
                "frame": self.frame,
                "map_name": "synthetic-contract-fixture",
                "map_hash": "synthetic-v1",
                "map_width": 4096,
                "map_height": 4096,
                "minerals": self.minerals,
                "gas": 0,
                "supply_used": self.supply_used,
                "supply_total": self.supply_total,
                "home": {"x": 320, "y": 320},
                "units": units,
                "enemies": [],
                "mineral_patches": [{"id": 100, "position": {"x": 420, "y": 360}}],
                "locations": [
                    {
                        "id": "start_1",
                        "kind": "start",
                        "position": {"x": 2048, "y": 2048},
                        "explored": any(v == "move" for v in self.orders.values()),
                    }
                ],
                "counters": self.counters,
                "receipts": self.receipts[-64:],
                "ended": ended,
            }
        )

    def execute(self, decision: DecisionEnvelope):
        attempted = accepted = effective = 0
        reason = "ok"
        if (
            decision.match_id != self.match_id
            or not decision.observed_frame <= self.frame <= decision.expires_frame
        ):
            reason = "stale_or_wrong_match"
        else:
            legal = ActionGenerator().generate(self.observe(), set(Scheduler.periods))
            for command in decision.commands:
                attempted += len(command.unit_ids)
                if not any(a.commands == (command,) for a in legal):
                    reason = "revalidation_failed"
                    continue
                for unit_id in command.unit_ids:
                    accepted += 1
                    previous = self.orders.get(unit_id)
                    self.orders[unit_id] = command.kind
                    effective += int(previous != command.kind or command.kind in ("train", "build"))
                    if command.kind == "train":
                        self.minerals -= 50
                        self.supply_used += 1
                        self.counters["spent_minerals"] += 50
                        self.jobs.append(
                            (self.frame + 48, "train", unit_id, {"type": command.unit_type})
                        )
                    elif command.kind == "build":
                        cost = 100 if command.unit_type == "Terran_Supply_Depot" else 150
                        self.minerals -= cost
                        self.counters["spent_minerals"] += cost
                        building = {
                            "id": self.next_id,
                            "type": command.unit_type,
                            "position": {
                                "x": command.tile.x * 32,
                                "y": command.tile.y * 32,
                            },
                            "hit_points": 500,
                            "completed": False,
                        }
                        self.next_id += 1
                        self.units.append(building)
                        self.jobs.append(
                            (self.frame + 72, "build", unit_id, {"id": building["id"]})
                        )
        for key, value in zip(
            ("commands_attempted", "commands_accepted", "commands_effective"),
            (attempted, accepted, effective),
        ):
            self.counters[key] += value
        self.receipts.append(
            {
                "decision_id": decision.decision_id,
                "frame": self.frame,
                "attempted": attempted,
                "accepted": accepted,
                "effective": effective,
                "reason": reason,
            }
        )

    def advance(self):
        self.frame += 6
        mined = sum(v == "gather" for v in self.orders.values()) * 2
        self.minerals += mined
        self.counters["gathered_minerals"] += mined
        remaining = []
        for end, kind, unit_id, data in self.jobs:
            if end > self.frame:
                remaining.append((end, kind, unit_id, data))
                continue
            self.orders.pop(unit_id, None)
            if kind == "train":
                self.units.append(
                    {
                        "id": self.next_id,
                        "type": data["type"],
                        "position": {"x": 350, "y": 350},
                        "hit_points": 40,
                    }
                )
                self.next_id += 1
            else:
                building = next(u for u in self.units if u["id"] == data["id"])
                building["completed"] = True
                if building["type"] == "Terran_Supply_Depot":
                    self.supply_total = min(200, self.supply_total + 8)
        self.jobs = remaining
