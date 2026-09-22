"""Compact, lossless wire encoding for staged model requests."""

import re
from copy import deepcopy

from minglecraft.models import DecisionRequest


def estimate_tokens_conservative(text: str) -> int:
    """Heuristic token estimator for JSON wire payloads without an official tokenizer.

    This is an empirical rough heuristic, not a calibrated tokenizer, formal upper bound,
    or provider guarantee. Actual provider token counts vary by proprietary vocabulary.
    Separate ASCII letters, digits, and punctuation so combined identifiers
    (e.g., 'spatial_attack_unit_123') are not treated as single tokens. Non-ASCII
    characters are conservatively weighted by UTF-8 byte length.
    """
    # Match ASCII letters, digits, non-ASCII runs (ord > 127), and remaining symbols/punctuation
    pieces = re.findall(r"[a-zA-Z]+|[0-9]+|[^\x01-\x7f]+|[^\s]", text)
    count = 0
    for p in pieces:
        if p.isdigit():
            count += max(1, (len(p) + 1) // 2)
        elif p.isascii() and p.isalpha():
            count += max(1, (len(p) + 2) // 3)
        elif not p.isascii():
            count += max(1, len(p.encode("utf-8")) // 2)
        else:
            count += 1
    return int(count * 1.15)


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
        command["tech"],
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
        "can_siege",
        "can_unsiege",
        "can_cloak",
        "can_decloak",
        "can_stim",
        "can_patrol",
        "can_return_cargo",
        "can_burrow",
        "can_unburrow",
        "can_lift",
        "can_land",
        "can_unload_all",
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
                unit.get("training", False),
                unit.get("constructing", False),
                unit.get("can_move", False),
                unit.get("can_attack", False),
                unit.get("can_siege", False),
                unit.get("can_unsiege", False),
                unit.get("can_cloak", False),
                unit.get("can_decloak", False),
                unit.get("can_stim", False),
                unit.get("can_patrol", False),
                unit.get("can_return_cargo", False),
                unit.get("can_burrow", False),
                unit.get("can_unburrow", False),
                unit.get("can_lift", False),
                unit.get("can_land", False),
                unit.get("can_unload_all", False),
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


def compact_state(
    state: dict,
) -> dict:
    compact = deepcopy(state)
    compact.pop("choice_tree", None)
    if "observation" in compact:
        compact["observation"] = _observation(compact["observation"])
    if "candidate_actions" in compact:
        compact["candidate_actions"] = _actions(compact["candidate_actions"])
    return compact


def compact_request_payload(
    request: DecisionRequest,
    model: str,
    choice_tree: dict | None = None,
) -> dict:
    state = compact_state(request.state)
    payload = {
        "model": model,
        "state": {"schema": "jev/compact-v1", **state},
        "questions": {},
    }
    payload["state"]["legend"] = {
        "pos": "[x,y]",
        "commands": "[kind,unit_ids,unit_type,target_id,position,tile,tech]; positions use pos",
        "units": "[id,type,position,hit_points,completed,idle,training,constructing,can_move,can_attack,can_siege,can_unsiege,can_cloak,can_decloak,can_stim,can_patrol,can_return_cargo,can_burrow,can_unburrow,can_lift,can_land,can_unload_all]",
        "criteria": "criteria keys are valid option IDs; values contain natural language option descriptions",
    }
    for key, question in request.questions.items():
        data = question.model_dump()
        if "criteria" in data:
            data["criteria"] = dict(data["criteria"])
        payload["questions"][key] = data
    return payload
