# 0022. A response reaches the log whole, and retries are logged

Status: accepted (M3)
Date: 2026-10-26

## Context
The free tier fails often and in several ways. In the M3 probes: HTTP 429 from a shared upstream pool, 404 from a dead upstream function, a 403 gate, and on other days truncated streams. Spec 4.3 says the adapter retries transient errors with backoff and a cap, and logs each retry. A retry is only safe if nothing from the failed attempt is in the log. A half-logged response followed by a full retry would show the model the same tool call twice.

## Options considered
- Emit each item as soon as it is complete (a tool call is complete when the next call index starts): the earliest possible `ItemDone`. A failure after the first item cannot be retried without duplicates, so most failures become terminal.
- Emit items only after `finish_reason` arrives: deltas still stream for display, and the log sees all of a response or none of it. A failure at any point before the end can be retried.
- Retry in the loop instead of the adapter: the loop would need to know which provider errors are transient, which is protocol knowledge.

## Decision
`ChatStreamAssembler.finish()` is the only place items are produced, and it runs only after a `finish_reason`. Until then the adapter yields `ItemStarted` and `ItemDelta` only.

Retryable: HTTP 408, 409, 425, 429, 500, 502, 503, 504 and 520 to 529; any `httpx.TransportError` (connect, read timeout, protocol error); a stream that ends without `finish_reason`; `finish_reason: "error"`; an error object inside the stream with a retryable code or no code; a malformed chunk. Everything else, including 400, 401, 403 and 404, ends the stream with `stop_reason="error"` and the provider's message.

Backoff is `min(cap, base * 2^(attempt-1))` scaled by a jitter factor in [0.5, 1], raised to the wait the server asks for. That wait is `Retry-After`, or, when `x-ratelimit-remaining` is 0, the time until `x-ratelimit-reset` (epoch milliseconds, as OpenRouter sends it). If the server's wait is longer than `max_server_wait_s` (120 s, revised in M5), the adapter fails at once and names the wait, instead of retrying early into a limit that cannot have reset. The first real runs exhausted the free daily quota, and before this rule the adapter spent four retries on a 429 with a reset ten hours away. Defaults: base 1 s, cap 30 s, 4 retries (5 attempts). M5 split the two limits: Gemini's free tier allows 5 requests a minute and asks for waits like 37 s, which the old rule (fail when the wait exceeds the 30 s backoff cap) turned into dead runs. Backoff stays capped at 30 s; a server-requested wait is honored up to 120 s; only longer waits, which mean a daily quota, fail fast. The rule lives in `providers/http.py`, shared by both adapters. The backoff sleep waits on the cancel token, so an interrupt during backoff ends the stream with `aborted`.

Before each retry the adapter yields `StreamRetry(attempt, delay_s, reason, status)`. The loop logs it as a new internal event, `provider_retry(request_id, attempt, delay_s, reason, status)`, under the request it belongs to. The event type is additive (0020).

## Consequences
- One `model_request_started`, any number of `provider_retry`, then the items and one `model_request_finished`. `tests/test_openai_loop.py` checks this shape with a 429 followed by a truncated stream.
- On cancel no items are returned, even text the user already saw stream by. Spec 4.3 says the adapter returns finished items on cancel. Under this rule nothing is finished until the response ends, so the rule holds trivially. The user loses partial text, and the log never contains a response the model did not finish.
- Tokens spent on failed attempts are not in `Usage`. The provider may bill them, so cost from the log can undercount when retries happen.
- A client that renders deltas shows a failed attempt's partial text and then the retry's text. The CLI prints a `[retry n]` line between them.
- Display latency is unchanged. Tool calls start only after the response ends, which they did already, since tools run after the stream.

## What would change it
Long responses where losing partial output on a late failure costs more than a retry, or a provider whose items finish incrementally and can be resumed (Responses API `previous_response_id`, for example), where emitting earlier gains something real.
