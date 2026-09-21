from collections import Counter

from jevcraft.models import Action, Command, Observation, Position


def _is_worker(u) -> bool:
    return (
        "SCV" in u.type
        or "Probe" in u.type
        or "Drone" in u.type
        or bool(u.can_gather)
        or bool(u.build_sites)
    )


class ActionGenerator:
    """Generate BWAPI-legal candidates for baseline or exhaustive Jev selection."""

    def generate(self, obs: Observation, due: set[str], exhaustive: bool = False) -> list[Action]:
        if exhaustive:
            return self._generate_exhaustive(obs, due)
        return self._generate_baseline(obs, due)

    def _generate_baseline(self, obs: Observation, due: set[str]) -> list[Action]:
        actions = [Action(id="wait", category="wait", group="wait", label="Keep current orders")]
        counts = Counter(u.type for u in obs.units)
        units = sorted(obs.units, key=lambda u: u.id)
        workers = [u for u in units if _is_worker(u) and u.completed and not u.constructing]
        minerals = {m.id: m for m in obs.mineral_patches}
        if "economy" in due:
            idle_workers = [
                u for u in workers if u.idle and any(m in minerals for m in u.can_gather)
            ]
            if len(idle_workers) > 1 and minerals:
                patch = min(minerals.values(), key=lambda m: obs.home.distance_squared(m.position))
                actions.append(
                    Action(
                        id=f"gather_all_idle_{len(idle_workers)}",
                        category="economy",
                        group="workers",
                        label=f"Send all {len(idle_workers)} idle SCVs to mine minerals",
                        priority=120,
                        commands=(
                            Command(
                                kind="gather",
                                unit_ids=tuple(u.id for u in idle_workers),
                                target_id=patch.id,
                            ),
                        ),
                    )
                )
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
                    if obs.minerals < 50 or obs.supply_used >= obs.supply_total:
                        continue
                    priority = 75 if counts[kind] < 20 else 50
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
                    cost = (
                        100
                        if "Supply" in site.unit_type
                        or "Pylon" in site.unit_type
                        or "Overlord" in site.unit_type
                        else 150
                    )
                    if key in sites or obs.minerals < cost:
                        continue
                    sites.add(key)
                    in_progress = any(x.type == site.unit_type and not x.completed for x in units)
                    if "Supply" in site.unit_type or "Pylon" in site.unit_type:
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
        combat_army = [
            u
            for u in units
            if u.completed
            and not u.constructing
            and not _is_worker(u)
            and (u.can_attack or u.can_move)
        ]
        squads = {
            "squad_1": [u for u in combat_army if u.id % 2 == 0][:200],
            "squad_2": [u for u in combat_army if u.id % 2 == 1][:200],
            "all_combat": combat_army[:200],
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
                            label=f"Attack-move {name} ({len(squad)} units) to {target}",
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
                        label=f"Retreat {name} ({len(squad)} units) to home",
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
                            label=f"Defend home with {name} ({len(squad)} units)",
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

    def _generate_exhaustive(self, obs: Observation, due: set[str]) -> list[Action]:
        """Generate every finite candidate represented by the observation contract.

        This deliberately does not use priorities to filter candidates.  The normal
        generator remains bounded and heuristic-driven; this path is for the second
        Jev stage, which needs the complete legal choice set.
        """
        # Exhaustive Jev candidates are independent unit actions.  The legacy
        # baseline is intentionally isolated above; its aggregate/parity policy
        # must not leak into the full candidate set.
        actions = [Action(id="wait", category="wait", group="wait", label="Keep current orders")]
        action_ids = {action.id for action in actions}
        units = sorted(obs.units, key=lambda unit: unit.id)
        workers = [
            unit
            for unit in units
            if (
                unit.can_gather
                or unit.build_sites
                or "SCV" in unit.type
                or "Probe" in unit.type
                or "Drone" in unit.type
            )
            and unit.completed
            and not unit.constructing
        ]
        minerals = {mineral.id: mineral for mineral in obs.mineral_patches}

        def add(action: Action) -> None:
            if action.id not in action_ids:
                actions.append(action)
                action_ids.add(action.id)

        if "production" in due:
            for unit in units:
                if not unit.completed or unit.training or unit.constructing:
                    continue
                for unit_type in unit.can_train:
                    # BWAPI capability is the source of legality here; native
                    # revalidation remains authoritative at execution.
                    add(
                        Action(
                            id=f"train_{unit.id}_{unit_type}",
                            category="production",
                            group=f"unit_{unit.id}",
                            label=f"Train {unit_type} from {unit.type} {unit.id}",
                            commands=(
                                Command(
                                    kind="train",
                                    unit_ids=(unit.id,),
                                    unit_type=unit_type,
                                ),
                            ),
                        )
                    )

        refineries = {
            u.id: u
            for u in units
            if u.completed
            and ("Refinery" in u.type or "Assimilator" in u.type or "Extractor" in u.type)
        }

        if "economy" in due:
            for worker in workers:
                for target_id in worker.can_gather:
                    if target_id in minerals:
                        mineral = minerals[target_id]
                        add(
                            Action(
                                id=f"gather_{worker.id}_{mineral.id}",
                                category="economy",
                                group="workers",
                                label=f"Send {'idle ' if worker.idle else ''}SCV {worker.id} to visible mineral patch {mineral.id}",
                                priority=100 if worker.idle else 0,
                                commands=(
                                    Command(
                                        kind="gather",
                                        unit_ids=(worker.id,),
                                        target_id=mineral.id,
                                    ),
                                ),
                            )
                        )
                    elif target_id in refineries:
                        refinery = refineries[target_id]
                        add(
                            Action(
                                id=f"gather_gas_{worker.id}_{refinery.id}",
                                category="economy",
                                group="workers",
                                label=f"Send {'idle ' if worker.idle else ''}SCV {worker.id} to {refinery.type} {refinery.id}",
                                priority=100 if worker.idle else 0,
                                commands=(
                                    Command(
                                        kind="gather",
                                        unit_ids=(worker.id,),
                                        target_id=refinery.id,
                                    ),
                                ),
                            )
                        )

        if "construction" in due:
            for worker in workers:
                for site in worker.build_sites:
                    add(
                        Action(
                            id=f"build_{worker.id}_{site.unit_type}_{site.tile.x}_{site.tile.y}",
                            category="construction",
                            group=site.unit_type,
                            label=(
                                f"Build {site.unit_type} with SCV {worker.id} "
                                f"at tile {site.tile.x},{site.tile.y}"
                            ),
                            commands=(
                                Command(
                                    kind="build",
                                    unit_ids=(worker.id,),
                                    unit_type=site.unit_type,
                                    tile=site.tile,
                                ),
                            ),
                        )
                    )

        visible_targets = [
            (f"visible_{enemy.id}", enemy.position) for enemy in obs.enemies if enemy.visible
        ]
        start_targets = [
            (location.id, location.position)
            for location in obs.locations
            if location.kind == "start"
        ]
        move_targets = [*visible_targets, *start_targets, ("home", obs.home)]
        attack_targets = [*visible_targets, *start_targets, ("home", obs.home)]

        if "scout" in due:
            for scout in workers:
                if not scout.can_move:
                    continue
                for target, position in start_targets:
                    add(
                        Action(
                            id=f"scout_{scout.id}_{target}",
                            category="scout",
                            group="scout_worker",
                            label=f"Scout possible start {target} with SCV {scout.id}",
                            commands=(
                                Command(kind="move", unit_ids=(scout.id,), position=position),
                            ),
                        )
                    )

        combat_units = [
            unit
            for unit in units
            if unit.completed and not unit.constructing and (unit.can_move or unit.can_attack)
        ]
        if "defense" in due or "attack" in due:
            for unit in combat_units:
                if "defense" in due and unit.can_move:
                    add(
                        Action(
                            id=f"move_{unit.id}_home",
                            category="defense",
                            group=f"unit_{unit.id}",
                            label=f"Move {unit.type} {unit.id} home",
                            commands=(
                                Command(kind="move", unit_ids=(unit.id,), position=obs.home),
                            ),
                        )
                    )
                if "attack" in due and unit.can_attack:
                    for target, position in attack_targets:
                        add(
                            Action(
                                id=f"attack_unit_{unit.id}_{target}",
                                category="attack",
                                group=f"unit_{unit.id}",
                                label=f"Attack {target} with {unit.type} {unit.id}",
                                commands=(
                                    Command(
                                        kind="attack",
                                        unit_ids=(unit.id,),
                                        position=position,
                                    ),
                                ),
                            )
                        )
                if "attack" in due and unit.can_move:
                    for target, position in move_targets:
                        add(
                            Action(
                                id=f"move_{unit.id}_{target}",
                                category="attack",
                                group=f"unit_{unit.id}",
                                label=f"Move {unit.type} {unit.id} to {target}",
                                commands=(
                                    Command(kind="move", unit_ids=(unit.id,), position=position),
                                ),
                            )
                        )
                if unit.can_move:
                    add(
                        Action(
                            id=f"spatial_move_unit_{unit.id}",
                            category="spatial_move",
                            group=f"unit_{unit.id}",
                            label=f"Move {unit.type} {unit.id} to ground coordinates via spatial selection",
                            commands=(Command(kind="move", unit_ids=(unit.id,)),),
                        )
                    )
                if unit.can_attack:
                    add(
                        Action(
                            id=f"spatial_attack_unit_{unit.id}",
                            category="spatial_attack",
                            group=f"unit_{unit.id}",
                            label=f"Attack-move {unit.type} {unit.id} to ground coordinates via spatial selection",
                            commands=(Command(kind="attack", unit_ids=(unit.id,)),),
                        )
                    )
                if unit.can_move:
                    add(
                        Action(
                            id=f"stop_{unit.id}",
                            category="defense",
                            group=f"unit_{unit.id}",
                            label=f"Stop {unit.type} {unit.id}",
                            commands=(Command(kind="stop", unit_ids=(unit.id,)),),
                        )
                    )
                    add(
                        Action(
                            id=f"hold_{unit.id}",
                            category="defense",
                            group=f"unit_{unit.id}",
                            label=f"Hold position with {unit.type} {unit.id}",
                            commands=(Command(kind="hold_position", unit_ids=(unit.id,)),),
                        )
                    )
                if "Tank" in unit.type:
                    if "Siege Mode" in unit.type:
                        add(
                            Action(
                                id=f"unsiege_{unit.id}",
                                category="defense",
                                group=f"unit_{unit.id}",
                                label=f"Unsiege Tank {unit.id}",
                                commands=(Command(kind="unsiege", unit_ids=(unit.id,)),),
                            )
                        )
                    else:
                        add(
                            Action(
                                id=f"siege_{unit.id}",
                                category="defense",
                                group=f"unit_{unit.id}",
                                label=f"Siege Tank {unit.id}",
                                commands=(Command(kind="siege", unit_ids=(unit.id,)),),
                            )
                        )
                if "Wraith" in unit.type or "Ghost" in unit.type:
                    add(
                        Action(
                            id=f"cloak_{unit.id}",
                            category="defense",
                            group=f"unit_{unit.id}",
                            label=f"Cloak {unit.type} {unit.id}",
                            commands=(Command(kind="cloak", unit_ids=(unit.id,)),),
                        )
                    )
                    add(
                        Action(
                            id=f"decloak_{unit.id}",
                            category="defense",
                            group=f"unit_{unit.id}",
                            label=f"Decloak {unit.type} {unit.id}",
                            commands=(Command(kind="decloak", unit_ids=(unit.id,)),),
                        )
                    )
                if _is_worker(unit):
                    for target_u in units:
                        if target_u.id != unit.id and target_u.hit_points < 1000:
                            add(
                                Action(
                                    id=f"repair_{unit.id}_{target_u.id}",
                                    category="defense",
                                    group=f"unit_{unit.id}",
                                    label=f"Repair {target_u.type} {target_u.id} with SCV {unit.id}",
                                    commands=(
                                        Command(
                                            kind="repair",
                                            unit_ids=(unit.id,),
                                            target_id=target_u.id,
                                        ),
                                    ),
                                )
                            )

        # Group actions by unit type and all combat units
        combat_groups: dict[str, list[int]] = {}
        for u in combat_units:
            clean_type = u.type.replace("Terran ", "").replace(" ", "_").lower()
            combat_groups.setdefault(f"all_{clean_type}s", []).append(u.id)
        if len(combat_units) >= 2:
            combat_groups["all_combat"] = [u.id for u in combat_units]

        for group_name, group_unit_ids in combat_groups.items():
            if len(group_unit_ids) < 2:
                continue
            u_ids = tuple(group_unit_ids[:200])
            group_label = group_name.replace("_", " ")
            if "attack" in due or "defense" in due:
                add(
                    Action(
                        id=f"spatial_attack_group_{group_name}",
                        category="spatial_attack",
                        group=group_name,
                        label=f"Attack-move {group_label} ({len(u_ids)} units) to ground coordinates via spatial selection",
                        commands=(Command(kind="attack", unit_ids=u_ids),),
                    )
                )
                add(
                    Action(
                        id=f"spatial_move_group_{group_name}",
                        category="spatial_move",
                        group=group_name,
                        label=f"Move {group_label} ({len(u_ids)} units) to ground coordinates via spatial selection",
                        commands=(Command(kind="move", unit_ids=u_ids),),
                    )
                )
                add(
                    Action(
                        id=f"stop_group_{group_name}",
                        category="defense",
                        group=group_name,
                        label=f"Stop {group_label} ({len(u_ids)} units)",
                        commands=(Command(kind="stop", unit_ids=u_ids),),
                    )
                )
                add(
                    Action(
                        id=f"hold_group_{group_name}",
                        category="defense",
                        group=group_name,
                        label=f"Hold position with {group_label} ({len(u_ids)} units)",
                        commands=(Command(kind="hold_position", unit_ids=u_ids),),
                    )
                )
                if "attack" in due:
                    for target, position in attack_targets:
                        add(
                            Action(
                                id=f"attack_group_{group_name}_{target}",
                                category="attack",
                                group=group_name,
                                label=f"Attack {target} with {group_label} ({len(u_ids)} units)",
                                commands=(
                                    Command(kind="attack", unit_ids=u_ids, position=position),
                                ),
                            )
                        )

        return actions
