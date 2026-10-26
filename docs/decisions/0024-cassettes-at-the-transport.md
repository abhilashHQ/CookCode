# 0024. Cassettes record HTTP at the transport, and replay is strict

Status: accepted (M3)
Date: 2026-10-26

## Context
Spec section 2 asks for recorded provider streams that replay in CI, so adapter regressions are caught offline. Free-tier quota is 50 requests a day on this key, which makes re-recording expensive. A cassette has to catch two kinds of drift: the adapter parsing a stream differently, and the request side (context builder plus `to_messages`) sending something different.

## Options considered
- Record `StreamEvent`s at the Provider interface: replays the loop, but skips the SSE decoder, the assembler and the request mapping, which are the code most likely to break.
- Record HTTP with a VCR-style library: works, but adds a dependency and its matching rules are generic (URL and method, body optional).
- Record at the `httpx` transport ourselves: the request body as JSON and the raw response bytes. Replay through a transport that refuses any request that differs from the recording.

## Decision
`providers/cassette.py`. `RecordingTransport` wraps the real transport and tees the response stream as it flows, so streaming and cancellation behave the same while recording. The recording keeps the method, URL, request JSON, status, `content-type`, `retry-after` and the body. It never keeps request headers, so the API key is never written. `Cassette.save` runs everything through the `Redactor`. `ReplayTransport` serves interactions in order and raises `CassetteMismatch` with the first differing JSON path (`$.messages[1].content: expected 'first', got 'second'`). `assert_exhausted` fails if a recorded request was never made.

Tests replay by default. `HARNESS_RECORD=1` with `OPENROUTER_API_KEY` set records fresh tapes into `tests/cassettes/`. CI runs ruff, pyright and pytest on every push (`.github/workflows/ci.yml`).

## Consequences
- A recorded test exercises SSE framing, chunk assembly, usage mapping, reasoning capture and the request mapping of the second turn against real bytes from two backends.
- Strict matching means any change to the request a recorded test sends, such as a tool description or a field order, fails replay until the tape is re-recorded. The recorded tests build their requests from fixed inputs in the test file, so changes to the CLI prompt or the default tools do not touch them.
- Tapes are about 50 KB each, because OpenRouter streams one chunk per token.
- Matching is in order, so a test whose request count depends on timing would be flaky. Retries during recording are recorded too and replay the same way.

## What would change it
Recorded full-agent runs where tool output varies between machines (absolute paths, timings). Those need a normalizer on both sides of the comparison, or a fixed workspace path. Also, a cheap paid model that makes re-recording free.
