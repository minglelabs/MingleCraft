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
            ),
            Unit(
                id=5,
                type="Terran Wraith",
                position=Position(x=250, y=250),
                hit_points=120,
                can_attack=True,
                can_move=True,
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
