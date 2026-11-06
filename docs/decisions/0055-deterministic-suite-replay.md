# 0055. Deterministic suite replay: sorted search, no bytecode, a pinned parent, drift counted once

Status: accepted (2026-11-06)
Date: 2026-11-06

## Context
0036 left replay drift as the open issue: drift on almost every task, memory-money-a passing on some replays and failing on others, and bugfix-backup-script-spaces reporting x16 on one replay and x22 on the next. A capture of every recorded and replayed request body across the 36 tasks showed that no request field outside the messages differs (`model`, `tools`, `stream`), and neither do the system or user prompts. Every difference is in tool output, and the transport always serves in order. The sources were:
- **Search order.** `grep` ran `rg` without `--sort`. rg walks in parallel, and 30 runs of one command in one directory gave 4 orders. A `--glob` also overrides rg's hidden-file skip, so `glob="*"` searched `.git/hooks`, which the grep fallback never does.
- **Stale bytecode.** memory-money-a edits `10001` to `10010` in a test, the same size, and replay makes both edits within one second. Python validates a cached `.pyc` by mtime seconds and size, so pytest ran the old code while the traceback showed the new line. The hidden check read the same cache. The live recording took 235 s and never hit it.
- **The parent directory.** `prepare` pinned the mtimes of the work directory and everything in it, but `ls -la` also prints `..`, whose mtime and link count changed with every run and with attempt directories left by earlier runs.
- **The count.** A sequenced replay noted only the first difference per request, and every request repeats the history before it. One early difference became `drift x25`, and it hid any later one.
- **Not fixable here:** 15 cassettes predate the fixture normalization of 0035 (branch `master`, other commit ids), and program output varies on its own (pytest durations, object addresses, `mktemp` paths, clocks). The agent can also read outside its work directory (other tasks' end states, `ps`, the harness's own logs), since a suite run has no sandbox.

## Options considered
- **Normalize volatile text before comparing** (durations, addresses, paths). Hides the noise, but also hides real changes that look like noise, and does nothing for the pass/fail flip, which is a real difference in what ran.
- **Fix the sources the harness owns** and leave program output alone. Small and exact. Existing cassettes keep the drift they were recorded with.
- **Run the suite in a sandbox** so the agent sees only its fixture. Needed for fully hermetic replay and for memory pairs (0043) to measure memory rather than access to task A's directory. A design of its own, deferred.
- **Re-record every cassette.** Spends free quota against the current no-evals direction (owner, 2026-11-03).

## Decision
Fix the sources the harness owns:
- `grep` runs rg with `--sort=path`, and `--glob=!.*/` after any user glob, so rg and the fallback search the same files in a stable order.
- `LocalEnvironment` takes an `env` overlay. Suite runs pass `PYTHONDONTWRITEBYTECODE=1`, so no command writes bytecode that a later same-size edit could leave stale.
- `prepare` pins the parent directory's mtime too, and `run_suite` removes each task's earlier attempt directories before it starts. Leftovers are removed per run, not in `prepare`, because attempts of one task can run at the same time.
- A sequenced replay notes each difference once, at the first request that carries it, and reports every difference in a request instead of only the first. Strict replay still fails on the first.

## Consequences
- Five replays of memory-money-a and bugfix-backup-script-spaces: before, memory-money-a passed 2 of 5 and bugfix-backup reported x16 or x22; after, both pass 5 of 5, and bugfix-backup reports the same 4 differences every time. memory-money-a reports 2 differences, or 3 when pytest's duration happens to differ.
- Full replay: 35 of 36 pass, and the one failure (memory-money-b) also failed when it was recorded. Drift entries fell from 436 to 101; they now count differences, not requests.
- The drift that remains is in the cassettes or in program output: the stale cassettes, `..` mtimes and rg orders recorded before this change, and pytest durations. A re-record under this harness would remove the first group. memory-testenv-a picks up one entry because its cassette holds rg's unsorted order.
- `rg --sort=path` searches on one thread. Fine for suite fixtures and normal repositories, slower on very large ones.
- The stale-bytecode trap exists in interactive use too, with a fast model editing the same file twice in one second. Only suite runs set the variable for now.

## What would change it
A sandboxed suite environment (which would take over the parent directory and leftover handling), a re-record that makes drift zero on fresh cassettes so any drift means a regression, or a large-repository user for whom single-threaded search is too slow.
