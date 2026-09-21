import asyncio

import pytest
from pydantic import ValidationError

from minglecraft.actions.generator import ActionGenerator
from minglecraft.actions.hierarchy import ChoiceTree
from minglecraft.actions.pruner import prune
from minglecraft.agents import RandomProvider, RuleBasedProvider
from minglecraft.models import ChoiceAnswer, Enemy, Position, Unit
from minglecraft.state import StateBuilder
from minglecraft.strategy.scheduler import Scheduler


def generate(obs):
    return ActionGenerator().generate(obs, set(Scheduler.periods))


def test_hidden_enemy_never_enters_state_or_actions(observation):
    enemy = Enemy(
        id=900,
        type="Terran_Marine",
        position=Position(x=2000, y=2000),
        hit_points=40,
        visible=False,
    )
    obs = observation.model_copy(update={"enemies": (enemy,)})
    state = StateBuilder().build(obs)
    assert state["enemy_visible"] == []
    assert state["enemy_last_seen"] == []
    assert "visible_900" not in str(generate(obs))


def test_memory_retains_only_last_visible_position_then_expires(observation):
    builder = StateBuilder(memory_frames=24)
    enemy = Enemy(
        id=900, type="Terran_Marine", position=Position(x=100, y=100), hit_points=40, visible=True
    )
    builder.build(observation.model_copy(update={"enemies": (enemy,)}))
    hidden = enemy.model_copy(update={"visible": False, "position": Position(x=2000, y=2000)})
    state = builder.build(observation.model_copy(update={"frame": 12, "enemies": (hidden,)}))
    assert state["enemy_last_seen"][0]["last_position"] == {"x": 100, "y": 100}
    assert state["enemy_last_seen"][0]["age_frames"] == 12
    assert builder.build(observation.model_copy(update={"frame": 25}))["enemy_last_seen"] == []


def test_memory_resets_between_matches(observation):
    builder = StateBuilder()
    enemy = Enemy(
        id=900, type="Terran_Marine", position=Position(x=1, y=1), hit_points=40, visible=True
    )
    builder.build(observation.model_copy(update={"enemies": (enemy,)}))
    assert (
        builder.build(observation.model_copy(update={"match_id": "new_match"}))["enemy_last_seen"]
        == []
    )


def test_observed_death_removes_memory(observation):
    builder = StateBuilder()
    enemy = Enemy(
        id=900, type="Terran_Marine", position=Position(x=1, y=1), hit_points=40, visible=True
    )
    builder.build(observation.model_copy(update={"enemies": (enemy,)}))
    assert (
        builder.build(observation.model_copy(update={"destroyed_enemy_ids": (900,)}))[
            "enemy_last_seen"
        ]
        == []
    )


@pytest.mark.parametrize("updates", [{"minerals": 0}, {"supply_used": 10}, {"supply_total": 0}])
def test_training_checks_resources_and_supply(observation, updates):
    assert not any(
        a.id.startswith("train_") for a in generate(observation.model_copy(update=updates))
    )


def test_state_builder_counts_non_terran_workers(observation):
    probes = tuple(
        Unit(
            id=probe_id,
            type="Protoss_Probe",
            position=observation.home,
            hit_points=20,
            idle=True,
            can_gather=(100,),
        )
        for probe_id in range(2, 6)
    )
    state = StateBuilder().build(
        observation.model_copy(update={"self_race": "Protoss", "units": probes})
    )
    assert state["idle_workers"] == 4


def test_native_capability_is_required(observation):
    units = tuple(
        u.model_copy(update={"can_train": (), "can_gather": (), "build_sites": ()})
        for u in observation.units
    )
    assert all(
        a.category not in {"production", "economy", "construction"}
        for a in generate(observation.model_copy(update={"units": units}))
    )


def test_busy_producer_and_builder_are_not_interrupted(observation):
    units = tuple(
        u.model_copy(update={"training": True, "constructing": True}) for u in observation.units
    )
    assert not any(
        a.category in {"production", "construction", "economy", "scout"}
        for a in generate(observation.model_copy(update={"units": units}))
    )


def test_wait_is_always_legal_and_scheduler_is_separate(observation):
    actions = ActionGenerator().generate(observation, set())
    assert [a.id for a in actions] == ["wait"]
    scheduler = Scheduler()
    scheduler.selected("production", 0)
    assert "production" not in scheduler.due(23)
    assert "production" in scheduler.due(24)
    assert "attack" in scheduler.due(6)


def test_pruning_keeps_categories_and_cap(observation):
    marines = tuple(
        Unit(
            id=i,
            type="Terran_Marine",
            position=observation.home,
            hit_points=40,
            can_attack=True,
            can_move=True,
        )
        for i in range(20, 40)
    )
    enemies = tuple(
        Enemy(
            id=i,
            type="Terran_Marine",
            position=Position(x=1000, y=1000),
            hit_points=40,
            visible=True,
        )
        for i in range(1000, 1010)
    )
    actions = generate(
        observation.model_copy(update={"units": observation.units + marines, "enemies": enemies})
    )
    pruned = prune(actions, 8)
    assert len(pruned) == 8
    assert pruned[0].id == "wait"
    assert {a.category for a in pruned} == {a.category for a in actions}


def test_hierarchy_chooses_one_server_owned_action(observation):
    actions = generate(observation)
    tree = ChoiceTree(StateBuilder().build(observation), actions)
    result = asyncio.run(RuleBasedProvider().decide(tree.request))
    action, path = tree.resolve(result)
    assert action.category == "economy"
    assert len(path) == 1
    assert action in actions


def test_injected_action_is_rejected(observation):
    tree = ChoiceTree({}, generate(observation))
    result = asyncio.run(RuleBasedProvider().decide(tree.request))
    answers = dict(result.answers, action=ChoiceAnswer(choice="delete_everything"))
    with pytest.raises(ValueError, match="outside|do not match"):
        tree.resolve(result.model_copy(update={"answers": answers}))


def test_malformed_distribution_is_rejected(observation):
    tree = ChoiceTree({}, generate(observation))
    result = asyncio.run(RuleBasedProvider().decide(tree.request))
    answers = dict(result.answers)
    answers["action"] = ChoiceAnswer(
        choice=next(iter(tree.request.questions["action"].criteria)),
        probabilities={k: 0 for k in tree.request.questions["action"].criteria},
    )
    with pytest.raises(ValueError, match="sum"):
        tree.resolve(result.model_copy(update={"answers": answers}))


def test_rounded_probability_distribution_is_accepted(observation):
    tree = ChoiceTree({}, generate(observation))
    result = asyncio.run(RuleBasedProvider().decide(tree.request))
    keys = list(tree.request.questions["action"].criteria)
    probabilities = {key: 0.0 for key in keys}
    probabilities[keys[0]] = 0.49
    probabilities[keys[1]] = 0.50
    answers = dict(result.answers)
    answers["action"] = ChoiceAnswer(
        choice=keys[0],
        probabilities=probabilities,
    )
    selected, _ = tree.resolve(result.model_copy(update={"answers": answers}))
    assert selected.id == keys[0]


def test_random_provider_seed_is_reproducible(observation):
    request = ChoiceTree({}, generate(observation)).request
    assert asyncio.run(RandomProvider(17).decide(request)) == asyncio.run(
        RandomProvider(17).decide(request)
    )


def test_complete_map_and_duplicate_ids_are_rejected(observation):
    from minglecraft.models import Observation

    payload = observation.model_dump()
    payload["complete_map_information"] = True
    with pytest.raises(ValidationError):
        Observation.model_validate(payload)
    payload["complete_map_information"] = False
    payload["units"] = (*payload["units"], payload["units"][0])
    with pytest.raises(ValidationError):
        Observation.model_validate(payload)
