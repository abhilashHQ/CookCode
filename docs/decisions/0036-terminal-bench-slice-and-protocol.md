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

## Final results (2026-10-29)
All runs on NIM free, `minimaxai/minimax-m3.1-flash`. Attempts lost to the provider (429 past the wait budget, a DNS outage) are excluded and re-run, and the pooled numbers come from `cookcode eval report`. For a memory pair, losing task A excludes its B too.

**Terminal-Bench slice, 10 tasks × 5 attempts.** The sweep ran at 0cb5ec8, and the 2 fix-git trials lost to 429s were re-run from a worktree at that commit.

| Agent | Score | 95% CI | Per-round | Paired MDE |
|---|---|---|---|---|
| CookCode | 0.800 | 0.60 to 0.94 | 0.70 to 0.90 | 0.20 |
| Terminus 2 | 0.760 | 0.54 to 0.96 | 0.50 to 0.90 | 0.18 |

No detected difference. Per task, the agents split: CookCode leads on cobol-modernization (0.60 against 0.00) and sanitize-git-repo (0.80 against 0.60), and Terminus 2 leads on kv-store-grpc (1.00 against 0.80) and crack-7z-hash (0.40 against 0.20).

**Context strategies, live, 24 base tasks × 1 attempt.**

| Strategy | Score | Requests | Input tokens | Cache reads |
|---|---|---|---|---|
| raw | 1.000 | 193 | 1.08M | 89% |
| mask_batched:k6:b8 | 0.833 | 202 | 1.08M | 61% |
| hybrid:k6:b8:t48000 | 0.958 | 215 | 1.33M | 81% |

The live runs confirm the counterfactual (0038). On short tasks masking saves no tokens and costs a third of the cache hits. The score gap is confounded: all 5 masking and hybrid failures ran into the 900 s wall cap after 3 to 18 turns, and those arms ran at 2 in flight while raw ran at 1 next to the Terminus 2 sweep. I read that as provider latency, not strategy.

**Verify-first, 6 bugfix tasks × 2 attempts.** Both arms pass 12/12. Verify-first costs 11.8 turns against 9.6, and 87k input tokens per attempt against 58k (+50%). `eval repro`: all 12 valid attempts registered a reproduction, all 12 failed on the original code and passed on the reference fix, and all 12 were solved. The lock works and the reproductions are right. On tasks this easy, it is pure cost.

**Memory, 6 pairs × 2 attempts, B tasks only.**

| Arm | B pass | Turns | Input tokens |
|---|---|---|---|
| none | 0.83 | 9.0 | 42k |
| notes | 0.83 | 7.7 | 27k |
| facts | 0.83 | 8.8 | 43k |

The pairs do not separate the arms on pass rate. Every B task except money passes without memory, and money-b fails in all three arms. Notes cut B's input tokens by about a third and its turns by 1.3. Facts change nothing measurable: the profile is shown, but the conventions were cheap to rediscover. The pairs were too easy for a flash model to need memory, which is the risk 0043 named.

**Sub-agents, 12 investigate and refactor tasks × 1 attempt.** The `task` tool was offered in every run and called in none: tasks averaging 7 turns are not worth delegating, and the model agreed. The arm therefore measures an unused tool, 11/12 against raw's 12/12, which is within noise. Delegation and the report contract are verified by tests, not by a live run.

**Jobs, crack-7z-hash × 3 (one more lost to 429s).** 0/3. The agent started the crack with `job_start` and then polled with `bash` instead of `job_check`, so the mechanism worked and the tool description did not.

**Smoke runs, live:** skills (loaded before acting, format followed exactly), hooks (a write blocked before approval, then routed around) and MCP (`tool_search` loaded the schema, then the call returned 42).

**Known issue: replay drift.** Replay of all 36 tasks passes 34. Most cassettes, including those recorded on 2026-10-27, show drift on some requests, and two recorded passes (memory-money-a, memory-money-b) replay as failures. Drift on a fresh cassette means something in the request still varies between record and replay. Finding it is the first job after this milestone, before any cassette is trusted as a regression gate.

Batch totals from `cookcode stats`: 300 sessions, 2,449 requests and 2,673 logged retries, all on the free tier.
