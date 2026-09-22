import random

from minglecraft.actions.hierarchy import MAX_CHOICES_PER_QUESTION, ChoiceTree
from minglecraft.models import Action, Command, Position


def _make_action(
    action_id: str,
    kind: str,
    unit_ids: tuple[int, ...],
    target_id: int | None = None,
    position: Position | None = None,
    tile: Position | None = None,
    unit_type: str | None = None,
    tech: str | None = None,
    priority: float = 0.0,
    group: str = "group_default",
) -> Action:
    if not unit_ids:
        return Action(
            id=action_id,
            category="wait",
            group="wait",
            label="Keep current orders",
            priority=priority,
            commands=(),
        )
    return Action(
        id=action_id,
        category=f"cat_{kind}",
        group=group,
        label=f"Label for {action_id} (kind={kind}, units={unit_ids})",
        priority=priority,
        commands=(
            Command(
                kind=kind,
                unit_ids=unit_ids,
                target_id=target_id,
                position=position,
                tile=tile,
                unit_type=unit_type,
                tech=tech,
            ),
        ),
    )


def test_1000_plus_actions_exact_leaf_coverage_and_max_choices():
    """Requirement 1: 1000+ action exact leaf coverage, no duplicates, all choice questions <= MAX_CHOICES_PER_QUESTION."""
    state = {
        "observation": {
            "units": [{"id": i, "type": "Terran_Marine"} for i in range(1500)]
        }
    }
    actions = []
    for i in range(1200):
        kind = "attack" if i < 600 else ("move" if i < 900 else "patrol")
        unit_id = (i % 250) + 1
        actions.append(
            _make_action(
                action_id=f"act_{kind}_{unit_id}_{i}",
                kind=kind,
                unit_ids=(unit_id,),
                target_id=i,
                priority=float(i % 100),
            )
        )

    tree = ChoiceTree(state, actions, hierarchical=True)

    for node_id, options in tree.nodes.items():
        assert len(options) <= MAX_CHOICES_PER_QUESTION, f"Node {node_id} has {len(options)} options, exceeding {MAX_CHOICES_PER_QUESTION}"

    for q_id, q in tree.request.questions.items():
        assert len(q.criteria) <= MAX_CHOICES_PER_QUESTION, f"Question {q_id} has {len(q.criteria)} criteria"

    root = "command_kind" if tree.is_hierarchical else "action"
    reachable_leaves = tree.descendants(root)
    assert len(reachable_leaves) == len(actions)
    assert len(set(reachable_leaves)) == len(actions)
    assert set(reachable_leaves) == {a.id for a in actions}


def test_action_input_permutation_invariance():
    """Requirement 2: Permutation of action input list produces identical tree nodes and criteria."""
    state = {
        "observation": {
            "units": [
                {"id": 1, "type": "Terran_SCV"},
                {"id": 2, "type": "Terran_Marine"},
                {"id": 3, "type": "Terran_Siege_Tank_Tank_Mode"},
            ]
        }
    }
    actions = [
        _make_action("train_scv", "train", (1,), unit_type="Terran_SCV", priority=50.0),
        _make_action("move_scv_1", "move", (1,), position=Position(x=100, y=100), priority=10.0),
        _make_action("move_scv_2", "move", (1,), position=Position(x=200, y=200), priority=20.0),
        _make_action("attack_marine", "attack", (2,), target_id=99, priority=80.0),
        _make_action("siege_tank", "siege", (3,), priority=95.0),
        _make_action("wait_action", "wait", (), priority=0.0),
    ]

    tree_orig = ChoiceTree(state, actions, hierarchical=True)

    for seed in (42, 123, 999):
        shuffled = list(actions)
        random.Random(seed).shuffle(shuffled)
        tree_shuffled = ChoiceTree(state, shuffled, hierarchical=True)

        assert tree_shuffled.nodes.keys() == tree_orig.nodes.keys()
        for node in tree_orig.nodes:
            assert tree_shuffled.nodes[node] == tree_orig.nodes[node]

        assert tree_shuffled.request.questions.keys() == tree_orig.request.questions.keys()
        for q_id in tree_orig.request.questions:
            assert (
                tree_shuffled.request.questions[q_id].criteria
                == tree_orig.request.questions[q_id].criteria
            )


def test_priorities_invariance_for_nodes_and_criteria():
    """Requirement 3: Reversing or randomizing priorities does not alter tree topology or criteria."""
    state = {
        "observation": {
            "units": [{"id": 10, "type": "Terran_SCV"}, {"id": 20, "type": "Terran_Marine"}]
        }
    }
    actions = [
        _make_action("act_a", "attack", (20,), target_id=1, priority=10.0),
        _make_action("act_b", "attack", (20,), target_id=2, priority=50.0),
        _make_action("act_c", "attack", (20,), target_id=3, priority=90.0),
        _make_action("act_m", "move", (10,), position=Position(x=10, y=10), priority=5.0),
        _make_action("act_w", "wait", (), priority=0.0),
    ]

    tree_orig = ChoiceTree(state, actions, hierarchical=True)

    # Invert priorities
    actions_inverted = [
        _make_action(
            a.id,
            a.commands[0].kind if a.commands else "wait",
            a.commands[0].unit_ids if a.commands else (),
            target_id=a.commands[0].target_id if a.commands else None,
            position=a.commands[0].position if a.commands else None,
            unit_type=a.commands[0].unit_type if a.commands else None,
            priority=100.0 - a.priority,
        )
        for a in actions
    ]
    tree_inverted = ChoiceTree(state, actions_inverted, hierarchical=True)

    assert tree_inverted.nodes == tree_orig.nodes
    for q_id in tree_orig.request.questions:
        assert tree_inverted.request.questions[q_id].criteria == tree_orig.request.questions[q_id].criteria

    # Random priorities
    actions_random = [
        _make_action(
            a.id,
            a.commands[0].kind if a.commands else "wait",
            a.commands[0].unit_ids if a.commands else (),
            target_id=a.commands[0].target_id if a.commands else None,
            position=a.commands[0].position if a.commands else None,
            unit_type=a.commands[0].unit_type if a.commands else None,
            priority=random.uniform(-100, 100),
        )
        for a in actions
    ]
    tree_random = ChoiceTree(state, actions_random, hierarchical=True)

    assert tree_random.nodes == tree_orig.nodes
    for q_id in tree_orig.request.questions:
        assert tree_random.request.questions[q_id].criteria == tree_orig.request.questions[q_id].criteria


def test_multi_command_actions_coverage():
    """Requirement 4: Actions containing multi-unit or multiple commands are covered without loss or crash."""
    state = {
        "observation": {
            "units": [
                {"id": 1, "type": "Terran_Marine"},
                {"id": 2, "type": "Terran_Marine"},
                {"id": 3, "type": "Terran_Medic"},
            ]
        }
    }
    squad_action = Action(
        id="squad_attack",
        category="attack",
        group="marine_squad",
        label="Squad attack target",
        priority=60.0,
        commands=(
            Command(kind="attack", unit_ids=(1, 2), target_id=99),
        ),
    )
    combo_action = Action(
        id="stim_and_attack",
        category="combat",
        group="marine_1",
        label="Stim and attack",
        priority=70.0,
        commands=(
            Command(kind="stim", unit_ids=(1,)),
            Command(kind="attack", unit_ids=(1,), target_id=99),
        ),
    )
    simple_action = _make_action("heal_medic", "use_tech", (3,), tech="Restoration")

    tree = ChoiceTree(state, [squad_action, combo_action, simple_action], hierarchical=True)
    root = "command_kind" if tree.is_hierarchical else "action"
    leaves = set(tree.descendants(root))
    assert leaves == {"squad_attack", "stim_and_attack", "heal_medic"}


def test_actor_type_vs_target_or_train_unit_type_confusion_prevention():
    """Requirement 5: Branch semantics distinguish acting unit type from trained unit type or target unit."""
    state = {
        "observation": {
            "units": [
                {"id": 50, "type": "Terran_Barracks"},
                {"id": 60, "type": "Terran_Command_Center"},
            ]
        }
    }
    train_marine = _make_action("train_marine", "train", (50,), unit_type="Terran_Marine")
    train_scv = _make_action("train_scv", "train", (60,), unit_type="Terran_SCV")

    tree = ChoiceTree(state, [train_marine, train_scv], hierarchical=True)

    actor_node_options = None
    for node, opts in tree.nodes.items():
        if "actors" in node and "train" in node:
            actor_node_options = opts
            break

    assert actor_node_options is not None, "Did not find actors node for 'train' command"
    assert "unit_50" in actor_node_options
    assert "unit_60" in actor_node_options

    actor_question = None
    for q_id, q in tree.request.questions.items():
        if "actors" in q_id and "train" in q_id:
            actor_question = q
            break

    assert actor_question is not None
    assert "Barracks" in actor_question.criteria["unit_50"]
    assert "Command_Center" in actor_question.criteria["unit_60"]
