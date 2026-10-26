# Flagship 1: design spec

Status: draft for review, updated 2026-10-26 after the grilling session. Nothing here is implemented.
Companion pages: `docs/blueprint.html` (visual overview), `reports/Coding agent architecture 2026.md` (evidence).
Working name: `<codename>` until the naming gate.

## 1. What this is

A coding agent CLI written from scratch in Python. It runs in a terminal against a local repo, and the same core runs headless inside Terminal-Bench containers (via Harbor) and inside Flagship 2's sandboxes.

**Purpose: an interview showcase.** It exists to demonstrate system design and applied AI concepts that hold up under cross-questioning. Outside users are not a goal. Its consumer is Flagship 2, through a versioned headless contract. Numbers back the concepts up, but the concepts and the decisions behind them come first.

What sets it apart is that every session is a replayable, measurable log. Context strategies, prompts and features can be compared on the same sessions and the same task suite.

### Goals
- A working daily-driver agent: read, edit, search, run commands, with permission gating and safe interruption.
- A core that is a library, consumed by a plain CLI, a TUI, a headless JSONL mode and a Harbor adapter.
- Context management that is measured: masking vs. summarization vs. hybrid, with cost, solve rate and cache effects reported.
- A clean contract for Flagship 2: headless events, resume, a pluggable Environment, and a log that makes kill-and-resume safe.
- A decision record for every gate decision, plus a walkthrough and likely cross-questions at the end of every milestone.

### Non-goals (for this spec)
- Provider breadth. Two adapters for two genuinely different protocols (by M5) prove the abstraction, and more adapters add nothing conceptually.
- Native Windows. The target is Linux; development happens in WSL2.
- An IDE extension, ACP server or web UI. These can come later as clients.
- A multi-provider gateway (left out on purpose per FLAGSHIPS.md).
- Training or fine-tuning models.

## 2. Success criteria

Concepts (each has a decision record; headline ones also have an experiment):

| # | Concept | Stage | Evidence |
|---|---|---|---|
| 1 | Event-sourced log, schema evolution | 1 | Record, replay command |
| 2 | Write-ahead intent, crash recovery, outcome unknown | 1 | **Headline:** chaos test vs. naive re-run |
| 3 | Cancellation, structured concurrency | 1 | Record, interrupt tests |
| 4 | Adapters over divergent provider protocols | 2 | Two adapters, reasoning replay rules |
| 5 | Environment abstraction | 2 | Same tools locally, in Harbor, in Flagship 2 |
| 6 | Tool-call concurrency control | 1 | Record |
| 7 | Context engineering | 3 | **Headline:** five-strategy ablation |
| 8 | Cache-aware request building | 3 | Cache-read share per strategy |
| 9 | Tool and agent-computer interface design | 1 | Record, failed-edit recovery stats |
| 10 | Eval methodology, variance | 3 | **Headline:** variance study |
| 11 | Verification loops | 4 | **Headline:** verify-first on vs. off |
| 12 | Sub-agents as context isolation | 4 | Record, token comparison |
| 13 | Failure analysis from traces | 3, 4 | Write-up |
| + | Long-term memory with code provenance | 4 | Memory ablation |

Product:
- Ctrl-C at any point never leaves a session that fails to resume.
- Runs on Linux and WSL2.

Engineering:
- Every loop behavior is covered by tests on the FakeProvider, with no network.
- Crash test: kill -9 the process at random points across 1,000 scripted runs; every log replays to a valid state and resumes.
- Recorded provider streams (cassettes) replay in CI so adapter regressions are caught offline.

Experiments (each published with task set version, harness commit, model, effort, limits and attempts):
- Chaos test (Stage 1, no model cost): kill -9 at random points in scripted runs with mutating commands. Compare our three-state repair against a naive re-run of whatever was in flight. Report duplicated side effects and resume success.
- Variance study (Stage 3): one config run k times. Report the spread and how many repeats it takes to separate two configs. This sets the repeat count for everything else.
- Context ablation (Stage 3): raw, mask, mask batched, summarize, hybrid. Solve rate, cost per task, turns, cache-read share.
- Verify-first (Stage 4): on vs. off on bug-fix tasks. Solve rate and failed-for-the-right-reason rate.
- Memory ablation (Stage 4): none vs. notes file vs. fact memory.
- One headline Terminal-Bench run on a pinned version against Terminus 2 on the same model.
- A failure-analysis write-up built from traces.

Models: free tiers (OpenRouter, Gemini) for development and smoke tests. Ablations run on one cheap open-weight model, and numbers only count once they're rerun on a pinned model with paid credits (Azure, when available).

Tasks: our own suite of 30 to 40 small deterministic tasks on fixture repos (long-session tasks for context, real bugs for verification), plus a fixed, published Terminal-Bench slice.

## 3. Architecture

Three layers. Clients consume the core's event stream and send input and approvals back. The core runs the loop, builds model requests from the log, and gates tool calls. Everything external sits behind one of four interfaces: Provider, Environment, SessionStore, Approver.

```
clients:     plain CLI | headless JSONL | Harbor adapter | TUI (M8) | Flagship 2
core:        Agent loop | Context builder | Permission gate | Tool registry | Budget
interfaces:  Provider | Environment | SessionStore | Approver
```

Dependency rule: `core` imports only interfaces and its own types. It never prints, reads stdin, opens sockets, spawns processes or reads env vars for config. Clients and adapters do that.

## 4. Core contracts

Signatures are sketches to pin the shape. Exact names get settled in M0.

### 4.1 Items (the neutral model)

```python
Item = Message | Reasoning | ToolCall | ToolResult | OtherItem

Message:    role ("user" | "assistant" | "system"), parts (text now, images later)
Reasoning:  provider, model_family, payload (opaque, verbatim), summary_text (optional)
ToolCall:   id (ours), provider_call_id, provider_item_id (optional), name, arguments (JSON)
ToolResult: call_id (ours), status ("ok" | "error" | "interrupted" | "denied" | "unknown"), content
OtherItem:  provider, raw (anything we don't model yet; replayed to the same provider only)
```

Rules:
- Reasoning goes back only to the same provider and model family. When it doesn't match, the context builder either drops it or turns it into labeled text, and the session logs which one it did.
- Parallel tool calls are consecutive ToolCall items from one model response.

### 4.2 Events (the log)

Every event has `seq` (monotonic per session), `id`, `ts`, `schema_version`, `layer` ("model" or "internal"), and a typed body. Pydantic discriminated union with an `Other` fallback.

| Event | Layer | Purpose |
|---|---|---|
| `session_started` | internal | Run config: model, provider, effort, tool set and versions, system prompt hash, harness commit, limits. Hashed into `config_id`. |
| `user_message` | model | Initial prompt or follow-up. |
| `steer_message` | model | User input injected while a turn is running. Picked up at the next step boundary. |
| `model_request_started` | internal | request_id, context view id (which compaction strategy produced it), token estimate. |
| `item` | model | A finished Item from the model. |
| `model_request_finished` | internal | stop_reason (normalized), usage (new input, cache read, cache write, output, reasoning), raw usage, raw provider payload ref. |
| `tool_call_started` | internal | Write-ahead intent: call id, tool, args, side-effect class. Written and flushed **before** execution. |
| `tool_call_finished` | internal | Exit status, duration, output size, truncation info. Followed by the `item` carrying the ToolResult. |
| `approval_requested` / `approval_resolved` | internal | Gate decisions, including the policy rule that decided. |
| `checkpoint_created` | internal | File snapshot taken before a mutating tool ran (for undo). |
| `compaction_applied` | internal | Strategy, event range affected, tokens before and after. |
| `interrupted` | internal | Where the interrupt hit (streaming, tool, idle) and what was killed. |
| `repaired` | internal | Synthetic results written for dangling calls on resume, with status and reason. |
| `budget_exceeded` | internal | Which limit (turns, tokens, cost, wall clock). |
| `error` | internal | Classified: provider (retryable or not), tool, environment, internal. |
| `session_finished` | internal | Final status and totals. |

### 4.3 Provider

```python
class Provider(Protocol):
    def capabilities(self, model: str) -> ModelCaps: ...   # context window, parallel tools, reasoning kind, caching
    def stream(self, req: ModelRequest, cancel: CancelToken) -> AsyncIterator[StreamEvent]: ...

StreamEvent = ItemStarted | ItemDelta | ItemDone | UsageReport | StreamEnd
```

- `ItemDelta` is for display only. The loop logs `ItemDone` items only.
- `StreamEnd.stop_reason` is normalized to: `end_turn`, `tool_use`, `max_tokens`, `refusal`, `aborted`, `error`.
- On cancel, the stream ends with `aborted`, and the adapter returns any finished items it has.
- The adapter keeps the raw request and response payloads available for the log (stored by reference).
- The adapter owns retries for transient errors (429, overload, 5xx), with backoff and a cap. Each retry is logged.

### 4.4 Environment

```python
class Environment(Protocol):
    async def exec(self, cmd: str, *, cwd: str, timeout_s: float, max_output_bytes: int,
                   cancel: CancelToken) -> ExecResult: ...
    async def read_file(self, path: str) -> bytes: ...
    async def write_file(self, path: str, data: bytes) -> None: ...
    async def snapshot(self, paths: list[str]) -> SnapshotRef: ...   # for undo
```

Implementations: `LocalEnvironment` (M0), `HarborEnvironment` (M4), Flagship 2 sandbox (later).
`exec` runs in its own process group, closes stdin, caps output (head and tail kept, with a truncation notice), and kills the whole group on timeout or cancel.
Open at the M2 gate: whether grep and glob are Environment primitives (so each environment can pick ripgrep or a fallback) or tools built on `exec`.

### 4.5 Tools

A tool is data plus a handler: name, description, JSON schema for args, `side_effect` class (`read_only`, `mutating`, `external`), and an async handler that receives args and an Environment. Tool specs are data so MCP tools can register the same way later.

Initial set: `read` (windowed, line numbers), `edit` (exact match with distinct errors for zero and multiple matches), `write`, `grep`, `glob`, `bash`.
Every mutating tool snapshots the files it touches before running. A failed edit leaves the file unchanged and returns the reason.
Read-only calls from one model turn can run concurrently. Mutating calls run in order.

### 4.6 Approver and permissions

```python
class Approver(Protocol):
    async def decide(self, call: ToolCall, risk: Risk) -> Decision: ...   # allow | deny(reason) | allow_always(rule)
```

`Risk` is the tool's side-effect class plus whichever policy rule matched (for example a bash command matching a deny pattern). The gate is a pure function of call, policy and mode. It runs before dispatch.
Modes: `ask` (default interactive), `auto-edit` (file edits allowed, bash asks), `yolo` (all allowed, intended for containers).
The CLI asks the user. Headless mode answers from the policy file or over stdin as JSON. Flagship 2 answers over its API.

### 4.7 SessionStore

Append-only JSONL, one file per session, with a single-writer lock. Each event is flushed when written, and `tool_call_started` is fsynced before the tool executes.
Operations: `append`, `read(from_seq)`, `fork(at_seq) -> new session`, `list`.
Raw provider payloads go in a sidecar file keyed by request_id, so the main log stays readable.
Secrets are redacted before anything is written (see 9).

## 5. Loop semantics

One step means: build request, stream, log items, run tool calls, log results. The loop repeats steps until one of:
- the model returns no tool calls (`end_turn`)
- a budget limit is hit (turns, tokens, cost, wall clock), logged as `budget_exceeded`
- a non-retryable error
- an interrupt

Steering: user input that arrives during a turn is queued and becomes a `steer_message` at the next step boundary. It never splits a tool-call/result pair.

Context overflow: if a request would exceed the window, the context builder applies the active compaction strategy. If the provider still rejects the request for length, the builder retries once with emergency masking, and that is logged.

Done detection beyond `end_turn` (stuck-loop detection, repeated identical calls) is decided at the M1 gate and measured at M6.

## 6. Durability and resume

The log is written so a crash or kill at any point resumes cleanly, and so Flagship 2 never repeats a side effect.

- A tool call has three states in the log: model-emitted (`item` ToolCall), started (`tool_call_started`), finished (`tool_call_finished` plus the ToolResult item).
- On resume, the repair pass scans for calls without results:
  - Emitted but never started: status `interrupted`, "not executed". These are safe to re-issue.
  - Started but never finished, `read_only`: status `interrupted`, "outcome unknown, safe to retry".
  - Started but never finished, `mutating` or `external`: status `unknown`, "may have run; inspect before retrying". The repair pass never re-executes these.
- Each synthetic result is logged as a `repaired` event and then a ToolResult item, so the next request is valid for every provider.
- An interrupted turn is left alone on resume unless the user continues it.
- LLM calls are never re-made on replay. Replay reads the logged items.

## 7. Context builder

The context builder turns the log into a `ModelRequest`. It is pure: the same log plus the same strategy gives the same request.

- Prefix stability for prompt caching: tools and system prompt first and unchanged for the whole session, then history in log order. Cache breakpoints are placed by the adapter from hints the builder gives it.
- Strategies (selected by config, recorded in `model_request_started`):
  - `raw`: full history.
  - `mask`: replace tool outputs older than N steps with a placeholder.
  - `mask_batched`: same, but only re-mask when crossing a threshold, to keep cache hits between thresholds.
  - `summarize`: summarize older steps with a model call, keeping the last N verbatim.
  - `hybrid`: masking, with summarization as a fallback near the window.
- A token ledger per request splits tokens into system, tools, user, assistant, reasoning and tool output.
- Instruction files: `AGENTS.md` (and `CLAUDE.md` if present) from the repo root and the current directory are loaded into the system section.

## 8. Clients

- Plain CLI (M3): streams text, shows tool calls and diffs, asks for approvals, Ctrl-C handling.
- Headless (M4): `<name> -p "<prompt>" --json` writes one JSON event per line. `session_started` comes first and `session_finished` last. Approvals come from a policy file or from stdin. The schema is versioned, and the JSON schema is exported from the pydantic models.
  - Exit codes: 0 done, 1 error, 2 budget exceeded, 130 interrupted.
- Commands: `resume <id>`, `fork <id> [--at seq]`, `sessions`, `undo [n]`, `replay <id> --strategy <s>` (rebuild requests from a log under another strategy, for inspection and ablations), `export <id> --format atif|otel`.
- TUI (M8): Textual vs. prompt_toolkit plus Rich, decided at the gate.

## 9. Safety and hygiene

- Redaction: before writing, scan event bodies for known secret patterns (provider keys, cloud credentials, tokens, private keys) and for values of env vars the user marks secret. Replace them with `[REDACTED:<kind>]`. This applies to the log, exports and traces.
- The permission gate is the only local safety layer before M10. OS sandboxing is a candidate Environment wrapper, decided at its own gate. The reference point is bubblewrap plus seccomp, which is what both Codex and Claude Code use on Linux. Codex dropped Landlock for filesystem-restricted policies.
- Network access from tools is unrestricted locally. Flagship 2 owns network isolation.

## 10. Evaluation

- Harbor adapter (M4): `BaseAgent` subclass that runs our loop on the host with `HarborEnvironment` wrapping `environment.exec`. It reports usage and trajectory as ATIF (`schema_version` "ATIF-v1.8" as of Harbor 0.25.0, spec at docs.harborframework.com/agents/atif.md; see decision 0029).
  - Top level: `agent`, `steps`, `session_id`, `trajectory_id`, `final_metrics`, `subagent_trajectories`, `extra`.
  - Per step: `step_id`, `source`, `message`, `tool_calls` (`tool_call_id`, `function_name`, `arguments`), `observation.results` (`source_call_id`, `content`), `metrics`.
  - ATIF's pydantic models reject undeclared fields, so anything of ours goes under `extra`.
  - The join key across our log, ATIF and OTel is the provider call ID, exported as `tool_call_id` / `source_call_id`. Our internal ID stays internal.
- Run config: every eval run records dataset name and version, Harbor version, harness commit, `config_id`, model, effort, limits, attempts.
- Protocol: oracle run first, Terminus 2 baseline on the same model and budget, at least 5 attempts, mean plus min and max.
- Local regression suite (M6): 20 to 40 small tasks on fixture repos, runnable in minutes. On every change it runs against cassettes. Nightly, it runs against the real model.
- Benchmark slice for ablations: a fixed, published subset of Terminal-Bench tasks, chosen at the M6 gate to fit the budget.

## 11. Observability

The log is the trace. Exporters are projections:
- OTel spans following the GenAI semantic conventions (agent invocation, model call, tool execution), behind an optional extra so the base install stays light. The conventions moved to github.com/open-telemetry/semantic-conventions-genai. Span and attribute names get pinned from that repo's YAML at the gate, not from memory.
- ATIF for Harbor.
- A `stats` command: tokens by category, cost, cache-read share, tool error rate, per session or across a run directory.

## 12. Undo

Before a mutating file tool runs, the Environment snapshots the target files: copies stored under the session directory, recorded in `checkpoint_created` and grouped by user turn. `undo` reverts the file changes of the last turn, and `undo <n>` goes back n turns. Each restore is logged. Code and conversation are rewound separately: `undo` restores files, `fork --at <seq>` rewinds the conversation. Claude Code splits rewind the same way.

Bash side effects are not covered, and the docs say so. Claude Code's checkpoints have the same limit. A git-based shadow snapshot is an option if file copies prove too narrow, decided at the M2 gate.

## 12a. Long-term memory (Stage 4)

Supermemory-style, adapted to code. Facts are atomic statements about the repo, each with provenance: file paths, commit, content hash. Relations: `updates` (the old fact stays for history, marked not-latest), `extends`, `derives`. A consolidation pass runs at session end and reads the log, which already holds everything.

Staleness is the code-specific part. When a file's hash changes, facts that cite it are marked stale and excluded from retrieval until re-confirmed.

Entry points into the request: a small fixed profile at session start, plus a `recall` tool. Memory is never injected per turn, because that changes the prompt prefix and breaks the cache. In-session context management (section 7) stays separate: memory does nothing about tool output, which is most of the tokens.

Store: SQLite with FTS5. Vector search is added only if retrieval quality needs it, decided at the gate.

In-session `recall`: masked tool outputs stay in the log and can be fetched back by ID with the same tool, so masking loses nothing.

## 13. Stages and milestones

Four stages, each with its own architecture diagram in `docs/blueprint.html`. Stage 1 is the harness core (M0 to M2, FakeProvider only, ends with the chaos test). Stage 2 is the working agent (M3 to M5, two real adapters, headless, Harbor). Stage 3 is measurement and context engineering (M6 to M7). Stage 4 is memory and extensions (M8 to M10).

Every milestone's definition of done includes decision records for its gate, a walkthrough of the core files, and 10 to 15 likely cross-questions with answer sketches.

| # | Build | Accept when | Gate |
|---|---|---|---|
| M0 | Repo, tooling, items, events, SessionStore, FakeProvider, LocalEnvironment, redaction | Round-trip of every event type; fork; redaction tests | Codename |
| M1 | Loop, steering queue, budgets, interrupts, repair on resume, chaos test harness | Scripted scenarios on FakeProvider; chaos test vs. naive baseline published | Parallel tool policy, turn cap, stuck-loop rule |
| M2 | Tools, gate, approver, snapshots, undo | Tool tests on a temp repo; failed edit leaves file intact; process-group kill verified | Edit format details, grep/glob placement, snapshot method |
| M3 | Adapter A (OpenAI-compatible, covers OpenRouter), plain CLI, cassettes, AGENTS.md loading | Real tasks on this repo with a free model; cassette replay in CI | Free model choice, SDK vs. raw HTTP |
| M4 | Headless JSONL, Harbor adapter, ATIF export | Oracle run passes; one real task end to end in WSL2 | Schema version policy, Terminal-Bench version |
| M5 | Adapter B (native protocol with reasoning: Gemini or Anthropic), resume, fork, sessions, replay command | Reasoning round-trips on B; resume after interrupt in every state | Which native protocol, reasoning replay rules |
| M6 | Task suite, Terminal-Bench slice, run configs, stats, variance study | Variance study published | Suite composition, repeat count |
| M7 | Token ledger, compaction strategies, `recall`, cache measurement | Context ablation published | Default strategy, summarizer model, keep `recall` or not |
| M8 | Long-term memory, verify-first | Memory and verify-first ablations published | Store, fact schema, staleness rule |
| M9 | Sub-agents, skills, hooks | Sub-agent token comparison; skills measured as tokens saved | Return contract, hook API |
| M10 | MCP client, TUI, config, name | TUI runs a full session; MCP tools load lazily | TUI library, MCP deferral |

## 14. Decision log

Locked now:
1. Python 3.12+, uv, asyncio TaskGroup, pydantic v2, ruff, pyright strict, pytest.
2. Append-only event log is the source of truth. Context is a projection.
3. Item-based neutral model, with reasoning stored opaque and tagged.
4. Streaming Provider interface with cancel, plus FakeProvider first.
5. Core is a library; clients only consume events.
6. Environment interface for all tool I/O. Linux target.
7. Write-ahead tool logging and the repair rules in section 6.
8. Redaction before write.
9. Bash runs as a fresh process per call. The working directory is tracked as logged state, and environment changes don't persist (the tool says so).
10. Unknown outcomes after a crash: interactive mode asks the user, unattended runs hand the call to the model with a "verify before retrying" instruction.
11. Agents write the code. Ownership comes from the decision records, walkthroughs and cross-questions in every milestone's definition of done.
12. Development runs in WSL Ubuntu. Docker comes from Docker Desktop's WSL integration (revised at M4, decision 0031), with about 7.6 GB available.

Deferred to gates: listed in the milestone table.

## 15. Open questions

- What each provider API accepts for repaired tool results (M1, then M3 with real calls).
- Exact-match edit behavior details if we mirror a provider-native tool (M2).
- Whether reasoning payloads round-trip unchanged through the chosen endpoint (M3).
- ATIF required fields (M4).
- Harbor under WSL2 with Docker Desktop (M4).
- Cache-read cost of each compaction strategy (M7).
- OTel GenAI span and attribute names and their stability level (M6).

## 16. Table stakes vs. ours

From the competitor survey (`research_notes/Coding agent gaps 2026/competitor_features.md`):

| Feature | Seen in 4+ agents | Where it lands here |
|---|---|---|
| Project instruction files (AGENTS.md / CLAUDE.md) | yes | M3, context builder |
| MCP client | yes | M10, tools are data so it slots in |
| Multiple providers, model switching | yes | Two adapters by M5 |
| Skills / prompt templates | yes | M9, measured as tokens saved |
| Undo / checkpoints | 3 verified | M2 |
| Headless mode | 3 verified | M4 |
| Hooks | Claude Code only | M9, through the permission gate |
| Cost display and budget caps | Claude Code verified | M1 budgets, M6 `stats` |

None of the agents checked documents an "outcome unknown" state for tool calls cut off mid-run. Durable runtimes (DBOS, Cloudflare Workflows) re-run the in-flight step, and Inngest's own docs note that a side effect can commit before its result is saved. The three-state repair in section 6 is our own design, and it's the part to explain in interviews.

## 17. Repo and records

- Decision records live in `docs/decisions/`, one per gate decision: problem, options, choice, what would change it.
- Walkthroughs and cross-questions live in `docs/walkthroughs/`, one per milestone.
- When the repo goes public is still open. Decide at the end of Stage 1.
- The name is still open. The package uses a placeholder until it's picked.
