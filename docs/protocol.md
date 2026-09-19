# Bridge protocol v1

The Python service listens only on `127.0.0.1:8765`. The native worker sends JSON over HTTP. `GET /health` returns the protocol version and configured map. No API credentials are sent to or stored in the game module. StarCraft and the BWAPI module run on Windows; the Python service can run in Windows Python or WSL2. With WSL2's default localhost forwarding enabled, the Windows BWAPI process can reach the WSL service through the same loopback endpoint. Windows 11 22H2+ mirrored networking is an alternative when NAT forwarding or a VPN causes problems.

## Observation

`POST /step` accepts the [generated schema](observation.schema.json); see [an example](../examples/observation.json).

- `match_id` is unique per game. Reusing an existing log directory is rejected rather than overwriting a result.
- `frame` advances strictly within a match. There is one active match per server and one request in flight.
- `map_name` must match the service configuration. `map_hash` must remain unchanged within the match.
- Coordinates in `position` are **pixels**; construction `tile` coordinates are **32-pixel build tiles**.
- `supply_used` and `supply_total` are human supply (BWAPI values divided by two).
- `units` includes owned units and capability lists computed by BWAPI (`canTrain`, `canBuild`, `canGather`). Python also checks resources, supply, completion and producer state.
- `enemies` includes only currently visible units. Python filters on `visible` again. Start locations are public map hypotheses, not known enemy bases.
- Last-seen enemy positions expire after 2,880 frames, remain explicitly dated, and are removed on an observed death. Memory resets per game. The initial generator targets visible enemies and public starts; remembered positions inform the provider but are not independently generated attack targets yet.
- `complete_map_information: true`, non-Terran races, duplicate entity IDs and extra top-level fields are rejected.
- `counters` are cumulative per match. `receipts` is the last 64 execution results, intentionally repeated until later snapshots. Consumers must deduplicate receipts by `decision_id`.
- The model-facing history is cumulative per match and resets at match boundaries. It records timestamped observations with hidden enemies filtered, issued actions, deduplicated command receipts, prior value estimates and the final match event. Observation deltas carry forward unchanged fields; a replaced list replaces the prior list.

The game module never enables complete-map information, user control or other cheating flags. It checks the non-cheating condition at match start and while playing. Terrain knowledge is public; hidden enemy unit properties are not.

## Decision

A successful response contains:

```json
{
  "protocol_version": 1,
  "match_id": "example_match",
  "decision_id": "example_match_1",
  "observed_frame": 24,
  "expires_frame": 48,
  "action_id": "train_1_Terran_SCV",
  "commands": [
    {"kind": "train", "unit_ids": [1], "unit_type": "Terran_SCV",
     "target_id": null, "position": null, "tile": null}
  ],
  "fallback_reason": null
}
```

Supported commands: `train`, `build`, `gather`, `attack` (attack-move), `move`. A `wait` has no commands. One macro action may apply to multiple units, but a response contains at most one command object in v1. The action generator, not the provider, owns command arguments.

Before executing on the game thread, the native module checks match ID, observed frame, expiry, monotonicity, owned/live/completed units, permitted unit types, current BWAPI legality and duplicate continuous orders. Commands may partially succeed if members of a squad disappear. Receipts retain attempted/accepted/effective unit-command counts and a reason; the reason describes the last encountered rejection/suppression and is not an atomic squad transaction.

All BWAPI calls occur on the game thread. Only serialized JSON enters the background network task. HTTP requests are bounded and responses are limited to 2 MB. Connection failure retains existing game orders and delays the next request by 48 frames.

## End and failure semantics

`POST /end` carries a final observation with `ended: true` and `result: win|loss|draw|unknown`. It flushes the summary. The native `onEnd(bool)` callback maps to win/loss; external runners should distinguish aborts/draws before comparing results. An in-flight decision at match end is not executed.

Shutting down the Python service cleanly while a match is active produces an incomplete summary with unknown outcome. A crashed/disconnected game can leave a trace without a summary; do not count it as a loss. Restart the service for an abandoned active match.

For Jev, an eligible decision consists of two sequential provider requests: one Noul `win_probability` request and one Choice policy request. The policy request receives the fresh value estimate. A shared per-step deadline covers both requests; partial value success is retained in the trace if policy fails. The same Jev model performs both roles.

Provider requests have no in-call retries. Timeout, invalid options/probabilities, rate limits, cumulative request-size guard failures and other provider failures select wait and start a two-second wall-clock cooldown. Every fallback is logged. A request-size guard must reject before sending an oversized cumulative history; live HTTP errors return 400/409/413/415/500 and never issue a command.

Changing the shared provider deadline does not change bridge transport timeouts. Keep it under one second for this initial bridge or update both configurations together. Two sequential calls and full history make the six-frame, 2–4 decisions/second cadence a target only. Receipts report command acceptance/rejection, not completion; later observations are required to infer effects.
