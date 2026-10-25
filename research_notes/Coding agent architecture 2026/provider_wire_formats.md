# Provider Wire Formats and Provider-Abstraction Layers (as of 2026-10-19)

Method note: all sources were retrieved with WebFetch on 2026-10-19. WebFetch answers from a small model, so "quotes" below are close paraphrases of the fetched text, not verified verbatim. Spot-check exact field names against the live docs before coding against them. Where a page had no visible date, that is stated. Scope follows the assignment: no agent-loop, edit-format, compaction-strategy, or benchmark research. Redirected pages were followed only where noted. Tool-call count went past the 15-call guideline because the question set is broad; the gaps section lists what was not covered.

## Anthropic Messages API

### Takeaway
Anthropic's wire format is a flat list of typed content blocks per message. Thinking blocks carry a `signature` that must be returned unmodified on later turns, and the server validates it. Tool calls are `tool_use` blocks matched by id to `tool_result` blocks. Caching is explicit breakpoints on a prefix hierarchy (tools, system, messages). Streaming is a named-event SSE protocol with `content_block_*` deltas.

### Cited Findings
- Thinking is enabled by `thinking: {type: "enabled", budget_tokens: N}` (manual) or `thinking: {type: "adaptive"}`. `budget_tokens` minimum is 1,024 and must be less than `max_tokens`, except under interleaved thinking. Thinking tokens count toward `max_tokens`.: [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- Manual `enabled` is deprecated on Opus 4.6 and Sonnet 4.6, and rejected with a 400 on Claude 4.7 and later. Adaptive thinking is the replacement. Haiku 5.5, Opus 5.5, Sonnet 5, Sonnet 5.5, Fable 5.1 are named as 4.7+ class models that reject `enabled`.: [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- On Opus 5.5, Opus 5, Sonnet 5.5, Sonnet 5, Fable 5.1, Mythos 5.1, Fable 5, Mythos 5, Mythos Preview, and Haiku 5.5, thinking is on by default. `display` defaults to `"omitted"`, so thinking text is hidden unless `display: "summarized"` is set. On Opus 4.8, 4.7, 4.6, and Sonnet 4.6, thinking is off until `type: "adaptive"` is set.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- `display` accepts `"summarized"`, `"omitted"`, and a beta value `"updates"` (header `thinking-display-updates-2026-08-18`). With `"omitted"`, `thinking` blocks still come back with an empty `thinking` string and a real `signature`. The signature is identical whatever `display` is set to. Switching `display` between turns is supported. "Summarized" text is a summary, not the raw chain of thought.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- Thinking block shape: `{"type": "thinking", "thinking": "...", "signature": "..."}`. Redacted thinking appears as `redacted_thinking` blocks. Their field layout was not extracted in this session.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- Preservation rule: "Pass every `thinking` block back to the API complete and unmodified, alongside the `tool_use` block it accompanied." Within the latest assistant message, consecutive thinking blocks must match the original order, and this includes `redacted_thinking` blocks. Modified thinking blocks are rejected with a 400. The one exception is that text placed in the empty `thinking` field of an omitted block is ignored.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- The server decrypts the `signature` to reconstruct the original thinking for prompt construction. This is why round-tripped omitted blocks can carry an empty `thinking` field.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- Model-dependent retention: on Opus 4.5+, Sonnet 4.6+, and Haiku 5.5, prior-turn thinking blocks are preserved and billed as input. On earlier models, previously cached thinking blocks are stripped when non-tool-result user content is added.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- In manual mode, the final assistant turn of a thinking-enabled request must begin with a thinking block. Adaptive mode drops that requirement. Interleaved thinking in manual mode needs the `interleaved-thinking-2025-05-14` beta header on Opus 4.5, Sonnet 4.5, and earlier 4 models. Opus 4.6 has no interleaving in manual mode; only its adaptive mode interleaves. Haiku 4.5 does not interleave.: [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- Streaming event sequence: `message_start`, then one or more of `content_block_start` / `content_block_delta` / `content_block_stop` per block, then one or more `message_delta`, then `message_stop`. `ping` events can appear anywhere. `error` is a named event. Token counts in `message_delta.usage` are cumulative.: [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- Delta types: `text_delta` (`text`), `input_json_delta` (`partial_json`), `thinking_delta` (`thinking`), `signature_delta` (`signature`). The `signature_delta` arrives once, just before `content_block_stop` of the thinking block. Current models stream tool input one complete key/value at a time, so there can be gaps between deltas during tool use.: [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- `thinking_tokens` is reported in `usage.output_tokens_details.thinking_tokens`. In streaming, it appears only on the final `message_delta`.: [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- Streaming SDK accumulation helpers (`get_final_message()` in Python) reassemble complete blocks including signatures. The docs advise against concatenating deltas by hand.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- Prompt caching: up to 4 `cache_control` breakpoints. Automatic caching (top-level `cache_control`) uses one slot. Cache is checked in hierarchy order `tools`, `system`, `messages`, up to and including the breakpoint block. "Changes at each level invalidate that level and all subsequent levels." Lookback is at most 20 positions per breakpoint. Minimum cacheable length is 512 tokens on the newest models (Haiku 5.5, Opus 5.5, Sonnet 5.5, Fable 5.1, and others), 1,024 on Sonnet 4.6 and Sonnet 5, 2,048 on Opus 4.7, and 4,096 on Opus 4.6, Opus 4.5, and Haiku 4.5. Shorter prompts are silently not cached.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Cache invalidation: tool definition changes invalidate all levels. `tool_choice` changes invalidate the messages level. Adding or removing images invalidates messages. Thinking parameter changes always invalidate message blocks. Changing `output_config.effort` always invalidates message blocks, but setting it to the model default does not. The table in the source uses a check mark for "level not invalidated," which I read as the inverse of an X.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Pricing multipliers on base input price: 5-minute cache write 1.25x, 1-hour cache write 2x, cache read 0.1x. Opus 5.5 and Sonnet 5.5 read at 0.05x. Fable 5.1 and Mythos 5.1 read at 0.025x. Breakpoints themselves add no cost.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Usage fields: `input_tokens` (tokens after the last breakpoint), `cache_creation_input_tokens`, `cache_read_input_tokens`. Total input = `cache_read + cache_creation + input`. A `cache_creation` object breaks the write down into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. Cache hit check: if both cache fields are 0, the prompt was not cached.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Cache TTL is 5 minutes by default, refreshed at no cost on use. A 1-hour entry must appear before any 5-minute entry. 1-hour TTL (`"ttl": "1h"`) is listed as available on the Claude API, Bedrock, Claude Platform on AWS, Google Cloud, and Microsoft Foundry.: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Tool definitions: `name` must match `^[a-zA-Z0-9_-]{1,128}$`. Fields are `name`, `description`, `input_schema`, and optional `input_examples`, `strict`, `cache_control`, `defer_loading`, `allowed_callers`. `input_examples` are not supported on server tools.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- `tool_choice` is `auto` (default), `any`, `tool` (named), or `none`. Manual `enabled` thinking rejects `any` and `tool`. Claude Opus 5.5, Sonnet 5.5, Fable 5.1, and Mythos 5.1 return a 400 for `any` and `tool`, and the page recommends `auto` with `strict: true`. Forced tool use skips thinking: the response starts with `tool_use` and has no thinking block.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- Example assistant turn: `content` is an array with a `text` block followed by a `tool_use` block with `id` (example value `toolu_01A09q90qw90lq917835lq9`), `name`, and `input` (object, not string). The id prefix `toolu_` appears in the example only.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- Client tools support date-versioned Anthropic-schema tool types (bash, text editor). Computer use and browser use are "client toolsets" that bundle member tools under one entry with no `name`.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)

### Inferences
- Anthropic's signature model means a thinking block is only valid as a unit, byte-for-byte, attached to the assistant turn that produced it. An internal model that stores thinking as text cannot reconstruct valid requests. Store the raw block (text, signature, redacted flag) keyed by provider and model.
- Because `display` changes what `thinking` contains but not `signature`, an internal model should store the signature as the authoritative field and treat displayed text as optional.
- A cache breakpoint placed on a block that changes each turn gives no reads. The docs make this a common pitfall. The harness must place breakpoints on the last stable block.
- Model-specific thinking retention means the same stored history will be billed and preserved differently across models. This is a cost concern, not a correctness one, but the harness should not assume history is dropped.

### Gaps
- Exact `stop_reason` enumeration was not extracted. Only `end_turn` appeared in the fetched examples. `tool_use`, `max_tokens`, `pause_turn`, and `refusal` were not verified.
- `tool_result` content types (text, image, document) and their exact field layout were not verified. The fetch returned the define-tools page, not the handle-tool-calls page.
- Server tool block types (`server_tool_use`, web search and code execution result blocks) were not extracted.
- The `redacted_thinking` block field layout was not extracted.
- The exact tool-use id regex and length limit were not verified. Only the example id is known.
- Retry guidance for 429, 529, and overloaded errors was not researched. See the Python section gap.

## OpenAI Responses API and Chat Completions

### Takeaway
The Responses API is an item-based protocol. Output is a list of typed items (`message`, `reasoning`, `function_call`, `custom_tool_call`, and others), and inputs are the same item types echoed back. Reasoning comes back as an opaque `encrypted_content` blob, and the docs recommend replaying it with tool outputs. For the bridge in use here, the critical fact is that gpt-6-astra and gpt-6.1-sol do not support function calling over Chat Completions, so the Responses API is the only path for tools on those models.

### Cited Findings
- Function calling on GPT-6 Astra and GPT-6.1 Sol requires the Responses API. Chat Completions does not support it on those models.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning) (redirected from platform.openai.com/docs/guides/reasoning)
- Reasoning items: "highly recommend[s] you pass back any reasoning items returned with the last function call" along with the function output. If several functions are called in a row, pass back all reasoning, function call, and function call output items since the last user message. The reasoning guide calls this a recommendation. The function-calling page says "any reasoning items returned in model responses with tool calls must also be passed back with tool call outputs." Treat the function-calling page as the stricter reading.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning); [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- Irrelevant reasoning items are ignored by the API. Reasoning items stay opaque. The API does not return reasoning text.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- Reasoning items in `output` include `encrypted_content` by default for stateless use. The legacy `reasoning.encrypted_content` value in `include` is still accepted for compatibility.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- Stateless mode applies when `store` is false or the org has Zero Data Retention. The guidance is to preserve every item in `output` and replay the full history.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning); [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- `reasoning.context: "all_turns"` brings earlier-turn reasoning into the next sample. It only has effect when the request can reach earlier response items, through `previous_response_id`, a conversation, or replayed history. The GPT-5.6 family and GPT-6.1 Sol support `all_turns`. Earlier models default to `current_turn`.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- Persisted reasoning is reusable only within a model family. GPT-5.6 models can reuse each other's reasoning, but not GPT-5.5's.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- `reasoning.effort` values are model-dependent and can include `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`. GPT-6 Astra rejects `none` with an HTTP 400. GPT-6.1 Sol rejects `none` and `minimal` and defaults to `medium`. `gpt-5.5` defaults to `medium`.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- Reasoning summaries: `reasoning.summary` accepts `auto`, which is equivalent to `detailed` on most reasoning models. Summaries appear in the `summary` array of the `reasoning` item only when explicitly requested. Some models restrict which summary levels they accept.: [Reasoning](https://developers.openai.com/api/docs/guides/reasoning)
- `previous_response_id` gives a stateful chain. All previous input tokens in the chain are billed as input, even though they are not resent. The max-tokens budget includes input, output, and reasoning tokens.: [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- Stored responses are kept for 30 days by default. `store: false` opts out. Conversation objects are exempt from the 30-day TTL.: [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- Compaction: server-side compaction is set with `context_management` and `compact_threshold` on `/responses`. A standalone `/responses/compact` endpoint gives explicit control. Without management, output can be truncated when it exceeds the context window.: [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- Assistant `phase` field: the conversation-state page warns that GPT-5.5 can treat an intermediate update as the final answer, and says to verify the integration preserves `phase` on assistant messages. Replaying full output keeps reasoning items and `phase` intact.: [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- `function_call` item fields: `type` ("function_call"), `id` (prefix `fc_`), `call_id` (prefix `call_`), `name`, `arguments` (JSON-encoded string). The sample did not show `status` on `function_call`, but it does show `status: "completed"` on `custom_tool_call`.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- `function_call_output` fields: `type`, `call_id`, `output`. `output` is usually a string. For functions that return images or files, it can be an array of image or file objects. The page did not name the part type strings.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- `call_id` links output to call. Streaming delta events reference the function call's `id` as `item_id`. The docs do not explain the relationship between reasoning item ids and function call ids.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- Custom tools: `type: "custom"`. The model emits free-form text in `input`, not JSON. The output item is `custom_tool_call` with `call_id`, `name`, `input`, and `status`. An optional grammar format supports `lark` and `regex` CFG syntax with listed restrictions.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- `tool_choice`: `"auto"` (default), `"required"`, a named function, `"none"`, and `allowed_tools`. `allowed_tools` restricts calls to a subset without changing the tools list, which the page says helps prompt caching.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- `parallel_tool_calls: false` limits the model to zero or one tool call. Built-in tools cannot be part of a parallel function-call batch on GPT-5 and later.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- `strict: true` requires `additionalProperties: false` on every object and every property listed in `required`. Optional fields use a nullable type. If strict is omitted in Responses, the API normalizes the schema when it can and otherwise falls back to non-strict. Chat Completions is non-strict by default.: [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- Codex's internal `ResponseItem` enum (serde tag `type`, snake_case) includes: `Message` (with `phase`, `id`, `status`, `encrypted_content`), `Reasoning` (`summary`, `content`, `encrypted_content`, `id`), `LocalShellCall`, `FunctionCall` (`name`, `namespace`, `arguments`, `call_id`, `encrypted_function_args`), `FunctionCallOutput` (`call_id`, `output: FunctionCallOutputPayload`), `CustomToolCall`, `CustomToolCallOutput`, `WebSearchCall`, `Compaction` (alias `compaction_summary`, `encrypted_content`), `ToolSearchCall`, `ToolSearchOutput`, `ImageGenerationCall`, `CompactionTrigger`, `ContextCompaction`, and an `Other` catch-all with `#[serde(other)]`. Content items: `InputText`, `InputImage` (flattened `image_url` or `file_id`, plus `detail`), `InputAudio`, `OutputText` (with `annotations`, `logprobs`).: [Codex models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)
- Codex's `Reasoning` item carries `encrypted_content` as an optional string that is always serialized, and `Compaction` carries it as a required string. `Other` exists so unknown item types deserialize without failure.: [Codex models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)

### Inferences
- The item-based model is the closest match to what a coding harness needs: reasoning and tool calls are first-class items, not fields on a message. Codex's enum confirms this design in production.
- Two IDs per tool call (`id` with `fc_` prefix and `call_id` with `call_` prefix) are a real mismatch with Anthropic's single `tool_use.id`. The internal model needs both, with `call_id` as the join key for output.
- Reasoning opacity plus model-family scoping means reasoning cannot be carried across providers, and only partially across OpenAI models. Persist it as a provider-tagged, model-family-tagged opaque blob, not as a neutral field.
- The bridge allowlist includes gpt-6-astra. If the bridge exposes it to Chat Completions clients, tool calls will fail. Route all tool-using traffic through `/v1/responses`.

### Gaps
- OpenAI Responses streaming event type list (for example `response.output_item.added`, `response.function_call_arguments.delta`, `response.reasoning_summary_text.delta`) was not fetched. Event names are not cited here.
- OpenAI prompt caching behavior (automatic prefix caching, minimum lengths, `prompt_cache_key`, retention) was not fetched. Claims about it are not made.
- Chat Completions wire format (`tool_calls`, `reasoning_content` field name variations) was not fetched directly. Only the pi-ai and Zechner references below touch it.
- The `function_call_output` image part type strings (`input_image`, `input_file`) were not confirmed. The API reference page was not fetched.
- Token counting endpoints and counting differences were not researched.
- `stop reason` equivalents for Responses (`status`, `incomplete_details`) were not researched.

## Google Gemini API, Azure AI Foundry, and Other Formats

### Takeaway
Gemini uses encrypted "thought signatures" that must be returned in stateless multi-turn function calling. The docs say they appear on "any part" in `generateContent` and are required for reasoning continuity. The Gemini function-calling URL returned Interactions API content rather than `generateContent` `functionCall`/`functionResponse` parts, so the parallel-call rules are unverified. Azure AI Foundry was not fetched in this session.

### Cited Findings
- Thought signatures are "encrypted representations of the model's internal reasoning" and are "required to maintain reasoning continuity across multi-turn interactions." The page (last updated 2026-10-14 UTC) says signatures are "metadata that can be attached to any part," including inside `functionCall` parts or the final part of a response.: [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- In the Interactions API, signatures appear on `thought` steps and built-in tool steps, and never on user inputs, model outputs, or standard function calls. In stateless mode, all thought blocks must be resent exactly as received and not modified. Model switching still requires resending the previous model's thought blocks, and "the backend manages compatibility.": [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- Stateful mode (`store: true` with `previous_interaction_id`) lets the server manage signatures automatically.: [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- The thought-signatures URL now redirects to the thinking guide. Gemini config names on that page are `thinking_level` (`minimal`, `low`, `medium`, `high`, model-dependent) and `thinking_summaries` (`"auto"`). `max_output_tokens` includes thought tokens and is a hard cutoff. These are Interactions API names. The `generateContent` names `thinkingConfig`, `thinkingBudget`, and `includeThoughts` were not confirmed.: [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking)
- The Gemini function-calling page (fetched) documents the Interactions API: `function_call` step with `type`, `name`, `arguments`; results returned as `function_result` with `name`, `call_id`, `result`. Modes: `auto`, `any`, `none`, `validated`. Parallel calls are shown, but the page does not say how parts are batched or where signatures go.: [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- Zechner reports that Google "seem[s] to not support tool call streaming" (as of his 2025-11-30 post). This may have changed.: [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)

### Inferences
- Gemini signatures are per-part metadata, not per-block. An internal model that binds a signature to a whole assistant message will mis-map Gemini responses with several function call parts.
- Gemini's stateless requirement is the same shape as Anthropic's: resend the opaque data unmodified. Both point to one internal rule: opaque reasoning payloads are replayed verbatim, keyed by provider.

### Gaps
- Gemini `generateContent` `functionCall` / `functionResponse` structure, `id` handling, parallel-call batching, and multimodal function responses were not verified. The fetched page covered Interactions API.
- The dummy-value skip option for signatures, and per-model enforcement differences (Gemini 2.5 vs 3), were not found in the fetched pages.
- Gemini streaming format (SSE shape, chunk structure) was not fetched.
- Azure AI Foundry docs were not fetched. Claims about Foundry serving OpenAI-compatible and Anthropic-compatible endpoints are not made here. The Anthropic caching page does list Microsoft Foundry among platforms that support the 1-hour cache TTL and have their own minimum lengths, which is the only Foundry-related fact verified here. The user's bridge, not Foundry, is the path in use.
- No other formats (Mistral, Bedrock Converse) were researched.

## Cross-Provider Mismatches That Break Naive Abstractions

### Takeaway
Reasoning cannot be translated across providers. Anthropic thinking is a signed block that must round-trip byte-exact. OpenAI reasoning is an opaque encrypted item scoped to a model family. Gemini signatures are opaque per-part metadata. A neutral representation can only carry a human-readable summary, and pi-ai shows the lossy fallback: Anthropic thinking becomes `<thinking>`-tagged text when switching providers.

### Cited Findings
- Zechner: "Anthropic's signed blobs must be replayed on later requests, which adds a transformation layer." When switching providers, Anthropic thinking becomes `<thinking>` tagged text. He says these traces "don't actually represent what's happening behind the scenes." OpenAI's Completions API lacks reasoning traces.: [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Zechner: reasoning field naming differs by provider. Some Completions endpoints use `reasoning_content`, others `reasoning`. He also reports `store` field differences, `max_tokens` vs `max_completion_tokens`, and the `developer` role.: [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Zechner: usage is reported "at the start of the SSE stream, others only at the end." Tracking is best-effort, "not for accurate billing.": [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Zechner: "Many unified LLM APIs also don't return partial results to you" and "completely ignore providing a way to abort requests.": [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Zechner: cross-provider handoff is "inherently best-effort," and he reports that Claude → GPT → Gemini handoff "work[s] pretty well.": [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Anthropic forced tool use (`any`, `tool`) conflicts with manual thinking and is rejected on newer models. Forced calls skip thinking entirely. An internal "force a tool" flag has different legal values per provider and model.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools); [Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- Tool call IDs: Anthropic has one id (`tool_use.id`, `toolu_` prefix in examples). OpenAI Responses has an item `id` (`fc_`) and a `call_id` (`call_`) that joins output. Codex's `FunctionCall` has `call_id: String` and `id: Option`. Gemini's id handling is unverified.: [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools); [Function calling](https://developers.openai.com/api/docs/guides/function-calling); [Codex models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)
- Parallel tool calls: OpenAI can return several `function_call` items in one response, and `parallel_tool_calls: false` restricts this. Anthropic can return several `tool_use` blocks in one assistant message. Gemini's batching rules were not verified.
- Tool result content: OpenAI `function_call_output.output` can be a string or an array of image or file objects. Vercel AI SDK tool results support `json` and `content` output types with `text` and `file-data` items. Anthropic's tool-result image support was not verified in this session.
- Token accounting: Anthropic total input is `cache_read + cache_creation + input`. OpenAI bills the whole `previous_response_id` chain as input, and max-token budgets include reasoning. Anthropic includes thinking tokens in `max_tokens`. Gemini's `max_output_tokens` includes thought tokens.
- Stop reasons: pi-ai normalizes to `pending`, `stop`, `length`, `toolUse`, `error`, `aborted`. Provider-native stop reasons were only verified for Anthropic (`end_turn` in examples).

### Inferences
- Store reasoning in the neutral model as: `{provider, model_family, kind: "signed" | "encrypted" | "summary" | "redacted", payload (opaque, verbatim), text (optional display summary)}`. Only replay `payload` to the same provider and family. Fall back to `text` (tagged) for cross-provider handoff, and say so in the UI.
- Tool calls need `internal_id`, `provider_call_id`, and optionally `provider_item_id`. Do not assume one id.
- Parallel tool calls should be a list of calls per assistant message, not one call per message. Results go back as a list keyed by call id.
- Usage should be normalized into `input_new`, `cache_read`, `cache_write`, `output`, `reasoning`, and `total`, with the raw provider usage kept alongside. Provider counts are not comparable without that split.

### Gaps
- No verified data on Gemini parallel call batching or part-level signature placement for multiple calls.
- No verified Anthropic tool-result image format.
- Stop reason mappings for OpenAI Responses and Gemini were not researched.
- Token counting endpoints (Anthropic count tokens, OpenAI input token counts, Gemini countTokens) were not researched.

## Existing Abstraction Layers and Stated Lessons

### Takeaway
Only two sources gave primary evidence on design lessons: the pi-ai README (via the fetch of the package page, truncated) and Zechner's post. Codex's internal `ResponseItem` is a primary source for a production item model. LiteLLM documents its Anthropic mapping. Vercel AI SDK documents its ModelMessage parts and provider options. Pydantic AI and the OpenAI Agents SDK were not retrieved. OpenCode was not researched.

### Cited Findings
- pi-ai separates a provider (owns auth, model catalog, stream behavior) from an API implementation (wire protocol, such as `anthropic-messages`, `openai-responses`, `openai-completions`, `google-generative-ai`). Mixed providers dispatch per model.: [pi-ai README](https://github.com/badlogic/pi-mono/tree/main/packages/ai)
- pi-ai assistant content blocks are `text`, `thinking`, and `toolCall`. Tool results use role `toolResult` with `toolCallId`, `toolName`, `isError`. Stop reasons are `pending` (partial only), `stop`, `length`, `toolUse`, `error`, `aborted`.: [pi-ai README](https://github.com/badlogic/pi-mono/tree/main/packages/ai)
- pi-ai streaming: `start`, then `text_*`, `thinking_*`, `toolcall_*` events each tied to a block by `contentIndex`. Each event carries a `partial` snapshot mutated in place, which "shouldn't be retained as history." Failure emits `error` with reason `error` or `aborted` plus the partial message.: [pi-ai README](https://github.com/badlogic/pi-mono/tree/main/packages/ai)
- pi-ai tool-call `arguments` during streaming are a best-effort partial parse. Validation runs separately. Redacted thinking may arrive complete at block start with no deltas.: [pi-ai README](https://github.com/badlogic/pi-mono/tree/main/packages/ai)
- Zechner chose to build pi-ai on top of provider SDKs directly, for "full control." He says Vercel AI SDK-based tools often failed with self-hosted models, "specifically when it comes to tool calling." He calls the APIs of existing harnesses to have "organic evolution" baggage. He says the abstraction "can never be perfect due to leaky abstractions" and "won't guarantee that new models and providers will just work out of the box." Tool-result streaming was missing at the time. He says pi-ai was used in "seven different production projects.": [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Codex's internal model uses internally tagged enums with an `Other` catch-all and aliased variant names, so new upstream item types do not break deserialization.: [Codex models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)
- Vercel AI SDK: `ModelMessage` has roles `system`, `user`, `assistant`, `tool`. Parts: `text`, `file` (with `mediaType`, `data`, optional `filename`), `tool-call` (`toolCallId`, `toolName`, `input`), `tool-result` (`toolCallId`, `toolName`, `output`). Tool output types shown are `json` and `content`. Provider options live at three levels: call, message, and part. The page states that `useChat` returns `UIMessage` objects, which "do not support provider options," and recommends `convertToModelMessages`. System messages in `messages` are rejected unless `allowSystemInMessages` is set. The page has no date.: [AI SDK prompts](https://ai-sdk.dev/docs/foundations/prompts)
- Vercel's reasoning part shape was not shown in the fetched text, so the signature field for AI SDK reasoning parts is unverified.: [AI SDK prompts](https://ai-sdk.dev/docs/foundations/prompts)
- LiteLLM Anthropic provider: `reasoning_effort` maps to `budget_tokens` (1024, 2048, 4096 for low, medium, high) before Claude 4.6. On 4.6 and 4.7 models, any value other than `"none"` turns thinking on, sends adaptive thinking, and sends `output_config.effort`. `thinking` can pass through unchanged on the `/v1/messages` route. The page warns `budget_tokens` is "rejected entirely on Opus 4.7." Response shape: `thinking_blocks` (with `signature`) at top level and in `provider_specific_fields`, and text in `reasoning_content`. Prompt caching works via `cache_control` on content blocks with no beta header. The page is undated.: [LiteLLM Anthropic](https://docs.litellm.ai/docs/providers/anthropic)
- LiteLLM's page does not say whether redacted thinking or signatures survive a round trip through its OpenAI-shaped `thinking_blocks`. This needs a test before anyone relies on it.: [LiteLLM Anthropic](https://docs.litellm.ai/docs/providers/anthropic)

### Inferences
- The consistent lesson across Zechner and the Vercel SDK fetch: unified APIs fail at tool calling and at reasoning round-trips first, so those paths need explicit adapter tests.
- LiteLLM maps a single `reasoning_effort` knob onto different provider mechanisms per model generation. A neutral harness should expose a provider-neutral effort level and let adapters map it, with the mapping in one table keyed by model.
- Pydantic AI's `provider_name` tagging of thinking parts (from search memory, not verified) matches the tagging approach above, but it was not confirmed.

### Gaps
- OpenAI Agents SDK: not fetched.
- OpenCode provider layer: not fetched. Its architecture and any stated lessons are not reported.
- Pydantic AI message history: the docs redirected to pydantic.dev/docs/ai/core-concepts/message-history/ and the redirect was not followed. Part types and provider-name tagging are not confirmed.
- Vercel AI SDK reasoning part shape and provider-metadata keys for signatures: not verified.
- The pi-ai "Cross-Provider Handoffs" and "Context Serialization" sections were truncated on the fetched page. The source files in `packages/ai/src` were not read.
- Codex's internal `ResponseItem` was read from `models.rs` only. Codex's conversion code to and from provider wire formats was not read.

## Python Implementation Considerations

### Takeaway
Nothing in this session's fetches covered SDK versions, retry policy, or async SSE. The official Anthropic and OpenAI SDKs both have sync and async streams per the docs. Zechner's stated preference for direct provider SDKs over unified APIs is the only evidence gathered. Raw httpx plus SSE parsing was not researched.

### Cited Findings
- Anthropic docs: "The Python SDK allows both sync and async streams," and the Python SDK exposes `client.messages.stream(...)` as a context manager with `text_stream`.: [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- Anthropic docs: the stream helpers accumulate complete blocks (including signatures) and recommend these over hand-written delta concatenation.: [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- Zechner prefers provider SDKs directly over unified APIs.: [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- Zechner: unified APIs often lack abort support and partial results. An abort path needs to be designed in from the start.: [Zechner, pi](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)

### Inferences
- Use the official SDKs for the Anthropic and OpenAI adapters. They handle signature and event accumulation, which is the part most likely to break. Keep the neutral model and the adapter boundary in your own code, so the SDK's types do not leak into the core.
- Use a streaming accumulator that yields partial snapshots, not deltas to the core. The core should only ever see finished blocks, with a separate partial view for UI.
- Abort handling needs a defined partial-message state. Zechner's `error`-with-partial pattern is one option.

### Gaps
- Retry and backoff behavior for 429, 529 (Anthropic overloaded), and OpenAI rate-limit errors: not researched. No claims made about default SDK retry counts or header-based backoff.
- SDK version pins for `anthropic` and `openai` Python packages: not checked. Per the system instruction, pins should be at least two weeks old, which is not relevant to these notes but matters for any package choice.
- Raw httpx and SSE parsing trade-offs: not researched.
- Token counting endpoints in Python SDKs: not researched.

## Open Questions for the Harness Design
- Whether the bridge at 127.0.0.1:4100 passes through `reasoning.encrypted_content` and `output` items unchanged. If it normalizes items, replay of gpt-5.6 reasoning may silently break.
- Whether `reasoning.context: "all_turns"` is honored through the bridge for gpt-5.6 models.
- Which Anthropic models are in use. Anthropic's current docs list Opus 5.5 and Sonnet 5.5 as rejecting forced `tool_choice`, which affects the harness's "must call a tool" paths.
