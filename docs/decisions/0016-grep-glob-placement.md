# 0016. grep and glob placement

Status: accepted (M2 gate, 2026-10-26)
Date: 2026-10-26

## Context
Searching a tree is the most frequent read the model does. ripgrep is the best engine for it, but it is not installed everywhere, and the same agent has to run against a local directory now and against a Harbor container and the Flagship 2 sandbox later. Where the search lives decides how much each environment has to implement.

## Options considered
- Environment primitives (`env.grep`, `env.glob`): each environment chooses its own engine and can return structured results. Every new environment must implement them, and the protocol grows with each search feature.
- Tools built on `env.exec`: the tool builds a shell command that prefers `rg` and falls back to `grep` and `find`. The protocol stays at `exec`, `read_file`, `write_file` and `remove_file`. The cost is a shell string that has to be quoted correctly, and a fallback that behaves slightly differently from `rg`.
- Search in Python through `read_file`: no dependency on binaries in the environment, but walking a tree costs a round trip per file against a remote environment.

## Decision
**Gate:** Accepted at the M2 gate on 2026-10-26: grep and glob stay tools over env.exec, preferring rg with a grep and find fallback.

Recommendation: tools built on `env.exec`. The code implements it (`src/cookcode/tools/search.py`).

- `grep` runs one command: `if command -v rg ...; then rg ...; else grep ...; fi`. Pattern, glob and path go in as shell-quoted arguments after `-e` and `--`. Exit code 1 means `no matches`, any other non-zero exit is an error result with the tool's output. Results over `max_results` (default 200) are cut with a count of the rest.
- `glob` first runs `cd <path>`, then `rg --files --glob=<pattern>` or a `find` fallback. A missing directory is an error result.
- The `find` fallback does not use `-name` or `-path`, because `*` in `find` crosses slashes and `**/` would not match zero directories. The glob is translated to a POSIX extended regular expression (`glob_to_regex`) and matched with `-regex`. A test checks that both backends return the same list for `*.py`, `src/**/*.py`, `**/*.md` and `src/*.py`.
- Both tools are `read_only`, so they run concurrently and the gate allows them in every mode.
- A test feeds `'; rm -rf x; '` as pattern, glob and path through both backends and checks that a file in `x/` survives.

## Consequences
- The `Environment` protocol stays small, which is what a container or sandbox implementer has to build.
- Results depend on whether `rg` exists in the environment. The fallback differs in two ways: `grep` ignores `.gitignore`, and its `--include` matches file names only, so a glob containing a slash behaves differently from `rg --glob`. Both backends skip hidden directories. The fallback keeps the tool working and returns different results in a repo with ignored files.
- Quoting is a security surface owned by this code. The injection test covers the three inputs the model controls.
- Timeouts and cancels return `timeout` and `killed` exit statuses. A read-only timeout is an `error` result, and the tool adds a `[timed out after Ns]` note because the loop adds none for read-only calls.

## What would change it
An environment without a shell, or one where the search must run on the other side of a slow link. Either would make a primitive worth its protocol cost. A measured gap between `rg` and the fallback in real tasks would make `rg` a requirement of the Harbor image instead.
