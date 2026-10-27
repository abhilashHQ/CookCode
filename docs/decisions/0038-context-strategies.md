# 0038. Context strategies: masking, batched masking, logged summaries, recall

Status: accepted (M7 gate, 2026-10-27)
Date: 2026-10-27

## Context
Spec section 7 asks how the harness keeps long sessions inside the context window and the token budget. Every request resends the whole history, so cost grows with the square of session length. Most of that history is tool output the model read once. Two constraints come from earlier decisions: the context builder is a pure function of the log (0001), so replay rebuilds every request byte for byte, and providers cache prompt prefixes, so a strategy that rewrites early history can cost more than it saves.

## Options considered
- Raw: send everything. Simple and fully cacheable, until the window runs out.
- Observation masking: replace tool outputs older than the last k steps with a placeholder. Cheap and deterministic. Every step moves the cutoff, which changes an early item and breaks the cached prefix.
- Batched masking: move the cutoff in jumps of b steps, so the prefix stays the same between jumps.
- Summarization: an LLM call rewrites older steps into a summary. It costs a request, can lose detail, and its output is not reproducible, so replay needs it logged.
- Hybrid: batched masking, plus a summary once the request passes a token trigger.
- Truncation (drop the oldest turns): loses the task statement and early findings. Rejected.

## Decision
- `core/strategy.py:Strategy` with `raw`, `mask`, `mask_batched`, `summarize` and `hybrid`, options `keep_steps` (k, default 6), `batch_steps` (b, 8) and `trigger_tokens` (t, 48000). It is recorded in `RunConfig`, so replay and resume rebuild under the strategy that built the request. `raw` is the default and the measured baseline.
- Masking happens in the builder (`context.py:_mask`). Results under 200 chars stay. The placeholder names the tool, the size and the call id, and tells the model that `recall(call_id=...)` returns the output. `recall` reads the original from the log, so nothing is lost, only moved out of the request. Masking strategies require the recall tool.
- A summary is its own logged request (view `summarize`), and its text is stored in `compaction_applied` with the range it covers. The builder swaps the range for the logged text. Replay never calls the model, and the rebuilt request matches.
- Summaries are cumulative. `summary_cut` starts every new summary at the first user message and only accepts one that reaches further than the last.

## Consequences
- What the model sees is a function of the log plus the strategy, so ablations and replays stay exact.
- Recall makes masking safe to try: a masked output the model still needs costs one tool call, not a failed task.
- Measured counterfactual (0039), same trajectories rebuilt per strategy:
  - Local suite, 24 sessions, 196 requests: `mask:k6` cuts input tokens 4.2% and `mask:k3` cuts 9.8%. Uncached tokens double under both (236k raw, 488k under `mask:k6`). `mask_batched:k6:b8` saves 0.3%. Short sessions have little to mask.
  - Terminal-Bench, 50 sessions, 794 requests: `mask:k6` cuts input tokens 39.7%, `mask:k3` 47.9% and `mask_batched:k6:b8` 29.6%. Uncached tokens: 1.32M raw, 4.41M `mask:k6`, 3.00M `mask:k3`, 1.89M `mask_batched`.
  - NIM reported 94% cache reads on those runs. If cached tokens cost a tenth, the effective input is 3.14M raw, 5.15M `mask:k6`, 3.72M `mask:k3` and 3.08M `mask_batched`. Plain masking costs more than doing nothing. Only batched masking comes out ahead, by about 2%, and its real win is headroom: 30% fewer tokens in the window at almost no cache cost.
- The live comparison (raw, `mask_batched:k6:b8`, `hybrid:k6:b8:t48000` on the suite) measures whether the model behaves worse with masked history. The counterfactual cannot, because the trajectory is fixed. Results are in 0036.

## What would change it
A provider without prefix caching (then `mask:k3` wins outright), sessions long enough that the window, not cost, is the binding limit (then hybrid), or live runs showing that masking makes the model re-read files often enough to erase the savings.
