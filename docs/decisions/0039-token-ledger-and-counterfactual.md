# 0039. Token ledger per request and the counterfactual context ablation

Status: accepted (M7 gate, 2026-10-27)
Date: 2026-10-27

## Context
0038 needs numbers: where a request's tokens go, and what a strategy would have saved. Providers report only totals (input, cached input, output), after the fact. Running every strategy live on every task costs free quota for each arm and mixes two effects, the tokens a strategy sends and how the model behaves with what it was sent.

## Options considered
- Provider-reported totals only: exact, but with no breakdown and only for the strategy that actually ran.
- A tokenizer per model: exact counts, but one dependency per provider, and NIM's models do not all publish one.
- A chars/4 estimate per category, calibrated against the reported totals.
- Live A/B runs for every strategy: measure behavior too, at the full quota cost of each arm.
- A counterfactual over logged trajectories: rebuild every logged request under another strategy, with no API calls.

## Decision
- `context.py:token_ledger` logs, on every `model_request_started`, the estimated tokens per category: system, tools, user, assistant, reasoning, tool_output. The field is additive (0020). `cookcode stats` divides reported input by the estimate and prints the scale, so a reader knows how far to trust the breakdown.
- `cookcode eval context <runs> --strategies ...` (`evals/context.py`) rebuilds each logged agent request under each strategy, then reports per strategy: requests, input tokens, change against the first strategy, the cacheable share and the uncached tokens. Cacheable means what the request shares, item for item from the start, with the previous request (`shared_prefix_tokens`), plus the system prompt and tools.
- Summaries need model calls, so the counterfactual covers raw and the masking strategies only. Summarize and hybrid are measured live.
- Live runs answer the behavior question. The counterfactual answers the cost question for free.

## Consequences
- Calibration on 50 Terminal-Bench sessions: reported input / estimate = 1.07. The per-category split is within about 7% in total.
- On those sessions, tool output dominates and the window fills with it, which is what masking targets. The ledger shows that directly instead of leaving it as an assumption.
- The counterfactual holds the trajectory fixed. A model that sees a placeholder may call `recall` or re-read the file, and that is invisible here. 0038 states the counterfactual saving as an upper bound, and the live runs bound it from the other side.
- The ablation runs on any directory of session logs: suite runs, Harbor jobs, a user's own sessions.

## What would change it
A calibration scale drifting far from 1 on a new provider (then add its tokenizer), or summaries becoming cheap and deterministic enough to include in the counterfactual.
