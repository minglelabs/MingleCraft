import asyncio
import json

import httpx

from minglecraft.actions.generator import ActionGenerator
from minglecraft.actions.hierarchy import ChoiceTree
from minglecraft.agents.encoding import (
    compact_request_payload,
    estimate_tokens_conservative,
)
from minglecraft.agents.providers import JevProvider
from minglecraft.bwapi.synthetic import SyntheticGame
from minglecraft.loop import AgentLoop
from minglecraft.models import (
    Action,
    BuildSite,
    ChoiceQuestion,
    Command,
    DecisionRequest,
    Enemy,
    Mineral,
    Observation,
    Position,
    Receipt,
    Unit,
)


def _make_rich_observation() -> Observation:
    """Create a realistic, rich observation with own units, enemies, minerals, receipts."""
    own_units = (
        Unit(
            id=1,
            type="Terran_SCV",
            position=Position(x=100, y=100),
            hit_points=60,
            completed=True,
            idle=True,
            can_move=True,
            can_gather=(101, 102),
            build_sites=(
                BuildSite(unit_type="Terran_Supply_Depot", tile=Position(x=10, y=10)),
                BuildSite(unit_type="Terran_Barracks", tile=Position(x=15, y=15)),
            ),
            repair_targets=(2,),
        ),
        Unit(
            id=2,
            type="Terran_Siege_Tank_Tank_Mode",
            position=Position(x=200, y=200),
            hit_points=150,
            completed=True,
            idle=False,
            can_move=True,
            can_attack=True,
            can_siege=True,
            can_unsiege=False,
        ),
        Unit(
            id=3,
            type="Terran_Science_Vessel",
            position=Position(x=300, y=300),
            hit_points=200,
            completed=True,
            idle=False,
            can_move=True,
            can_use_tech=("EMP Shockwave", "Defensive Matrix"),
            can_use_tech_at_position=("EMP Shockwave",),
            tech_target_ids={"Defensive Matrix": (1, 2)},
        ),
    )
    enemies = (
        Enemy(id=50, type="Zerg_Zergling", position=Position(x=500, y=500), hit_points=35, visible=True),
        Enemy(id=51, type="Zerg_Hydralisk", position=Position(x=520, y=520), hit_points=80, visible=True),
    )
    minerals = (
        Mineral(id=101, position=Position(x=80, y=90)),
        Mineral(id=102, position=Position(x=85, y=95)),
    )
    receipts = (
        Receipt(decision_id="dec_0", frame=0, attempted=1, accepted=1, effective=1, reason="ok"),
    )
    return Observation(
        protocol_version=1,
        match_id="test_match",
        frame=24,
        map_name="test_map",
        map_hash="hash123",
        map_width=4096,
        map_height=4096,
        self_race="Terran",
        enemy_race="Zerg",
        minerals=150,
        gas=0,
        supply_used=6,
        supply_total=20,
        home=Position(x=100, y=100),
        units=own_units,
        enemies=enemies,
        mineral_patches=minerals,
        receipts=receipts,
    )


def test_provider_wire_body_matches_default_encoder():
    """Verify that JevProvider transmits the exact same JSON payload as default compact_request_payload."""
    obs = _make_rich_observation()
    actions = ActionGenerator().generate(obs, due={"economy", "construction", "defense"}, exhaustive=True)
    tree = ChoiceTree(
        {"observation": obs.model_dump(), "candidate_actions": [a.model_dump() for a in actions]},
        actions,
        hierarchical=True,
    )

    transmitted_payloads = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        transmitted_payloads.append(body)
        answers = {
            q_id: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 1.0, "probabilities": {k: 1.0 if i == 0 else 0.0 for i, k in enumerate(q["criteria"])}}
            for q_id, q in body["questions"].items()
        }
        return httpx.Response(200, json={"model": "jev-latest", "answers": answers})

    transport = httpx.MockTransport(mock_handler)
    provider = JevProvider("test_key", model="jev-latest", transport=transport)

    import asyncio
    asyncio.run(provider.decide(tree.request))

    assert len(transmitted_payloads) == 1
    actual_wire = transmitted_payloads[0]
    expected_wire = compact_request_payload(tree.request, "jev-latest")

    assert actual_wire == json.loads(json.dumps(expected_wire))


def test_no_choice_tree_on_wire_and_meaningful_criteria_labels():
    """Verify choice_tree is strictly kept local and criteria values contain descriptive natural language labels."""
    obs = _make_rich_observation()
    actions = [
        Action(id="wait", category="wait", group="wait", label="Wait and hold orders"),
        Action(
            id="attack_50",
            category="attack",
            group="tanks",
            label="Attack Zergling 50 with Tank 2",
            commands=(Command(kind="attack", unit_ids=(2,), target_id=50),),
        ),
    ]
    tree = ChoiceTree(
        {"observation": obs.model_dump(), "candidate_actions": [a.model_dump() for a in actions]},
        actions,
        hierarchical=True,
    )

    payload = compact_request_payload(tree.request, "jev-latest", choice_tree=tree.nodes)

    assert "choice_tree" not in payload["state"], "choice_tree leaked into state!"
    assert "choice_tree" not in payload

    for q_id, question in payload["questions"].items():
        for opt_id, label in question["criteria"].items():
            assert not label.startswith(("leaf:", "node:")), (
                f"Criteria option '{opt_id}' in question '{q_id}' was overwritten with internal ref: {label}"
            )
            assert len(label.strip()) > 0


def test_all_entity_facts_preserved_and_legality_lists_omitted():
    """Verify core unit facts and small capabilities are retained while heavy legality lists are omitted."""
    obs = _make_rich_observation()
    request = DecisionRequest(
        state={"observation": obs.model_dump()},
        questions={"dummy": ChoiceQuestion(instructions="choose", criteria={"w": "wait"})},
        priorities={},
    )
    payload = compact_request_payload(request, "jev-latest")
    units_table = payload["state"]["observation"]["units"]

    cols = units_table["columns"]
    rows = units_table["rows"]

    for req_fact in ("id", "type", "position", "hit_points", "completed", "idle", "training", "constructing"):
        assert req_fact in cols, f"Missing core fact: {req_fact}"

    for cap in ("can_move", "can_attack", "can_siege", "can_stim"):
        assert cap in cols, f"Missing boolean capability: {cap}"

    for forbidden_legality in ("can_gather", "build_sites", "load_targets", "unload_targets", "repair_targets", "can_use_tech", "tech_target_ids"):
        assert forbidden_legality not in cols, f"Heavy legality list '{forbidden_legality}' leaked to wire!"

    id_idx = cols.index("id")
    type_idx = cols.index("type")
    pos_idx = cols.index("position")
    hp_idx = cols.index("hit_points")

    unit_by_id = {row[id_idx]: row for row in rows}
    assert 1 in unit_by_id and unit_by_id[1][type_idx] == "Terran_SCV"
    assert unit_by_id[1][pos_idx] == [100, 100]
    assert unit_by_id[1][hp_idx] == 60

    assert 2 in unit_by_id and unit_by_id[2][type_idx] == "Terran_Siege_Tank_Tank_Mode"
    assert unit_by_id[2][cols.index("can_siege")] is True

    assert len(payload["state"]["observation"]["enemies"]["rows"]) == 2
    assert len(payload["state"]["observation"]["mineral_patches"]["rows"]) == 2
    assert len(payload["state"]["observation"]["receipts"]["rows"]) == 1


def test_token_estimate_mixed_identifiers_and_unicode():
    """Regression test: combined identifiers with underscores/numbers and non-ASCII are not undercounted."""
    # Identifier test: 'spatial_attack_unit_123' has 4 words, 3 underscores, 1 number
    # Previous flawed regex counted this whole string as 1 token (severe undercount)
    identifier = "spatial_attack_unit_123"
    est_ident = estimate_tokens_conservative(identifier)
    # spatial (2-3) + _ (1) + attack (2) + _ (1) + unit (1-2) + _ (1) + 123 (2) => ~9-12 tokens
    assert est_ident >= 6, f"Identifier was undercounted: {est_ident}"

    # Unicode test: Korean instructions or labels
    korean_text = "기지 방어 명령: 본진으로 이동하라"
    est_kr = estimate_tokens_conservative(korean_text)
    # 17 Korean/symbol characters, ~40 UTF-8 bytes => must estimate >= 15 tokens
    assert est_kr >= 15, f"Unicode text was undercounted: {est_kr}"


def test_http_budget_splits_large_tree_and_selects_last_leaf_without_dumping_tree(tmp_path):
    """Large HTTP traversal stays sequential, bounded, and lossless."""
    payloads = []
    raw_bodies = []

    def respond(request: httpx.Request) -> httpx.Response:
        raw_bodies.append(bytes(request.content))
        payload = json.loads(request.content)
        payloads.append(payload)
        questions = payload["questions"]
        assert len(questions) == 1
        question_id, question = next(iter(questions.items()))
        choice = next(reversed(question["criteria"]))
        answer = {
            question_id: {
                "type": "choice",
                "choice": choice,
                "confidence": 1,
                "probabilities": {
                    key: float(key == choice) for key in question["criteria"]
                },
            }
        }
        return httpx.Response(200, json={"model": "jev-latest", "answers": answer})

    provider = JevProvider("test-key", transport=httpx.MockTransport(respond))
    loop = AgentLoop(provider, tmp_path, single_stage=True, byte_budget=5000, token_budget=24000)

    def generate(obs, due, exhaustive=False):
        assert exhaustive is True
        return [
            *[
                Action(
                    id=f"attack_{index}",
                    category="attack",
                    group=f"group_{index}",
                    label=f"Attack target {index}",
                    commands=(Command(kind="attack", unit_ids=(1,), target_id=index),),
                )
                for index in range(1200)
            ],
        ]

    loop.generator.generate = generate
    observation = SyntheticGame("large_http_budget").observe()
    decision = asyncio.run(loop.step(observation))

    assert decision.action_id == "attack_1199"
    assert payloads
    assert all(len(payload["questions"]) == 1 for payload in payloads)
    assert all(
        len(next(iter(payload["questions"].values()))["criteria"]) <= 200
        for payload in payloads
    )
    assert all("choice_tree" not in payload["state"] for payload in payloads)
    assert max(len(body) for body in raw_bodies) <= 5000
    assert max(estimate_tokens_conservative(body.decode()) for body in raw_bodies) <= 24000
    assert max(len(payload["state"]["candidate_actions"]["rows"]) for payload in payloads) < 1201
    record = json.loads(
        (tmp_path / "large_http_budget" / "decisions.jsonl").read_text().splitlines()[0]
    )
    sent_calls = [call for call in record["calls"] if call["sent"]]
    assert record["query_count"] == len(payloads)
    assert [call["request_bytes"] for call in sent_calls] == [len(body) for body in raw_bodies]


def test_dynamic_budget_split_preserves_all_leaf_ids():
    actions = [
        Action(
            id=f"attack_{index}",
            category="attack",
            group=f"group_{index}",
            label=f"Attack target {index}",
            commands=(Command(kind="attack", unit_ids=(index,), target_id=index),),
        )
        for index in range(400)
    ]
    tree = ChoiceTree(
        {"observation": {"units": [{"id": index, "type": "Terran_Marine"} for index in range(400)]}},
        actions,
        hierarchical=True,
    )
    root = "command_kind"
    node = next(node for node, options in tree.nodes.items() if len(options) > 2)
    before = set(tree.descendants(root))
    AgentLoop.__new__(AgentLoop)._split_budget_node(tree, node)
    after = set(tree.descendants(root))
    assert after == before == {action.id for action in actions}
