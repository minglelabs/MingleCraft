# JevCraft

**A model-agnostic real-time decision harness for StarCraft: Brood War.**

JevCraft turns BWAPI observations into compact state and finite, legal action candidates. A interchangeable `DecisionProvider` selects an action; a native BWAPI module executes it. Jev is the primary integration, alongside rule-based, random, OpenAI and local-model providers.

The first milestone asks **whether a decision model can operate an RTS in real time**, not whether it can beat strong StarCraft bots.

```text
StarCraft → BWAPI → State Builder → Action Generator → DecisionProvider
               ↑                                          ↓
               └──── revalidate + execute ← finite choice ─┘
```

## Status

This is an initial v0.1 implementation. Python end-to-end tests use a deterministic **synthetic contract fixture**, not the StarCraft engine. The Windows bridge is built separately. Actual Brood War gameplay, live Jev responses, achievable decision frequency and competitive performance require the live acceptance procedure below; no win-rate or latency claims are made.

Implemented:

- Terran SCV/Marine production, mineral gathering, Supply Depot/Barracks construction, worker scouting, squad attack-move, defense and retreat.
- Resource/supply checks plus BWAPI capability checks; at most 50 candidates, with fewer when the state has fewer legal choices.
- Category → squad/producer group → concrete action. Speculative branch questions are batched in one Jev call.
- Game-frame scheduling: micro/economy 6 frames, production 24 frames, construction/scouting 72 frames.
- A non-blocking Windows BWAPI module with one inference in flight, response expiry, command revalidation and duplicate-order suppression.
- Fog-of-war filtering and timestamped last-seen memory, cleared between matches.
- Per-match JSONL traces, execution receipts, model usage and explicitly defined evaluation metrics.
- A provider-neutral interface, a CLI, a local HTTP service, tests and CI.

Not yet included: sophisticated combat micro/path planning, gas/advanced tech, automatic tournament orchestration, a map asset, a game installer, or validated live performance. The default live scenario is **1v1 Terran vs Terran on `(2)Destination.scx`**. Use the same map hash, opponent, starting-position policy and configuration when comparing models.

## Run without StarCraft or an API key

Requires Python 3.10+.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
jevcraft demo --provider rule --steps 240
jevcraft demo --provider random --seed 17 --steps 240
jevcraft report runs
pytest -q
```

Alternatively, use `uv sync --extra dev` and prefix commands with `uv run`.

Each demo produces `runs/demo_<id>/manifest.json`, `decisions.jsonl` and `summary.json`. Synthetic matches deliberately report `result: "unknown"` and `mode: "synthetic"`; they must not be presented as game performance.

## Providers

| Provider | Credentials | Decision output |
| --- | --- | --- |
| `rule` | None | Deterministic heuristic choices |
| `random` | None | Seeded uniform choices at each hierarchy node |
| `openrouter-jev` | `OPENROUTER_API_KEY` | Jev typed Choice answers through OpenRouter |
| `jev` | `TYPESAFE_API_KEY` | Direct TypeSafe Jev API; optional |
| `openai` | `OPENAI_API_KEY`, `--model` | Strict JSON-schema choices; no invented probabilities |
| `local` | `--model`, optional `--base-url` | OpenAI-compatible structured-output endpoint |

The default provider is **rule**. Selecting a remote provider explicitly enables billable calls.

```bash
# Recommended when you do not have a direct TypeSafe account:
export OPENROUTER_API_KEY=your-key
jevcraft serve --provider openrouter-jev --model '~typesafe/jev-latest' --deadline-ms 800

# Direct TypeSafe access is optional and requires its own account/key:
export TYPESAFE_API_KEY=your-key
jevcraft serve --provider jev --model jev-latest --deadline-ms 200

# Select a model that supports Chat Completions structured outputs:
jevcraft serve --provider openai --model YOUR_MODEL --deadline-ms 800

# A local model server must already be running and support JSON schemas:
jevcraft serve --provider local --model YOUR_MODEL --base-url http://127.0.0.1:8000/v1
```

The direct Jev adapter implements the [official TypeSafe HTTP contract](https://docs.typesafe.ai/api): `POST /v1/systemone`, structured `state`, `model`, and typed `questions`. The OpenRouter Jev adapter sends the same typed request to OpenRouter's Jev Decisions endpoint using `OPENROUTER_API_KEY` and the `~typesafe/jev-latest` model route. This repository does not require a direct TypeSafe signup when using OpenRouter. It currently uses Choice; Noul and Score are extension points rather than fabricated signals. See [Jev primitives](https://docs.typesafe.ai/primitives) and [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

All providers receive the same state and finite questions. Heuristic priorities are private to the rule baseline and pruner. Random is uniform **per hierarchy node**, not over all leaf actions. Direct Jev and OpenRouter Jev return model probabilities/confidence; rule, random, OpenAI and local providers do not invent calibrated confidence.

## Connect real StarCraft

The native BWAPI 4.4.0 module runs inside **32-bit StarCraft: Brood War 1.16.1 on Windows**. A modern macOS StarCraft installation is not a substitute. See the [official BWAPI setup](https://github.com/bwapi/bwapi/tree/v4.4.0).

1. Install your own licensed Brood War, patch 1.16.1, BWAPI 4.4.0 and Chaoslauncher. Install Visual Studio 2022 C++ desktop tools and CMake 3.24+.
2. Download the official [BWAPI 4.4.0 SDK archive](https://github.com/bwapi/bwapi/releases/tag/v4.4.0) and extract it. `Release_Binary` must contain `include/` and `BWAPILIB/`. JevCraft builds the SDK library from those sources if `lib/BWAPI.lib` is absent.
3. Build **Release, Win32** from the repository root:

   ```powershell
   cmake -S bwapi -B build/bwapi -A Win32 -DBWAPI_ROOT="C:/BWAPI/Release_Binary"
   cmake --build build/bwapi --config Release --parallel
   ```

4. Copy `build/bwapi/Release/JevCraft.dll` into `StarCraft/bwapi-data/AI/`. Point the `[ai]` `ai` setting in `bwapi-data/bwapi.ini` to `bwapi-data/AI/JevCraft.dll`.
5. Start `jevcraft serve --provider rule` on the Windows machine. The bridge uses `http://127.0.0.1:8765`; start the service before the match. To host Python elsewhere, forward that loopback port with an SSH tunnel. The service is intentionally not exposed to the network.
6. In Chaoslauncher, enable the BWAPI **Release** injector. Create a 1v1 Terran vs Terran match on `(2)Destination.scx`. Supply the map yourself; no Blizzard game/map files are distributed here. If your map has a different filename, pass the exact BWAPI filename with `--map`.
7. Inspect `runs/bwapi_<id>/` after the game. Confirm real execution receipts and `mode: "live"` before trying `--provider jev`.

The bridge sets 42 ms/frame, samples no faster than every 6 game frames, and permits only one request in flight. **2–4 decisions/second is a target**, not a guarantee: network inference, state construction and available choices affect actual throughput. Existing orders continue during inference. A 200 ms default deadline and a 24-frame expiry prevent late choices from silently acting on stale state. Provider errors cause a two-second cooldown and a logged `wait`, never an unreported baseline takeover.

## Evaluation and reproducibility

Every decision trace includes the observation, compressed state, candidate list, all questions/answers, selected path, timing, fallback reason and command envelope. Later observations carry native execution receipts. The manifest records provider/model, seed, map hash, scheduling, deadline, candidate limit and optional prices.

Summaries include outcome, game duration, API calls, average/maximum call latency, mean reported confidence on the selected path, usage, estimated cost, command APM/effective APM, resource spending and unit exchange ratio. Unknown values are `null`, not zero. Supply uses human units, not BWAPI's doubled internal units.

Pass `--input-price USD_PER_MILLION --output-price USD_PER_MILLION` to estimate remote cost from reported tokens. No provider prices are hard-coded; failed calls can incur unreported charges, so their total cost remains unknown. See [metric definitions and benchmark protocol](docs/evaluation.md).

## Layout

```text
bwapi/                  Windows C++ AIModule and asynchronous HTTP transport
src/jevcraft/
  bwapi/                Local service and synthetic fixture
  state/                Compact state and last-seen memory
  actions/              Generator, pruner, choice hierarchy and executor envelope
  agents/               DecisionProvider and model/baseline adapters
  strategy/             Multi-rate scheduler
  evaluation/           Trace logger and match metrics
  loop.py               Observation → decision → envelope
  cli.py                demo / serve / report / schema
examples/               Example observation and live BWAPI configuration
tests/                  Fog, legality, contracts, deadlines and HTTP integration
docs/                   Wire protocol, evaluation and live acceptance
```

Implement `DecisionProvider.decide(DecisionRequest) -> ProviderResult` to add another model. Models select option IDs; they never supply free-form executable commands. The [wire protocol](docs/protocol.md) separates portable Python logic from game-dependent execution.

## License

JevCraft code is MIT licensed. BWAPI is a separate LGPL dependency; nlohmann/json is MIT licensed. See [third-party notices](THIRD_PARTY_NOTICES.md). StarCraft and Brood War are Blizzard trademarks. This project is not affiliated with Blizzard or TypeSafe.
