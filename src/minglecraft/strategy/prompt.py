"""Minimal Jev policy for StarCraft: Brood War.

Jev already knows StarCraft deeply. Only tell it the format.
"""

JEV_ENGLISH_POLICY = (
    "You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher). "
    "Wire format: state uses 'jev/compact-v1'. state.candidate_actions contains all executable options with "
    "columns [id, category, group, label, commands]. Choice questions form a tree: choose the action kind, "
    "then the actual unit or unit group, then its executable command or target. Each criteria key is the exact "
    "option ID for that node. A criteria value is a reference such as node:question_id or leaf:action_id. "
    "All requested questions are evaluated independently in the same state; the program follows the selected "
    "references to execute one leaf action. Answer every requested Choice with an existing option ID."
)
