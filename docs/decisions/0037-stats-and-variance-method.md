# 0037. Stats come from the log; variance is reported as round spread, a bootstrap CI and a paired MDE

Status: accepted (M6)
Date: 2026-10-26

## Context
M7 to M9 publish ablations: compaction strategies, memory, verify-first, sub-agents. Each is a claim that config B beats config A. Before running any of them, I need to know how much a score moves between identical runs, or a 5-point difference could be noise and I would not know. Spec section 11 also asks for a `stats` command (tokens by category, cost, cache-read share, tool error rate) per session or across a run.

## Options considered
- Report mean pass rate only: the usual benchmark table. It hides the spread entirely.
- Mean plus min and max over attempts (spec section 10's protocol): shows spread, but says nothing about which differences are real.
- Add a confidence interval and a minimum detectable effect (MDE): one more paragraph of method, and it answers the question the ablations need.
- Stats from Harbor's own fields or provider dashboards: incomplete (Harbor drops reasoning, timing and tool errors), and unavailable for local runs.

## Decision
- `harness stats <session | dir>` reads session logs only: every `*.jsonl` inside a `sessions/` directory under the target. That covers the data directory, a suite run and a Harbor job. It reports requests and retries, input tokens with cache-read share, output and reasoning tokens, tool calls and error rate, wall time split into model time (request start to finish) and tool time, cost from an optional price file (zero on free models), and repeat streaks: 3 or more consecutive identical call-and-result pairs, the stuck-loop signal 0014 deferred to M6.
- `harness eval report <run | job>` reads suite results or Harbor trial results and prints:
  - score: the mean over tasks of each task's mean reward;
  - per-round scores: the score of attempt round r alone, which is what one single-attempt run would have reported, so min and max are the spread a reader of a one-shot table would face;
  - a 95% bootstrap CI, resampling tasks and then attempts within each task (10,000 draws, fixed seed);
  - within-task standard deviation;
  - the paired MDE: the smallest true difference two configs, run on the same tasks with the same attempt count, can show with 80% power at 5% two-sided. Pairing by task cancels task difficulty, so the noise left is attempt-to-attempt: MDE = (z.975 + z.8) * sqrt(2 * s_w^2 / (k * n)), where s_w^2 is the mean within-task variance, k the attempts and n the tasks. It ignores task-by-config interaction, so it is a floor, and it is reported as one.
- Harbor trials that raise before the verifier score 0, matching Harbor's own mean.

## Consequences
- Every ablation in M7 to M9 states its MDE next to its result. A difference below it is reported as no detected difference.
- The variance study is the input to that: it fixes n and k for the slice.
- `stats` works the same on local, suite and Harbor runs, because all three write the same log.

## What would change it
Ablations whose effects are far above the MDE (then fewer attempts are enough), or graded rewards instead of pass/fail, where a t-based interval is as good as the bootstrap.
