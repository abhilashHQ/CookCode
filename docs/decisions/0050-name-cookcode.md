# 0050. The name: cookcode

Status: accepted (M10 gate, 2026-10-27)
Date: 2026-10-27

## Context
The package and CLI were called `harness`, a placeholder since M0. The repository is CookCode. A final name had to land before release, and renaming touches stored data: the data directory, environment variables, the Harbor agent's import path, the event schema id, and every log and cassette already written.

## Options considered
- Keep `harness`: generic, collides with every other harness, and doesn't match the repository.
- `cookcode` everywhere, including event field names and stored paths. That breaks every existing log and cassette.
- `cookcode` for everything a user types or imports, with stored formats left alone.

## Decision
- Renamed: the package and CLI (`cookcode`), the data directory (`~/.local/share/cookcode`), the settings directory (`.cookcode/`), the environment variables (`COOKCODE_*`), the Harbor agent (`cookcode.harbor.agent:CookCodeAgent`, ATIF agent name `cookcode`) and the schema id (`cookcode-events-v1`).
- Kept: the event field names `harness_version` and `harness_commit`, the suite work root `/tmp/harness-suite`, which is recorded in cassettes, and the synthetic call id prefix `harness-call-`. Changing any of them would invalidate stored logs and cassettes for a cosmetic gain.

## Consequences
- Every log and cassette written before the rename still reads and replays. Replay of the 24 base tasks was unchanged (24/24).
- The Terminal-Bench sweep ran at 0cb5ec8 under the agent name `harness`. Its re-runs use a worktree at that commit, so pooled results come from one agent version.
- A few internal strings say `harness`, and this record explains why.

## What would change it
A schema version 2, which would be the moment to rename the stored fields as well.
