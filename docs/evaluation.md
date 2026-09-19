# Evaluation protocol

## Scope and evidence

Synthetic fixtures exercise contracts and state transitions. They do not implement Brood War movement, combat, timings, map geometry or economics and cannot validate strategy strength. Never combine synthetic and live results.

For live comparisons, keep the same map **hash**, opponent build/configuration, starting-position schedule, BWAPI version, game speed, hardware, candidate generator, pruning rules, timeout and scheduling policy. Record the repository commit alongside results. `jev-latest` can change; use a pinned model identifier if the service provides one and retain the response's model field in each trace.

Use repeated paired games with balanced starting positions and several seeds. Report sample sizes, failures, confidence intervals and timeout rates alongside win rate. There is no automatic tournament runner in v0.1; `jevcraft report runs` lists completed summary files without pooling incomparable configurations.

The pruner uses the same heuristics for every model. Its cap is 50, not a minimum of 20: early-game states can have only a few actions. The rule baseline selects priorities directly; random samples uniformly at each hierarchy node. Those are distinct policies, not interchangeable control groups.

## Metric definitions

| Field | Definition / limitation |
| --- | --- |
| `result` | Final callback outcome; unknown for fixtures or interrupted runs. A missing summary is not a loss. |
| `game_seconds` | BWAPI frame / 24, a simulation-time convention, not wall time. |
| `provider_calls` | Attempted model/baseline invocations, including failed requests; singleton paths and cooldown skips make no call. |
| `mean_latency_ms`, `max_latency_ms` | Wall time spent awaiting each provider call and validating it, including timeouts. Does not include BWAPI capture, network bridge round-trip or command execution. |
| `mean_path_confidence` | Arithmetic mean of provider-reported confidence for chosen multi-option nodes; no synthetic confidence for rule/OpenAI/local providers. Not a win probability. |
| `estimated_api_cost_usd` | Configured input/output prices times reported usage. Null when unknown or any provider error occurred. Local compute cost is not estimated. |
| `known_usage_cost_usd` | Cost of recorded usage only; may exclude billed failed calls. |
| `command_apm` | Unit-command attempts at the executor × 60 / game seconds. This is harness command APM, not human input APM. |
| `effective_apm` | Accepted, non-duplicate unit commands × 60 / game seconds. Continuous duplicate orders are suppressed before issuance. |
| `resource_spend_fraction` | (spent minerals + spent gas) / (50 starting minerals + gathered minerals + gathered gas). Proxy for spending, not economic efficiency; minerals/gas weighted equally. |
| `unit_exchange_ratio` | Own cumulative credited kills / own deaths, including buildings and workers; null with no deaths. Not a value-weighted combat ratio. |

Frames, accepted command counts, resource totals and death/kill counters originate from the game bridge. Model selections are not treated as successful actions. Match summaries consume final cumulative counters, so repeated receipt windows cannot inflate APM.

## Live acceptance checklist

- Build the Windows Release DLL using the pinned BWAPI SDK; pass Python tests and CI.
- Start the rule provider and play the default Terran mirror scenario.
- Confirm idle workers gather, SCVs/Marines train, Depots/Barracks complete, and the scout moves.
- Confirm both squad and all-Marine attack/defend/retreat choices can execute.
- Check that an unseen enemy does not enter observations and a previously seen enemy remains only in dated memory.
- Pause/delay the Python service while a decision is pending: the game must keep running, old orders must continue, and expired responses must not execute.
- Kill a selected unit or spend its required resources during inference; native revalidation must reject the now-illegal command.
- End a game and start another: logs, cumulative counters, memory and random seed state must reset.
- Configure `TYPESAFE_API_KEY`, select Jev and verify real response distributions, latency, timeout rate and usage in logs. Keys must never appear in traces.
- Run paired games before drawing conclusions about Jev versus another provider.

These game-level checks require Windows and StarCraft and are **pending** until actually performed. Mock HTTP tests establish adapter shape, not live service compatibility or account access.

