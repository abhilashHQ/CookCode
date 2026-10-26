# 0015. Edit format

Status: accepted (M2 gate, 2026-10-26)
Date: 2026-10-26

## Context
The model changes existing files through a tool. The format decides how often an edit fails to apply, how many tokens each edit costs, and how much the model's training helps. The spec lists the details of exact-match editing as an open question for M2.

## Options considered
- Exact string replacement: `path`, `old_string`, `new_string`, optional `replace_all`. The model must quote the old text, so each edit costs those tokens. It fails on a whitespace slip or a text that occurs more than once, and both failures can be reported precisely.
- Patch format (Codex `apply_patch`): one call can change several files and hunks, and models may be trained on it. It needs a parser, a rule for fuzzy context matching, and a policy for a patch that applies in part. Each of those is a new place for an edit to go wrong silently.
- Whole-file rewrite through `write`: no match can fail, but cost grows with file size and any line the model forgets to copy is lost without notice. `write` stays for new files and full replacements.
- Line-range edit: short calls, but line numbers go stale after any earlier edit in the same response.

## Decision
**Gate:** Accepted at the M2 gate on 2026-10-26: exact string replacement. Revisit with a patch format if M6 measures a high failed-edit rate.

Recommendation: exact string replacement, with `write` for new files. The code implements it (`src/harness/tools/files.py`), so choosing another option means writing a second tool.

What the implementation does:
- `edit` reads the file as UTF-8, checks the call, and writes only after every check passes. `LocalEnvironment.write_file` replaces the file atomically, so a failed edit leaves it byte-identical.
- Each failure has its own message: file not found, directory, not a text file, `old_string` and `new_string` identical, empty `old_string`, `old_string` not found, and `old_string` found N times without `replace_all`.
- A not-found error adds a hint when the text exists with different whitespace, so the model knows to copy from `read`.
- Edit works on decoded text with no newline translation, so CRLF files keep their line endings.
- Both `edit` and `write` declare `touches`, which gives the gate and the snapshot step the path without running the handler (0017, 0018).

## Consequences
- A multi-file change is several calls, each with its own gate decision and checkpoint.
- The model pays for the old text on every edit, and a large `replace_all` costs the same as a single one.
- The error messages are the model's only recovery path after a failed edit, so they name the fix.

## What would change it
A failed-edit rate that is high in the M6 task suite, measured per model. A provider whose models are trained on a patch format and do measurably better with it would also justify a second, patch-based tool next to this one.
