import math
from collections import defaultdict

from jevcraft.models import Action, ChoiceQuestion, DecisionRequest, ProviderResult


class ChoiceTree:
    """Domain -> squad/producer group -> concrete action, batched in one call."""

    def __init__(self, state: dict, actions: list[Action]):
        self.actions = {a.id: a for a in actions}
        self.nodes: dict[str, dict[str, str]] = {}
        self.priorities: dict[str, dict[str, float]] = {}
        categories = defaultdict(lambda: defaultdict(list))
        for action in actions:
            categories[action.category][action.group].append(action)
        self.nodes["domain"] = {}
        self.priorities["domain"] = {}
        for category, groups in categories.items():
            domain = f"group_{category}"
            self.nodes["domain"][category] = domain
            self.priorities["domain"][category] = max(
                a.priority for g in groups.values() for a in g
            )
            self.nodes[domain] = {}
            self.priorities[domain] = {}
            for group, candidates in groups.items():
                node = f"action_{category}_{group}"
                self.nodes[domain][group] = node
                self.priorities[domain][group] = max(a.priority for a in candidates)
                self.nodes[node] = {a.id: f"leaf:{a.id}" for a in candidates}
                self.priorities[node] = {a.id: a.priority for a in candidates}
        questions = {}
        for node, options in self.nodes.items():
            if len(options) <= 1:
                continue
            criteria = {}
            for option, child in options.items():
                leaves = self.descendants(child)
                criteria[option] = "; ".join(self.actions[a].label for a in leaves)
            questions[node] = ChoiceQuestion(
                instructions=(
                    f"For the current Terran vs Terran state, choose one option at stage {node}. "
                    "Evaluate this stage assuming its parent has selected it. Sustain economy, "
                    "avoid supply blocks, preserve units and defeat the opponent. "
                    "Unseen enemy strength is unknown. Positions named start are only hypotheses."
                ),
                criteria=criteria,
            )
        self.request = DecisionRequest(
            state=state,
            questions=questions,
            priorities={k: self.priorities[k] for k in questions},
        )

    def descendants(self, child: str) -> list[str]:
        if child.startswith("leaf:"):
            return [child[5:]]
        return [leaf for sub in self.nodes[child].values() for leaf in self.descendants(sub)]

    def resolve(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        # Validate all reported answers, including speculative branches.
        if set(result.answers) != set(self.request.questions):
            raise ValueError("Provider answer IDs do not match the request")
        for node, answer in result.answers.items():
            options = self.request.questions[node].criteria
            if answer.choice not in options:
                raise ValueError("Provider chose an option outside the finite choice set")
            if answer.probabilities is not None:
                probabilities = answer.probabilities
                if set(probabilities) != set(options):
                    raise ValueError("Probability keys do not match the choice set")
                if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
                    raise ValueError("Invalid probability")
                if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.01):
                    raise ValueError("Probabilities must sum to one")
        node, path = "domain", []
        while not node.startswith("leaf:"):
            options = self.nodes[node]
            answer = result.answers.get(node)
            choice = answer.choice if answer else next(iter(options))
            path.append(
                {"node": node, "choice": choice, "answer": answer.model_dump() if answer else None}
            )
            node = options[choice]
        return self.actions[node[5:]], path
