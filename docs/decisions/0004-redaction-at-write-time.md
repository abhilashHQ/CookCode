# 0004. Redaction at write time

Status: accepted (M0)
Date: 2026-10-26

## Context
Tool output and model text can contain API keys and tokens. The log feeds exports, traces, eval artifacts and Flagship 2 copies, so wherever a secret lands in the log it spreads.

## Options considered
- Redact at export: the raw log keeps secrets, and every new consumer must remember to redact. One missed path leaks.
- Redact at write: the secret never reaches disk. Redaction is permanent.
- Never store raw tool output: avoids the problem but throws away the data replay and debugging need.

## Decision
`JsonlSessionLog.append` builds the event, dumps it to JSON-compatible data, runs `Redactor.redact` over every string value, validates the result back into an `Event`, and only then writes. The returned event is the redacted one, so memory and disk agree.

`Redactor` (`src/cookcode/redact.py`) replaces text matching known patterns (private key blocks, Anthropic, OpenRouter, OpenAI, AWS, GitHub, Google and Slack keys, bearer tokens) with `[REDACTED:<kind>]`. It also replaces exact secret values, normally taken from named environment variables through `Redactor.from_env`, with `[REDACTED:secret_value]`. Exact values are applied first, longest first, and values shorter than 8 characters are ignored. The `sk-` patterns only match when `sk-` does not follow a token character, so identifiers such as `use-disk-space-monitoring-hook.ts` or `fix/task-queue-retry` stay intact. Before any matching, lone surrogates (from `os.fsdecode` on non-UTF-8 file names) are escaped with `backslashreplace`, so every stored string is valid UTF-8.

## Consequences
- A secret caught at write time is absent from every derived artifact.
- Redaction is irreversible. If a pattern misses a secret, it is on disk. If a pattern over-matches, the original text is gone.
- Regex coverage needs upkeep as providers add key formats. Exact-value matching covers secrets that no pattern can recognize, but only the ones we know to list.
- Redaction is idempotent, which `tests/test_redact.py` checks.
- String values and dictionary keys are rewritten. Two keys that redact to the same text merge.
- Redaction also runs over structural fields. A configured secret value equal to a role or type name would rewrite it. Secret values under 8 characters are ignored, which covers most such names, but not all.

## What would change it
A real false positive that corrupts something the agent needs, such as a long token-like string in source code that the model later has to reproduce. The response would be tighter patterns or an allowlist, not a move back to export-time redaction.
