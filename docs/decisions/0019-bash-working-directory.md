# 0019. Bash working directory as logged state

Status: accepted (M2)
Date: 2026-10-26

## Context
Each bash call is a fresh process (0006), so `cd` would be forgotten at the end of every call. A model that runs `cd src` and then `pytest` expects to be in `src`. Shell state kept in a live process is lost on resume and is invisible to the log.

## Options considered
- A persistent shell: all state carries over, and none of it is in the log. A resume starts from a different shell than the one the model last saw.
- A fresh process with no memory: the model must put `cd dir &&` in front of every command.
- A fresh process plus a tracked working directory recorded in the log: `cd` persists, and the state is a projection of events.
- Track exported variables too: unbounded, and replaying a variable dump back into a shell is a second source of surprises.

## Decision
The tool reports the directory the command ended in, the loop records it on `tool_call_finished.cwd`, and the current cwd is a projection: the last non-null `cwd` in the log, or `AgentConfig.cwd` if there is none (`_current_cwd`, read per call). Environment variable changes do not persist, and the tool description says so.

**Entering the tracked cwd.** Bash always execs at environment cwd `.`, the workspace root. The wrapper's first line is `cd -P -- <quoted tracked cwd> 2>/dev/null || { printf '%s\n' <no-cwd marker>; exit 97; }`. A relative tracked cwd is passed as `./<cwd>`, because bash's `cd` reads an operand `-` as `$OLDPWD` even after `--` and looks a bare relative name up in `CDPATH`. The marker is `__flagship_nocwd_<32 hex>__`, new for every call. If the `cd` fails, the command never runs.

**Reporting.** The next line sets `trap 'printf "\n%s\n%s\n" <sentinel> "$(pwd -P 2>/dev/null)"' EXIT`, and the command follows. The sentinel is `__flagship_cwd_<32 hex>__` from `secrets.token_hex`, new for every call. The tool takes the last sentinel in the output, so a command that learns the sentinel and prints a fake trailer still cannot override the real one. The trailer is stripped from the content.

**Path form.** The reported cwd is `workspace_path(env.root, ".", final)` (0018): relative to the root when the final directory is the root or under it (`.` for the root), absolute otherwise. `pwd -P` gives the physical path and `LocalEnvironment.root` is resolved, so both sides are free of symlinks and compare correctly. Leaving the workspace and coming back yields a relative cwd again. The first M2 build inferred the root as the start directory minus the tracked cwd suffix. That inference failed as soon as the tracked cwd was absolute, so one `cd /tmp` kept every later cwd absolute and silently disabled every relative rule. The root now comes from `Environment.root`, so no inference is left.

**No trailer means no change.** A command that ends the shell without the trap (`kill -9 $$`, `exec`, `trap - EXIT`, its own `trap ... EXIT`), or a call killed by a timeout or cancel, reports `cwd=None`, and the previous cwd stands.

**A deleted cwd resets through the log.** If the wrapper's `cd` fails (the directory is gone, is now a file, or cannot be entered), the output is exactly the no-cwd marker with exit status 97. The tool then returns an error result saying the directory no longer exists or cannot be entered, with `cwd="."`, and the log records the reset. A command that prints the marker and exits 97 itself does not match, because the real trap trailer follows its output. Running the command somewhere unexpected is worse than refusing it, and the model sees why.

**Ordering.** Bash is `mutating`, so its calls never overlap with other calls, and a `cd` takes effect before the next call starts. Within one response the gate has already judged the later calls against the old cwd, which 0018 covers with the `not run: the working directory changed` refusal.

## Consequences
- The cwd survives a resume, a fork and a reopened log with no extra mechanism. `test_cwd_survives_a_resume_through_the_log` checks it.
- Projecting the cwd reads the whole log once per tool call. It costs the same order as the per-step read the loop already does.
- Exported variables, shell functions, aliases and virtualenv activation do not carry over. The model has to repeat them in each command.
- A command that sets its own `EXIT` trap replaces the wrapper's, and the cwd update for that call is lost.
- If `max_output_bytes` is below roughly 400 to 500 bytes, truncation can cut the trailer and the cwd is `None`. The 30000 default is not affected.
- A `cd` through a symlinked directory is reported by its physical path (`cd link` reports `real`). If the command deletes its own final directory, `pwd -P` prints nothing, the cwd is `None`, and the previous cwd stands. If that one is gone too, the next call resets.
- Every environment must report its root. For a container or a remote sandbox that is the workspace path as the environment itself sees it.

## What would change it
A real task that needs state beyond the directory, such as an activated virtualenv or exported credentials. The answers would be a recorded environment diff replayed at the start of each call, or a persistent shell whose state is rebuilt from the log on resume.
