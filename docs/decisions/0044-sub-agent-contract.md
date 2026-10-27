# 0044. Sub-agents: a task tool with a structured report contract

Status: accepted (M9 gate, 2026-10-27)
Date: 2026-10-27

## Context
Spec section 12b: some jobs read a lot and conclude a little (find where X is handled, survey a module, check every call site). Doing them in the main context fills it with output the parent never needs again. A sub-agent reads in its own context and returns only a conclusion. The open questions were what the child may do, what it must return, and how it fits the log, resume and approvals.

## Options considered
- Return contract: free text, a JSON schema, or a fixed Markdown report checked by the harness.
- Child tools: read-only tools only, a per-call tool list, or the parent's full tools minus delegation.
- Concurrency: children in parallel, or one at a time.
- Logging: inline in the parent's log, or a separate session that the parent's log points to.

## Decision
- `subagents.py:task_tool`. `task(prompt)` runs a child `Agent` with the parent's tools minus `task` (no recursion), the parent's mode, policy and approver, the same environment and the same session store. `Spawner` carries what the child shares.
- Full tools, sequential (owner's call). `task` is a mutating tool, so calls run one at a time and a child interrupted by a crash is `unknown` on resume, like any mutating call (0011). The child's edits are real edits in the shared workspace.
- The child gets its own session log. The parent's tool result ends with a footer naming the child session, so `cookcode replay` and `export` reach it.
- Structured report (owner's call): sections Summary, Files, Evidence, Open questions. A stop hook on the child (`report`) sends it back while sections are missing, up to `max_stop_blocks`. The report is capped at 8000 chars before it reaches the parent.
- `CHILD_MAX_TURNS` is 25, so a child cannot spend the parent's whole budget.

## Consequences
- The parent pays for the report only. What the child read stays in the child's log. The sub-agent ablation (`sub-on` against `ctx-raw` on investigate and refactor tasks) measures parent input tokens and total tokens, since delegation can lower the first and raise the second (0036).
- Sequential children are slower than parallel ones, and they never race on the same files. Parallel fan-out would need workspace isolation per child, which is a different design.
- Sharing the approver means a child in ask mode asks the user, and the prompt names the child call, so delegation never widens permissions.

## What would change it
Read-only fan-out (many parallel investigators) becoming the main use. That wants parallel children with read-only tools and no shared writes.
