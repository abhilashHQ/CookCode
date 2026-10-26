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
`cohere/north-mini-code:free` as the CLI default (`DEFAULT_MODEL`, overridable with `--model` or `HARNESS_MODEL`), with `nvidia/nemotron-3.5-lightning:free` as the fallback. North costs about a sixth of the reasoning tokens per turn, which matters on a 50-request day.

## Consequences
- Free-tier availability changes daily, so the fallback is part of the decision.
- Not yet measured: whether either model completes a multi-step task end to end. The acceptance run is the fixture task in the walkthrough, run with `harness --once`.

## What would change it
The real-task runs: if north fails tasks that nemotron completes, swap them. Azure credits for runs that count replace both for measurement.
