import hashlib
import math
import re

from minglecraft.models import Action, ChoiceAnswer, ChoiceQuestion, DecisionRequest, ProviderResult
from minglecraft.strategy.policy import POLICY_INSTRUCTIONS

MAX_CHOICES_PER_QUESTION = 200
BranchEntry = tuple[str, str, str, float]


class ChoiceTree:
    """Build a lossless action tree whose every Choice question stays below Jev's limit."""

    def __init__(
        self,
        state: dict,
        actions: list[Action],
        instructions: str = POLICY_INSTRUCTIONS,
        hierarchical: bool | None = None,
    ):
        if not actions:
            raise ValueError("ChoiceTree requires at least one action")
        if len({action.id for action in actions}) != len(actions):
            raise ValueError("ChoiceTree requires unique action IDs")

        self.actions = {action.id: action for action in actions}
        use_hierarchy = len(actions) > MAX_CHOICES_PER_QUESTION or hierarchical is True
        if use_hierarchy:
            self._init_hierarchical(state, actions, instructions)
        else:
            self._init_flat(state, actions, instructions)

    def _init_flat(self, state: dict, actions: list[Action], instructions: str) -> None:
        self.is_hierarchical = False
        self.nodes = {"action": {action.id: f"leaf:{action.id}" for action in actions}}
        self.priorities = {"action": {action.id: action.priority for action in actions}}
        questions = {}
        if len(actions) > 1:
            questions["action"] = ChoiceQuestion(
                instructions=instructions,
                criteria={action.id: action.label for action in actions},
            )
        self.request = DecisionRequest(
            state=dict(state),
            questions=questions,
            choice_tree={"action": dict(self.nodes["action"])},
            priorities={"action": dict(self.priorities["action"])} if questions else {},
        )

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug or "unknown"

    @classmethod
    def _actor_descriptor(cls, action: Action) -> tuple[str, str]:
        unit_ids = sorted({unit_id for command in action.commands for unit_id in command.unit_ids})
        if not unit_ids:
            return "global", "global actions"
        if len(unit_ids) == 1:
            unit_id = unit_ids[0]
            return f"unit_{unit_id}", f"unit {unit_id}"

        digest = hashlib.sha1(",".join(map(str, unit_ids)).encode()).hexdigest()[:8]
        group_name = cls._slug(action.group)
        return (
            f"group_{group_name}_{digest}",
            f"{action.group.replace('_', ' ')} ({len(unit_ids)} units)",
        )

    @staticmethod
    def _category_label(category: str) -> str:
        return category.replace("_", " ").capitalize()

    def _init_hierarchical(self, state: dict, actions: list[Action], instructions: str) -> None:
        """Build category -> actor/group -> executable command/action branches."""
        self.is_hierarchical = True
        self.nodes: dict[str, dict[str, str]] = {}
        self.priorities: dict[str, dict[str, float]] = {}
        questions: dict[str, ChoiceQuestion] = {}

        categories: dict[str, list[Action]] = {}
        for action in actions:
            categories.setdefault(action.category, []).append(action)

        category_entries: list[BranchEntry] = []
        for category_index, (category, category_actions) in enumerate(categories.items(), 1):
            actor_node = f"actors_{category_index}_{self._slug(category)}"
            actor_groups: dict[str, tuple[str, list[Action]]] = {}
            for action in category_actions:
                actor_key, actor_label = self._actor_descriptor(action)
                if actor_key not in actor_groups:
                    actor_groups[actor_key] = (actor_label, [])
                actor_groups[actor_key][1].append(action)

            actor_entries: list[BranchEntry] = []
            for actor_index, (actor_key, (actor_label, actor_actions)) in enumerate(
                actor_groups.items(), 1
            ):
                command_node = f"commands_{category_index}_{actor_index}_{self._slug(actor_key)}"
                command_entries = [
                    (action.id, action.label, f"leaf:{action.id}", action.priority)
                    for action in actor_actions
                ]
                self._add_partition_node(
                    command_node,
                    command_entries,
                    f"{instructions} Choose the executable command or target for the selected actor/group.",
                    "command",
                    questions,
                )
                actor_entries.append(
                    (
                        actor_key,
                        actor_label,
                        command_node,
                        max(action.priority for action in actor_actions),
                    )
                )

            self._add_partition_node(
                actor_node,
                actor_entries,
                f"{instructions} Choose the actual unit or unit group that should act.",
                "actor/group",
                questions,
            )
            category_entries.append(
                (
                    category,
                    self._category_label(category),
                    actor_node,
                    max(action.priority for action in category_actions),
                )
            )

        self._add_partition_node(
            "category",
            category_entries,
            f"{instructions} Choose the kind of action to take.",
            "category",
            questions,
        )
        self.request = DecisionRequest(
            state=dict(state),
            questions=questions,
            choice_tree={node: dict(options) for node, options in self.nodes.items()},
            priorities={node: dict(self.priorities[node]) for node in questions},
        )

    def _add_partition_node(
        self,
        node: str,
        entries: list[BranchEntry],
        instructions: str,
        level: str,
        questions: dict[str, ChoiceQuestion],
    ) -> None:
        if not entries:
            raise ValueError(f"Choice node {node} has no options")

        if len(entries) <= MAX_CHOICES_PER_QUESTION:
            self.nodes[node] = {key: child for key, _, child, _ in entries}
            self.priorities[node] = {key: priority for key, _, _, priority in entries}
            if len(entries) > 1:
                questions[node] = ChoiceQuestion(
                    instructions=instructions,
                    criteria={key: label for key, label, _, _ in entries},
                )
            return

        bucket_count = min(MAX_CHOICES_PER_QUESTION, len(entries))
        bucket_size = math.ceil(len(entries) / bucket_count)
        buckets = [
            entries[start : start + bucket_size] for start in range(0, len(entries), bucket_size)
        ]
        bucket_entries: list[BranchEntry] = []
        for bucket_index, bucket in enumerate(buckets, 1):
            child = f"{node}_bucket_{bucket_index}"
            bucket_entries.append(
                (
                    f"bucket_{bucket_index}",
                    f"{level.capitalize()} group {bucket_index}/{len(buckets)} ({len(bucket)} options)",
                    child,
                    max(priority for _, _, _, priority in bucket),
                )
            )
            self._add_partition_node(child, bucket, instructions, level, questions)

        self.nodes[node] = {key: child for key, _, child, _ in bucket_entries}
        self.priorities[node] = {key: priority for key, _, _, priority in bucket_entries}
        questions[node] = ChoiceQuestion(
            instructions=instructions,
            criteria={key: label for key, label, _, _ in bucket_entries},
        )

    def descendants(self, child: str) -> list[str]:
        if child.startswith("leaf:"):
            return [child[5:]]
        if child not in self.nodes:
            raise ValueError(f"Unknown choice node: {child}")
        return [
            leaf
            for next_child in self.nodes[child].values()
            for leaf in self.descendants(next_child)
        ]

    @staticmethod
    def _validate_answer(answer: ChoiceAnswer, question: ChoiceQuestion) -> None:
        if answer.type != "choice":
            raise ValueError("Expected a Choice answer")
        if answer.choice not in question.criteria:
            raise ValueError("Provider chose an option outside the finite choice set")
        if answer.probabilities is not None:
            probabilities = answer.probabilities
            if set(probabilities) != set(question.criteria):
                raise ValueError("Probability keys do not match the choice set")
            if any(
                not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()
            ):
                raise ValueError("Invalid probability")
            if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.01):
                raise ValueError("Probabilities must sum to one")

    def resolve(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        if not self.request.questions:
            default_action = self.actions.get("wait") or next(iter(self.actions.values()))
            root = "category" if self.is_hierarchical else "action"
            choice = next(iter(self.nodes[root]))
            return default_action, [{"node": root, "choice": choice, "answer": None}]

        if set(result.answers) != set(self.request.questions):
            raise ValueError("Provider answer IDs do not match the request")
        for node, question in self.request.questions.items():
            self._validate_answer(result.answers[node], question)

        root = "category" if self.is_hierarchical else "action"
        node = root
        path: list[dict] = []
        while True:
            options = self.nodes[node]
            if node in self.request.questions:
                answer = result.answers[node]
                choice = answer.choice
                path.append({"node": node, "choice": choice, "answer": answer.model_dump()})
            else:
                if len(options) != 1:
                    raise ValueError(f"Choice node {node} has no answer")
                choice = next(iter(options))
                path.append({"node": node, "choice": choice, "answer": None})

            child = options[choice]
            if not child.startswith("leaf:"):
                node = child
                continue
            action_id = child[5:]
            try:
                return self.actions[action_id], path
            except KeyError as exc:
                raise ValueError(f"Unknown leaf action: {action_id}") from exc
