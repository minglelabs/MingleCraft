"""Minimal Jev policy for StarCraft: Brood War.

Jev already knows StarCraft deeply. Only tell it the format.
"""

JEV_ENGLISH_POLICY = (
    "You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher). "
    "Wire format: state uses 'jev/compact-v1'. state.candidate_actions contains all executable options with "
    "columns [id, category, group, label, commands]. Each criteria key is an action id mapped to leaf:action_id. "
    "Pick the single best action id from criteria to advance victory. Return only the requested Choice answer."
)
