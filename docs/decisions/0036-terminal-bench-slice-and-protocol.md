# 0036. Terminal-Bench slice: 10 tasks, 5 attempts, Terminus 2 on the same model as the baseline

Status: accepted (M6 gate, 2026-10-26)
Date: 2026-10-26

## Context
Spec section 10 defers the benchmark slice to this gate, "chosen to fit the budget". The budget is free quota only: NVIDIA NIM at 40 requests a minute, with MiniMax M3.1 Flash answering in roughly 10 to 35 s per request. Gemini's 20 requests a day rule it out for sweeps. The protocol asks for an oracle run, a Terminus 2 baseline on the same model and budget, and at least 5 attempts reported as mean, min and max.

## Options considered
- 6 tasks x 5 attempts: about 2 h for both agents, but the paired MDE (0037) grows with 1/sqrt(n), so M7 differences of 10 to 15 points might not be resolvable.
- 10 tasks x 5 attempts: the spec's attempt count, about 3 to 4 h for both agents at 4 trials in parallel.
- 10 tasks x 10 attempts: tighter intervals, 7 to 8 h, roughly double the quota.
- Without the Terminus 2 baseline: half the cost, but no reference point for "is this harness any good".

## Decision
- The slice is `evals/tb-slice.txt`: fix-git, prove-plus-comm, cobol-modernization (all easy) and crack-7z-hash, constraints-scheduling, kv-store-grpc, nginx-request-logging, openssl-selfsigned-cert, git-leak-recovery, sanitize-git-repo (medium, expert estimate up to 30 min). Excluded: tasks that pull model weights or need GPUs or VMs (pytorch, hf, mteb, qemu), and the hard tier, where a free flash model would score near zero and add no variance signal. The 10 images total 4.4 GB.
- 5 attempts per task, for both agents.
- `evals/run_tb.sh` runs both agents with the same endpoint (NIM's OpenAI-compatible URL), model and 50-turn cap. Terminus 2 goes through litellm as `openai/<model>` with `api_base`, so neither agent gets a different provider path. Harbor's retries stay at its default of 0, so a failed trial costs one attempt, not several.
- Before any sweep, a pilot of one trial per agent.

## Pilot (fix-git, 1 attempt each, 2026-10-26)
| agent | reward | requests | wall | input tokens (cache share) | output tokens |
|---|---|---|---|---|---|
| harness | 1.0 | 11 | 6m26s (360 s model, 7 s tools) | 91k (87%) | 4.3k (2.1k reasoning) |
| Terminus 2 | 1.0 | 7 | 3m07s | 35k (75%) | 3.1k |

## The first sweep was aborted
Four trials ran at once (2 per agent). Within 5 minutes, all 5 of our finished trials had ended with `HTTP 429 after 5 attempts`, and their scores measured the rate limiter. A probe of 1 to 4 parallel requests settled the cause: NIM's free tier allows 2 requests in flight per key, and the third is rejected in 0.1 s with no `Retry-After` and no rate-limit headers. Per-minute volume (about 8 a minute) was never the constraint. The sweep was stopped, its jobs were moved out of the results, and two things changed:
- The harness budgets 429s in seconds of waiting instead of attempts (0022). Giving up after 15 s against a provider that is merely busy was a robustness bug, and it would have hit any user sharing a key.
- The sweep runs one trial per agent at a time, which is exactly the cap, with both agents running at once so they see the same provider conditions.

## Consequences
- The sweep is 100 trials. On the pilot rates and a guess of 2 to 3x more steps on medium tasks, that is about 1,500 to 2,500 requests and roughly 7 to 9 h at 2 in parallel. The 50-turn cap bounds the worst case at 5,000.
- The slice is fixed for M7 to M9, so their ablations compare against this baseline.
- The local suite (0035) covers per-change regressions. The slice is for measurement only.

## What would change it
A paid or faster endpoint (then more attempts or the hard tier), or a variance result showing 5 attempts cannot resolve the effects M7 expects.
