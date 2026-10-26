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

**Reporting.** `bash` wraps the command as `start=$PWD; trap 'printf "\n%s\n%s\n%s\n" <sentinel> "$start" "$PWD"' EXIT` followed by the command. The sentinel is `__flagship_cwd_<32 hex>__` from `secrets.token_hex`, new for every call, and the start variable name is random too. The tool takes the last sentinel in the output, so a command that learns the sentinel and prints a fake trailer still cannot override the real one. The trailer is stripped from the content.

**Path form.** The tool works out the workspace root as the start directory minus the tracked cwd suffix. A final directory inside the root is reported relative to it (`.` for the root). Otherwise it is reported absolute. An unchanged directory is reported as the normalized tracked cwd, so the value is never `None` after a normal run.

**No trailer means no change.** A command that ends the shell without the trap (`kill -9 $$`, `exec`, `trap - EXIT`, its own `trap ... EXIT`), or a call killed by a timeout or cancel, reports `cwd=None`, and the previous cwd stands.

**A deleted cwd resets through the log.** If `exec` fails because the tracked directory is gone (`FileNotFoundError` or `NotADirectoryError`, cwd not `.`), bash does not run the command. It returns an error result saying so, with `cwd="."`. The log then records the reset. Running the command somewhere unexpected is worse than refusing it, and the model sees why.

**Ordering.** Bash is `mutating`, so its calls never overlap with other calls, and a `cd` takes effect before the next call starts. Within one response the gate has already judged the later calls against the old cwd, which 0018 covers with the `not run: the working directory changed` refusal.

## Consequences
- The cwd survives a resume, a fork and a reopened log with no extra mechanism. `test_cwd_survives_a_resume_through_the_log` checks it.
- Projecting the cwd reads the whole log once per tool call. It costs the same order as the per-step read the loop already does.
- Exported variables, shell functions, aliases and virtualenv activation do not carry over. The model has to repeat them in each command.
- A command that sets its own `EXIT` trap replaces the wrapper's, and the cwd update for that call is lost.
- If `max_output_bytes` is below roughly 400 to 500 bytes, truncation can cut the trailer and the cwd is `None`. The 30000 default is not affected.
- When the start directory is reached through a symlink, the reported path is absolute.

## What would change it
A real task that needs state beyond the directory, such as an activated virtualenv or exported credentials. The answers would be a recorded environment diff replayed at the start of each call, or a persistent shell whose state is rebuilt from the log on resume.
