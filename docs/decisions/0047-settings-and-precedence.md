# 0047. Settings files and their precedence

Status: accepted (M10 gate, 2026-10-27)
Date: 2026-10-27

## Context
By M9, configuration came from flags, a resumed session's recorded config, environment variables, presets and a policy file, each resolved in its own place. M9 added hooks and M10 added MCP servers, which need a file. The precedence had to be one rule, written down and tested, because "why did it use that model" is the first support question for any agent.

## Options considered
- Flags and environment only: no place for hooks, MCP servers or rules.
- One user file: no per-repository settings, so a team cannot share hooks or skills settings.
- User file plus repository file, merged under a fixed precedence, with lists that add up.

## Decision
- `config.py:Settings`: provider, model, base_url, api_key_env, mode, max_turns, strategy, features, allow, deny, hooks, and `mcp.servers`. Unknown keys are an error.
- Files: the user's `settings.toml` in the data directory, and the repository's `.cookcode/settings.toml`.
- Precedence, highest first: flag, resumed session's config, environment (`COOKCODE_PROVIDER` and friends), repository file, user file, built-in preset.
- Scalars take the highest value set. `allow`, `deny` and `hooks` add up across files. MCP servers merge by name, with the repository winning.
- The Runtime always merges the settings' allow and deny rules into the policy, including when a `--policy` file is given.

## Consequences
- One function (`over`, `load_settings`) and one test table cover the whole resolution.
- A repository can tighten or extend rules, and with list merging it cannot drop the user's deny rules. Rules add up, and deny wins in the policy.
- A repository file can declare hooks and MCP commands, so both stay inert until the user turns on the feature (0046, 0048).
- Building this exposed a real bug: `run_cli` passed a policy into the Runtime, which then skipped the settings' rules, so allow and deny from settings were silently ignored in the interactive CLI. The fix moved the merge into the Runtime so every path gets it, with a regression test.

## What would change it
Organization-managed settings that must override the user, which would be a new top layer.
