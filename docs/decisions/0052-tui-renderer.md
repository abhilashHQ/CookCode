# 0052. A TUI renderer of its own

Status: superseded by 0053 (2026-11-03). It superseded the rendering part of 0049.
Date: 2026-11-03

## Context
0049 put the plain CLI's `Display` inside a `RichLog`, so the TUI looked like the CLI in a pane. Its "what would change it" named structured widgets per event: collapsible tool output and diffs. Plan mode (0051) adds a plan to review and a mode to show and change, which a line log handles badly.

## Options considered
- **Keep `Display` and add widgets around it.** Cost: two renderers would each see half the events and disagree about state.
- **A TUI renderer that consumes the same two streams.** `Transcript` takes stream events from `DisplayProvider` and log events from `ObservedLog`, exactly what `Display` takes (0026), and builds widgets.
- **A web UI.** A different product, rejected in 0049 for the same reason.

## Decision
- `cli/tui_render.py:Transcript` is a `VerticalScroll` that turns events into widgets: `AssistantText` (Markdown, grown delta by delta), `Thinking` (collapsed reasoning), `ToolBlock` (a `Collapsible` whose title carries the call and its state, and whose body holds the diff and a 20-line result preview), `PlanBlock` (the proposed plan, bordered green or red by the decision) and `Note` for the rest.
- Edits open with their diff, failures open with their output, and successful reads stay collapsed. Ctrl-O opens or closes every block.
- Side calls (summaries, memory) do not stream into the transcript; they show as a one-line note.
- `ApprovalScreen` shows the diff. `PlanScreen` renders the plan as Markdown with approve-into-ask, approve-into-auto-edit and keep-planning. A denial opens `FeedbackScreen`, a separate screen, so typing a reason never triggers the y/n/a bindings.
- Shift-Tab cycles ask, auto-edit and plan, and `/mode` sets any mode, both through `Agent.set_mode` between runs. A side panel shows mode, model, usage, the last request's token estimate and the files changed (from `checkpoint_created`), and hides below 100 columns.
- Every widget keeps its text in `plain`, so tests assert on content instead of rendering.

## Consequences
- The plain CLI and the TUI now render separately, so a new event type needs a branch in both. The cost is small: both renderers ignore what they do not know.
- Still zero core changes for rendering. The only core addition this milestone, `set_mode`, belongs to plan mode.
- Textual stays an optional extra.

## What would change it
A second live client on one session (Flagship 2), which would justify a fan-out with backpressure (0026's own condition) rather than another renderer.
