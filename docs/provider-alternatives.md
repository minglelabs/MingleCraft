# Provider alternatives for realtime finite-action decisions

Research date: 2026-09-21. This is a provider-selection note, not a provider switch. Model IDs below are exact IDs shown in current provider model or pricing documentation on the research date; announcement names and retired aliases are excluded.

## MingleCraft baseline and decision target

The current `src/minglecraft/agents/providers.py` already has an `OpenAIProvider` that sends one `/chat/completions` request, accepts a configurable `base_url` when instantiated programmatically, and builds a strict JSON Schema whose string enums come from each question's current `criteria`. This is the lowest-risk switching path.

The latest post-history-removal live trace `bwapi_29908_1789891637947956` reported a successful value response at frame 2199 with `usage.input_tokens=32306` and a policy response with `400`. The input-token count confirms that the real context limit is materially binding; byte size alone is not an adequate test. Across 19 successful steps, the local measurements were:

| Metric | Measured value |
| --- | ---: |
| Value median | 1,437 ms |
| Policy median | 1,471 ms |
| Serial total median | 2,953 ms |
| Serial total maximum | 3,125 ms |
| Maximum actions in a step | 218 |

The practical target is one successful decision request per step. A provider's tokens-per-second figure is not an end-to-end latency result: request upload, input prefill, queueing, schema-constrained decoding, network RTT, and JSON validation all remain in the path. No provider documentation found here publishes a MingleCraft-equivalent first-success latency, so latency must be measured with the real 32K-token class payload and up to 218 dynamic action enums.

## Ranked candidates

| Rank | Exact callable model ID | Context / output limit | Structured finite enums | Published price per 1M tokens | Latency evidence and caveat | Switching scope |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `gpt-5-nano` | 400,000 context; 128,000 max output | Current model page lists Structured Outputs; the existing adapter's dynamic string `enum` schema is the intended shape | Input $0.05; cached input $0.005; output $0.40 | OpenAI calls it the fastest, cheapest GPT-5 version and positions it for classification. That is the closest documented workload match, but no public E2E decision latency is supplied. | Low in code: existing Chat Completions adapter and model string; account/model behavior still needs a live probe. |
| 2 | `gpt-5.6-luna` | 1,050,000 context; 128,000 max output | Current catalog lists Structured Outputs; `reasoning.effort=none` is supported by the model page, but the current adapter does not expose that parameter | Input $0.20; cached input $0.02; output $1.20 | Current catalog describes it as cost-sensitive, high-volume, and supports no-reasoning mode. It is a stronger current-family candidate, but using `none` requires a later adapter parameter change and no public E2E decision latency is supplied. | Low model-protocol scope, but not a CLI-only switch if `reasoning.effort=none` is required. |
| 3 | `gemini-3.1-flash-lite` | 1,048,576 input; 65,536 output | Current stable model page lists Structured Outputs; Google's JSON Schema subset explicitly supports string `enum` | Input $0.25; output $1.50 | Google describes it as low-latency and cost-efficient, but that is product positioning, not a MingleCraft E2E number. It is the current GA choice over 2.5 Flash-Lite when model generation currency matters. | Medium: add a Gemini adapter or endpoint-specific request/response mapping. |
| Control | `gpt-4.1-mini` | 1,047,576 context; 32,768 max output | Strict Structured Outputs; the existing adapter already emits per-question dynamic `enum` values | Input $0.40; cached input $0.10; output $1.60 | It remains the cleanest explicitly non-reasoning baseline and is useful for isolating model-generation effects from integration effects. | Lowest-risk baseline: existing adapter and model string. |
| Latency control | `openai/gpt-oss-20b` on Groq | 131,072 context; 65,536 max completion | Groq strict mode supports this exact model and guarantees schema-constrained output; strict mode does not support streaming or tool use | Input $0.075; output $0.30 | Groq publishes about 1,000 tokens/s for this model. That is decode throughput only; 32K input prefill, queueing, rate limits, and schema decoding still need measurement. | Low in code: the API is OpenAI-compatible, but the current CLI does not expose its base URL for `--provider openai`. |

## Recommendation

The revised first candidate is `gpt-5-nano` with one policy call. It is the current catalog's fastest/cheapest GPT-5 tier, its 400K context comfortably exceeds the observed 32,306 input tokens, and its documented workload fit is classification. `gpt-5.6-luna` is the higher-capability current-family follow-up; use it only after exposing and setting `reasoning.effort=none` if the latency objective requires that mode. `gpt-4.1-mini` remains the control because it is explicitly non-reasoning and already matches the adapter's tested strict-schema path. None of these documented properties proves lower MingleCraft E2E latency.

Run `gemini-3.1-flash-lite` as the current Google alternative and `openai/gpt-oss-20b` on Groq as the throughput-focused control. Gemini 2.5 Flash-Lite is still callable and materially cheaper ($0.10/$0.40), but it is an older generation; choose it only if the cost advantage survives the real decision-quality and latency test. The `gemini-3.1-flash-lite-preview` name is not a candidate: Google says that preview was shut down and replaced by the stable `gemini-3.1-flash-lite` ID.

### Programmatic adapter versus CLI command

`OpenAIProvider` accepts `base_url` in Python, so Groq or another OpenAI-compatible service is programmatically possible. The CLI mapping in `src/minglecraft/cli.py` is narrower: `--base-url` defaults to `http://127.0.0.1:8000/v1`, and `provider_for()` passes it only to `LocalModelProvider`. For `--provider openai`, the CLI constructs `OpenAIProvider(api_key, args.model)` and therefore uses its default `https://api.openai.com/v1`. A Groq switch currently needs an adapter/configuration change or a programmatic caller; changing only `--model` on the existing `serve` command is insufficient.

Do not preserve Jev's serial value/policy shape for these providers unless the value is required for a separate product metric. The fastest fair comparison is one request containing the finite-action policy decision. If `win_probability` is still required, put it beside the selected action in the same response schema; that is a schema/parser change, not a reason to issue a second sequential request. The current adapter returns only `ChoiceAnswer` values, so this combined response would require a later adapter/model-contract change and is deliberately outside this research-only edit.

## Source and availability notes

All links were checked on 2026-09-21.

- [OpenAI GPT-4.1 mini model card](https://developers.openai.com/api/docs/models/gpt-4.1-mini): exact ID, 1,047,576 context, 32,768 output, Structured Outputs, and standard pricing.
- [OpenAI GPT-5 nano model card](https://developers.openai.com/api/docs/models/gpt-5-nano): current exact ID, 400,000 context, 128,000 output, Structured Outputs, and current pricing.
- [OpenAI GPT-5.6 Luna model card](https://developers.openai.com/api/docs/models/gpt-5.6-luna): current exact ID, 1,050,000 context, reasoning controls including `none`, Structured Outputs, and current pricing.
- [OpenAI GPT-5.4 Mini model card](https://developers.openai.com/api/docs/models/gpt-5.4-mini): newer mini-family comparison point; 400K context, Structured Outputs, reasoning support, and higher documented price.
- [OpenAI GPT-5.4 nano model card](https://developers.openai.com/api/docs/models/gpt-5.4-nano): newer nano-family comparison point; 400K context, Structured Outputs, and lower documented price, but still a reasoning-capable family.
- [OpenAI model catalog](https://developers.openai.com/api/docs/models): current catalog and deprecated-model list; `gpt-4.1-nano` and older GPT-5 snapshots are not treated as the primary recommendation.
- [OpenAI Fast Mode](https://openai.com/api-fast-mode/): separate throughput/latency service claims; not used as an E2E MingleCraft estimate.
- [Google Gemini 2.5 Flash-Lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite): exact ID, limits, capabilities, and low-latency positioning.
- [Google Gemini 3.1 Flash-Lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite): stable exact ID, limits, Structured Outputs, and low-latency positioning.
- [Google Gemini model catalog](https://ai.google.dev/gemini-api/docs/models): current callable model list and lifecycle status.
- [Google Gemini changelog](https://ai.google.dev/gemini-api/docs/changelog): `gemini-3.1-flash-lite-preview` shutdown and stable-ID replacement.
- [Google Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing): current standard, cached, batch, flex, and priority prices.
- [Google structured outputs](https://ai.google.dev/gemini-api/docs/structured-output): supported JSON Schema subset, `enum`, streaming behavior, and schema limitations.
- [Mistral Small 4 model card](https://docs.mistral.ai/models/mistral-small-4-0-26-03): exact ID, 256K context, structured outputs, and model price.
- [Mistral pricing](https://docs.mistral.ai/inference/pricing): current input, cached-input, and output prices.
- [Mistral structured outputs](https://docs.mistral.ai/studio/conversations/structured-output): custom schema and JSON modes.
- [Mistral known limitations](https://docs.mistral.ai/resources/known-limitations): context accounting and request-limit caveats.
- [Groq supported models](https://console.groq.com/docs/models): exact `openai/gpt-oss-20b` ID, context, max completion, price, and published decode speed.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): strict model list, schema guarantees, and unsupported streaming/tool-use caveat.
- [Groq rate limits](https://console.groq.com/docs/rate-limits): organization-level RPM/TPM behavior and 429 handling.
- Local implementation references: [`src/minglecraft/agents/providers.py`](../src/minglecraft/agents/providers.py) for programmatic `base_url`, and [`src/minglecraft/cli.py`](../src/minglecraft/cli.py) for the `local`-only CLI `--base-url` mapping.

The exact callable IDs above are the IDs in current model documentation. Names such as `gemini-3.1-flash-lite-preview`, `gpt-4.1-nano-2025-04-14`, and the deprecated `gpt-5-mini-2025-08-07` snapshot are explicitly excluded. Current aliases still require an account-level live request before switching; this research did not use credentials or claim provider access.
