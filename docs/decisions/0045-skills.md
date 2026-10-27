# 0045. Skills as SKILL.md folders, loaded on demand

Status: accepted (M9 gate, 2026-10-27)
Date: 2026-10-27

## Context
Repositories and users have procedures the model should follow for some tasks: how to write a changelog entry, how to cut a release, how to add a migration. Putting all of them in the system prompt costs tokens on every request whether they are used or not, and the prompt grows with every procedure added.

## Options considered
- Inline everything in the system prompt or an AGENTS.md: simple, and the cost scales with the number of procedures.
- Retrieval: embed the procedures and inject the relevant ones per turn. That adds a model call, and per-turn injection breaks the cached prefix.
- Progressive disclosure: list names and one-line descriptions at session start, and let the model load a body with a tool when it needs one. This is the SKILL.md folder convention other agents use.

## Decision
- `skills.py`: a skill is a folder holding a `SKILL.md` whose frontmatter has `name` (lowercase, hyphens, at most 64 chars) and `description`. Folders live in the repository's `.cookcode/skills/` or the user's `skills/` directory. The repository wins on a name clash.
- The system prompt lists names and descriptions only, fixed for the session, so the prefix stays cacheable.
- `skill(name)` returns the body and lists the other files in the folder, which the model reads with the normal file tools.
- Behind the `skills` feature flag. Eval runs point the user directory at an empty one, so a developer's own skills never leak into measurements.
- The measure is tokens not loaded: the bodies a session never loaded, against inlining every skill.

## Consequences
- Adding a skill costs one line per request until it is used.
- The model has to recognize that a skill applies. A vague description means a skill that is never loaded. The live smoke run loaded the changelog skill before acting and followed its format exactly.
- Skills are plain files in the repository, so they are reviewed and versioned like code.

## What would change it
Enough skills that the listing itself becomes a cost, which would call for search over skills, the same move MCP tools made in 0048.
