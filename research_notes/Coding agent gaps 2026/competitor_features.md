# Coding agent feature survey (as of 2026-10-22)

## Method and limits

Primary sources only: official docs pages and repo READMEs, fetched on 2026-10-22. About 23 fetches were available, so many cells are UNVERIFIED rather than NO. A NO means I read an explicit statement or contrary evidence. UNVERIFIED means I could not check it from a primary source in this pass. I did not search Show HN or changelog praise, so the "rare but loved" section is based on documented design choices, not user sentiment.

Redirects I hit: Codex docs now live under learn.chatgpt.com (developers.openai.com redirects there). Goose's repo page is aaif-goose/goose. The pi README I read is under earendil-works/pi.

## Status grid

Codes: Y yes, P partial, N no (explicit), U unverified. Where a cell has two codes, the first is the primary claim.

| # | Feature | Claude Code | Codex CLI | OpenCode | pi | Aider | Goose | Crush |
|---|---|---|---|---|---|---|---|---|
| 1 | Undo / checkpoints | Y (snapshots, /rewind) | U | Y (/undo, /redo) | U | Y (git commits, /undo) | U | N (none documented) |
| 2 | MCP client | Y | Y | U (page linked, not read) | U | U | Y | Y |
| 3 | AGENTS.md / project instructions | Y (reads AGENTS.md) | Y (/init creates it) | Y (/init creates it) | U | U | U (file present, behavior U) | Y (default context file) |
| 4 | Hooks / custom commands / skills | Y / Y / Y | U / U / U | U | U / U / P (extensions, skills, prompt templates) | U | U | U / U / P (reads skill dirs) |
| 5 | OS sandbox, Linux | Y, opt-in (bubblewrap) | Y (bubblewrap + seccomp) | U | U | U | U | N (none documented) |
| 5b | OS sandbox, macOS | Y, opt-in (Seatbelt) | U | U | U | U | U | N (none documented) |
| 6 | Git integration | P (commit via headless example; diff and PR U) | U | U | U | Y (auto-commit, /diff) | U | P (attribution trailers on commits and PRs) |
| 7 | Image input / web | U / U | Y / Y | U | U | U | U | U |
| 8 | Model switching / providers / local | Y / P / U | P (ChatGPT or API key; local U) | P (any provider by key; local not mentioned) | U | U | Y (15+ incl. Ollama) | Y (mid-session; Ollama, llama.cpp, LM Studio) |
| 9 | Cost display / budget cap | Y / Y | U | U | U | U | U | U |
| 10 | Todo / plan / sub-agents | Y / Y / Y | U | U | N (plan mode and sub-agents omitted, per README) | U | U | U |
| 11 | Headless mode | Y (`claude -p`) | Y (`codex exec`; output options U) | U (CLI page linked, not read) | Y (print/JSON, RPC, SDK) | U | U | U (`crush run` not confirmed) |
| 12 | Session share / export / replay | P (resume, fork, JSONL transcripts; export U) | P (`codex resume`; fork U) | U (share page returned 404) | P (publish to Hugging Face via separate tool) | U | U | U |
| 13 | Config format | Y (JSON settings files) | U | U | U | U | U | P (crushrc named, format U) |
| 14 | Install / startup | U / U | Y (methods) / U | Y (methods) / U | U | U | Y (methods) / U | Y (methods) / U |

## Per-agent sources

### Claude Code
1. Checkpoints. Snapshot taken before each prompt that starts a turn. Only edits made through Claude's file tools are tracked. The 100 most recent checkpoints keep snapshots. Snapshots are swept after about 30 days (`cleanupPeriodDays`). Rewind options: restore code and conversation, restore conversation only, restore code only, and two summarize options. Bash-side changes (rm, mv, cp) are not tracked. Subagent edits are generally not tracked. Symlinked and hard-linked paths are skipped with a warning. Source: https://code.claude.com/docs/en/checkpointing
2. MCP. Client over stdio, HTTP (recommended for remote), SSE (deprecated), and WebSocket via JSON config. Scopes: local and user in `~/.claude.json`, project in `.mcp.json`. Can also run as an MCP server via `claude mcp serve`. Source: https://code.claude.com/docs/en/mcp
3. Memory. Reads CLAUDE.md and can read AGENTS.md in its place. Auto memory loads the first 200 lines or 25KB. Rules can be scoped with `.claude/rules/`. Source: https://code.claude.com/docs/en/memory
4. Hooks. 33 events, including PreToolUse, PostToolUse, PermissionRequest, PreCompact, and SessionStart. Hook types: command, http, mcp_tool, prompt, and agent (agent is experimental). Exit code 2 blocks on blockable events. `onFailure: block` is available in v2.1.295 and later. Source: https://code.claude.com/docs/en/hooks. Custom commands and skills run from the prompt (`/skill-name`), per https://code.claude.com/docs/en/headless.
5. Sandbox. Opt-in via `/sandbox` or `sandbox.enabled`. Linux and WSL2 use bubblewrap plus socat. The optional seccomp filter (from the `@anthropic-ai/sandbox-runtime` npm package) adds Unix domain socket blocking. macOS uses Seatbelt. Network has no direct route out. Traffic goes through a proxy on the host with an allowlist that starts empty. Tools that ignore proxy env vars cannot connect even to allowed hosts. Default read access covers the whole machine except denied paths, which still includes credential files unless you add `sandbox.credentials` entries. Default write access covers the working directory, a per-user temp dir, and `--add-dir` paths. Native Windows runs commands unsandboxed and requires WSL2 for the sandbox. Source: https://code.claude.com/docs/en/sandboxing
6. Git. Headless docs show a commit flow with scoped `allowedTools` for git commands. I did not verify a diff view or PR creation in the CLI docs. Source: https://code.claude.com/docs/en/headless
7. Image and web tools: UNVERIFIED in this pass.
8. Models. `/model` switches mid-session. Providers include Amazon Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, and LLM gateways. Local model support: UNVERIFIED. Source: https://code.claude.com/docs/en/costs
9. Cost. `/usage` shows session cost computed locally at list price, with a managed `modelPricing` override for contracted rates. Prompt cache statistics are shown. `--max-budget-usd` caps spend and stops background work when reached. Source: https://code.claude.com/docs/en/costs and https://code.claude.com/docs/en/headless
10. Sub-agents. Markdown files with YAML frontmatter in `.claude/agents/` or `~/.claude/agents/`. Each has its own context window, tool allow or deny lists, and `isolation: worktree`. Up to 20 run concurrently by default. Todo tools: TodoWrite is referenced, and TaskCreate, TaskGet, TaskList, and TaskUpdate are referenced. Plan mode is reached with Shift+Tab. Source: https://code.claude.com/docs/en/sub-agents and https://code.claude.com/docs/en/costs
11. Headless. `claude -p`. Output: text (default), json (result, session_id, total_cost_usd, and structured_output with `--json-schema`), and stream-json (NDJSON, with `--verbose` and `--include-partial-messages`). Exit code 0 on success. `--bare` skips auto-discovery of hooks, skills, MCP, and CLAUDE.md. `--permission-prompts none` denies anything that would prompt. Source: https://code.claude.com/docs/en/headless
12. Sessions. `--continue`, `--resume <id>`, and `--fork-session` exist. Transcripts are JSONL files. Source: https://code.claude.com/docs/en/headless and https://code.claude.com/docs/en/checkpointing. Export and share: UNVERIFIED.
13. Config. JSON settings in `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`, and managed policy. MCP in `.mcp.json` and `~/.claude.json`. Source: https://code.claude.com/docs/en/hooks and https://code.claude.com/docs/en/mcp
14. Install and startup: UNVERIFIED. `--bare` is described as reducing startup time, with no number given. Source: https://code.claude.com/docs/en/headless

### Codex CLI
1. Undo / checkpoints: UNVERIFIED in what I read.
2. MCP. `codex mcp` adds local or remote servers, handles auth, and lists tools. Source: https://learn.chatgpt.com/docs/codex/cli (links to developers.openai.com/codex/extend/mcp). MCP server mode: UNVERIFIED.
3. AGENTS.md. `/init` creates one. Source: https://learn.chatgpt.com/docs/codex/cli (links to developers.openai.com/codex/agent-configuration/agents-md)
4. Hooks, custom commands, skills: UNVERIFIED.
5. Sandbox, Linux. Launch flow: the helper finds `bwrap` on PATH outside the cwd, or uses a bundled copy. `bwrap` builds the mount view. The root is read-only (`--ro-bind / /`). Writable roots are bind-mounted rw. Protected paths (`.git`, `.codex`) inside writable roots are re-mounted ro. Symlinks and missing protected paths are masked with `/dev/null`. Namespaces: user always, pid by default, net when network is restricted. In managed proxy mode, net is combined with a TCP-to-Unix-socket-to-TCP bridge so traffic reaches only configured proxy endpoints. The helper then sets `PR_SET_NO_NEW_PRIVS` and installs a seccomp filter. Restricted network policies block network syscalls. All modes block `io_uring`. Filesystem-restricted policies with open networking still block `AF_VSOCK`. In managed proxy mode, new standalone `AF_UNIX` sockets are blocked unless explicitly allowed. Landlock survives only as a legacy option and is rejected for filesystem-restricted policies, because it cannot isolate app-server Unix sockets. Source: https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md
5b. Sandbox, macOS: UNVERIFIED. Approval and sandbox mode names, defaults, and workspace-write network defaults: UNVERIFIED. The linked pages (developers.openai.com/codex/sandboxing, permission-modes) were not reachable from my fetches.
6. Git: UNVERIFIED.
7. Image and web. `codex --image` passes images with the first prompt, and images can be pasted into the composer. `codex --search` enables live web search. Source: https://learn.chatgpt.com/docs/codex/cli (links to developers.openai.com/codex/image-inputs and /web-search)
8. Models. Sign in with a ChatGPT plan or use an API key. Provider config and local or OSS models: UNVERIFIED (https://developers.openai.com/codex/configuration linked, not read).
9. Cost and budget: UNVERIFIED.
10. Sub-agents and todo: UNVERIFIED.
11. Headless. `codex exec` for scripts, CI, and pipelines. Output options (including any `--json`): UNVERIFIED. Source: https://learn.chatgpt.com/docs/codex/cli (links to developers.openai.com/codex/non-interactive-mode)
12. Sessions. `codex resume` reopens a recent chat from the repo or searches local chats. Fork: UNVERIFIED.
13. Config format: UNVERIFIED.
14. Install: curl script (Mac/Linux), PowerShell script (Windows), `npm install -g @openai/codex`, `brew install --cask codex`, GitHub Releases binaries. Startup: UNVERIFIED. Source: https://github.com/openai/codex

### OpenCode
1. Undo. `/undo` reverts changes and restores the prompt. `/redo` reapplies them. The mechanism (git snapshot or otherwise) is not stated on the page. Source: https://opencode.ai/docs/
2. MCP: the page links to https://opencode.ai/docs/mcp-servers/. I did not read it, so this is UNVERIFIED.
3. AGENTS.md: `/init` makes OpenCode analyze the project and create AGENTS.md. Source: https://opencode.ai/docs/
4-7. Hooks, skills, sandbox, git, image and web: UNVERIFIED. Permissions page linked at https://opencode.ai/docs/permissions/ and not read.
8. Providers: any provider by API key. `/connect` sets up OpenCode Zen. Local models: not mentioned. Source: https://opencode.ai/docs/
10-11. Sub-agents and headless: linked pages (opencode.ai/docs/agents/, /docs/cli/) not read. UNVERIFIED.
12. Share: https://opencode.ai/docs/share returned 404 during my fetch. UNVERIFIED.
14. Install: `curl -fsSL https://opencode.ai/install | bash`, npm, Homebrew, pacman, Chocolatey, Scoop, Docker. Startup: UNVERIFIED. Source: https://opencode.ai/docs/

### pi coding agent
Fetched from the pi repo README (earendil-works/pi).
- No sub-agents and no plan mode. The README says pi "skips features like sub-agents and plan mode." Source: https://github.com/earendil-works/pi/tree/main/packages/coding-agent
- Extensions, skills, and prompt templates are supported (docs/extensions.md, docs/skills.md, docs/prompt-templates.md). Same source.
- Print/JSON mode, RPC, and SDK (docs/cli.md, docs/rpc.md, docs/sdk.md). Same source.
- MCP, permission prompts, sandboxing, hooks, AGENTS.md, undo, cost display, session fork: not mentioned in the README, so UNVERIFIED. Treat as unknown, not as NO.

### Aider
- Undo: `/undo` discards the last change. Source: https://aider.chat/docs/git.html
- Git: auto-commits each change by default (`--no-auto-commits` disables). Commit messages come from a weak model, following Conventional Commits by default. `/diff` shows changes since your last message. Before editing files with uncommitted changes, aider commits those first, so the two sets of edits stay separate (`--no-dirty-commits` disables). PR creation: not mentioned on the page. Source: https://aider.chat/docs/git.html
- All other rows (MCP, AGENTS.md or conventions, hooks, sandbox, image, web, models, cost, headless, sessions, config, install): UNVERIFIED. I did not fetch the other Aider pages.

### Goose (block/goose, now aaif-goose/goose)
- MCP: yes. Connects to 70+ extensions through MCP. Source: https://github.com/block/goose
- Providers: 15+, including Anthropic, OpenAI, Google, Ollama, OpenRouter, Azure, and Bedrock. Ollama is the only listed local option. Source: same.
- Install: desktop app (macOS, Linux, Windows), CLI download script, PowerShell script, Docker, Nix flake. Source: same.
- Recipes exist (what they are: not explained). `.goosehints` and AGENTS.md files are in the repo, behavior UNVERIFIED.
- Headless, sub-agents, sandbox or permission modes, undo, git, cost: not described on the README, so UNVERIFIED.

### Crush (charmbracelet/crush)
- MCP: stdio, HTTP, and SSE transports. HTTP and SSE support built-in OAuth via `"oauth": true`. Source: https://github.com/charmbracelet/crush
- LSP: yes, configured with `lsp add` in a crushrc. Source: same.
- Context files: AGENTS.md is the default project file (renamable). Global: `~/.config/crush/CRUSH.md` and `~/.config/AGENTS.md`. CLAUDE.md is not named. Skill directories `.claude/skills` and `.cursor/skills` are read. Source: same.
- Permissions: asks before tool calls by default. `permissions allow` auto-approves named tools, `permissions deny` hides them, and `--yolo` skips all prompts. Source: same.
- Models: mid-session switch with ctrl+l keeps context. Local providers: Ollama, llama.cpp, LM Studio, LiteLLM, with auto discovery. Source: same.
- Commit and PR attribution trailers (`assisted-by`, `co-authored-by`, or none). Source: same.
- No undo documented. No OS sandbox documented. `crush run` not confirmed in the README.
- Install: Homebrew, npm (`@charmland/crush`), `go install`, distro repos, Winget, Scoop, Nix. Startup: UNVERIFIED. Source: same.

## Answers

### Features in at least 4 agents (table stakes)
Verified in 4 or more agents:
- MCP client: Claude Code, Codex, Goose, Crush (OpenCode's MCP page is linked but unread).
- Project instruction file: Claude Code, Codex, OpenCode, Crush.
- Model switching across providers: Claude Code, OpenCode, Goose, Crush.
- Skills or prompt templates: Claude Code, pi, Crush (reads skill dirs), with Codex and OpenCode unverified.

Likely table stakes but short of 4 verified:
- Undo or checkpoints: Claude Code, OpenCode, Aider verified. Codex, pi, Goose, and Crush were not confirmed. Crush documents none.
- Headless mode: Claude Code, Codex, pi verified. OpenCode's CLI page is linked but unread.

Verified in fewer than 4: OS sandbox (Claude Code and Codex, both on bubblewrap for Linux), sub-agents (Claude Code only, and pi explicitly omits them), hooks (Claude Code only), cost and budget caps (Claude Code only), session export and share (none verified).

### Rare but loved (from documented design choices, not user praise)
These are features the primary docs describe in enough detail to be worth copying:
- Aider's pre-edit dirty commit. It snapshots your uncommitted work before its own edits so diffs stay separable. Source: https://aider.chat/docs/git.html
- Claude Code's rewind menu splits "restore conversation" from "restore code", and adds "summarize from here". Source: https://code.claude.com/docs/en/checkpointing
- Claude Code's `/usage` attributes spend to skills, sub-agents, plugins, and MCP servers, with a one-key fix shortcut. Source: https://code.claude.com/docs/en/costs
- Codex's network isolation: a TCP-to-Unix-socket bridge in managed proxy mode, with blocks on new Unix sockets. Source: https://github.com/openai/codex/blob/main/codex-rs/linux-sandbox/README.md
- Claude Code's `--permission-prompts none` for unattended runs, with denials reported in stream-json. Source: https://code.claude.com/docs/en/headless
- pi's choice to leave sub-agents and plan mode out in favor of extensions. Source: https://github.com/earendil-works/pi/tree/main/packages/coding-agent
- Crush's built-in OAuth for remote MCP servers. Source: https://github.com/charmbracelet/crush

### Mechanics

Claude Code checkpoints. Snapshots are taken at each turn-starting prompt, and only Claude's own file-tool writes are recorded. Bash commands are not tracked, and subagent writes mostly are not either. Retention is a 100-checkpoint window, swept after about 30 days. I could not confirm the storage format (shadow git repo vs per-file copies) from the page. Source: https://code.claude.com/docs/en/checkpointing

Codex Linux sandbox. Your premise needs one correction: Codex does not use Landlock as its default. The Linux path is bubblewrap for filesystem and namespace isolation, and seccomp for network filtering. Landlock is legacy and rejected for filesystem-restricted policies. The read-only root plus rw bind mounts for writable roots, the protected-path remounts, and the seccomp rules are described in the source above. Codex's default network policy in workspace-write, and its macOS Seatbelt path, are UNVERIFIED.

Claude Code sandbox. Same underlying primitive on Linux (bubblewrap), with a separate host-side proxy that enforces a domain allowlist. Native Windows has no sandbox. Source: https://code.claude.com/docs/en/sandboxing

Practical read for your CLI: bubblewrap is the primitive both major agents use on Linux, so Landlock is not a requirement for parity. Claude Code's default is deny-by-default network via its proxy allowlist (verified). Codex's restricted network policy blocks network syscalls, but its default setting in workspace-write is UNVERIFIED.

### Install and startup
Startup times are UNVERIFIED for all seven agents. Install methods are listed in each section above.
