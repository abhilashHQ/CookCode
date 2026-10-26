# 0021. Adapter A over raw HTTP, not the OpenAI SDK

Status: accepted (M3)
Date: 2026-10-26

## Context
Adapter A speaks Chat Completions and has to cover OpenRouter. The spec gives the adapter three jobs that an SDK also wants to own: mapping the stream to neutral items, retrying transient errors (with every retry logged), and ending on cancel with `aborted`. OpenRouter adds fields that are not in the OpenAI schema (`reasoning_details`, `reasoning`, `usage.cost`, `native_finish_reason`, error objects inside a 200 stream).

## Options considered
- `openai` SDK pointed at OpenRouter's base URL: typed requests and a maintained SSE parser. Its retries happen inside the client, so the log cannot see them. OpenRouter's extra fields arrive as untyped extras, and replaying `reasoning_details` means passing `extra_body` and dicts anyway. Cancellation means closing the SDK's stream object from outside.
- `httpx` plus our own SSE decoder and chunk assembler: about 450 lines in `providers/openai_compat.py`. Every byte on the wire passes through code we own, so retries, cancellation and the extra fields are ours to define and test.
- `aiohttp`: same as httpx with a less convenient transport hook for cassettes.

## Decision
Raw HTTP over `httpx`, the only new runtime dependency. `SSEDecoder` implements the SSE framing (comments, multi-line `data:`, flush at close). `ChatStreamAssembler` turns chunks into display events and, once the response ends, into items. `OpenAICompatProvider` owns the request, retries and cancellation. The pure functions `to_messages` and `build_body` map a `ModelRequest` to the wire (0023).

## Consequences
- The transport is a constructor argument, which gives cassettes a clean seam (0024) and lets tests run the whole adapter over `httpx.MockTransport`.
- We own SSE edge cases. OpenRouter sends `: OPENROUTER PROCESSING` comment lines and per-token chunks. Both are covered by tests and by the recorded cassettes.
- Each `stream()` call opens its own `AsyncClient`, so there is no connection reuse across turns. That costs one TLS handshake per turn, which is noise next to model latency.
- A second OpenAI-compatible endpoint needs only a different `base_url` and `name`.

## What would change it
A provider feature that is expensive to reimplement and stable in an SDK (for example structured outputs with server-side schema validation), or a second adapter that needs the same SSE work, at which point the decoder becomes shared code rather than a reason to adopt an SDK.
