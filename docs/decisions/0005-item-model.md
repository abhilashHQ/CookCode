# 0005. Items, not messages

Status: accepted (M0)
Date: 2026-10-26

## Context
Providers return more than assistant text. OpenAI Responses and Codex treat reasoning blocks and tool calls as top-level items. Anthropic returns content blocks inside a message. The internal model must hold both without loss.

## Options considered
- Chat messages with nested tool calls and content blocks: matches the older chat APIs, but reasoning and calls have no place of their own.
- Items: a flat sequence of typed records. Anthropic blocks map onto items one for one.

## Decision
`src/harness/core/items.py` defines `Message`, `Reasoning`, `ToolCall`, `ToolResult`, `OtherItem` and the fallback `UnknownItem`, all frozen pydantic models joined by the `Item` union.

- `Reasoning.payload` is stored verbatim. `Reasoning.replayable_to(provider, model_family)` returns true only when both match, because signatures and encrypted content cannot be translated between providers or model families.
- `ToolCall` has our `id`, a `provider_call_id`, and an optional `provider_item_id`. OpenAI uses two identifiers (`call_id` and the item `id`) and Anthropic uses one. `ToolResult.call_id` refers to our `id`.
- `OtherItem` keeps a provider item we do not model, tagged with its provider.

## Consequences
- Provider adapters translate to and from items at the edge, and the rest of the system sees one shape.
- Switching models mid-session is possible, but reasoning from another family is excluded from the next request by `replayable_to`. The text and tool history carry over.
- Nothing validates that a `ToolResult` points at an existing `ToolCall`. That check belongs in the loop or a context projection.
- Exporting to other formats needs the provider call ID as the join key. We keep both identifiers for that reason.

## What would change it
A provider whose output cannot be expressed as a flat sequence of these items, or a need to compare reasoning across families, which would require a translation layer the opaque payload currently rules out.
