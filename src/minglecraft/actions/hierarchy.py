import hashlib
import math
import re
from collections import Counter

from minglecraft.models import Action, ChoiceAnswer, ChoiceQuestion, DecisionRequest, ProviderResult
from minglecraft.strategy.policy import POLICY_INSTRUCTIONS

MAX_CHOICES_PER_QUESTION = 200
PROBABILITY_SUM_TOLERANCE = 0.02
BranchEntry = tuple[str, str, str, float]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def _extract_units_map(state: dict) -> dict[int, str]:
    units_map: dict[int, str] = {}
    if "observation" in state and isinstance(state["observation"], dict):
        obs = state["observation"]
        if "units" in obs:
            units = obs["units"]
            if isinstance(units, list):
                for u in units:
                    if isinstance(u, dict) and "id" in u and "type" in u:
                        units_map[u["id"]] = u["type"]
            elif isinstance(units, dict) and "columns" in units and "rows" in units:
                cols = units["columns"]
                if "id" in cols and "type" in cols:
                    id_idx = cols.index("id")
                    type_idx = cols.index("type")
                    for row in units["rows"]:
                        units_map[row[id_idx]] = row[type_idx]
    return units_map


def _action_sort_key(action: Action) -> tuple:
    if not action.commands:
        return ("wait", 0, "", "", 0, 0, action.id)
    cmd = action.commands[0]
    pos_x = cmd.position.x if cmd.position else (cmd.tile.x if cmd.tile else 0)
    pos_y = cmd.position.y if cmd.position else (cmd.tile.y if cmd.tile else 0)
    return (
        cmd.kind,
        cmd.target_id if cmd.target_id is not None else -1,
        cmd.unit_type or "",
        cmd.tech or "",
        pos_x,
        pos_y,
        action.id,
    )


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

        # Deterministic mechanical sorting independent of priority
        sorted_actions = sorted(actions, key=_action_sort_key)
        self.actions = {action.id: action for action in sorted_actions}
        self.units_map = _extract_units_map(state)
        use_hierarchy = len(actions) > MAX_CHOICES_PER_QUESTION or hierarchical is True
        if use_hierarchy:
            self._init_hierarchical(state, sorted_actions, instructions)
        else:
            self._init_flat(state, sorted_actions, instructions)

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

    def _action_command_kind(self, action: Action) -> str:
        if not action.commands:
            return "wait"
        return action.commands[0].kind

    def _actor_descriptor(self, action: Action) -> tuple[str, str]:
        unit_ids = sorted({unit_id for command in action.commands for unit_id in command.unit_ids})
        if not unit_ids:
            return "global", "global / system orders"
        if len(unit_ids) == 1:
            unit_id = unit_ids[0]
            unit_type = self.units_map.get(unit_id) or "unit"
            return f"unit_{unit_id}", f"{unit_type} #{unit_id}"

        digest = hashlib.sha1(",".join(map(str, unit_ids)).encode()).hexdigest()[:8]
        group_name = _slug(action.group)
        type_counts = Counter(self.units_map.get(uid, "unit") for uid in unit_ids)
        comp_parts = [f"{count} {t}" for t, count in sorted(type_counts.items())]
        comp_summary = ", ".join(comp_parts[:4])
        if len(comp_parts) > 4:
            comp_summary += f", +{len(comp_parts)-4} types"
        clean_group = action.group.replace("_", " ")
        return (
            f"group_{group_name}_{digest}",
            f"{clean_group} (units={','.join(map(str, unit_ids))}; {len(unit_ids)} units: {comp_summary})",
        )

    @staticmethod
    def _kind_label(kind: str) -> str:
        return kind.replace("_", " ").capitalize()

    def _kind_descriptor(self, kind: str, actions: list[Action], actor_count: int) -> str:
        parameter_kinds = sorted(
            {
                parameter
                for action in actions
                for command in action.commands[:1]
                for parameter, value in (
                    ("target", command.target_id),
                    ("unit_type", command.unit_type),
                    ("tech", command.tech),
                    ("position", command.position or command.tile),
                )
                if value is not None
            }
        )
        suffix = ", ".join(parameter_kinds) or "no parameters"
        return f"{self._kind_label(kind)} ({len(actions)} actions, {actor_count} actors; parameters: {suffix})"

    def _init_hierarchical(self, state: dict, actions: list[Action], instructions: str) -> None:
        """Build Command.kind -> actor unit/group -> concrete action branches."""
        self.is_hierarchical = True
        self.nodes: dict[str, dict[str, str]] = {}
        self.priorities: dict[str, dict[str, float]] = {}
        questions: dict[str, ChoiceQuestion] = {}

        # Group by command kind deterministically
        kinds: dict[str, list[Action]] = {}
        for action in actions:
            kind = self._action_command_kind(action)
            kinds.setdefault(kind, []).append(action)

        sorted_kinds = sorted(kinds.keys())

        kind_entries: list[BranchEntry] = []
        for kind_index, kind in enumerate(sorted_kinds, 1):
            kind_actions = kinds[kind]
            actor_node = f"actors_{kind_index}_{_slug(kind)}"
            actor_groups: dict[str, tuple[str, list[Action]]] = {}
            for action in kind_actions:
                actor_key, actor_label = self._actor_descriptor(action)
                if actor_key not in actor_groups:
                    actor_groups[actor_key] = (actor_label, [])
                actor_groups[actor_key][1].append(action)

            sorted_actor_keys = sorted(actor_groups.keys())
            actor_entries: list[BranchEntry] = []
            for actor_index, actor_key in enumerate(sorted_actor_keys, 1):
                actor_label, actor_actions = actor_groups[actor_key]
                command_node = f"commands_{kind_index}_{actor_index}_{_slug(actor_key)}"

                # Sort strictly by mechanical key, independent of priority
                actor_actions.sort(key=_action_sort_key)
                command_entries = [
                    (action.id, action.label, f"leaf:{action.id}", action.priority)
                    for action in actor_actions
                ]
                self._add_partition_node(
                    command_node,
                    command_entries,
                    f"{instructions} For command kind '{kind}' and actor '{actor_label}', "
                    "choose the executable target or parameter.",
                    "action",
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
                f"{instructions} For command kind '{kind}', choose the acting unit or unit group.",
                "actor",
                questions,
            )
            kind_entries.append(
                (
                    kind,
                    self._kind_descriptor(kind, kind_actions, len(actor_groups)),
                    actor_node,
                    max(action.priority for action in kind_actions),
                )
            )

        self._add_partition_node(
            "command_kind",
            kind_entries,
            f"{instructions} Choose the kind of command to issue.",
            "kind",
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

        bucket_count = min(MAX_CHOICES_PER_QUESTION, math.ceil(len(entries) / MAX_CHOICES_PER_QUESTION))
        bucket_size = math.ceil(len(entries) / bucket_count)
        buckets = [
            entries[start : start + bucket_size] for start in range(0, len(entries), bucket_size)
        ]
        bucket_entries: list[BranchEntry] = []
        for bucket_index, bucket in enumerate(buckets, 1):
            child = f"{node}_partition_{bucket_index}"
            first_label = bucket[0][1]
            last_label = bucket[-1][1]
            partition_label = (
                f"{level.capitalize()} range [{first_label} ... {last_label}] ({len(bucket)} options)"
            )
            bucket_entries.append(
                (
                    f"partition_{bucket_index}",
                    partition_label,
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
            if not math.isclose(
                sum(probabilities.values()),
                1,
                rel_tol=0.0,
                abs_tol=PROBABILITY_SUM_TOLERANCE,
            ):
                raise ValueError("Probabilities must sum to one")

    def resolve(self, result: ProviderResult) -> tuple[Action, list[dict]]:
        if not self.request.questions:
            default_action = self.actions.get("wait") or next(iter(self.actions.values()))
            root = "command_kind" if self.is_hierarchical else "action"
            choice = next(iter(self.nodes[root]))
            return default_action, [{"node": root, "choice": choice, "answer": None}]

        root = "command_kind" if self.is_hierarchical else "action"
        node = root
        path: list[dict] = []
        while True:
            options = self.nodes[node]
            if node in self.request.questions:
                if node not in result.answers:
                    raise ValueError(f"Provider answer IDs do not match request: missing visited node {node}")
                question = self.request.questions[node]
                answer = result.answers[node]
                # Lazily validate ONLY the visited node's answer
                self._validate_answer(answer, question)
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
