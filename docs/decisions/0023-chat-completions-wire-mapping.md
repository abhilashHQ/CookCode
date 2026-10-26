# 0023. Mapping neutral items to Chat Completions

Status: accepted (M3)
Date: 2026-10-26

## Context
The log holds a flat list of items (0005). Chat Completions wants messages: one assistant message per model response, carrying text, `tool_calls` and (on OpenRouter) `reasoning_details`, followed by one `tool` message per call. Several cases have no obvious mapping: reasoning from another model, a result status other than `ok` (the API has no error flag), tool-call arguments that are not valid JSON, and items from another provider.

## Options considered
For grouping:
- Group by `request_id` on the `item` event: exact, but `ModelRequest` carries items, not events, and adding request ids to items widens the neutral model for one adapter.
- Group by adjacency: every run of assistant-side items (assistant `Message`, `Reasoning`, `ToolCall`) between two non-assistant items is one response. The context builder already guarantees results follow their calls and user messages separate responses.

For unparseable arguments:
- Replace them with `{}`: the loop would report "path: Field required", which tells the model nothing about what it did wrong.
- Raise in the adapter: one malformed call would end the run.
- Keep the raw text on the item and let the loop answer with an error result.

## Decision
`to_messages` in `providers/openai_compat.py`:
- **Grouping by adjacency.** Assistant-side items accumulate into one assistant message. A user or system `Message`, a `ToolResult`, or a same-provider `OtherItem` flushes it. `content` is `null` when there are tool calls and no text, and `""` when the turn is reasoning only.
- **Reasoning** is replayed only when `replayable_to(provider, request.model)`. The model family is the full model id, `:free` suffix included, because a free variant can route to a different upstream. `reasoning_details` go back verbatim, and plain `reasoning` text is sent only if there are no details. Otherwise it is dropped. Logging the drop is part of the M5 replay-rules gate.
- **Results** are keyed by the call's `provider_call_id`. A non-`ok` result is sent as `[<status>] <content>`, so `denied`, `interrupted` and `unknown` reach the model as words. Empty content is sent as `(no output)`. The first real run ended with HTTP 400 on Cohere (`all elements in tool_results must have the 'outputs' property specified`) after a `py_compile` that printed nothing. Nvidia accepts empty content, Cohere does not. The log keeps the empty string, since that is what the tool returned. A result with no matching call in the request raises `ValueError`, and the loop turns that into a provider error. That is better than sending a request every backend rejects.
- **Unparseable arguments.** `ToolCall.raw_arguments` (new optional field, additive per 0020) holds the text when it does not parse to a JSON object, and `arguments` is `{}`. `Agent._resolve` answers such a call with `invalid arguments for <tool>: not a JSON object: ...` before gating, so it never reaches the gate or a handler. On replay the raw text goes back as `function.arguments`, so the model sees what it actually sent. Empty arguments mean `{}`. A call with no id gets `call_<ours>` so its result can match.
- **`OtherItem`** goes back verbatim to the same provider only. `UnknownItem` is dropped.

## Consequences
- Probed on OpenRouter with two backends (Cohere and Nvidia): a missing result is a hard 400 (`TOOL_CALLS_MISSING_RESULTS`), so the repair pass in section 6 is load-bearing. Results in swapped order and a user message between a call and its result were both accepted by these two. Other backends may be stricter, so `undo` still refuses on unresolved calls.
- The recorded cassettes replay a real round trip with an `ok` result, an `interrupted` result and the model's own reasoning, and both backends accepted it.
- A model that keeps sending broken JSON spends a turn per attempt. The stuck-loop rule (0014) is what bounds that.

## What would change it
A backend that rejects the `[status]` prefix or needs a structured error flag, or a provider whose responses interleave text and calls in an order adjacency cannot recover. Both would push request ids onto items.
