from jevcraft.models import Action, DecisionEnvelope, Observation


def envelope(obs: Observation, action: Action, sequence: int, ttl_frames: int, reason=None):
    """Only server-generated actions cross the bridge; models never emit commands."""
    return DecisionEnvelope(
        match_id=obs.match_id,
        decision_id=f"{obs.match_id}_{sequence}",
        observed_frame=obs.frame,
        expires_frame=obs.frame + ttl_frames,
        action_id=action.id,
        commands=action.commands,
        fallback_reason=reason,
    )
