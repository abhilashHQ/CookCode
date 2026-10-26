# 0018. Permission gate and approvals

Status: accepted (M2)
Date: 2026-10-26

## Context
The model proposes calls and the harness runs them. Something has to decide, before each call runs, whether it runs, is refused, or needs a person. The decision has to be testable without a terminal or a filesystem, has to survive a resume, and has to be honest about what it can and cannot stop.

## Options considered
- Ask for every call: safe, and unusable after the third prompt.
- Trust the model (yolo only): fine inside a container, wrong on a developer machine.
- Match the whole bash command against rules: simple, and `ls && rm -rf x` matches `ls*`.
- Judge each subcommand: handles compound commands, and needs a shell lexer, which is never complete.
- An OS sandbox: a real boundary. Not available before M10 and Flagship 2.

## Decision
**The gate is a pure function.** `decide(tool, arguments, cwd, root, mode, policy, session_allow)` in `src/harness/agent/gate.py` returns a `GateDecision` with an action (`allow`, `deny` or `ask`), the rule that decided, and a reason. It does no I/O. Only `ask` reaches the `Approver`.

**Modes.**
- `ask` (the default): read-only calls are allowed, everything else asks.
- `auto-edit`: read-only calls and file edits inside the workspace are allowed. Bash and any path outside the workspace ask.
- `yolo`: everything is allowed.

In every mode a matching deny rule wins first. Read-only tools are allowed in every mode, including reads outside the workspace. Only a deny rule guards `~/.ssh`.

**Paths are canonical against the workspace root.** `Environment.root` is the absolute workspace path inside the environment (`LocalEnvironment` gives its resolved root). `workspace_path(root, cwd, path)` in `harness.agent.tools` computes `normpath(join(root, cwd, path))`, so an absolute cwd or path is taken as given and then normalized, and a leading `//` collapses to `/`. If the result is the root or under it, the function returns the root-relative form (`.` for the root). Otherwise it returns the absolute form. Every spelling of a file therefore has one canonical form: `secrets/key`, `./secrets/../secrets/key`, `../<rootname>/secrets/key` and `/abs/root/secrets/key` all become `secrets/key`. A path is inside the workspace exactly when its canonical form is relative. The file tools, the gate's subjects, `inside_workspace`, the snapshot step, the cwd-drift check and `undo` all call this one function, and the loop passes `self._env.root` to the gate. Symlink escapes are not detected.

**Rules.** `Rule(tool, pattern)` is an `fnmatch` pattern over the call's subject: the bash command, or the canonical path for file tools. The policy has allow and deny lists, and the session adds allow rules (below). For a path inside the root, deny also tries the absolute form, so a deny rule written as `read:/abs/root/secrets/*` still covers `secrets/key`. Allow matches the canonical form only. A relative rule never matches a path outside the root, so an allow rule for a sibling directory must be written absolute (`write:/abs/shared/*`, not `write:../shared/*`).

**Bash is judged per subcommand.** `split_command` is a quote-aware lexer that splits on `;`, `&&`, `||`, `|`, `|&`, `&`, newlines and subshell parentheses. It handles single, double and `$'...'` quoting, backslashes, comments and redirections.
- Deny if any subcommand matches a deny rule. Matching tries the raw text and canonical forms: assignments, redirections and quoting removed, whitespace collapsed, and the basename of the command. It recurses into wrappers (`env`, `sudo`, `timeout`, `nohup`, `command`, `exec`, `xargs` and others), `eval`, `sh -c`, `find -exec` and substitution bodies.
- Allow by rule only if every subcommand matches an allow rule and none redirects output to a file other than `/dev/null`, `/dev/stdout`, `/dev/stderr` or an fd duplicate.
- Allow rules, from the policy and the session alike, match the executed form of each subcommand and never its raw text. The executed form is the word values after leading assignments and prefix words (`!`, `{`, `if`, `then` and the like), joined by spaces. `PYTHONPATH=src pytest -q` has the executed form `pytest -q`, so `bash:pytest*` allows it. `pytest=1 rm -rf victim` has the executed form `rm -rf victim`, so the same rule does not. A subcommand with leading assignments gets a rule-based allow only if none of them is suspicious: a subscripted name, `PATH`, `IFS`, `BASH_ENV` and the other listed variables, or an `LD_`, `DYLD_` or `BASH_FUNC_` prefix.
- Command substitution, backticks, process substitution, here-documents, complex parameter expansion, unbalanced quotes and nesting deeper than 32 make the command unanalyzable. Outside `yolo` that means ask.
- When a deny rule exists, an opaque command (a dynamic command word, `source`, `eval` of a dynamic string, `xargs sh -c`, a changed `PATH` or `BASH_ENV`, and similar) gets no rule-based allow, because the gate cannot check the deny rule against code it cannot see. With no deny rules, `bash:*` still allows everything.
- Bash is always `mutating`, so its calls never run concurrently. The gate may still auto-allow known-safe commands through policy.

**This is defense in depth and not a security boundary.** Deny rules are text matches. `rm -r -f`, `rm --recursive --force` and `rm -fr` all get past `bash:rm -rf*`. Interpreter one-liners are not closed: `python -c`, `perl -e`, `node -e`, `awk` with `system()`, `git -c alias.x=!...`, `make`, `npm run`. Allowing `pytest*` already allows any code the model wrote into a test file. Variables assembled at runtime can reach an arithmetic evaluation site that the lexer does not list, and the list is a blocklist over a Turing-complete language. The gate catches mistakes and the obvious forms of misuse. The boundary against a hostile command is an OS sandbox (M10 and Flagship 2). Until then `yolo` belongs inside a container.

**"Always allow" is a projection of the log.** The approver can answer `allow_always` with a rule string `tool:pattern`. The loop honors it only if the string parses as `tool:pattern`, names the call's tool, and would allow this very call under the gate's matching (`covers` in `gate.py`, which runs `decide` in `ask` mode with the policy's deny rules and the new rule as the only allow). An honored rule is logged on `approval_resolved.rule`. Any other answer is logged as `approval_resolved(decision="allow", rule=None)` with the reason `allow_always rule <rule> does not cover this call; allowed once`, so a misbehaving approver cannot grant `bash:*` while approving an edit. `session_rules(events)` rebuilds the extra allow rules from the log on every gate check, so a resumed session keeps them and a fresh `Agent` on a reopened log needs no approver for a call they cover. Policy and mode allows are logged as plain `allow` and are not session rules.

**Order and approvals.** All calls of a response are gated up front, in call order, before any executes (`_dispatch`, `_gate_all`). Unknown tools and invalid arguments get their error result first and are never gated. For an `ask`, the loop appends `approval_requested(call_id, risk, rule)`, races `approver.decide(call, risk, cancel)` against the run's cancel token (the `Risk` carries the side effect, the rule, the reason, the tracked `cwd` and the canonical `subjects`, so the approver sees where a relative path lands), and appends `approval_resolved(call_id, decision, reason, rule)`. A denied call gets `ToolResult(status="denied", content="denied: <reason>")`, never a `tool_call_started`, and never a checkpoint.

What gets logged:
- A read-only call allowed by a mode default logs nothing.
- A mutating or external call allowed by a mode default logs `approval_resolved(allow, rule="mode:<mode>")`.
- A policy or session-rule allow logs `approval_resolved(allow, rule)` for any side effect.
- A gate deny logs `approval_resolved(deny, reason, rule)`.

With no approver, `PolicyOnlyApprover` denies with `no approver available; the policy did not allow this call`. An approver that raises gets the deny `approver failed: <Type>: <message>` and the run continues.

**Cancel wins.** If the token fires during an approval wait, the answer is dropped even if the approver returned in the same instant. No `approval_resolved` is written. The ungated and already-approved calls of that response get the not-executed close-out (0011), and the run ends through the normal interrupt path.

**The working directory can change between the gate and the run.** The gate judges every call against the cwd as it was before the response started running. An approved `bash cd ..` followed by `edit x.txt` in the same response would be judged inside the workspace and run outside it. `_run_call` therefore compares the call's subjects (the canonical paths, or the command) under the gated cwd and under the current cwd, and a call whose subjects differ gets the error result `not run: the working directory changed to <cwd> after this call was gated, so its paths now resolve differently. Issue the call again.` It gets no checkpoint and no start.

**Mode and policy are part of run provenance.** `RunConfig` carries `mode` and `policy` (each rule rendered as `allow tool:pattern` or `deny tool:pattern`), so a mode change between runs changes `config_id` (0020).

## Consequences
- The gate is a function of values, so `tests/test_gate.py` exercises it with no loop, filesystem or terminal. The root is a parameter, so tests use a fixed `/ws/work`. The lexer carries most of the 273 tests and about 800 lines of code that need upkeep as bash grows new syntax.
- Canonical paths close aliasing. Before the final review fix, a deny rule `read:secrets/*` was evaded by `../<rootname>/secrets/key` or the absolute spelling, and one `cd` out of the workspace made the tracked cwd absolute for good, so every later relative rule stopped matching. Both came from the same gap: nothing in the gate knew the root.
- Relative allow rules that point outside the root (`../shared/*`) stop matching, because the subject is absolute there. That is the safe direction.
- A false deny is possible. Here-document bodies are lexed as commands, so a deny rule can fire on text that is only data. That is the safe direction.
- Reads outside the workspace are allowed without a prompt. A deny rule is the only guard.
- A cancel during an approval wait leaves `approval_requested` with no `approval_resolved`. The call is still resolved by its not-executed result, so repair and the projections are unaffected.
- An `allow_always` rule is kept only when it covers the call it was given for. Rules match per subcommand, so a rule that is a whole compound command does not cover that command and is downgraded to a one-time allow, and `*?[` in a command are read as glob characters. No rule is synthesized for the user.
- The production default is `ask` with no approver, which denies every mutating call. Tests that are not about gating run in `yolo`. A real approver arrives with the M3 CLI.
- The chaos experiment runs in `yolo`, which adds a `run_started` event and an `approval_resolved(mode:yolo)` per mutating call to the reference log.

## What would change it
An OS sandbox in the `Environment` makes the gate a prompting layer, and deny rules would stop being a safety claim. A bypass found in real use that a text rule could have caught would extend the lexer. A bypass it could not catch would end the effort to extend it.
