# harness (working name)

Flagship 1: a coding agent harness built from scratch around a replayable event log.

- Design spec: `docs/specs/2026-10-22-flagship1-design.md`
- Visual overview: `docs/blueprint.html`
- Decision records: `docs/decisions/`

Development runs in WSL Ubuntu: `uv sync`, then `uv run pytest -q`.

Run the agent from inside a repo with `NVIDIA_API_KEY` set (NVIDIA NIM, the default), with
`--provider gemini` and `GEMINI_API_KEY` (native Gemini; the free tier allows 5 requests a
minute and 20 a day), or with `--provider openrouter` and `OPENROUTER_API_KEY`: `uv run harness` for the prompt, or
`uv run harness --once "<task>"` for one run. `--model`, `--mode ask|auto-edit|yolo` and
`--max-turns` override the defaults. Sessions live under `$HARNESS_HOME`
(default `~/.local/share/harness`).

Headless: `uv run harness -p "<task>" --json` writes every event as one JSON line
(`--policy file.json` for allow and deny rules, `--approvals stdin` to answer the rest).
`uv run harness schema` prints the event schema, and `uv run harness export <session>` writes
the session as ATIF.

Sessions: `uv run harness sessions` lists them newest first. `uv run harness resume <id>` (or
`--resume <id>`) reopens one at the prompt or with `-p`, repairing any tool call a crash left
open; `--continue` finishes an interrupted turn headless without a new prompt. Provider and
model default to the session's unless given. `harness fork <id> [--at SEQ]` copies the log into a
new session, and `harness replay <id>` rebuilds every logged request from the log and checks its
items hash.

Terminal-Bench through Harbor (Docker required):
`uv run harbor run -d terminal-bench@2.0 -i <task> -a harness.harbor.agent:HarnessAgent -m nim/minimaxai/minimax-m3.1-flash`.
`evals/run_tb.sh harness|terminus-2 <attempts> [task ...]` runs the fixed slice in
`evals/tb-slice.txt` with either agent on the same endpoint, model and turn cap.

Evals: `uv run harness eval run` replays the local suite (`evals/suite/`, 24 fixture tasks) from
cassettes with no API calls; `--mode oracle` runs the reference solutions, `--mode record` calls
the model once per task and saves the cassettes, `--mode live --attempts N` measures.
`uv run harness eval report <run-dir | harbor-job-dir>` prints the score, per-attempt spread,
bootstrap CI and paired MDE. `uv run harness stats <session | dir>` totals requests, tokens,
cache-read share, time split and tool errors from the session logs.

Tests replay recorded provider traffic from `tests/cassettes/`. To re-record against OpenRouter:
`HARNESS_RECORD=1 uv run pytest tests/test_cassettes.py -k openrouter` (or `-k nim`, `-k gemini`).
