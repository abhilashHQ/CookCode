# CookCode

Flagship 1: a coding agent harness built from scratch around a replayable event log.

- Design spec: `docs/specs/2026-10-22-flagship1-design.md`
- Visual overview: `docs/blueprint.html`
- Decision records: `docs/decisions/`

Development runs in WSL Ubuntu: `uv sync`, then `uv run pytest -q`.

Run the agent from inside a repo with `NVIDIA_API_KEY` set (NVIDIA NIM, the default), with
`--provider gemini` and `GEMINI_API_KEY` (native Gemini; the free tier allows 5 requests a
minute and 20 a day), or with `--provider openrouter` and `OPENROUTER_API_KEY`: `uv run cookcode` for the prompt, or
`uv run cookcode --once "<task>"` for one run. `--model`, `--mode ask|auto-edit|yolo` and
`--max-turns` override the defaults. Sessions live under `$COOKCODE_HOME`
(default `~/.local/share/cookcode`).

Headless: `uv run cookcode -p "<task>" --json` writes every event as one JSON line
(`--policy file.json` for allow and deny rules, `--approvals stdin` to answer the rest).
`uv run cookcode schema` prints the event schema, and `uv run cookcode export <session>` writes
the session as ATIF.

Sessions: `uv run cookcode sessions` lists them newest first. `uv run cookcode resume <id>` (or
`--resume <id>`) reopens one at the prompt or with `-p`, repairing any tool call a crash left
open; `--continue` finishes an interrupted turn headless without a new prompt. Provider and
model default to the session's unless given. `cookcode fork <id> [--at SEQ]` copies the log into a
new session, and `cookcode replay <id>` rebuilds every logged request from the log and checks its
items hash.

Terminal-Bench through Harbor (Docker required):
`uv run harbor run -d terminal-bench@2.0 -i <task> -a cookcode.harbor.agent:CookCodeAgent -m nim/minimaxai/minimax-m3.1-flash`.
`evals/run_tb.sh cookcode|terminus-2 <attempts> [task ...]` runs the fixed slice in
`evals/tb-slice.txt` with either agent on the same endpoint, model and turn cap.

`uv run cookcode --tui` opens the full-screen client (install the `tui` extra).

Context strategies (decision 0038): `--strategy raw|mask|mask_batched|summarize|hybrid`, with
options like `mask_batched:k6:b8` or `hybrid:k6:b8:t48000`. Raw is the default.

Features, all off by default (`--features a,b`, `TB_FEATURES=` for `run_tb.sh`):
`jobs` (background commands, 0040), `verify_first` (reproduce before fixing, 0041), `notes` and
`facts` (memory across sessions, 0042), `subagents` (the `task` tool, 0044), `skills`
(`.cookcode/skills/<name>/SKILL.md`, 0045), `hooks` (user hooks from settings, 0046) and `mcp`
(stdio MCP servers with deferred schemas, 0048; install the `mcp` extra).

Settings (0047): `$COOKCODE_HOME/settings.toml` and the repository's `.cookcode/settings.toml`
hold provider, model, mode, max_turns, strategy, features, allow and deny rules, `[[hooks]]` and
`[mcp.servers.<name>]`. A flag beats a resumed session, which beats `COOKCODE_*` environment
variables, which beat the repository file, which beats the user file. Rules and hooks add up.

Evals: `uv run cookcode eval run` replays the local suite (`evals/suite/`, 24 fixture tasks plus
6 memory pairs) from cassettes with no API calls; `--mode oracle` runs the reference solutions,
`--mode record` calls the model once per task and saves the cassettes, `--mode live --attempts N`
measures, and `--strategy` and `--features` pick the arm.
`uv run cookcode eval report <run-dir | harbor-job-dir>` prints the score, per-attempt spread,
bootstrap CI and paired MDE. `uv run cookcode stats <session | dir>` totals requests, tokens,
cache-read share, time split and tool errors from the session logs.
`uv run cookcode eval context <dirs> --strategies raw,mask:k6` rebuilds logged requests under
other strategies (0039), and `uv run cookcode eval repro <run-dir>` checks that verify-first
reproductions failed for the right reason. Neither makes API calls.

Walkthroughs with cross-questions for every milestone: `docs/walkthroughs/`.

Tests replay recorded provider traffic from `tests/cassettes/`. To re-record against OpenRouter:
`COOKCODE_RECORD=1 uv run pytest tests/test_cassettes.py -k openrouter` (or `-k nim`, `-k gemini`).
