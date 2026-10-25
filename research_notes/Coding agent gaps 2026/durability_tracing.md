# Durability and tracing research for the agent event log

Research date: 2026-10-22. Method: primary docs fetched directly where the URL resolved. Where I only had a search snippet, I say so. Anything I could not confirm from a primary source is marked UNVERIFIED. The span and attribute names in section B are from memory and need a check against the semconv repo before anyone pins them.

## A. Durable execution

### Consensus on LLM calls
Every system I read says the same thing: the model response is recorded, and replay reads the recorded result. The model is never called again on replay.

Temporal: the Python OpenAI Agents SDK integration says the agent loop runs in workflow code while "model calls run as Activities," and that model activities "are not repeated during Workflow replay." Conversation history lives in workflow state. Source: https://docs.temporal.io/develop/python/integrations/openai-agents

Temporal also says inline tools must not do network, DB, or filesystem I/O. I/O tools go through `activity_as_tool` or Nexus operations. MCP servers run outside the workflow, so each MCP operation is its own activity. Callbacks that run during replay (`tool_filter`, `on_approval_request`) must be deterministic. Secrets passed as `factory_argument` get recorded in workflow history, so do not pass them that way. Same page: https://docs.temporal.io/develop/python/integrations/openai-agents

The Temporal page does not give idempotency-key guidance. It says to design that yourself.

The Temporal Python Deep Agents plugin wraps each LLM call and each I/O tool call as an activity, and the docs call it experimental. Source (snippet only, not fetched): https://python.temporal.io/temporalio.contrib.deepagents.html

Cloudflare Workflows: https://developers.cloudflare.com/workflows/build/rules-of-workflows/
- A successful step "will not run again." Persisted step results are what survive restarts.
- Code outside steps "may be duplicated." Keep side effects inside steps.
- Step names are the cache key, so they must be deterministic.
- A step "might be retried multiple times," and the page says steps should be idempotent. Its recommended pattern is to check whether the work is still needed before a non-idempotent call.
- If the engine restarts mid-step, it "will start over from the beginning of that step." A side effect can commit before the step fails, so retries can repeat it.

DBOS: https://docs.dbos.dev/architecture
- Workflow inputs and step outputs are checkpointed in Postgres. On recovery, each step checks for a saved output and returns it if one exists.
- A step that was running at crash time has no checkpoint, so recovery re-runs it: "If a workflow fails while executing a step, it retries the step during recovery." After a checkpoint, the step is "never re-executed."
- Keep step outputs small. Put large artifacts in S3 and return pointers.
- The page links to a "Build Durable AI Agents" section (`/ai/ai-quickstart`). I did not fetch it, so DBOS agent specifics are UNVERIFIED.

Inngest: https://www.inngest.com/docs/learn/how-functions-are-executed
- A successful `step.run()` result is saved. On replay, "The SDK skips the callback and returns the saved result."
- Code outside a step "can run again each time the handler replays."
- The page's own example: "Make `refundOrder` idempotent because a refund can succeed before Inngest saves the step result and retries the step."
- AgentKit: the page has no AgentKit-specific guidance. AgentKit docs are UNVERIFIED.

Restate: journaling. Source (search snippet, page not fully fetched): https://docs.restate.dev/develop/ts/journaling-results
- `ctx.run` results are journaled. On retry, the journaled result is returned rather than re-running the block.
- Failures retry until a terminal error. The terminal error can trigger compensation.
- Whether a crash between the side effect and the journal write re-runs the side effect: UNVERIFIED. The idempotency-key docs were not confirmed. My guessed URL `docs.restate.dev/guides/durable-execution` returned 404.

LangGraph: the URL I expected for durability modes returned 404. The page at https://docs.langchain.com/oss/python/langgraph/durable-execution is a persistence overview. It says checkpointers "persist a thread's graph state as checkpoints," for fault tolerance and thread-scoped memory, and recommends pruning or retention policies. Durability modes (exit, async, sync) and task-level caching of side effects: UNVERIFIED.

OpenHands resume: UNVERIFIED. I did not search it.

Crash-recovery pattern from Diagrid (search snippet): a caller-owned instance ID, so a retry attaches to the in-flight run instead of starting a second one. Source: https://docs.diagrid.io/develop/agents/spring-ai/spring-ai-crash-recovery/

### Non-idempotent side effects (shell commands)
None of the primary docs I read define an "outcome unknown" state, and none has a shell-specific pattern. The closest guidance is:
- Cloudflare: check whether the work is still needed before the non-idempotent call.
- Inngest: make the effect idempotent, because the effect can commit before the result is saved.
- DBOS: an interrupted step runs again, so steps must be safe to repeat.

A secondary blog (not primary, treat as opinion): https://tianpan.co/blog/2026/04/10/agent-idempotency-making-ai-side-effects-safely-retryable argues that idempotency keys must come from orchestrator state, not from LLM context, because LLM context disappears on replay. Its suggested pattern is to record effects, check the journal before a write, and replay the journal instead of re-executing.

My inference, not a documented practice:
- A tool call with an intent record and no completion record, found on resume, becomes `outcome_unknown`.
- Tools classified read-only may be re-run.
- Mutating shell commands must not be re-run automatically. Surface them to the model or the user, and let the model reconcile state (for example by checking for a marker file, a commit, or an output path).

## B. Tracing and observability

### OpenTelemetry GenAI semantic conventions
Status: the opentelemetry.io page is now a notice that the conventions "have moved" to https://github.com/open-telemetry/semantic-conventions-genai and that the page "is no longer maintained." The repo README says it covers GenAI spans, metrics, and events for GenAI clients, MCP, and provider-specific conventions such as OpenAI. Its schema URL section is literally "TODO." Stability status (experimental, development, or stable): UNVERIFIED.

I could not fetch the span, agent, or attribute definitions in this pass. These names are from memory and UNVERIFIED here: operation names `chat`, `execute_tool`, `invoke_agent`, `create_agent`; attributes `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`. I believe `gen_ai.system` was renamed to `gen_ai.provider.name`, which is also UNVERIFIED. Next step: read `docs/` and `model/` YAML in the semconv repo before pinning names.

### Harbor and ATIF
Harbor is the official harness for Terminal-Bench-2.0, from the Terminal-Bench creators. It evaluates Claude Code, OpenHands, and Codex CLI. Install: `uv tool install harbor` or `pip install harbor`. Apache-2.0. Source: https://github.com/laude-institute/harbor and https://docs.harborframework.com

ATIF (Agent Trajectory Interchange Format) spec page: https://docs.harborframework.com/agents/atif.md
- `schema_version`: the example value is `"ATIF-v1.7"`. Harbor accepts ATIF-v1.0 through v1.7. v1.7 adds embedded subagent trajectories, per-document trajectory IDs, `llm_call_count`, and extension fields.
- Top-level fields: `agent` (example uses `name`, `version`, `model_name`; tool definitions optional), `steps` (ordered list), `session_id` (optional, links trajectories from one run), `trajectory_id` (optional; required for embedded subagents), `final_metrics` (optional totals), `subagent_trajectories` (optional), `extra` (custom root metadata).
- Per-step fields: `step_id` (integer, sequential from 1), `source` (examples use `"user"` and `"agent"`; the page mentions system steps but I did not get the full enum), `message` (string), `tool_calls` (each with `tool_call_id`, `function_name`, `arguments`), `observation.results` (each with `source_call_id` matching a `tool_call_id`, and `content`), `metrics` (optional, subfields not listed).
- Harbor's Pydantic models reject undeclared fields, so custom metadata goes in `extra`.
- Full types and required flags are in an ATIF RFC linked from the page. I did not get the RFC URL: UNVERIFIED.

### Claude Code telemetry
Source: https://code.claude.com/docs/en/monitoring-usage
- Enable with `CLAUDE_CODE_ENABLE_TELEMETRY=1`.
- Exporters: `OTEL_METRICS_EXPORTER` (`console`, `otlp`, `prometheus`, `none`), `OTEL_LOGS_EXPORTER` (`console`, `otlp`, `none`), `OTEL_TRACES_EXPORTER` (`console`, `otlp`, `none`; beta).
- Transport: `OTEL_EXPORTER_OTLP_PROTOCOL` (`grpc`, `http/json`, `http/protobuf`; no default), plus `OTEL_EXPORTER_OTLP_ENDPOINT` and per-signal overrides. Intervals: metrics 60000 ms, logs 5000 ms.
- Spans are beta and need `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`. `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1` propagates trace context through a custom `ANTHROPIC_BASE_URL` proxy.
- Metrics: `claude_code.token.usage`, `claude_code.cost.usage`, `claude_code.session.count`, `claude_code.active_time.total`, `claude_code.code_edit_tool.decision`, and others.
- Events: `claude_code.user_prompt`, `claude_code.assistant_response`, `claude_code.tool_result`, `claude_code.tool_decision`, `claude_code.api_request`, `claude_code.api_error`, `claude_code.api_refusal`, `claude_code.api_request_body`, `claude_code.api_response_body`, `claude_code.mcp_server_connection`, `claude_code.plugin_installed`, and others (the page was truncated, so the list may be longer).
- Spans named in the page: `claude_code.interaction`, `claude_code.llm_request`, `claude_code.tool`.
- Content gates, all off by default: `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES` (falls back to `OTEL_LOG_USER_PROMPTS`), `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES`.
- Redaction defaults, per the page: prompts are redacted unless opted in; assistant responses are shown as `<REDACTED>`; spans redact prompt text, tool input details, and tool content by default.
- Metric cardinality controls: `OTEL_METRICS_INCLUDE_SESSION_ID` (default true), `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` (default true), `OTEL_METRICS_INCLUDE_VERSION` (default false), and others.
- These are telemetry exports. They do not replace the local session transcript, and I found nothing saying they can drive replay.

### Codex CLI telemetry
The official reference redirected from https://developers.openai.com/codex/config-reference to https://learn.chatgpt.com/docs/config-file/config-reference. Telemetry is configured under `[otel]` in `~/.codex/config.toml`. Codex ignores `otel` in a project-local `.codex/config.toml`.
- `otel.environment`: default `dev`.
- `otel.exporter`: `none`, `otlp-http`, `otlp-grpc`. Endpoint, headers, protocol (`binary` or `json`), and `tls.*` sit under `otel.exporter.<id>.`.
- `otel.trace_exporter`: same values, with its own `<id>` sub-keys.
- `otel.metrics_exporter`: default `statsig`. Values: `none`, `statsig`, `otlp-http`, `otlp-grpc`. Setting `analytics.enabled = false` also disables this exporter, including custom OTLP ones.
- `otel.log_user_prompt`: opt-in export of raw user prompts with logs.
- The reference page says nothing about redacting secrets in OTel output.
- Third-party vendor docs disagree on nested key names. Trust the reference page.
- Default metrics go to Statsig unless you change `metrics_exporter`. Worth knowing before you point Codex at production.

## C. Secrets in agent logs
- Claude Code telemetry: prompts, assistant text, and tool inputs are redacted or gated by default (source in section B). This covers the OTel export only.
- Claude Code local session transcripts: redaction behavior UNVERIFIED. I did not find a primary source.
- Codex local rollout or session logs: UNVERIFIED. The reference page only covers the OTel exporter.
- OpenHands: my search returned only unrelated redaction libraries. Redaction behavior: UNVERIFIED.
- Named tools (gitleaks, detect-secrets): not checked in this pass. Treat their pattern sets as UNVERIFIED.

No primary source I read documents a redaction approach for local agent transcripts. Design call, not sourced: redact at write time, before the event is appended to the JSONL. An append-only log cannot be scrubbed later without breaking its guarantees. Store secret references (env var names, secret store IDs), not values. Temporal's `factory_argument` warning is the same lesson applied to workflow history.

## D. Implications for the event log (my synthesis)
- Intent event before each side effect: `event_id`, `turn_id`, `tool_call_id`, `idempotency_key`, `tool`, `args_hash`, redacted args, timestamp, `kind=intent`.
- Completion event after: `intent_ref`, `status` (`ok`, `error`, `outcome_unknown`), `result_ref`, timestamp.
- LLM turns: append the full model response as an event (including the provider response ID). Replay reads it. Never re-call the model on resume.
- Use the model-issued `tool_call_id` as the join key everywhere: the log, `gen_ai.tool.call.id`, ATIF `tool_call_id`, and `source_call_id`. One ID then links the log, traces, and trajectory export.
- Resume rule: intent with no completion means `outcome_unknown`. Read-only tools may be re-run. Mutating shell commands go back to the model or the user.
- Keep the JSONL as source of truth. Derive OTel spans and ATIF from it, not the other way around.

## Open items
1. Pull the semconv repo `docs/` and `model/` YAML for span and attribute names and stability status.
2. Fetch the ATIF RFC for required flags and types.
3. Confirm Restate's behavior when a crash lands between a side effect and its journal write, from the idempotency docs.
4. Check LangGraph durability modes and task caching in the current docs.
5. Check Claude Code and Codex local transcript redaction directly, from source or the CLI's own docs.
