# 0046. User hooks: shell commands with the event as JSON on stdin

Status: accepted (M9 gate, 2026-10-27)
Date: 2026-10-27

## Context
0041 gave the loop hook points for built-in features. Users want their own checks at the same points: refuse writes to a protected path, run a linter before the turn ends, block a dangerous command pattern. The hook API is a public contract, so it has to be language-neutral, safe to get wrong and impossible to turn on by accident.

## Options considered
- Python plugins imported into the harness: full power, but tied to our language and our internals, and a crash in a plugin crashes the session.
- Shell commands that receive the event as JSON on stdin, with the exit code as the verdict.
- A long-running hook server over a socket: fast, and one more process to manage.

## Decision
- `userhooks.py`. Hooks are declared in settings (0047) as `[[hooks]]` with `point` (`pre_tool` or `stop`), `command`, an optional `match` (a regex on the tool name, checked when the settings load) and `timeout_s` (default 30, at most 600).
- The payload is JSON on stdin: `{point, tool, arguments, cwd}` for `pre_tool`, `{point, last_message}` for `stop`. The command runs through `Environment.exec` at the repository root, so hooks run inside the container on Harbor too.
- Exit 2 blocks, and the hook's output becomes the block message the model sees. Exit 0 continues. Any other exit, including a timeout, counts as a broken hook and is ignored, so a typo never wedges a session.
- Hooks are inert unless the `hooks` feature is on. A repository's settings can declare commands, and cloning a repository must not run them.
- User hooks are ordinary `Hook`s, so blocks are logged as `hook_blocked` and replay serves them from the log.

## Consequences
- Any language works, and the contract is three exit codes and one JSON object.
- One process per hooked call costs milliseconds next to a model call.
- Ignoring broken hooks means a broken guard fails open. That is the price of never wedging a session. A security boundary belongs in the policy (allow and deny rules), which fails closed.
- Live smoke: a `pre_tool` hook refused a write to `secrets.txt`, the model got the message and wrote to `notes.txt` instead, and the blocked call never reached approval or execution.

## What would change it
Hooks that need to rewrite tool arguments rather than only allow or block them, or hook latency mattering at high call rates.
