# 0040. Background jobs for commands that outlive the bash timeout

Status: accepted (M8, 2026-10-27)
Date: 2026-10-27

## Context
The bash tool kills a command at its timeout (120 s by default), so one tool call can never hang a session. Some tasks need a command to run longer: a hash crack, a long build, a test suite. On Terminal-Bench, crack-7z-hash failed every attempt because the agent could not keep a 10-minute crack alive. The tool needed to support it, in the local environment and in a Harbor container alike, with no daemon of our own.

## Options considered
- Raise the bash timeout per call: one long call blocks the loop, so steering and cancellation wait on it.
- Tell the model to use `nohup ... &`: it works sometimes. Output goes nowhere unless the model remembers to redirect, nothing caps the lifetime, and a later call cannot reliably find the process.
- A job manager process inside the environment: real supervision, but one more process to ship into every container.
- Two tools on top of `Environment.exec`: `job_start` launches the command detached and returns an id, `job_check` reports its state and output tail, with all state in files.

## Decision
- `tools/jobs.py`: `job_start(command, max_s)` and `job_check(job_id, wait_s, kill)`. A job runs in its own session (`setsid`), so the exec that starts it returns without killing it. Its pid, combined output and exit code live under `/tmp/cookcode-jobs/<id>/`.
- A watchdog in the same process group kills the whole group after `max_s` (default 1 hour, at most `MAX_LIFETIME_S`, 4 hours) and records why. `kill=true` kills the group on request.
- `job_check` can wait up to the bash timeout for the job to end, so polling costs one call per wait, not a tight loop.
- Everything is a shell script run through `exec`, so jobs work unchanged in Docker over Harbor (0030).
- Behind the `jobs` feature flag, off by default, so the measured baseline stays the M6 harness.

## Consequences
- Long work no longer depends on the model knowing shell tricks, and it never blocks the loop.
- A job survives the turn that started it but not the environment. A crashed session leaves an orphaned job until the lifetime cap kills it.
- Job state sits outside the log. `job_check` output is logged like any tool result, so replay still serves the same text.
- The Terminal-Bench check is crack-7z-hash with `TB_FEATURES=jobs`, 2 attempts (results in 0036).

## What would change it
Several concurrent jobs needing coordination (dependencies, resource limits), at which point a supervisor process starts paying for itself.
