"""Compact, lossless wire encoding for staged model requests."""

from copy import deepcopy

from minglecraft.models import DecisionRequest


def _columnar(items: list[dict], columns: list[str]) -> dict:
    return {
        "columns": columns,
        "rows": [[item.get(column) for column in columns] for item in items],
    }


def _position(value):
    if value is None:
        return None
    return [value["x"], value["y"]]


def _command(command: dict) -> list:
    return [
        command["kind"],
        command["unit_ids"],
        command["unit_type"],
        command["target_id"],
        _position(command["position"]),
        _position(command["tile"]),
    ]


def _actions(actions: list[dict]) -> dict:
    columns = ["id", "category", "group", "label", "commands"]
    rows = []
    for action in actions:
        rows.append(
            [
                action["id"],
                action["category"],
                action["group"],
                action["label"],
                [_command(command) for command in action.get("commands", [])],
            ]
        )
    return {"columns": columns, "rows": rows}


def _units(units: list[dict]) -> dict:
    columns = [
        "id",
        "type",
        "position",
        "hit_points",
        "completed",
        "idle",
        "training",
        "constructing",
        "can_move",
        "can_attack",
        "can_train",
        "can_gather",
        "build_sites",
    ]
    rows = []
    for unit in units:
        rows.append(
            [
                unit["id"],
                unit["type"],
                _position(unit["position"]),
                unit["hit_points"],
                unit["completed"],
                unit["idle"],
                unit["training"],
                unit["constructing"],
                unit["can_move"],
                unit["can_attack"],
                unit["can_train"],
                unit["can_gather"],
                [[site["unit_type"], _position(site["tile"])] for site in unit["build_sites"]],
            ]
        )
    return {"columns": columns, "rows": rows}


def _enemies(enemies: list[dict]) -> dict:
    columns = ["id", "type", "position", "hit_points", "visible"]
    return _columnar(
        [{**enemy, "position": _position(enemy["position"])} for enemy in enemies],
        columns,
    )


def _locations(locations: list[dict]) -> dict:
    columns = ["id", "position", "kind", "explored"]
    return _columnar(
        [{**location, "position": _position(location["position"])} for location in locations],
        columns,
    )


def _minerals(minerals: list[dict]) -> dict:
    return _columnar(
        [{**mineral, "position": _position(mineral["position"])} for mineral in minerals],
        ["id", "position"],
    )


def _receipts(receipts: list[dict]) -> dict:
    return _columnar(
        receipts,
        ["decision_id", "frame", "attempted", "accepted", "effective", "reason"],
    )


def _observation(observation: dict) -> dict:
    compact = deepcopy(observation)
    compact["home"] = _position(compact["home"])
    compact["units"] = _units(compact["units"])
    compact["enemies"] = _enemies(compact["enemies"])
    compact["mineral_patches"] = _minerals(compact["mineral_patches"])
    compact["locations"] = _locations(compact["locations"])
    compact["receipts"] = _receipts(compact["receipts"])
    return compact


def _reference(child: str) -> str:
    return f"leaf:{child[5:]}" if child.startswith("leaf:") else f"node:{child}"


def compact_state(
    state: dict,
    choice_tree: dict | None = None,
    asked_nodes: set[str] | None = None,
) -> dict:
    compact = deepcopy(state)
    if "observation" in compact:
        compact["observation"] = _observation(compact["observation"])
    if "candidate_actions" in compact:
        compact["candidate_actions"] = _actions(compact["candidate_actions"])
    if choice_tree is not None:
        compact["choice_tree"] = {
            node: {option: _reference(child) for option, child in options.items()}
            for node, options in choice_tree.items()
            if not asked_nodes or node not in asked_nodes
        }
    return compact


def compact_request_payload(
    request: DecisionRequest, model: str, choice_tree: dict | None = None
) -> dict:
    """Encode only model-facing JSON; local request objects remain lossless."""
    choice_tree = choice_tree or request.choice_tree
    asked_nodes = set(request.questions) if choice_tree is not None else None
    state = compact_state(request.state, choice_tree, asked_nodes)
    payload = {
        "model": model,
        "state": {"schema": "jev/compact-v1", **state},
        "questions": {},
    }
    payload["state"]["legend"] = {
        "pos": "[x,y]",
        "commands": "[kind,unit_ids,unit_type,target_id,position,tile]; positions use pos",
        "build_sites": "[unit_type,pos]",
        "choice_tree": "only unasked deterministic nodes; asked node mappings are in that question criteria",
        "choice_refs": "criteria/tree values use node:N or leaf:ACTION; option keys are exact answer IDs",
    }
    for key, question in request.questions.items():
        data = question.model_dump()
        if "criteria" in data:
            if choice_tree is None:
                data["criteria"] = dict(data["criteria"])
            else:
                data["criteria"] = {
                    option: _reference(choice_tree[key][option]) for option in data["criteria"]
                }
        payload["questions"][key] = data
    return payload
