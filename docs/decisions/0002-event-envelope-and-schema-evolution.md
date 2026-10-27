# 0002. Event envelope and schema evolution

Status: accepted (M0)
Date: 2026-10-26

## Context
Logs outlive the code that wrote them. A session recorded today must load after the schema grows, and a binary older than the log must not crash on events it has never seen.

## Options considered
- Strict union of known types: any new event type breaks every older reader.
- Untyped dicts: never breaks, but loses validation and type checking.
- Typed union with a catch-all variant: known types are validated, unknown ones are kept as data. This is what Codex does with its `Other` variant.

## Decision
Every line is an `Event` envelope with `seq`, `id`, `ts`, `schema_version`, `layer` and `body`. `layer` is `"model"` or `"internal"` and comes from the body class (`ClassVar LAYER`); a model validator in `Event` rejects a mismatch. The body and item unions use `tag_or_fallback` (`src/cookcode/core/_union.py`) as the pydantic discriminator: a `type` tag outside the known set routes to `OtherEvent` or `UnknownItem`, both declared with `extra="allow"`, so unknown fields survive a read and rewrite.

## Consequences
- Old code reads new logs. It sees unknown events as opaque `OtherEvent` objects and can skip or forward them.
- Only unknown tags fall back. A known tag whose shape changed fails validation. This is why changes inside one `SCHEMA_VERSION` must be additive: new optional fields, new event types.
- A breaking change bumps `SCHEMA_VERSION` and needs a read-time migration. M0 stores the version on every event but nothing reads it yet and no migration hook exists. That work belongs to the first breaking change.
- The layer check does not run for `OtherEvent`, so an unknown event keeps the layer it was written with, as long as that layer is one this version knows.
- The fallback only covers new `type` tags. A new value inside a known type (a new `exit_status`, a new `layer`) fails validation and makes the session raise `SessionCorruptError`. Fields a newer version adds to a known type are dropped in memory, though `fork` copies raw bytes and keeps them on disk.
- `tests/test_events.py` round-trips one instance of every event type from `tests/sample_events.py`, and a test fails if a new type is missing a sample.

## What would change it
The first breaking change forces the migration design. Needing to filter unknown events by layer in the context projection would also force a rule for how `OtherEvent` layers are treated.
