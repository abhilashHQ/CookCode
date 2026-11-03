# 0053. The interactive client lives in the terminal's scrollback

Status: accepted (2026-11-03). Supersedes 0049 and 0052.
Date: 2026-11-03

## Context
0052 gave the Textual TUI its own widget renderer. Used for real, it was the wrong model, not the wrong styling: a full-screen app owns the whole terminal, keeps the transcript inside a scroll widget, and frames everything in panels. The terminal's own scrollback, search, selection and copy stop working, and the screen reads as an application rather than a conversation. The owner's verdict was that it was ugly next to inline agent CLIs, and restyling cannot fix where the transcript lives.

## Options considered
- **Restyle the Textual app**, or run it in Textual's inline mode. Cost: the transcript still lives in a widget, so scrollback stays broken.
- **Print finished output to stdout and keep a small live region at the bottom.** Finished things are printed once and become ordinary terminal history; only what is still changing is redrawn. This is how inline agent CLIs are built.
- **A web UI.** Still a different product (0049).

## Decision
- `cli/render.py:Printer` renders with Rich into ANSI and writes it above the live region: the prompt, the answer as Markdown behind a `●`, one block per tool call printed when its result arrives (`● Update(dates.py)` with a `⎿` summary and, for edits, a diff with removed and added lines on red and green), the plan in a bordered panel, and one-line notes for mode changes, retries, errors, interrupts, budgets, compaction and undo. Denials show their first sentence.
- `cli/inline.py:InlineApp` is a non-full-screen prompt_toolkit application. Its live region holds the answer while it streams (the last lines, as Markdown), calls that are running, a spinner with elapsed time and streamed tokens, the input box in a rounded frame, and a mode line with usage on the right.
- Output above the app goes through `patch_stdout(raw=True)`, which erases the live region, writes, and redraws, so printed history and the live region never interleave.
- Approvals are numbered menus in the live region: arrows or digits to choose, Enter to confirm, Esc for the last option. The exit_plan review offers auto-accept edits, manual approval, or keep planning. Choosing "no" turns the input box into a feedback field, and what is typed becomes the denial reason.
- Shift-Tab cycles ask, auto-edit and plan, and `/mode` sets any mode. Because `Agent.set_mode` only works between runs, a change during a run is held and applied when the run ends, and the mode line says so.
- `run_cli` uses it when stdin and stdout are both TTYs. Pipes and tests keep the line client (`Terminal`, `Display`, `TerminalApprover`), which needs no terminal control.
- Textual, `--tui`, `cli/tui.py` and `cli/tui_render.py` are deleted. prompt_toolkit and Rich are core dependencies, since the default interactive client needs them.

## Consequences
- Two renderers remain: `Printer` for TTYs and `Display` for pipes. Both are observers of the same streams (0026), so the core is unchanged.
- The live region is redrawn ten times a second while a run is active, for the spinner. Each redraw renders a few lines with Rich; the printed history is never redrawn.
- Tests drive the real application through a pipe input and assert on what `Printer` wrote and on the menu state. The look was checked separately in a real terminal under tmux, because a terminal emulator library that does not answer cursor-position requests showed redraw artifacts a real terminal does not.

## What would change it
A second live client on one session (Flagship 2), which needs a fan-out of the event stream rather than another renderer. Or a request for a browser view of a session, which would be that fan-out plus a web client.
