# Bridge protocol v1

The Python service listens only on `127.0.0.1:8765`. The native worker sends JSON over HTTP. `GET /health` returns the protocol version and configured map. No API credentials are sent to or stored in the game module. StarCraft and the BWAPI module run on Windows; the Python service can run in Windows Python or WSL2. With WSL2's default localhost forwarding enabled, the Windows BWAPI process can reach the WSL service through the same loopback endpoint. Windows 11 22H2+ mirrored networking is an alternative when NAT forwarding or a VPN causes problems.

## Observation

`POST /step` accepts the [generated schema](observation.schema.json); see [an example](../examples/observation.json).

- `match_id` is unique per game. Reusing an existing log directory is rejected rather than overwriting a result.
- `frame` advances strictly within a match. There is one active match per server and one request in flight.
- `map_name` must match the service configuration. `map_hash` must remain unchanged within the match.
- Coordinates in `position` are **pixels**; construction `tile` coordinates are **32-pixel build tiles**.
- `map_width` and `map_height` are the actual map pixel dimensions from BWAPI (`mapWidth()*32` and `mapHeight()*32`); Jev coordinate selection uses these bounds.
- `supply_used` and `supply_total` are human supply (BWAPI values divided by two).
- `units` includes owned units and capability lists computed by BWAPI (`canTrain`, `canBuild`, `canGather`, movement, combat, transport, ability and tech capabilities). Python also checks resources, supply, completion and producer state.
- `enemies` includes only currently visible units. Python filters on `visible` again. Start locations are public map hypotheses, not known enemy bases.
- Last-seen enemy positions expire after 2,880 frames, remain explicitly dated, and are removed on an observed death. Memory resets per game. Target attacks are generated for every currently visible enemy unit; remembered positions remain provider context until the enemy is visible again.
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
     "target_id": null, "tech": null, "position": null, "tile": null}
  ],
  "fallback_reason": null
}
```

Supported commands: `train`, `build`, `gather`, `attack` (unit target or attack-move position), `move`, `patrol`, `return_cargo`, `repair`, `stop`, `hold_position`, `siege`, `unsiege`, `cloak`, `decloak`, `burrow`, `unburrow`, `lift`, `land`, `load`, `unload`, `unload_all`, `use_tech`, `stim`. A `wait` has no commands. One macro action may apply to multiple units, but a response contains at most one command object in v1. The action generator, not the provider, owns command arguments.

Before executing on the game thread, the native module checks match ID, observed frame, expiry, monotonicity, owned/live/completed units, permitted unit types, current BWAPI legality and duplicate continuous orders. Commands may partially succeed if members of a squad disappear. Receipts retain attempted/accepted/effective unit-command counts and a reason; the reason describes the last encountered rejection/suppression and is not an atomic squad transaction.

All BWAPI calls occur on the game thread. Only serialized JSON enters the background network task. HTTP requests are bounded and responses are limited to 2 MB. Connection failure retains existing game orders and delays the next request by 48 frames.

## End and failure semantics

`POST /end` carries a final observation with `ended: true` and `result: win|loss|draw|unknown`. It flushes the summary. The native `onEnd(bool)` callback maps to win/loss; external runners should distinguish aborts/draws before comparing results. An in-flight decision at match end is not executed.

Shutting down the Python service cleanly while a match is active produces an incomplete summary with unknown outcome. A crashed/disconnected game can leave a trace without a summary; do not count it as a loss. Restart the service for an abandoned active match.

For staged Jev, an eligible decision consists of two sequential provider requests: one Noul `win_probability` request and one Choice policy request. The policy request receives the fresh value estimate. A spatial ground action then adds one sequential Choice request per coordinate level. Single-stage skips the value request but uses the same policy and coordinate route. A shared per-step deadline covers all requests; partial value success is retained in the trace if policy fails. The same Jev model performs all roles.

Provider requests have no in-call retries. Timeout, invalid options/probabilities, rate limits, cumulative request-size guard failures and other provider failures select wait and start a two-second wall-clock cooldown. Every fallback is logged. A request-size guard must reject before sending an oversized cumulative history; live HTTP errors return 400/409/413/415/500 and never issue a command.

Changing the shared provider deadline does not change bridge transport timeouts. The native HTTP send/receive timeouts are currently 15 seconds. Keep the total decision deadline below that budget and configure `--ttl-frames` to cover the expected elapsed game frames; the bridge rejects expired decisions. Receipts report command acceptance/rejection, not completion; later observations are required to infer effects.


### Spatial Ground Coordinate Selection and Limitations
- Ground `move` and attack-move actions resolve uniformly over the complete map: 8x8 regions, then sequential 8x8 refinements until the selected rectangle is at most the configured precision (CLI: `--spatial-precision-px`, default 8px). The command uses the selected rectangle center.
- Region and each refinement are separate Jev Choice requests. They share the step deadline and request-size guard; a timeout, invalid ID, malformed dimensions, or exhausted deadline yields `wait` and never executes a placeholder.
- The default 200ms deadline may be too short for all sequential questions; configure a larger deadline for this path. This is a total-deadline contract, not a latency benchmark.
- Building placement still uses BWAPI `getBuildLocation(type, homeTile, 24)`, so construction candidates remain limited to that native-radius query. Exhaustive Jev candidates do not include baseline aggregate or parity squads.

Example for initial live testing (not a measured latency guarantee):

```sh
minglecraft serve --provider jev --single-stage --map "(2)Destination.scx" --spatial-precision-px 8 --deadline-ms 10000 --ttl-frames 480
```

Rebuild and replace the Windows bridge DLL to provide actual map dimensions, then restart the Python service. Inspect receipts for stale decisions and adjust the budgets to the actual game speed and measured latency. A large TTL accepts older state; it does not make inference faster. Use `--spatial-precision-px 1` to refine down to individual pixels, at the cost of additional calls. Map coverage means every pixel belongs to a selectable cell, not that every pixel is selectable at the default precision. Terrain/pathing data is not added by this change.
