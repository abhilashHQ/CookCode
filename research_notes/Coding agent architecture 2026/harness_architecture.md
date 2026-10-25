# Coding Agent Harness Architecture (as of Oct 2026)

## Q1. Agent loop and session event log: which harnesses use an append-only event log as source of truth, and what do the event/item types look like?

### Takeaway
The best-documented case of an event-sourced harness is the OpenHands Software Agent SDK: immutable typed events in an append-only log, with the agent itself a stateless spec that steps through events. Claude Code persists JSONL transcripts and streams typed JSON messages, but its internal transcript schema is not verified here. The Codex loop body could not be fetched, so its item model is only partially verified through the app-server protocol surface.

### Cited Findings
- OpenHands SDK: nine components; event-sourced state is §4.2. Base `Event` is immutable with ID, timestamp, and source, serialized via discriminated unions. Event types: `MessageEvent`, `ActionEvent` (tool calls), `SystemPromptEvent`, `CondensationSummaryEvent`, `ObservationBaseEvent` with children `ObservationEvent`, `UserRejectObservation`, `AgentErrorEvent`; internal-only: `ConversationStateUpdateEvent`, `CondensationRequest`, `Condensation`, `PauseEvent`: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1); abstract at [arXiv](https://arxiv.org/abs/2511.03690)
- OpenHands SDK: `ConversationState` is the only mutable component; Agent, Tool, and LLM objects are immutable and serializable. State splits into mutable metadata (`agent_status`, `stats`, `confirmation_policy`) and an append-only `EventLog`: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- OpenHands SDK persistence: metadata is rewritten to `base_state.json` on each change; `EventStore` writes each event as its own JSON file in a directory, so only new events hit disk. Resume loads `base_state.json` and replays the event directory: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- OpenHands SDK loop: the agent runs one event-driven step at a time and emits events through callbacks `on_event(event: Event) -> None`. Tool dispatch: LLM proposes JSON tool call, validated against an `Action` Pydantic model, run by a `ToolExecutor`, returned as an `Observation` converted to LLM content. Tools are referenced by registered name so specs stay serializable: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- OpenHands SDK condensation: condensation results are stored as events and applied before each LLM call, so the full log is preserved: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- Codex: the harness is described as the component that runs the agent loop, and the blog is the first in a planned series. Loop details (prompt assembly, streaming, dispatch) were not retrievable. openai.com returned HTTP 403 to fetch, and only the intro was visible in search: [Unrolling the Codex agent loop, Michael Bolin, Jan 23 2026](https://openai.com/index/unrolling-the-codex-agent-loop)
- Codex app-server item and event names seen in the README: `mcpToolCall`, `TurnAborted`, `Error`, `ThreadRolledBack` (kept for replaying old rollouts), `turn/completed`, and `thread/items/list` (paginated by item anchor cursor). The README does not document `item/*` notification names: [codex-rs/app-server README](https://github.com/openai/codex/tree/main/codex-rs/app-server)
- Claude Code headless `stream-json` is newline-delimited JSON with message types `system` (subtype `init`, carrying model, tools, `mcp_servers`, `plugins`, and an optional `capabilities` array such as `interrupt_receipt_v1`), `assistant` and `user` (with `parent_tool_use_id` for subagent nesting), `stream_event` (only with `--include-partial-messages`), `result` (last line), plus `system`/`api_retry` and `system`/`plugin_install`: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- Claude Code: the Agent SDK is described as giving "the same tools, agent loop, and context management that power Claude Code.": [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- pi (Mario Zechner): the loop runs until the model returns a response with no tool calls. No max-steps setting. The loop emits events "for everything" to support reactive UIs: [pi blog post, 2025-11-30](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- mini-swe-agent: history is strictly linear. Each step appends to the message list sent to the model on the next step, and the README says trajectory and LM messages are the same thing: [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)

### Inferences
- The field has converged on a typed, append-only log for crash recovery and replay (OpenHands explicitly; Codex via `ThreadRolledBack` replay semantics and rollout compression; Claude via transcript files). The shape difference is whether the log is the single source of truth (OpenHands) or a side record (most others, unverified).
- Two event layers recur: a model-visible layer (messages, tool calls, tool results) and an internal layer (state updates, pauses, condensation). OpenHands makes this split explicit in its type table.
- Dispatch-by-registered-name plus schema-validated args is the common pattern (OpenHands `Action` models, pi TypeBox schemas, Claude tool-permission rule strings).

### Gaps
- Codex loop internals and rollout file format: openai.com blocked, and the `codex-rs/protocol` README does not list type names. Need a direct read of `codex-rs/protocol/src` and `codex-rs/rollout` in a local clone.
- OpenHands paper §4.5 step function wording and whether `on_event` is the only observer path were summarized, not verified verbatim.
- Claude Code internal transcript JSONL schema: only referenced, not fetched ([sessions docs](https://code.claude.com/docs/en/sessions) not read).
- OpenCode, Goose, Aider, Cline, Amp: no verified event-model facts in this pass.
- Method note: WebFetch results were summarized by a small model. Quoted phrases from fetched pages should be checked against the source before reuse.

## Q2. Separation of core from UI: library plus clients, app-server protocols, ACP, headless output modes

### Takeaway
The strongest verified signal is Codex: its protocol crate is an explicit boundary between `codex-core`, `codex-tui`, and an external app-server protocol, and it ships a JSON-RPC-style app-server with stdio, in-process, and WebSocket transports. ACP is the editor-facing standard for the same split, and Claude Code exposes its loop through `claude -p` with `stream-json`. OpenHands separates SDK, tools, workspace, and a REST/WebSocket agent server.

### Cited Findings
- Codex `codex-rs/protocol` README: defines types for "communication between `codex-core` and `codex-tui`" (internal) and types "used with `codex app-server`" (external). It should have minimal dependencies and avoid "material business logic," with `Ext`-style traits in other crates: [codex-rs/protocol README](https://github.com/openai/codex/tree/main/codex-rs/protocol)
- Codex app-server transports: local stdio, in-process, and WebSocket, plus "remote-control" peers. Requests carry `method`, `id`, `params`; responses carry `id` and `result`; notifications carry `method` and `params` with no `id`. Thread methods include `thread/start`, `thread/resume`, `thread/fork`, `thread/read`, `thread/list`, `thread/archive`, `thread/delete`, `thread/revert`; turn methods include `turn/start`, steering, `turn/completed`: [codex-rs/app-server README](https://github.com/openai/codex/tree/main/codex-rs/app-server). Framing (JSONL vs length-prefixed) was not stated in the README.
- ACP: JSON-RPC over stdio. The editor starts the agent as a subprocess on demand, and "all communication happens over stdin/stdout." One connection can run several concurrent sessions. The agent streams updates via notifications and can send requests back to the editor, such as permission requests: [ACP architecture](https://agentclientprotocol.com/overview/architecture)
- ACP methods (from the protocol overview): `initialize`, `authenticate`, `logout` (capability-gated), `session/new`, `session/load` (capability `loadSession`), `session/prompt`, `session/cancel` (notification), `session/update` (notification, Agent to Client), `session/request_permission`, `fs/read_text_file`, `fs/write_text_file`, `terminal/create`, `terminal/output`, `terminal/release`, `terminal/wait_for_exit`, `terminal/kill`: [ACP protocol overview](https://agentclientprotocol.com/protocol/overview)
- ACP reuses MCP types where possible. The editor passes its MCP server config to the agent, which connects directly. Because agents may only support MCP over stdio, the editor can run a proxy that tunnels MCP back to itself: [ACP architecture](https://agentclientprotocol.com/overview/architecture)
- Claude Code `-p` modes: `--output-format` is `text` (default), `json`, or `stream-json`. Exit code 0 on success, non-zero on run failure: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- OpenHands SDK packages: `openhands.sdk` (Agent, Conversation, LLM, Tool, MCP, events), `openhands.tools`, `openhands.workspace` (Docker or hosted execution), `openhands.agent_server` (REST/WebSocket for remote execution): [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- Goose (secondary sources only): Rust workspace with crates `goose`, `goose-cli`, `goose-server`, `goose-mcp`, `goose-acp`; built-in extensions run as in-process MCP servers. Source is a Mintlify docs mirror and a third-party blog: [Goose architecture (Mintlify)](https://www.mintlify.com/block/goose/development/architecture), [third-party overview](https://codex.danielvaughan.com/2026/04/07/goose-mcp-native-architecture/)
- OpenCode: a TS/Bun-style monorepo (`packages/`, `sdks/vscode`, `services/`, Turborepo). The client/server split is not described on the fetched page: [OpenCode repo](https://github.com/sst/opencode)

### Inferences
- The "core as library, UI as client, protocol in between" pattern is now standard. Codex makes it explicit in crate layout, ACP standardizes it for editors, and Claude's `-p` plus `stream-json` gives scripts the same event stream without a long-lived server.
- For a from-scratch Python CLI, the cheapest version is one typed event union plus a JSONL writer on stdout. ACP compatibility is worth designing for only if editor integration is a goal, since ACP's surface (sessions, permissions, fs and terminal delegation) is large.

### Gaps
- Codex app-server framing and the exact `item/*` notification set were not verified.
- OpenCode's client/server boundary (HTTP API, SDK, TUI) was not verified; `V2_HTTP_API_AUDIT.md` hints at an HTTP API but its content was not read.
- Goose facts rest on secondary sources; the primary repo was not fetched.
- ACP `session/request_permission` direction is described inconsistently in the two fetched pages (one says "Client method," the architecture page says the agent sends permission requests). Treat as agent-to-client based on the architecture page, but verify in the schema.

## Q3. Cancellation and interrupts mid-stream and mid-tool; dangling tool calls on resume

### Takeaway
Claude Code documents the most concrete semantics: SIGINT ends the turn cleanly, SIGTERM kills the process tree and leaves the turn unfinished with no result recorded, and resume leaves the interrupted turn in place unless an env var tells it to continue. ACP makes cancel a notification and reports a stop reason on the prompt response. pi threads aborts through the whole pipeline and returns partial content marked `aborted`.

### Cited Findings
- Claude Code SIGTERM: exits with code 143, leaves the in-progress turn unfinished and records no result for it. SIGINT or SDK `interrupt()` ends the turn instead: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- On SIGTERM, Claude Code kills the process tree of any running Bash command, runs `SessionEnd` hooks, and starts no new tool call or model request. A running command is recorded as killed in the session. A pending permission prompt is left unanswered on raw SIGTERM; via the SDK, input closure cancels it: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- On resume, the interrupted turn is left as is and the next prompt drives the conversation. `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` makes Claude continue the interrupted turn instead: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- Claude Code `system/init` advertises `capabilities` such as `interrupt_receipt_v1` and `interrupt_cancel_queued_v1` for feature detection: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- ACP: `session/cancel` is a notification (no response expected). The turn ends and the agent returns a stop reason in the `session/prompt` response; the value set was not listed: [ACP protocol overview](https://agentclientprotocol.com/protocol/overview)
- pi: aborts are supported throughout the pipeline including tool calls. Aborted requests can return partial content with `stopReason` set to `aborted`. Steering works by queuing messages that are injected after the current turn; queue modes are one-at-a-time or all-at-once: [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- OpenHands: `pause()` persists state and emits a `PauseEvent`; `run()` resumes from the same point. Status values include RUNNING, PAUSED, FINISHED, ERROR: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- Codex: `TurnAborted` is an event whose error can carry `TooManyDenials` (`turn.error.codexErrorInfo = "tooManyDenials"`). Turn status values include `cancelled/interrupted`: [codex-rs/app-server README](https://github.com/openai/codex/tree/main/codex-rs/app-server)

### Inferences
- The clean design is: cancel is a state transition written to the log (PauseEvent, TurnAborted, Claude's "killed" record), not an exception that leaves the log half-written. Resume then has a defined state to read.
- Claude's choice to leave the interrupted turn alone on resume, with an opt-in to continue, is the most explicit policy seen. It implies the harness must decide per interrupted tool call whether it is safe to replay. Only Claude documents this.

### Gaps
- How any harness synthesizes missing `tool_result` blocks for dangling `tool_use` on resume (the usual Anthropic/OpenAI API invariant) was not verified for any harness. This is a key gap for the build.
- Mid-stream cancellation of a partially streamed assistant message (whether the partial text is kept in the log) is only verified for pi.
- Codex mid-tool interrupt behavior: not verified beyond the status names.

## Q4. Permissions, approvals, sandbox policies, hooks

### Takeaway
Permission models split into two camps. OpenHands gives a pluggable security analyzer plus confirmation policy that can change mid-session and pauses the agent. Claude Code offers permission modes, rule-string allowlists, and hooks that run even in headless mode unless `--bare` is set. pi takes the opposite stance and runs YOLO by default. Codex uses an automated reviewer (Guardian) with a denial circuit breaker, but its standard approval flow was not verified.

### Cited Findings
- OpenHands: `SecurityAnalyzer` rates each call low, medium, high, or unknown risk. `ConfirmationPolicy` decides whether approval is needed. When it is, the agent enters `WAITING_FOR_CONFIRMATION`; on rejection it may retry with safer options. Built-ins: `LLMSecurityAnalyzer` (adds a `security_risk` field) and `ConfirmRisky` (blocks above a threshold, default high). Policies can change mid-session: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- Claude Code permission modes: `auto` (classifier reviews actions), `dontAsk` (deny anything that would prompt), `acceptEdits` (auto-approves file edits and common fs commands). `--allowedTools` uses rule syntax such as `Bash(git diff *)`. `--permission-prompts none` denies anything that would prompt unless a `PermissionRequest` hook allows it: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- Claude Code hooks: a `-p` session runs hooks from the project's `.claude/settings.json` and servers from `.mcp.json` even in an untrusted folder, unless `--bare` is used: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- Codex app-server: approvals appear as MCP elicitations (`mcpServer/elicitation/request`) and Guardian, an automated reviewer with a denial limit. The README does not describe standard command or file-change approval requests: [codex-rs/app-server README](https://github.com/openai/codex/tree/main/codex-rs/app-server)
- ACP: the agent requests tool-call permission from the client via `session/request_permission`: [ACP protocol overview](https://agentclientprotocol.com/protocol/overview)
- pi: YOLO by default, no permission prompts. The author calls other agents' safeguards "security theater" and suggests containers instead: [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)

### Inferences
- The permission decision is a function of (tool call, policy, mode) and is best evaluated as a pure check before dispatch. OpenHands' pause-on-confirm and Claude's "deny if no host" are two implementations of the same gate.
- Hooks are a second extension surface that runs outside the model loop, and Claude's headless behavior means hooks are a security boundary worth thinking about for any CI use.

### Gaps
- Codex sandbox policy (filesystem/network modes) and how Guardian's denial limit is configured: not verified in this pass.
- Claude Code hook event list beyond `SessionStart`, `Setup`, `SessionEnd`, `Elicitation`, `PermissionRequest`: referenced, not fetched.
- Aider, Cline, Amp, Goose permission models: not researched.

## Q5. Session persistence, resume, fork: storage formats

### Takeaway
Storage formats in the verified set are: one JSON file per event plus a metadata file (OpenHands), rollout files with compression and a replay-aware event for rollback (Codex), and `.jsonl` transcripts resumable by session ID or file path (Claude Code). pi supports resume, continue, and branching with HTML export, but its article does not name a storage format.

### Cited Findings
- OpenHands: `base_state.json` plus per-event JSON files, resumable by replay: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- Codex: `thread/start`, `thread/resume`, `thread/fork`, `thread/read`, `thread/items/list`, and `rollout/compress` (best-effort background compression of local rollout storage; clients sharing a Codex home must support compressed rollouts). Old rollouts may contain `ThreadRolledBack` events, which are still replayed: [codex-rs/app-server README](https://github.com/openai/codex/tree/main/codex-rs/app-server)
- Claude Code: `--continue` continues the most recent conversation; `--resume <session-id>` resumes a specific one; `--resume` also accepts the absolute path to a session's `.jsonl` transcript. Sessions are findable by ID from any project on the machine. `--output-format json` returns `session_id` for capture: [Claude Code headless docs](https://code.claude.com/docs/en/headless)
- pi: session continue, resume, and branching, plus HTML export. Goal stated as a "cleanly documented session format" for post-processing; no format named: [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- mini-swe-agent: the README mentions a trajectory inspector; file format and location not given: [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)

### Inferences
- Resume-by-replay plus fork-by-copying-a-prefix is the common model. Fork is cheap if the log is append-only and events carry IDs (OpenHands) or item IDs (Codex `thread/items/list` anchors).
- Per-event files (OpenHands) and JSONL (Claude) are both append-friendly. JSONL is simpler for a solo build and matches the "post-process the session" goal pi states.

### Gaps
- Codex rollout on-disk format (JSONL or otherwise) and directory location: not verified.
- Claude Code transcript schema and branching semantics: referenced sessions doc not read.
- OpenCode storage backend (SQLite vs files): not verified; the README did not say.
- pi on-disk format: not stated in the article.

## Q6. Minimalist harnesses: mini-swe-agent, pi, Aider, OpenCode, Goose, Cline, Amp, and what the authors say about tradeoffs

### Takeaway
The minimal camp's own claims are consistent: a short system prompt, few tools (bash, or read/write/edit/bash), linear history, and no MCP, sub-agents, or plan mode by default. mini-swe-agent's authors argue an agent can be "100x simpler" and still work. pi's author argues tool schemas are already known to models and that context control matters more than features. Aider, Cline, and Amp were not researched in this pass.

### Cited Findings
- mini-swe-agent: "What if our agent was 100x simpler, and still worked nearly as well?" The goal is "to be a hackable tool, not a black box." It does not use native tool calling, so any model works. Actions run via `subprocess.run` with no persistent shell. Sandboxing is done by swapping in `docker exec`: [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)
- mini-swe-agent: core class is `DefaultAgent` in `src/minisweagent/agents/default.py`. The README says the agent class is "some 100 lines of python": [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent). PyPI reports SWE-bench Verified above 74% for v2: [PyPI](https://pypi.org/project/mini-swe-agent/); scores vary by model and version, per secondary sources.
- pi: four tools `read`, `write`, `edit`, `bash`; `grep`, `find`, `ls` exist but are off by default. System prompt plus tool definitions under 1000 tokens. "Exactly controlling what goes into the model's context yields better outputs." No MCP (author cites Playwright MCP at 13.7k tokens of tool descriptions), no sub-agents ("black box within a black box"), no plan mode (PLAN.md instead), no background bash (tmux), no built-in to-dos (TODO.md): [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- pi: rejects full-screen TUI in favor of terminal-native scrollback, with retained-mode components (`render(width)`, `handleInput(data)`) and differential rendering. Rejected Ink, Blessed, and OpenTUI: [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- OpenCode: built-in agents `build` (full access), `plan` (read-only), and `general` subagent: [OpenCode repo](https://github.com/sst/opencode)
- Goose: MCP-native; every capability including built-ins runs as an MCP server: [Goose architecture (Mintlify)](https://www.mintlify.com/block/goose/development/architecture) (secondary)

### Inferences
- Minimalism is mostly a context-budget argument (pi), an evaluation-legibility argument (mini-swe-agent: linear, debuggable trajectories), and an extension-by-convention argument (pi: CLI tools with READMEs over MCP). These are opinions from authors, not measured results, and should be treated as such.
- For a solo engineer, the practical floor is: one loop, a small typed tool set, a JSONL log, and a permission check before dispatch. Everything above that (MCP, sub-agents, plan modes) is add-on surface.

### Gaps
- Aider, Cline, Amp: no primary sources fetched. Their authors' design statements are not in this file.
- mini-swe-agent loop internals (answer parsing, completion sentinel, trajectory format) were not verified; `default.py` needs a direct read.
- Pi author's claims are from a single blog post as summarized; the talk "Building pi in a World of Slop" was not read.

## Q7. Language and runtime choices: Codex (Rust), OpenCode (TS), OpenHands (Python), and others

### Takeaway
The verified facts are OpenHands is Python with Pydantic-validated action models, and pi is Node.js/TypeScript with TypeBox schemas and a typed model registry. The Codex Rust choice is asserted in the task brief and implied by the crate layout, but its stated rationale was not retrieved. OpenCode's TS/Bun stack is inferred from root files. The Goose Python-to-Rust rewrite comes only from a low-quality aggregator and should be treated as unverified.

### Cited Findings
- OpenHands: Python packages with Pydantic `Action` models for tool-call validation: [arXiv 2511.03690 (HTML)](https://arxiv.org/html/2511.03690v1)
- pi: runs on Node.js with a terminal. The author calls building a TUI framework on Node "a fun little challenge," says memory and performance costs are acceptable on modern V8, and builds around TypeScript typing with a typed model registry and TypeBox schemas. No standalone rationale for TypeScript is given: [pi blog post](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Codex: `codex-rs` is a Rust workspace; the protocol crate is designed with minimal dependencies and no business logic, which is consistent with a Rust core shared by CLI and app-server: [codex-rs/protocol README](https://github.com/openai/codex/tree/main/codex-rs/protocol)
- OpenCode: root files include `bun.lock`, `bunfig.toml`, `tsconfig.json`, `turbo.json`, indicating Bun and TypeScript in a Turborepo monorepo. This is an inference from file names, not stated on the page: [OpenCode repo](https://github.com/sst/opencode)
- Goose: Rust crates per the Mintlify docs mirror; a third-party source says Block rewrote from Python to Rust "to enhance embeddability and performance," and says the desktop shell is moving from Electron to Tauri. Sources conflict and are low quality: [third-party overview](https://codex.danielvaughan.com/2026/04/07/goose-mcp-native-architecture/), [Mintlify](https://www.mintlify.com/block/goose/development/architecture)

### Inferences
- Rust for the Codex core is consistent with the protocol crate's minimal-dependency rule and the need to share one core across CLI, TUI, and app-server. The Codex team's own rationale is still unverified.
- TypeScript for pi was chosen for the terminal and tooling ecosystem and for typed schemas. Python for OpenHands fits its research and benchmark origin.
- For a Python build, Pydantic-validated typed events plus a pure-Python loop match what OpenHands does. Rust or Go are not required for a single-user CLI and would add cost.

### Gaps
- Codex's stated reason for the Rust rewrite: not retrieved. Need the blog body or the Codex repo docs.
- OpenCode's implementation language and runtime are inferred, not stated.
- Goose rewrite history conflicts across sources; primary repo not checked.
- Startup latency and binary distribution tradeoffs: not researched.

## Not covered in this pass
- Aider, Cline, and Amp primary sources (repos, docs, author posts): no fetch attempted.
- The OpenHands SDK paper's V0 vs V1 failure claims: the search abstract says production data shows substantially fewer failures, but the fetched paper body reports no quantitative V0 baseline (§3 is qualitative, §5.2 is V1-only). The conflict is unresolved.
- Benchmarks and context compaction were out of scope.
