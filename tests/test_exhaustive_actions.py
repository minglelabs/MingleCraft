from minglecraft.actions.generator import ActionGenerator
from minglecraft.models import BuildSite, Enemy, Location, Position, Unit


def test_exhaustive_includes_busy_workers_targets_and_all_scouts(observation):
    site = BuildSite(unit_type="Terran_Supply_Depot", tile=Position(x=20, y=20))
    workers = (
        Unit(
            id=10,
            type="Terran_SCV",
            position=observation.home,
            hit_points=60,
            idle=False,
            can_move=True,
            can_attack=True,
            can_gather=(100,),
            build_sites=(site,),
        ),
        Unit(
            id=11,
            type="Terran_SCV",
            position=observation.home,
            hit_points=60,
            can_move=True,
            can_gather=(100,),
            build_sites=(site,),
        ),
        Unit(
            id=12,
            type="Terran_SCV",
            position=observation.home,
            hit_points=60,
            can_move=True,
            can_attack=True,
            constructing=True,
        ),
    )
    enemies = tuple(
        Enemy(
            id=200 + index,
            type="Terran_Marine",
            position=Position(x=500 + index, y=500),
            hit_points=40,
            visible=True,
        )
        for index in range(8)
    )
    locations = (
        Location(id="start_a", kind="start", position=Position(x=1000, y=1000)),
        Location(
            id="start_b",
            kind="start",
            position=Position(x=2000, y=2000),
            explored=True,
        ),
    )
    obs = observation.model_copy(
        update={
            "minerals": 500,
            "units": (observation.units[0], *workers),
            "enemies": enemies,
            "locations": locations,
        }
    )
    actions = ActionGenerator().generate(
        obs,
        {"economy", "construction", "attack", "scout"},
        exhaustive=True,
    )
    ids = {action.id for action in actions}

    assert "gather_10_100" in ids
    assert "gather_11_100" in ids
    assert "build_10_Terran_Supply_Depot_20_20" in ids
    assert "build_11_Terran_Supply_Depot_20_20" in ids
    assert "attack_unit_10_visible_207" in ids
    assert "attack_unit_10_start_b" in ids
    assert "attack_unit_10_start_a" in ids
    assert "scout_10_start_b" in ids
    assert "scout_11_start_a" in ids
    assert not any(action.id.startswith(("attack_unit_12_", "move_12_")) for action in actions)


def test_exhaustive_preserves_per_unit_production_candidates(observation):
    producer = observation.units[0].model_copy(update={"can_train": ("Terran_SCV",)})
    obs = observation.model_copy(update={"units": (producer, *observation.units[1:])})

    actions = ActionGenerator().generate(obs, {"production"}, exhaustive=True)

    train = next(action for action in actions if action.id == "train_1_Terran_SCV")
    assert train.commands[0].kind == "train"
    assert train.commands[0].unit_ids == (1,)


def test_exhaustive_protoss_exposes_probe_pylon_and_worker_targets():
    from minglecraft.models import Mineral, Observation

    probe_build_site = BuildSite(unit_type="Protoss_Pylon", tile=Position(x=8, y=8))
    obs = Observation(
        match_id="protoss_match",
        frame=0,
        map_name="Destination.scx",
        map_hash="hash1",
        self_race="Protoss",
        minerals=200,
        gas=0,
        supply_used=4,
        supply_total=9,
        home=Position(x=100, y=100),
        units=(
            Unit(
                id=1,
                type="Protoss_Nexus",
                position=Position(x=100, y=100),
                hit_points=1000,
                can_train=("Protoss_Probe",),
            ),
            *(
                Unit(
                    id=probe_id,
                    type="Protoss_Probe",
                    position=Position(x=110 + probe_id, y=100),
                    hit_points=20,
                    idle=True,
                    can_move=True,
                    can_gather=(100,),
                    build_sites=(probe_build_site,),
                )
                for probe_id in range(2, 6)
            ),
        ),
        mineral_patches=(Mineral(id=100, position=Position(x=120, y=100)),),
    )

    actions = ActionGenerator().generate(
        obs,
        {"economy", "production", "construction", "attack", "defense", "scout"},
        exhaustive=True,
    )
    by_id = {action.id: action for action in actions}

    assert "train_1_Protoss_Probe" in by_id
    assert all(f"gather_{probe_id}_100" in by_id for probe_id in range(2, 6))
    assert all(f"build_{probe_id}_Protoss_Pylon_8_8" in by_id for probe_id in range(2, 6))
    assert "spatial_move_group_all_workers" in by_id
    assert not any(action.id.startswith("spatial_move_group_all_scvs") for action in actions)
    assert all(
        "SCV" not in action.label
        for action in actions
        if action.category in {"economy", "construction", "scout"}
    )


def test_exhaustive_has_more_than_fifty_candidates(observation):
    marines = tuple(
        Unit(
            id=100 + index,
            type="Terran_Marine",
            position=observation.home,
            hit_points=40,
            can_move=True,
            can_attack=True,
        )
        for index in range(12)
    )
    enemies = tuple(
        Enemy(
            id=300 + index,
            type="Terran_Marine",
            position=Position(x=600 + index, y=600),
            hit_points=40,
            visible=True,
        )
        for index in range(4)
    )
    obs = observation.model_copy(
        update={"units": (*observation.units, *marines), "enemies": enemies}
    )

    actions = ActionGenerator().generate(obs, {"attack", "defense"}, exhaustive=True)

    assert len(actions) > 50


def test_exhaustive_chunks_large_squads_to_native_command_limit(observation):
    marines = tuple(
        Unit(
            id=1000 + index,
            type="Terran_Marine",
            position=observation.home,
            hit_points=40,
            can_move=True,
            can_attack=True,
        )
        for index in range(205)
    )
    enemy = Enemy(
        id=9000,
        type="Terran_Marine",
        position=Position(x=700, y=700),
        hit_points=40,
        visible=True,
    )
    obs = observation.model_copy(
        update={"units": (*observation.units, *marines), "enemies": (enemy,)}
    )

    actions = ActionGenerator().generate(obs, {"attack", "defense"}, exhaustive=True)

    assert len(actions) > 0
    assert all(len(command.unit_ids) <= 200 for action in actions for command in action.commands)


def test_constructing_worker_has_no_movement_candidates(observation):
    worker = Unit(
        id=80,
        type="Terran_SCV",
        position=observation.home,
        hit_points=60,
        constructing=True,
        can_move=True,
        can_attack=True,
    )
    obs = observation.model_copy(update={"units": (worker,)})
    actions = ActionGenerator().generate(obs, {"attack", "defense", "scout"}, exhaustive=True)
    assert all(80 not in command.unit_ids for action in actions for command in action.commands)


def test_gas_gathering_and_special_abilities_and_groups():
    from minglecraft.actions.generator import ActionGenerator
    from minglecraft.models import Mineral, Observation, Position, Unit

    obs = Observation(
        match_id="test_match",
        frame=100,
        map_name="Destination.scx",
        map_hash="hash1",
        map_width=4096,
        map_height=4096,
        minerals=500,
        gas=100,
        supply_used=10,
        supply_total=20,
        home=Position(x=100, y=100),
        units=(
            Unit(
                id=1,
                type="Terran SCV",
                position=Position(x=100, y=100),
                hit_points=60,
                can_gather=(10, 20),
            ),
            Unit(
                id=20,
                type="Terran Refinery",
                position=Position(x=150, y=150),
                hit_points=750,
                completed=True,
            ),
            Unit(
                id=2,
                type="Terran Marine",
                position=Position(x=200, y=200),
                hit_points=40,
                can_attack=True,
                can_move=True,
            ),
            Unit(
                id=3,
                type="Terran Marine",
                position=Position(x=210, y=210),
                hit_points=40,
                can_attack=True,
                can_move=True,
            ),
            Unit(
                id=4,
                type="Terran Siege Tank - Tank Mode",
                position=Position(x=220, y=220),
                hit_points=150,
                can_attack=True,
                can_move=True,
                can_siege=True,
            ),
            Unit(
                id=5,
                type="Terran Wraith",
                position=Position(x=250, y=250),
                hit_points=120,
                can_attack=True,
                can_move=True,
                can_cloak=True,
                can_decloak=True,
            ),
        ),
        mineral_patches=(Mineral(id=10, position=Position(x=120, y=120)),),
    )

    gen = ActionGenerator()
    actions = gen._generate_exhaustive(obs, {"economy", "attack", "defense"})
    ids = {a.id for a in actions}

    assert "gather_1_10" in ids
    assert "gather_gas_1_20" in ids
    assert "siege_4" in ids
    assert "cloak_5" in ids
    assert "decloak_5" in ids
    assert "spatial_attack_group_all_marines" in ids
    assert "spatial_attack_group_all_combat" in ids


def test_exhaustive_target_attacks_groups_and_capabilities(observation):
    # Test worker groups, capability flags, and visible enemy target_id commands
    workers = (
        Unit(
            id=10,
            type="Terran_SCV",
            position=observation.home,
            hit_points=60,
            can_move=True,
            can_gather=(100,),
            can_patrol=True,
            can_return_cargo=True,
            can_attack=True,
        ),
        Unit(
            id=11,
            type="Terran_SCV",
            position=observation.home,
            hit_points=60,
            can_move=True,
            can_gather=(100,),
            can_attack=True,
        ),
    )
    combat = (
        Unit(
            id=20,
            type="Terran_Marine",
            position=observation.home,
            hit_points=40,
            can_move=True,
            can_attack=True,
            can_stim=True,
        ),
        Unit(
            id=21,
            type="Terran_Marine",
            position=observation.home,
            hit_points=40,
            can_move=True,
            can_attack=True,
        ),
        Unit(
            id=30,
            type="Terran_Siege_Tank_Tank_Mode",
            position=observation.home,
            hit_points=150,
            can_move=True,
            can_attack=True,
            can_siege=True,
        ),
        Unit(
            id=31,
            type="Terran_Siege_Tank_Siege_Mode",
            position=observation.home,
            hit_points=150,
            can_attack=True,
            can_unsiege=True,
        ),
        Unit(
            id=40,
            type="Terran_Wraith",
            position=observation.home,
            hit_points=120,
            can_move=True,
            can_attack=True,
            can_cloak=True,
        ),
        Unit(
            id=41,
            type="Terran_Wraith",
            position=observation.home,
            hit_points=120,
            can_move=True,
            can_attack=True,
            can_decloak=True,
        ),
        Unit(
            id=50,
            type="Zerg_Lurker",
            position=observation.home,
            hit_points=125,
            can_move=True,
            can_attack=True,
            can_burrow=True,
        ),
        Unit(
            id=60,
            type="Terran_Dropship",
            position=observation.home,
            hit_points=150,
            can_move=True,
            can_lift=True,
            can_land=True,
            can_unload_all=True,
            load_targets=(20,),
            unload_targets=(20,),
            can_use_tech=("Spider_Mines", "Stim_Packs"),
            can_use_tech_without_target=("Stim_Packs",),
            can_use_tech_at_position=("Spider_Mines",),
            tech_target_ids={"Spider_Mines": (777,)},
        ),
    )
    enemy = Enemy(
        id=777,
        type="Protoss_Zealot",
        position=Position(x=1500, y=1500),
        hit_points=160,
        visible=True,
    )
    obs = observation.model_copy(
        update={
            "units": (*workers, *combat),
            "enemies": (enemy,),
        }
    )
    actions = ActionGenerator().generate(
        obs,
        {"economy", "attack", "defense"},
        exhaustive=True,
    )
    actions_by_id = {a.id: a for a in actions}

    # 1. Target attack on visible enemy has target_id
    marine_attack = actions_by_id.get("attack_unit_20_visible_777")
    assert marine_attack is not None
    cmd = marine_attack.commands[0]
    assert cmd.kind == "attack"
    assert cmd.unit_ids == (20,)
    assert cmd.target_id == 777
    assert cmd.position == Position(x=1500, y=1500)

    # 2. Group target attack on visible enemy has target_id
    group_attack = actions_by_id.get("attack_group_all_marines_visible_777")
    assert group_attack is not None
    g_cmd = group_attack.commands[0]
    assert g_cmd.kind == "attack"
    assert set(g_cmd.unit_ids) == {20, 21}
    assert g_cmd.target_id == 777
    assert g_cmd.position == Position(x=1500, y=1500)

    # 3. Worker groups all_scvs and all_workers exist
    assert "spatial_attack_group_all_scvs" in actions_by_id
    assert "spatial_move_group_all_scvs" in actions_by_id
    assert "spatial_attack_group_all_workers" in actions_by_id
    assert "spatial_move_group_all_workers" in actions_by_id
    assert "attack_group_all_scvs_visible_777" in actions_by_id
    scv_cmd = actions_by_id["attack_group_all_scvs_visible_777"].commands[0]
    assert set(scv_cmd.unit_ids) == {10, 11}
    assert scv_cmd.target_id == 777

    # 4. Capability-based actions
    assert "stim_20" in actions_by_id
    assert actions_by_id["stim_20"].commands[0].kind == "stim"

    assert "patrol_10_home" in actions_by_id
    assert actions_by_id["patrol_10_home"].commands[0].kind == "patrol"

    assert "return_cargo_10" in actions_by_id
    assert actions_by_id["return_cargo_10"].commands[0].kind == "return_cargo"

    assert "burrow_50" in actions_by_id
    assert "lift_60" in actions_by_id
    assert "spatial_land_unit_60" in actions_by_id
    assert "load_60_20" in actions_by_id
    assert "unload_60_20" in actions_by_id
    assert "unload_all_60" in actions_by_id
    assert "spatial_unload_all_unit_60" in actions_by_id
    assert "use_tech_60_Stim_Packs" in actions_by_id
    assert "use_tech_60_Spider_Mines_777" in actions_by_id
    assert "spatial_use_tech_60_Spider_Mines" in actions_by_id
    assert "spatial_use_tech_60_Stim_Packs" not in actions_by_id

    assert "siege_30" in actions_by_id
    assert actions_by_id["siege_30"].commands[0].kind == "siege"

    assert "unsiege_31" in actions_by_id
    assert actions_by_id["unsiege_31"].commands[0].kind == "unsiege"

    assert "cloak_40" in actions_by_id
    assert actions_by_id["cloak_40"].commands[0].kind == "cloak"

    assert "decloak_41" in actions_by_id
    assert actions_by_id["decloak_41"].commands[0].kind == "decloak"


def test_choice_tree_hierarchical_split_guarantees_under_255_choices():
    import asyncio

    from minglecraft.actions.hierarchy import MAX_CHOICES_PER_QUESTION, ChoiceTree
    from minglecraft.agents.providers import RandomProvider, RuleBasedProvider
    from minglecraft.models import Action, Command

    # Create 350 candidate actions across different categories
    actions = [
        Action(id="wait", category="wait", group="wait", label="Keep current orders", priority=0)
    ]
    for i in range(1, 150):
        actions.append(
            Action(
                id=f"gather_worker_{i}_mineral_1",
                category="economy",
                group="workers",
                label=f"Send worker {i} to mineral 1",
                priority=100 if i == 1 else 10,
                commands=(Command(kind="gather", unit_ids=(i,), target_id=1),),
            )
        )
    for i in range(150, 300):
        actions.append(
            Action(
                id=f"attack_unit_{i}_enemy_1",
                category="attack",
                group="combat",
                label=f"Attack enemy 1 with unit {i}",
                priority=50,
                commands=(Command(kind="attack", unit_ids=(i,), target_id=1),),
            )
        )
    for i in range(300, 360):
        actions.append(
            Action(
                id=f"build_worker_{i}_pylon",
                category="construction",
                group="buildings",
                label=f"Build pylon with worker {i}",
                priority=80,
                commands=(Command(kind="build", unit_ids=(i,), target_id=1),),
            )
        )

    tree = ChoiceTree({}, actions)
    assert tree.is_hierarchical

    # Assert every question has <= 200 criteria (well below Jev 255 limit)
    for q_id, q in tree.request.questions.items():
        assert len(q.criteria) <= MAX_CHOICES_PER_QUESTION, (
            f"Question {q_id} has {len(q.criteria)} choices"
        )

    # Test RuleBasedProvider resolution
    rule_result = asyncio.run(RuleBasedProvider().decide(tree.request))
    chosen_action, path = tree.resolve(rule_result)
    assert chosen_action.id == "gather_worker_1_mineral_1"
    assert len(path) == 3
    assert path[0]["node"] == "category"
    assert path[0]["choice"] == "economy"
    assert path[1]["choice"] == "unit_1"
    assert path[-1]["choice"] == chosen_action.id

    # Test RandomProvider resolution
    rng_result = asyncio.run(RandomProvider(42).decide(tree.request))
    chosen_random, random_path = tree.resolve(rng_result)
    assert chosen_random in actions
    assert len(random_path) == 3
