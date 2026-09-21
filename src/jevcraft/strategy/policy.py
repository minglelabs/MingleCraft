"""User strategy and stage-specific Jev instructions."""

from jevcraft.strategy.prompt import JEV_ENGLISH_POLICY

SHARED_POLICY = JEV_ENGLISH_POLICY

VALUE_INSTRUCTIONS = """Will our player ultimately win this StarCraft: Brood War match,
conditional on the current observed state and continuing to select legal actions
under `strategy_policy`? Return the Noul probability of yes, not a positional
Score, action preference, or separate confidence. A draw counts as not winning.
Read `observation`, `candidate_actions`, and the complete chronological
`match_history`. History observations are top-level deltas: unchanged fields
carry forward; a replaced list replaces its previous contents. Each event has
frame and game_seconds. Visible sightings are facts only at their timestamp;
unseen enemy information remains unknown. An issued decision is only intent;
a command_receipt reports acceptance, not completion. Infer effects only from
subsequent observed changes. Do not treat missing observations as negative evidence.
Use the supplied strategy's strategic reference and factual uncertainty rules;
its Choice-only response instructions apply exclusively to the policy stage.
Consider economy, production, technology, army composition, terrain, current
threats, scouting age, and feasible continuation. Do not invent missing facts.
`latest_value` and history value_estimate events are previous model estimates,
not independent evidence or ground truth. Reassess from observations rather
than anchoring on those estimates; the current estimate does not exist yet.
This is an uncalibrated forecast until validated against actual match outcomes.
"""

POLICY_INSTRUCTIONS = (
    "You are playing StarCraft: Brood War v1.16.1 via BWAPI v4.4.0 (injected by Chaoslauncher). "
    "Wire format: state uses 'jev/compact-v1'. state.candidate_actions contains all executable options with "
    "columns [id, category, group, label, commands]. Each criteria key is an action id mapped to leaf:action_id. "
    "Pick the single best action id from criteria to advance victory. Return only the requested Choice answer."
)
