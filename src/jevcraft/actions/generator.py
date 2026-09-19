from collections import Counter

from jevcraft.models import Action, Command, Observation, Position


class ActionGenerator:
    """BWAPI capabilities establish legality; heuristics bound the choice space."""

    def generate(self, obs: Observation, due: set[str]) -> list[Action]:
        actions = [Action(id="wait", category="wait", group="wait", label="Keep current orders")]
        counts = Counter(u.type for u in obs.units)
        units = sorted(obs.units, key=lambda u: u.id)
        workers = [
            u for u in units if u.type == "Terran_SCV" and u.completed and not u.constructing
        ]
        minerals = {m.id: m for m in obs.mineral_patches}
        if "economy" in due:
            for u in workers:
                available = [minerals[m] for m in u.can_gather if m in minerals]
                if u.idle and available:
                    patch = min(available, key=lambda m: u.position.distance_squared(m.position))
                    actions.append(
                        Action(
                            id=f"gather_{u.id}_{patch.id}",
                            category="economy",
                            group="workers",
                            label=f"Send idle SCV {u.id} to visible mineral patch {patch.id}",
                            priority=100,
                            commands=(
                                Command(kind="gather", unit_ids=(u.id,), target_id=patch.id),
                            ),
                        )
                    )
        if "production" in due:
            for u in units:
                if not u.completed or u.training:
                    continue
                for kind in u.can_train:
                    producer = (
                        "Terran_Command_Center" if kind == "Terran_SCV" else "Terran_Barracks"
                    )
                    if (
                        u.type != producer
                        or obs.minerals < 50
                        or obs.supply_used >= obs.supply_total
                    ):
                        continue
                    priority = (75 if counts[kind] < 20 else 10) if kind == "Terran_SCV" else 60
                    actions.append(
                        Action(
                            id=f"train_{u.id}_{kind}",
                            category="production",
                            group=kind,
                            label=f"Train {kind} at {u.type} {u.id}",
                            priority=priority,
                            commands=(Command(kind="train", unit_ids=(u.id,), unit_type=kind),),
                        )
                    )
        if "construction" in due:
            # One worker per site avoids flooding the model with equivalent builders.
            sites: set[tuple] = set()
            for u in sorted(workers, key=lambda u: (not u.idle, u.id)):
                for site in u.build_sites:
                    key = (site.unit_type, site.tile.x, site.tile.y)
                    cost = 100 if site.unit_type == "Terran_Supply_Depot" else 150
                    if key in sites or obs.minerals < cost:
                        continue
                    if site.unit_type == "Terran_Barracks" and not any(
                        x.type == "Terran_Command_Center" and x.completed for x in units
                    ):
                        continue
                    sites.add(key)
                    in_progress = any(x.type == site.unit_type and not x.completed for x in units)
                    if site.unit_type == "Terran_Supply_Depot":
                        priority = (
                            95 if obs.supply_total - obs.supply_used <= 3 and not in_progress else 5
                        )
                    else:
                        priority = 80 if counts[site.unit_type] < 2 else 5
                    actions.append(
                        Action(
                            id=f"build_{u.id}_{site.unit_type}_{site.tile.x}_{site.tile.y}",
                            category="construction",
                            group=site.unit_type,
                            label=f"Build {site.unit_type} with SCV {u.id} at tile {site.tile.x},{site.tile.y}",
                            priority=priority,
                            commands=(
                                Command(
                                    kind="build",
                                    unit_ids=(u.id,),
                                    unit_type=site.unit_type,
                                    tile=site.tile,
                                ),
                            ),
                        )
                    )
        marines = [u for u in units if u.type == "Terran_Marine" and u.completed]
        squads = {
            "squad_1": [u for u in marines if u.id % 2 == 0],
            "squad_2": [u for u in marines if u.id % 2 == 1],
            "all_marines": marines,
        }
        targets: list[tuple[str, Position]] = [
            (f"visible_{e.id}", e.position) for e in obs.enemies if e.visible
        ][:6]
        # Public starting positions are hypotheses, never labelled as known enemy bases.
        targets += [(p.id, p.position) for p in obs.locations if p.kind == "start"]
        near_home = any(
            e.visible and e.position.distance_squared(obs.home) < 640**2 for e in obs.enemies
        )
        for name, squad in squads.items():
            if not squad:
                continue
            if "attack" in due and all(u.can_attack for u in squad):
                for target, pos in targets:
                    actions.append(
                        Action(
                            id=f"attack_{name}_{target}",
                            category="attack",
                            group=name,
                            label=f"Attack-move {name} ({len(squad)} Marines) to {target}",
                            priority=50 if len(squad) >= 6 else 1,
                            commands=(
                                Command(
                                    kind="attack", unit_ids=tuple(u.id for u in squad), position=pos
                                ),
                            ),
                        )
                    )
            if "defense" in due and all(u.can_move for u in squad):
                actions.append(
                    Action(
                        id=f"retreat_{name}",
                        category="defense",
                        group=name,
                        label=f"Retreat {name} ({len(squad)} Marines) to home",
                        priority=90 if near_home and len(squad) < 6 else -1,
                        commands=(
                            Command(
                                kind="move", unit_ids=tuple(u.id for u in squad), position=obs.home
                            ),
                        ),
                    )
                )
                if all(u.can_attack for u in squad):
                    actions.append(
                        Action(
                            id=f"defend_{name}",
                            category="defense",
                            group=name,
                            label=f"Defend home with {name} ({len(squad)} Marines)",
                            priority=92 if near_home else -1,
                            commands=(
                                Command(
                                    kind="attack",
                                    unit_ids=tuple(u.id for u in squad),
                                    position=obs.home,
                                ),
                            ),
                        )
                    )
        if "scout" in due:
            scout = next((u for u in workers if u.can_move), None)
            if scout:
                for target in obs.locations:
                    if target.kind == "start" and not target.explored:
                        actions.append(
                            Action(
                                id=f"scout_{scout.id}_{target.id}",
                                category="scout",
                                group="scout_worker",
                                label=f"Scout possible start {target.id} with SCV {scout.id}",
                                priority=35,
                                commands=(
                                    Command(
                                        kind="move", unit_ids=(scout.id,), position=target.position
                                    ),
                                ),
                            )
                        )
        return actions
