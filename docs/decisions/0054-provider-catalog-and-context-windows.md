# 0054. A provider catalog, keyless local endpoints and the context window the server loaded

Status: accepted (M12, 2026-11-06)
Date: 2026-11-06

## Context
Development is moving to a local model on a 6 GB GPU (owner, 2026-11-03), so CookCode has to talk to the OpenAI-compatible servers that run one: Ollama, llama.cpp, LM Studio and vLLM. Three things stood in the way. Presets were a 3-tuple per cloud endpoint, and every run demanded an API key. Local servers serve whatever model is loaded, so there is no default model name to put in a preset. And nothing in the harness knew the model's context window. `ModelCaps.context_window` existed, but no code read it, and the summarize trigger was a fixed 48,000 estimated tokens. A local model typically loads 4k to 32k of context, so a run under `raw` overflows and a run under `summarize` overflows before the trigger fires. Ollama does not even fail: it drops the front of the prompt and answers anyway.

A comparison with Sutra (`cneuralnetwork/sutra`) seeded the preset list and the keyless-local rule (`THIRD_PARTY.md`). Its handling of the window is the case this record argues against.

## Options considered
- **Read the window from the server's model metadata, whatever it is called.** Cost: every local server reports two numbers, and the obvious one is wrong. llama.cpp's `/v1/models` gives `meta.n_ctx_train` (131072 in its README example) while `/props` gives `n_ctx`, what `-c` actually loaded (1024 in the same README). LM Studio gives `max_context_length` 131072 next to `loaded_context_length` 4096. Ollama's `/api/show` gives the trained `<arch>.context_length` and does not expose the loaded `num_ctx` at all. Sizing by the trained number sets a 64k trigger on a 4k context.
- **A fixed default per local preset** (Sutra's approach, 32768 when the server reports nothing usable). Cost: right only when the server happened to be started with that size, and wrong in the direction that overflows when it was started smaller.
- **Always ask the user for the window.** Correct, and needless for three of the four local servers, which can tell us.
- **Check each request against the window before sending it.** Cost: the estimate is characters over four, which runs low on code. A hard stop on it would refuse requests that fit, and a warning would be noise.
- **Read only the effective window, fall back to configuration, record where it came from.** Cost: one probe per server type, each tied to an API that can change.

## Decision
The last option, in seven parts.

1. **Curated catalog, as data.** `providers/catalog.py:PRESETS` holds seven `Preset`s: `nim`, `openrouter` and `gemini` with their M11 values, and `ollama`, `llamacpp`, `lmstudio` and `vllm` on their default local ports. Other cloud endpoints are one entry each and are not added until one is used. `--base-url` on any OpenAI-protocol preset covers the rest.
2. **Keys only for remote endpoints.** `needs_key` is true when the preset names a key variable and the base URL's host is not `localhost`, `127.0.0.1`, `[::1]` or `0.0.0.0`. `--provider nim --base-url http://localhost:8000/v1` needs no key either.
3. **Effective window only.** `providers/discovery.py:discover` reads llama.cpp's `default_generation_settings.n_ctx`, LM Studio's `loaded_context_length`, vLLM's `max_model_len` and OpenRouter's `context_length`, and never `n_ctx_train`, `max_context_length` or `model_info`. A test serves both numbers and fails if the trained one is used. Discovery never raises. Whatever it cannot learn comes back as `problem`.
4. **Precedence:** `--context-window`, then `context_window` in settings (scoped to its provider, like `model`), then the server, then the preset. Local presets default to 4096, on purpose: a window set too low only summarizes early, a window set too high overflows. The source is recorded as `flag`, `settings`, `server` or `preset`.
5. **The window sizes the trigger only when the user gave none.** `Strategy.for_window` sets `trigger_tokens = min(48_000, window // 2)` for `summarize` and `hybrid` when the strategy string has no `t`. Half the window leaves room for the reply and for the estimate running low. The cap keeps every cloud run on the trigger M6 to M10 measured.
6. **The model is picked when one is served.** A local preset has no default model. If the server lists exactly one, it is used. Several, or none, end startup with a message naming the URL and the models.
7. **Local presets wait 600 s for a chunk** instead of 180, because prefill of a long prompt with partial CPU offload can take minutes before the first byte.

Replay does not need the window. The window decides when a summary is made. The summary is logged in `compaction_applied`, and the builder is a pure function of the log, so a replayed request comes out the same whatever window the replaying machine would resolve. `RunConfig.context_window` and `context_source` are provenance, re-resolved on every run like the provider.

## Consequences
- A run against a local server needs only `--provider llamacpp`: no key, no model name, a trigger sized to the loaded context, and a notice on stderr when the window is 32k or less and the strategy never summarizes.
- Cloud runs are unchanged: same request bodies, same strategy label, same trigger. `nim` and `gemini` make no extra request at startup. `openrouter` makes one `GET /models`.
- `RunConfig` gains two optional fields (0020). `config_id` changes for runs after M12, and ids from before are not comparable. Configs from earlier logs load with both fields `None`, which means "not recorded".
- The interactive client's usage line shows `ctx ~3.2k / 4.1k`, in amber from 80% of the window.
- Four probes are now tied to four servers' APIs. They are tested against the response shapes in each project's documentation, not against running servers (see below).
- Sub-agents, eval runs and Harbor runs build their config without a window, so they record `None` and keep the M11 trigger.

## Verified so far
Tested against documented response shapes only. No probe has been run against a live server yet. The vLLM field `max_model_len` comes from third-party code, not from vLLM's documentation, and is the least certain of the four.

## What would change it
- A live run that shows a probe reading the wrong field, or LM Studio moving `loaded_context_length` from `/api/v0/models` to its newer `/api/v1/models`.
- Ollama exposing the loaded `num_ctx`, which would replace its fixed 4096 default with a probe.
- A **truncation detector** that compares the provider's reported `prompt_tokens` with the estimate, to catch a window that is still wrong. It waits on a live check, because Ollama may count only the tokens it did not serve from its KV cache, which would fire on every turn after the first.
- **Pricing and `max_cost_usd`**: `/models` from OpenRouter carries per-token prices, and the loop still refuses cost limits.
- DeepSeek's `prompt_cache_hit_tokens` in `_usage`, if DeepSeek is ever used.
