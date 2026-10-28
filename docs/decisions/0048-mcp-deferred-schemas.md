# 0048. MCP client with deferred tool schemas

Status: accepted (M10 gate, 2026-10-27)
Date: 2026-10-27

## Context
MCP lets a user plug external tool servers into the agent. A single server can expose dozens of tools, and each schema costs tokens on every request. Ten servers can put thousands of tokens in front of every call before the task starts. Every tool the model sees also widens its choices and its chances of choosing wrong. Replay adds a constraint: the tool list is part of the request, so whatever decides it has to be in the log.

## Options considered
- Load every schema up front: simple, and the cost scales with the number of servers.
- A static allowlist per server in settings: cheaper, but the user has to predict what each task needs.
- Deferred schemas: list names and one-line descriptions, and let the model load the schemas it needs with a search tool. They are sent from the next step on.
- Transports: stdio, plus streamable HTTP and SSE.

## Decision
- `mcp.py:McpHub` connects to stdio servers from `settings.mcp.servers` (stdio only, owner's call). Each server is owned by one task, because the SDK's context managers must be entered and exited in the same task. That task connects, reports its tools, then waits to be told to close.
- Tools are registered as deferred and `external`, named `mcp__<server>__<tool>`. The system prompt lists names and descriptions (capped at 160 chars).
- `tool_search(names=[...])` loads schemas. Which tools are loaded is computed from the log (`agent/tools.py:loaded_tools`, a pure function of events), and `ToolRegistry.schemas(loaded)` builds the request's tool list from it. Replay therefore rebuilds the same tool list with no hub state.
- External tools are asked in ask mode and governed by policy like any tool.
- Behind the `mcp` feature, since starting servers runs commands from settings.
- SDK 2.x errors arrive wrapped in exception groups. `_cause` unwraps them to the innermost error, so a failed connect names the real reason.

## Consequences
- The fixed cost of a server is one line per tool. The schema cost is paid only for tools the session uses, from the step after loading.
- Loading takes one extra step. For a session that uses one MCP tool once, that step costs more than the saved schemas. Deferral pays off as the server count grows.
- Remote MCP servers are not supported. Adding streamable HTTP is a transport change behind the same hub.
- The first live smoke run hit 429 exhaustion with the Terminal-Bench sweep sharing the key. The rerun called `tool_search`, then `mcp__calc__add`, and wrote the result, in 4 requests.

## What would change it
Providers adding native deferred tool loading (then use theirs), or users needing remote servers.
