# harness (working name)

Flagship 1: a coding agent harness built from scratch around a replayable event log.

- Design spec: `docs/specs/2026-10-22-flagship1-design.md`
- Visual overview: `docs/blueprint.html`
- Decision records: `docs/decisions/`

Development runs in WSL Ubuntu: `uv sync`, then `uv run pytest -q`.

Run the agent from inside a repo with `NVIDIA_API_KEY` set (NVIDIA NIM, the default) or with
`--provider openrouter` and `OPENROUTER_API_KEY`: `uv run harness` for the prompt, or
`uv run harness --once "<task>"` for one run. `--model`, `--mode ask|auto-edit|yolo` and
`--max-turns` override the defaults. Sessions live under `$HARNESS_HOME`
(default `~/.local/share/harness`).

Tests replay recorded provider traffic from `tests/cassettes/`. To re-record against OpenRouter:
`HARNESS_RECORD=1 uv run pytest tests/test_cassettes.py -k openrouter` (or `-k nim`).
