# 0033. Reasoning replay rules: same provider and model, dropped in the builder, counted in the log

Status: accepted (M5)
Date: 2026-10-26

## Context
Every provider returns reasoning in a different, mostly opaque form. OpenRouter sends `reasoning_details`, NIM sends `reasoning_content` (0023), and Gemini attaches `thoughtSignature` to individual parts. The spec (section 4.1) says reasoning goes back only to the same provider and model family, and that the builder either drops it or turns it into labeled text and logs which. Until M5, adapter A dropped it silently inside its wire mapping.

Gemini 3 and later add a hard constraint. In the current turn (after the latest user text), the first `functionCall` part of every model step must carry its signature, or the request is rejected with 400. A session that switches to Gemini mid-turn, or a resumed fork, would therefore be unsendable unless the missing signatures are handled.

## Options considered
- Turn foreign reasoning into labeled text: readable, but signatures and encrypted details have no text form, and summaries from another model mislead more than they help.
- Drop it in each adapter: the decision stays invisible in the log, and every adapter repeats it.
- Drop it in the context builder and log the count, with each adapter keeping a defensive check.

## Decision
`build_request(..., provider=)` removes every `Reasoning` item for which `replayable_to(provider, model)` is false, which means a different provider or model id. `model_request_started.reasoning_dropped` records how many were removed, and `items_sha256` fingerprints exactly what was sent (both additive fields, 0020).

Gemini specifics:
- The assembler stores signatures in the response's `Reasoning` payload, keyed by the provider call id or by `text`.
- On replay, each signature goes back on the part it came from. A text signature that came on an empty part goes back as an empty text part within the same model content, never at the end of the conversation.
- Thought summary text is not replayed. The hard requirement is the signature, and summaries cost tokens.
- After replay, any model step in the current turn whose first call is still unsigned gets Google's documented skip value `skip_thought_signature_validator`. That covers history from another provider, another Gemini model, or a Gemini 2.5 call that came without a signature.

Measured, not assumed: on `gemini-3.5-flash`, a request whose current-turn call has no signature gets 400 ("Function call is missing a thought_signature"), and the same request with the skip value gets 200.

The boundary rule was corrected after a live run. A session started on NIM was resumed on Gemini with a new prompt. Adjacent user contents merge, so the prompt landed in the same content as NIM's function responses. We had treated any user content with text as the turn boundary, which left NIM's calls unsigned, and Gemini rejected the next request. Gemini does not treat a content that carries function responses as a user message. The boundary is now the latest user content that has text and no function responses. A regression test reproduces the exact sequence. The live re-run then hit the free daily quota before that request was validated, so the fix rests on the test plus the measured acceptance of the skip value.

## Consequences
- Switching model mid-session works on every adapter, and the log says what the new model did not see.
- The skip value trades away Gemini's verification of those calls. That is acceptable, because the calls were never Gemini's to verify.
- `items_sha256` makes `cookcode replay` possible: the builder can be checked against the log without the system prompt text, which the log stores only as a hash.

## What would change it
A provider that accepts another model's reasoning as useful input, or Google removing the skip value.
