# 0027. Free development model

Status: accepted (M3), to be confirmed by the real-task run
Date: 2026-10-26

## Context
M3 needs a free model for development and smoke tests (spec section 2: free tiers for development). It must stream tool calls through OpenRouter's Chat Completions endpoint. The key is free tier: 50 free-model requests a day.

## Options considered
Probed on 2026-10-26 against every `:free` model that advertises `tools`:
- `cohere/north-mini-code:free` (256k context, code-tuned): streamed parallel tool calls, short reasoning (about 100 tokens on the probe), accepted every history shape tried. Recorded in a cassette.
- `nvidia/nemotron-3.5-lightning:free` (1M context): streamed parallel tool calls, long reasoning (676 reasoning tokens on the same probe), accepted every history shape tried. Recorded in a cassette.
- `nvidia/nemotron-3-super-120b-a12b:free`: 404 from the upstream function.
- `poolside/laguna-s-2.1:free`, `google/gemma-4-31b-it:free`: 429 from the shared upstream pool.
- `thinkingmachines/inkling-small:free`: 403, gated to listed agent apps.

## Decision
Revised the same day: **NVIDIA NIM is the default provider** (`--provider nim`, key in `NVIDIA_API_KEY`), with `minimaxai/minimax-m3.1-flash` as the default model. OpenRouter stays available with `--provider openrouter`, defaulting to `cohere/north-mini-code:free`.

Why NIM: the OpenRouter free key allows 50 requests a day, which the first two real runs used up. NIM allows 40 requests a minute. It speaks the same Chat Completions protocol, so it needed only a base URL, a provider name and support for its `reasoning_content` field (0023).

NIM probes on 2026-10-26:
- `minimaxai/minimax-m3.1-flash`: parallel tool calls in 4.4 s, round trip in 1.1 s, accepted an empty tool result and its own reasoning echoed back. Recorded in a cassette.
- `nvidia/nemotron-3.5-lightning-30b-a3b`: tool calls in 1.9 s, round trip accepted.
- `openai/gpt-oss-20b`: works, but 80 s for the first response.
- `moonshotai/kimi-k2.6`, `z-ai/glm-5.3`, `z-ai/glm-5.3-flash`, `deepseek-ai/deepseek-v4.1-flash`: no response headers within 90 s. Popular models queue.
- `nvidia/nemotron-3-super-120b-a12b`: 404, same as on OpenRouter.

## Consequences
- **Acceptance run passed.** The fixture task on NIM with MiniMax finished with `done` in 4 turns, 9,042 input and 462 output tokens: it read both files in parallel, made one edit fixing both bugs, ran the tests (3 passed) and summarized.
- On OpenRouter, north fixed both fixture bugs correctly on its first edit, but that run ended at the turn cap because of a fixture defect, now fixed.
- A queued NIM model can sit longer than the 180 s read timeout. The adapter then retries, and the run fails after five attempts. Pick a fast model.

## What would change it
MiniMax becoming slow or unavailable on NIM (fallback: Nemotron 3.5 Lightning), or Azure credits for runs that count.
