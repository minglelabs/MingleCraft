import math

from jevcraft.models import Action, ChoiceQuestion, DecisionRequest, ProviderResult
from jevcraft.strategy.policy import POLICY_INSTRUCTIONS


class ChoiceTree:
    """Flat single-question tree: one question with every candidate action."""

    def __init__(
        self,
        state: dict,
        actions: list[Action],
        instructions: str = POLICY_INSTRUCTIONS,
    ):
        self.actions = {a.id: a for a in actions}
        self.nodes = {"action": {a.id: f"leaf:{a.id}" for a in actions}}
        self.priorities = {"action": {a.id: a.priority for a in actions}}
        questions = {}
        if len(actions) > 1:
            criteria = {a.id: a.label for a in actions}
            questions["action"] = ChoiceQuestion(
                instructions=instructions,
                criteria=criteria,
            )
        self.request = DecisionRequest(
            state=dict(state),
            questions=questions,
            choice_tree={"action": {a.id: f"leaf:{a.id}" for a in actions}},
            priorities={"action": self.priorities["action"]} if questions else {},
        )

    def descendants(self, child: str) -> list[str]:
        if child.startswith("leaf:"):
            return [child[5:]]
        return [leaf for sub in self.nodes[child].values() for leaf in self.descendants(sub)]

    def resolve(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        if set(result.answers) != set(self.request.questions):
            raise ValueError("Provider answer IDs do not match the request")
        for node, answer in result.answers.items():
            if answer.type != "choice":
                raise ValueError("Expected a Choice answer")
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
        answer = result.answers.get("action")
        if answer:
            choice = answer.choice
            path = [{"node": "action", "choice": choice, "answer": answer.model_dump()}]
        else:
            choice = next(iter(self.nodes["action"]))
            path = [{"node": "action", "choice": choice, "answer": None}]
        return self.actions[choice], path
