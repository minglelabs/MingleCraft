from collections import Counter

from jevcraft.models import Observation


class StateBuilder:
    """Memory contains only observations actually seen, with explicit age."""

    def __init__(self, memory_frames: int = 24 * 120):
        self.memory_frames = memory_frames
        self.match_id = None
        self.seen: dict[int, dict] = {}

    def build(self, observation: Observation) -> dict:
        if observation.match_id != self.match_id:
            self.seen.clear()
            self.match_id = observation.match_id
        for enemy in observation.enemies:
            if enemy.visible:
                self.seen[enemy.id] = {
                    "id": enemy.id,
                    "type": enemy.type,
                    "last_position": enemy.position.model_dump(),
                    "last_seen_frame": observation.frame,
                }
        for unit_id in observation.destroyed_enemy_ids:
            self.seen.pop(unit_id, None)
        self.seen = {
            k: v
            for k, v in self.seen.items()
            if observation.frame - v["last_seen_frame"] <= self.memory_frames
        }
        visible = [e for e in observation.enemies if e.visible]
        own = Counter(u.type for u in observation.units)
        complete = Counter(u.type for u in observation.units if u.completed)
        marines = [u for u in observation.units if u.type == "Terran_Marine" and u.completed]
        squads = {}
        for name, members in {
            "squad_1": [u for u in marines if u.id % 2 == 0],
            "squad_2": [u for u in marines if u.id % 2 == 1],
            "all_marines": marines,
        }.items():
            if members:
                squads[name] = {
                    "count": len(members),
                    "hit_points": sum(u.hit_points for u in members),
                    "centroid": {
                        "x": sum(u.position.x for u in members) // len(members),
                        "y": sum(u.position.y for u in members) // len(members),
                    },
                }
        return {
            "frame": observation.frame,
            "game_seconds": round(observation.frame / 24, 3),
            "map": {"name": observation.map_name, "hash": observation.map_hash},
            "resources": {
                "minerals": observation.minerals,
                "gas": observation.gas,
                "supply_used": observation.supply_used,
                "supply_total": observation.supply_total,
            },
            "own_counts": dict(own),
            "completed_counts": dict(complete),
            "squads": squads,
            "idle_workers": sum(u.type == "Terran_SCV" and u.idle for u in observation.units),
            "enemy_visible": [e.model_dump(exclude={"visible"}) for e in visible],
            "enemy_last_seen": [
                dict(v, age_frames=observation.frame - v["last_seen_frame"])
                for v in sorted(self.seen.values(), key=lambda v: v["id"])
            ],
            "home": observation.home.model_dump(),
            "locations": [p.model_dump() for p in observation.locations],
            "fog_of_war": "Unseen enemy information is unknown. Last sightings may be stale.",
        }
