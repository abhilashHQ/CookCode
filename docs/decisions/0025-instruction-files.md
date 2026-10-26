# 0025. Instruction files are read once per run into the system prompt

Status: accepted (M3)
Date: 2026-10-26

## Context
Project instruction files (`AGENTS.md`, and `CLAUDE.md` where a repo has one) are table stakes (spec section 16). Spec section 7 says they come from the repo root and the current directory and go into the system section. Open details: when to read them, what "current directory" means once bash can `cd`, duplicates, and size.

## Options considered
- Re-read on every request, following the tracked cwd: always current, but the system prompt changes mid-session, which breaks the prompt-cache prefix and makes `system_prompt_sha256` on `run_started` describe only the start.
- Read once when the run is built, from the root and the launch directory: stable prefix, and the hash on `run_started` describes exactly what the model saw.
- Walk every directory between the root and the launch directory: what some agents do. More files, more tokens, and it rarely matters in the repos this targets.

## Decision
`agent/instructions.py`. `load_instructions(env, cwd)` reads `AGENTS.md` then `CLAUDE.md` from the workspace root, then from `cwd` if it is a different directory inside the workspace, through the `Environment`. Identical contents load once (a `CLAUDE.md` symlinked to `AGENTS.md` is common). Each file is capped at 32,000 bytes, with a note giving the real size. `render_system(base, files)` appends a `# Project instructions` section with one `## <path>` subsection per file. The CLI builds the system prompt once per process. The workspace root is the git top level of the launch directory, or the launch directory if it is not in a repo.

## Consequences
- Editing `AGENTS.md` mid-session has no effect until the next CLI start. That is the cost of a stable prefix.
- The system prompt contains the absolute workspace path, so `config_id` differs between checkouts of the same repo.
- Instructions travel through the redactor like everything else on the request side, and the model sees them verbatim.

## What would change it
Measured cache-hit data at M7 showing the prefix does not matter at our session lengths, or a monorepo task where the nearest nested `AGENTS.md` is the one that matters.
