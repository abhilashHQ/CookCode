# 0051. Plan mode, enforced by the gate and left through a reviewed plan

Status: accepted (2026-11-03)
Date: 2026-11-03

## Context
A coding agent that edits before it understands the code makes the user review and undo work. Plan mode splits a task into a read-only investigation, a plan the user approves, and the implementation. The harness already has a pure permission gate (0018, `agent/gate.py`) that decides every call by mode, policy and tool side effect, and a log from which every request is rebuilt (0026, 0048).

## Options considered
- **Prompt only.** Tell the model not to edit. Cost: nothing enforces it. A weak model edits anyway, and the claim "plan mode is read-only" cannot be defended.
- **Filter tool schemas.** Send only read-only tools while planning. Cost: the tool list changes on entry and exit, which breaks the provider's prompt cache and makes the request depend on more than the log unless the mode is logged. The model also loses bash entirely, which it needs for `ls`, `git log` and `rg`.
- **Deny at the gate, keep the schemas.** The model sees the same tools, and the gate denies anything that changes state with a reason that names `exit_plan`. Bash is allowed when every command in it is a known read-only program.

## Decision
- `plan` is a fourth `Mode`. In `_decide_tool`, deny rules apply first, then a non-read-only tool is denied with rule `mode:plan`. User allow rules do not unlock it: a standing `edit:*` must not bypass a plan.
- `_plan_bash` reuses the gate's lexer. It allows a command only when it is analyzable (no substitution, here-document or opaque construct), nothing redirects to a file, no variable is set, and every command, nested ones included, is on `_PLAN_FORBIDDEN` with none of its forbidden flags. Per program: `find -exec/-delete/-fprint`, `sort -o`, `rg --pre`, `fd -x`, `tree -o`. `git` is limited to read-only subcommands (`status log diff show ls-files ...`) with `-C`, `--no-pager` and `-P` as the only global options, and without `--output`, `-O` or `--ext-diff`. Short flags are checked inside clusters, so `sort -uo` is caught.
- `exit_plan(plan)` is a `plan_only` tool. Its schema is sent only while `mode_at(events)` says plan, so sessions that never plan, and every recorded cassette, send the same requests as before.
- The loop gates `exit_plan` itself. The approver sees the plan. `allow` leaves plan mode for ask, `allow_always` for auto-edit, and a denial's reason goes back to the model as feedback while it stays in plan mode. Approval logs `mode_changed(source="exit_plan")`.
- A run that starts in plan mode, or leaves it between runs, logs `mode_changed(source="run")`, and the context builder renders it as a note: the plan-mode instructions, or "plan mode is off". The note comes from the log, so replay sees it. The exit_plan change is not rendered as a note, because a user message between a tool call and its result is an invalid request; the tool result carries it instead.
- The agent holds the mode in memory (`Agent.mode`, `set_mode` between runs) and logs it in every `run_started`. `mode_at` reads it back from the log.

## Consequences
- Schema change: one new event type, additive. `docs/schema/events-v1.json` is regenerated and the samples cover it.
- Headless runs in plan mode cannot leave it: `PolicyOnlyApprover` denies `exit_plan`, so the model ends with its plan. That is the right result for a non-interactive plan.
- Leaving plan mode changes the tool list once, a single cache miss per session.
- The read-only list is an allowlist. Anything not on it is denied with its name in the reason, so a model learns what to use instead.

## What would change it
A real session where a model loops on denials it cannot get past, which would call for listing the read-only commands in the plan note. Or a need for plan-mode tests (`pytest` is not read-only), which would need a sandboxed run, not a longer allowlist.

## Measured (2026-11-03, Nemotron 3.5 Super VL free, OpenRouter)
Two live runs on a two-file repo whose check fails. In both, the model read the files, ran `ls` and `git` under `mode:plan`, was denied `python check.py` with the reason, and called `exit_plan` with a correct one-line plan. Approved into auto-edit, it made the edit and the check passed (8 turns, 21,170 input tokens).
