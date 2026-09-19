from collections import defaultdict

from jevcraft.models import Action


def prune(actions: list[Action], limit: int = 50) -> list[Action]:
    """Round-robin categories preserve diversity and always retain wait."""
    if not 8 <= limit <= 50:
        raise ValueError("Candidate limit must be between 8 and 50")
    groups = defaultdict(list)
    for action in sorted(actions, key=lambda a: (-a.priority, a.id)):
        if action.id != "wait":
            groups[action.category].append(action)
    result = [next(a for a in actions if a.id == "wait")]
    while len(result) < limit and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(result) < limit:
                result.append(groups[key].pop(0))
    return result
