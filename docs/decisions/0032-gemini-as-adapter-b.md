# 0032. Adapter B is native Gemini, on a transport shared with adapter A

Status: accepted (M5)
Date: 2026-10-26

## Context
M5 needs a second adapter over a native protocol with reasoning (spec section 13), so the neutral item model is tested against a protocol that disagrees with Chat Completions. The candidates were Gemini and Anthropic. Only Gemini has a free tier, and Anthropic needs a paid key. The second question was how much of adapter A to reuse.

## Options considered
- Anthropic Messages: thinking blocks with signatures, well documented. No free tier, so no development without spend.
- Gemini `streamGenerateContent`: free tier on several thinking models, and thought signatures that must go back on specific parts. That is the harder reasoning-replay problem, which is the point of the milestone.
- Copy adapter A's retry and cancellation code into adapter B: fast, and two copies of the subtlest code in the repo.
- Extract the transport: `providers/http.py:StreamingHttpProvider` owns attempts, backoff, the fail-fast cap, the cancel race and the atomic rule (0022). A protocol supplies only `url`, `headers`, `build_body`, `assembler` and `error_failure`.

## Decision
Gemini, as `providers/gemini.py:GeminiProvider`, on the extracted transport. Adapter A became a subclass with no behavior change: all 896 tests passed unchanged, including the cassette replays, which compare request bodies byte for byte.

Wire mapping:
- Adjacent assistant-side items become one `model` content, and results become `functionResponse` parts in one `user` content. Adjacent contents with the same role merge.
- Results go back as `{"output": ...}`, or `{"error": "[status] ..."}` for non-ok statuses. An empty result goes back as `(no output)` (0023).
- Tool schemas go as `parametersJsonSchema`, because our schemas are full JSON Schema, not the OpenAPI subset.
- `includeThoughts` is on, so summaries stream as reasoning deltas.
- Calls arrive whole, never fragmented. A call without an id gets `harness-call-<ours>`, and that id is never sent back.
- Usage: input is the prompt minus cached tokens plus tool-use prompt tokens, and output is candidates plus thoughts (billed).
- `MALFORMED_FUNCTION_CALL` and `MALFORMED_RESPONSE` are resampled as retryable failures. Safety and recitation finishes map to `refusal`.
- A `RetryInfo.retryDelay` in the error details counts as a server wait, so a daily quota fails fast, as on NIM.

## Consequences
- A third protocol costs a codec, not a transport.
- Live probes on 2026-10-26 (`gemini-3.5-flash`):
  - `parametersJsonSchema` is accepted, including `title` and `additionalProperties`.
  - Parallel calls come back with ids, and the signature arrives on the first call only.
  - `candidatesTokenCount` excludes thoughts (86 + 32 + 70 = 188 total).
  - A text-only answer carries its signature on an empty text part.
  - A signed round trip is recorded in `tests/cassettes/test_gemini_signed_round_trip-*` and replayed in CI.
  - The fixture task finished `done` in 8 requests, and `harness replay` matched all 8.
- Free-tier reality: 5 requests a minute and 20 a day per model. The 20-a-day quota error named `gemini-3.6-flash` while we called `3.5-flash`, so quotas look shared or aliased. `gemini-3.8-flash` and `3.7-flash` returned 503 for high demand, and `2.5-flash` is closed to new users. The default is `gemini-3.5-flash`. Gemini is for adapter coverage and replay tests, not for long runs.

## What would change it
A paid Anthropic key, which would make the second native protocol cheap to add and worth comparing.
