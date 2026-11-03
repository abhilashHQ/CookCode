# 0049. A full-screen TUI on Textual, as another observer

Status: superseded by 0053 (2026-11-03). Accepted at the M10 gate, 2026-10-27.
Date: 2026-10-27

## Context
The plain CLI streams text and asks for approvals inline. Spec section 13 asks for a full-screen client: transcript, status line, a prompt that accepts input while the agent works, and an approval dialog. 0026 already decided that clients observe the runtime by decoration, so the TUI must not fork the runtime or its rendering.

## Options considered
- Textual: async-native, widgets for modals and logs, and testable headless with its pilot API.
- prompt_toolkit: excellent input handling, but layout and modals are hand-built.
- curses: no dependency, and everything is hand-built.
- A web UI: a different product.

## Decision
- `cli/tui.py:CookApp` on Textual, started with `--tui`. It is an optional extra (`cookcode[tui]`), and the dev group includes it so CI runs its tests.
- The plain CLI's `Display` renders into the transcript pane through `_PaneWriter`, a text stream that writes lines into a `RichLog` with their ANSI colors intact. One renderer, two surfaces.
- `TuiApprover` shows `ApprovalScreen`, a modal with y (allow), n (deny) and a (allow always).
- Typing while the agent works sends a steer message at the next step boundary (0013). Ctrl-C stops the run, a second Ctrl-C forces it, and Ctrl-C at the prompt quits. These are the plain CLI's semantics.
- Tests drive the app headless with Textual's pilot: a prompt runs into the transcript, and a mutating call asks in the dialog.

## Consequences
- Rendering changes land in both clients at once, and the TUI adds no event handling of its own.
- The TUI inherits the plain CLI's line-oriented look inside the pane. Rich widgets per event type would need a second renderer.
- Textual stays optional, so a headless install for Harbor does not carry it.

## What would change it
A design that needs structured widgets per event (collapsible tool output, diffs), which would justify a TUI-specific renderer.
