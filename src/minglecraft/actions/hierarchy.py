import math

from minglecraft.models import Action, ChoiceQuestion, DecisionRequest, ProviderResult
from minglecraft.strategy.policy import POLICY_INSTRUCTIONS

MAX_CHOICES_PER_QUESTION = 200


class ChoiceTree:
    """Hierarchical or flat choice tree guaranteeing at most 200 choices per question."""

    def __init__(
        self,
        state: dict,
        actions: list[Action],
        instructions: str = POLICY_INSTRUCTIONS,
    ):
        self.actions = {a.id: a for a in actions}
        if len(actions) <= MAX_CHOICES_PER_QUESTION:
            self._init_flat(state, actions, instructions)
        else:
            self._init_hierarchical(state, actions, instructions)

    def _init_flat(
        self,
        state: dict,
        actions: list[Action],
        instructions: str,
    ) -> None:
        self.is_hierarchical = False
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

    def _init_hierarchical(
        self,
        state: dict,
        actions: list[Action],
        instructions: str,
    ) -> None:
        self.is_hierarchical = True
        groups: dict[str, list[Action]] = {}
        for action in actions:
            groups.setdefault(action.category, []).append(action)

        final_subgroups: dict[str, list[Action]] = {}
        for cat, cat_actions in groups.items():
            if len(cat_actions) <= MAX_CHOICES_PER_QUESTION:
                final_subgroups[cat] = cat_actions
            else:
                for i in range(0, len(cat_actions), MAX_CHOICES_PER_QUESTION):
                    chunk_id = f"{cat}_{i // MAX_CHOICES_PER_QUESTION + 1}"
                    final_subgroups[chunk_id] = cat_actions[i : i + MAX_CHOICES_PER_QUESTION]

        self.nodes = {}
        self.priorities = {}
        questions = {}

        root_criteria = {}
        root_mapping = {}
        root_priorities = {}
        for sub_id, sub_actions in final_subgroups.items():
            node_name = f"action_{sub_id}"
            label = f"{sub_id.replace('_', ' ').capitalize()}: {len(sub_actions)} candidate actions"
            root_criteria[sub_id] = label
            root_mapping[sub_id] = f"node:{node_name}"
            root_priorities[sub_id] = max((a.priority for a in sub_actions), default=0.0)

            child_criteria = {a.id: a.label for a in sub_actions}
            self.nodes[node_name] = {a.id: f"leaf:{a.id}" for a in sub_actions}
            self.priorities[node_name] = {a.id: a.priority for a in sub_actions}
            questions[node_name] = ChoiceQuestion(
                instructions=f"{instructions} Choose a specific action within {sub_id}.",
                criteria=child_criteria,
            )

        self.nodes["category"] = root_mapping
        self.priorities["category"] = root_priorities
        questions["category"] = ChoiceQuestion(
            instructions=f"{instructions} Choose which high-level category of action to take.",
            criteria=root_criteria,
        )

        self.request = DecisionRequest(
            state=dict(state),
            questions=questions,
            choice_tree=dict(self.nodes),
            priorities=dict(self.priorities),
        )

    def descendants(self, child: str) -> list[str]:
        if child.startswith("leaf:"):
            return [child[5:]]
        return [leaf for sub in self.nodes[child].values() for leaf in self.descendants(sub)]

    def resolve(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        if not result.answers:
            default_choice = self.actions.get("wait") or next(iter(self.actions.values()))
            node_name = "action" if not getattr(self, "is_hierarchical", False) else "category"
            return default_choice, [
                {"node": node_name, "choice": default_choice.id, "answer": None}
            ]

        if getattr(self, "is_hierarchical", False):
            return self._resolve_hierarchical(result)
        return self._resolve_flat(result)

    def _resolve_flat(self, result: ProviderResult) -> tuple[Action, list[dict]]:
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

    def _resolve_hierarchical(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        for node, answer in result.answers.items():
            if node not in self.request.questions:
                continue
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

        root_answer = result.answers.get("category")
        if not root_answer:
            default_action = self.actions.get("wait") or next(iter(self.actions.values()))
            return default_action, [{"node": "category", "choice": "wait", "answer": None}]

        chosen_sub = root_answer.choice
        child_node = f"action_{chosen_sub}"
        child_answer = result.answers.get(child_node)
        if child_answer:
            chosen_action_id = child_answer.choice
            path = [
                {"node": "category", "choice": chosen_sub, "answer": root_answer.model_dump()},
                {
                    "node": child_node,
                    "choice": chosen_action_id,
                    "answer": child_answer.model_dump(),
                },
            ]
            return self.actions[chosen_action_id], path

        fallback_id = next(iter(self.nodes[child_node]))
        return self.actions[fallback_id], [
            {"node": "category", "choice": chosen_sub, "answer": root_answer.model_dump()}
        ]
