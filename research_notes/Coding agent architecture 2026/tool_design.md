# Tool Design for Coding Agents (as of October 2026)

Scope note: Most primary-source evidence I could retrieve is dated 2024 to November 2025. I found no 2026 peer-reviewed or independent edit-format benchmark. Older findings are marked. Several vendor docs were fetched through a summarizer, so quoted wording should be spot-checked before it goes into a spec. Sources that I only saw as search snippets or that returned errors are labeled as unverified.

## Key Question 1: Edit formats

### Takeaway
No format has won outright. Anthropic ships an exact-match str_replace tool, OpenAI ships a V4A patch tool (apply_patch), and Aider's leaderboard shows diff-style formats dominating the top of its polyglot benchmark, with whole-file rewrites still competitive on some models. The public data measures format compliance, not edit success per tool call, so the failure rates below are proxies.

### Cited Findings
- Anthropic text editor tool is named `str_replace_based_edit_tool`, type `text_editor_20250728` (`max_characters` is only valid on that version or later). Commands: `view` (1-indexed `view_range`, -1 means end of file), `str_replace` (`old_str`, `new_str`), `create` (`file_text`), `insert`. [Anthropic text editor docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool)
- The Anthropic text editor docs I retrieved do not state the exact-uniqueness or error behavior for `str_replace` (for example, what happens when `old_str` matches zero or multiple times). That needs a direct test against the API before I rely on it. [Anthropic text editor docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool)
- OpenAI apply_patch uses a V4A diff format with three operations: `create_file` (full content as a V4A diff), `update_file` (additions, deletions, replacements), and `delete_file` (no diff). The harness is responsible for interpreting and applying the diff. [OpenAI apply_patch guide](https://developers.openai.com/api/docs/guides/tools-apply-patch)
- OpenAI apply_patch failure messages: `Error: File not found at path '...'` (status `failed`) and `Error: Invalid Context:` followed by the hunk header on patch conflict. The docs recommend validating paths against directory traversal, making backups or working in a scratch copy, choosing all-or-nothing vs per-file atomicity, and returning exactly one output per `call_id`. [OpenAI apply_patch guide](https://developers.openai.com/api/docs/guides/tools-apply-patch)
- OpenAI's apply_patch support table lists GPT-5.5, GPT-5.4, GPT-5.2, and GPT-5.1. The docs do not say which models were trained on the V4A format. Their sample code uses `gpt-6-astra`, which is not in that table. [OpenAI apply_patch guide](https://developers.openai.com/api/docs/guides/tools-apply-patch)
- Aider's edit formats (older docs): `whole` (full file returned, "can be slow and costly"), `diff` (search/replace blocks in merge-conflict style, "efficient format"), `diff-fenced` (file path inside the fence, recommended for Gemini models, which "often fail to conform" to the fencing in `diff`), `udiff` (simplified unified diff, "mainly used" for GPT-4 Turbo, reduced "lazy coding" such as placeholder comments), and `editor-diff`/`editor-whole` for architect mode. [Aider edit formats](https://aider.chat/docs/more/edit-formats.html)
- Aider's older Python-only edit leaderboard (last updated April 12, 2025, 133 Exercism exercises, since replaced): o1 84.2% pass with diff, claude-3-5-sonnet-20241022 84.2% with diff (99.2% edit-format compliance), gemini-exp-1206 80.5% with whole (100% compliance), o1-preview 79.7% with diff (93.2% compliance). [Aider edit leaderboard](https://aider.chat/docs/leaderboards/edit.html)
- Aider polyglot leaderboard (last updated November 20, 2025; 225 Exercism exercises across C++, Go, Java, JavaScript, Python, Rust): gpt-5 (high) 88.0% with diff (91.6% well-formed, 23 error outputs); gpt-5 (medium) 86.7% with diff (88.4% well-formed); o3-pro (high) 84.9% with diff (97.8% well-formed); gemini-2.5-pro-preview-06-05 (32k think) 83.1% with diff-fenced (99.6% well-formed, 1 error output); o3 (high) + gpt-4.1 as editor 78.2% in architect mode (100% well-formed). Well-formed rates range from 86.7% to 100% in the top 10. [Aider polyglot leaderboard](https://aider.chat/docs/leaderboards/)
- Aider does not publish a causal claim linking edit format to pass rate on the polyglot page. [Aider polyglot leaderboard](https://aider.chat/docs/leaderboards/)
- SWE-agent (2024, GPT-4 Turbo, SWE-bench Lite): 51.7% of trajectories contained at least one failed edit. Eventual edit success was 90.5% overall but fell to 57.2% after a single failed edit. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Fast-apply vendors: Relace published two posts, "Instant Apply now 4300 tok/s" (May 25, 2025) and "A Year of Fast Apply, The Path to 10k Tokens per Second" (October 29, 2025). I only saw the titles. The article bodies did not load, and the links were relative paths I could not resolve. [Relace blog](https://relace.ai/blog)
- Morph fast-apply: the Morph blog returned HTTP 429, so I have no primary numbers from Morph. I found no independently verified failure-rate figures for fast-apply models. [Morph blog](https://morphllm.com/blog)

### Inferences
- The public benchmark data says diff-style edits win on score for strong models, and whole-file output has the lowest format-error rate on some models (for example, gemini-exp-1206 at 100% compliance). The trade-off is token cost vs format reliability, and it varies by model family. A from-scratch CLI should expect per-model format tuning, not a single universal format.
- Given SWE-agent's finding that one failed edit cuts eventual success sharply (2024 model, so treat as directional), error messages that let the model retry cheaply probably matter more than the specific format. This is an inference, not a tested claim.
- Anthropic and OpenAI each ship a format tied to their models. For a custom agent, using the provider's native edit tool when available and a diff-based fallback for other models follows from the evidence, but the docs do not say which models were trained on which format.

### Gaps
- No primary source says which models were trained on str_replace vs apply_patch vs udiff. The OpenAI docs list supported models but not training data.
- Did not read the openai/codex source, so I have no direct evidence on its V4A parser, fuzzy matching, or failure handling.
- No 2026 Aider or independent benchmark results for edit formats. Aider's newest data is November 2025.
- No independently verified fast-apply failure rates. Morph and Relace numbers are unverified or unavailable.
- Exact-uniqueness behavior and error text for Anthropic `str_replace` are not in the docs I retrieved.

## Key Question 2: Bash and shell execution

### Takeaway
Anthropic's reference bash tool is a client-owned persistent session with sentinel-delimited output, restart semantics, and a process-group kill on timeout. OpenAI's shell tool is non-interactive, with per-command timeouts and an output cap. mini-swe-agent runs every action as a fresh subprocess and reports strong SWE-bench scores with a bash-only design, but those scores are self-reported.

### Cited Findings
- Anthropic bash tool (`bash_20250124`): the application keeps one bash process alive across calls, so working directory, env vars, and created files persist. The API itself is stateless, and the application owns the shell process, the timeout, and the safety checks. [Anthropic bash tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool)
- Anthropic bash tool parameters: `command`, and `restart` (`true` kills the shell and starts a new one, which clears cwd, env, and running processes). Stdout and stderr are returned together in one `tool_result`. [Anthropic bash tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool)
- Anthropic reference implementation: a long-lived bash process with `exec 2>&1` so errors interleave with output in order; each command is followed by `echo __CLAUDE_BASH_DONE_<uuid>__` as a unique sentinel to mark where output ends; the process starts in its own process group (`start_new_session` / `detached` / `Setpgid`) so a timeout can kill all child processes. [Anthropic bash tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool)
- I did not find an explicit output-truncation limit or default timeout in the portion of the Anthropic bash doc I searched. [Anthropic bash tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool)
- OpenAI shell tool: `shell_call` takes a `commands` array with per-command `timeout_ms` (example 120000). `max_output_length` appears on `shell_call` and must be echoed in `shell_call_output`. A timed-out command returns `{"type": "timeout"}` with partial output. Local executor examples default to 60 seconds. The docs say "shell tool execution should be non-interactive" and "Don't rely on interactive commands." Concurrency across multiple commands in one call is not stated. [OpenAI shell tool guide](https://developers.openai.com/api/docs/guides/tools-shell)
- OpenAI shell tool persistence: hosted containers persist across requests via `container_reference` or `previous_response_id`, with `expires_after` (example 20 minutes from last activity). The local example runs each command in a new process, so shell state does not persist unless the harness adds it. Background process handling is not documented beyond hosted services. [OpenAI shell tool guide](https://developers.openai.com/api/docs/guides/tools-shell)
- mini-swe-agent: bash-only, no other tools. Each action runs as an independent `subprocess.run`, explicitly not a stateful shell. Sandboxing is done by swapping in `docker exec`. The README claims "Scores >74% on the SWE-bench verified benchmark" without naming the model or date. [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)
- Claude Code and Codex shell implementation details (persistent vs fresh subprocess, timeout defaults, background handling) were not confirmed from primary sources in this pass.

### Inferences
- Persistent sessions give the model cwd and env continuity, but they need sentinel parsing, restart logic, and a way to recover from a hung process. Fresh subprocesses are simpler and easier to sandbox, and mini-swe-agent shows they can score well with the right prompting. A from-scratch CLI could support both: a fresh subprocess by default, with an optional session for workflows that depend on state.
- Both Anthropic's reference and OpenAI's local executor kill the whole process group on timeout. A custom CLI should do the same, or child processes (test runners, dev servers) survive the timeout.
- Both vendor docs push non-interactive commands. An interactive prompt in a persistent session hangs the agent, so the harness should close stdin and set non-interactive flags.

### Gaps
- Did not verify output truncation policy (head/tail or byte cap) for any harness except OpenAI's `max_output_length`, which has no documented default.
- Background process handling for Claude Code, Codex CLI, OpenHands, and SWE-agent was not verified in this pass.
- The mini-swe-agent SWE-bench Verified number is self-reported and unattributed to a model.

## Key Question 3: Agent-Computer Interface findings (SWE-agent)

### Takeaway
The SWE-agent paper (2024, GPT-4 Turbo, SWE-bench Lite) measured each ACI component. Windowed file viewing (100 lines), a lint check on each edit, and summarized search results each contributed several points. The paper is old and the model is old, so these are directional for 2026 models, not settled.

### Cited Findings
- SWE-agent ablations (SWE-bench Lite, % resolved): full SWE-agent 18.0; without linting 15.0; no edit tool 10.3; iterative search (one result at a time) 12.0; no search 15.7; 30-line viewer 14.3; full-file viewer 12.7; full history 15.0; without demonstrations 16.3. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Viewer: `open`, `goto`, `scroll_up`, `scroll_down`, each window 100 lines. Line numbers are shown so the agent does not need arithmetic when editing. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Linting: flake8 runs on the edit with selected codes (for example F821, E999). A failing edit is reverted and the agent gets the error, a before/after snippet, and the original content. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Search: `find_file`, `search_file`, `search_dir`, results capped at 50. Over the cap, results are withheld and the agent must narrow the query. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Shell-only baseline (GPT-4 Turbo, SWE-bench Lite): 11.0 vs SWE-agent 18.0. RAG baseline: 2.67. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Paper's stated reason: simple commands with few options and concise docs are easier for agents to use. "Human user interfaces are not always suitable as agent-computer interfaces." [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- Paper's main result on full SWE-bench test: 12.47% (GPT-4 Turbo) and 10.46% (Claude 3 Opus). The paper reports no SWE-bench Verified results. [SWE-agent paper, HTML v3](https://arxiv.org/html/2405.15793v3)
- mini-swe-agent, a 2025 project, reaches >74% on SWE-bench Verified with bash alone, which suggests that much of SWE-agent's ACI scaffolding is no longer needed for strong models. This is self-reported. [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent)

### Inferences
- The strongest surviving findings are the cheap guardrails: edit validation with revert on error, and bounded search output. Both reduce cascade failures, which should still matter for weaker or cheaper models.
- Windowing and search caps are about context budget. Frontier models with larger windows may tolerate a full-file view, so the 100-line default should be treated as a tunable, not a constant.
- The result that shell-only underperforms SWE-agent (11.0 vs 18.0) conflicts with mini-swe-agent's bash-only claim. The difference is model generation and scaffolding, not something this data resolves.

### Gaps
- No 2026 ablation of ACI components on frontier models. I found no follow-up paper in this pass.
- The paper's SWE-bench Lite ablations do not say whether the linter effect holds for typed languages or for models with native edit tools.
- The arXiv abstract and HTML fetch did not contain the HumanEvalFix table in full. The paper text gives 88.3% for Python while the table gives 87.7%. I did not resolve this.

## Key Question 4: Search and navigation

### Takeaway
The best-documented evidence favors combining grep-style search with semantic search for large codebases, but the strong numbers come from Cursor (self-reported, offline benchmark not released) and from Morph (vendor). Claude Code's public stance, as summarized secondhand, moved away from RAG to agentic search.

### Cited Findings
- Cursor (November 6, 2025): semantic search improved answer accuracy by an average of 12.5% on Cursor Context Bench, range 6.5% to 23.5% by model. Per-model numbers are only in a chart image. [Cursor semantic search blog](https://cursor.com/blog/semsearch)
- Cursor online A/B: code retention up 0.3% with semantic search, up 2.6% on codebases with 1,000+ files. Dissatisfied follow-up requests were 2.2% higher without semantic search. The authors say "not all requests require search." [Cursor semantic search blog](https://cursor.com/blog/semsearch)
- Cursor concludes that "Semantic search is currently necessary to achieve the best results, especially in large codebases," and that grep plus semantic search performs best. [Cursor semantic search blog](https://cursor.com/blog/semsearch)
- Morph (vendor, unverified): on 500 tasks, semantic search combined with grep reached 84.1% success vs 64.2% for grep alone. I only saw this in a search summary, and the Morph blog returned HTTP 429. [Morph semantic search article](https://morphllm.com/blog/semantic-search)
- GrepRAG (cited in secondary commentary, not retrieved): grep can beat semantic methods on structured repository-level code completion. The two results measure different tasks. [Search summary, unverified](https://www.zenml.io/llmops-database/enhancing-ai-coding-agent-performance-with-custom-semantic-search)
- Claude Code moved from RAG with a local vector database to agentic search, per a talk summary (Boris Cherny). I did not view the talk or a primary transcript. [AI Engineer talk page, unverified](https://www.ai.engineer/talks/UM6sFg_jdlE-rag-is-dead-right-kuba-rogut)
- Anthropic's Claude Code guidance (from the writing-tools article): tool responses are restricted to 25,000 tokens by default, and truncated results should include instructions that steer toward narrower queries. [Anthropic writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

### Inferences
- The claim that semantic search adds value on top of grep comes from one vendor with an unpublished benchmark. The A/B deltas are small. Treat the case for embeddings as plausible, not established.
- The pattern that holds across sources is that grep plus file-name search is the baseline, and semantic search is an optional addition for fuzzy, natural-language queries on large repos. For a from-scratch CLI, grep/ripgrep plus glob is the reasonable default, with LSP or embeddings as later additions.
- SWE-agent's search result (capped, summarized results beat iterative paging) still applies to any grep wrapper: unbounded output is worse than a capped list plus a narrowing instruction.

### Gaps
- No primary Claude Code, Codex, or Amp document on search design in this pass.
- No independent comparison of LSP-based navigation vs grep for coding agents.
- The Morph and GrepRAG numbers are unverified in this pass.

## Key Question 5: Tool count, descriptions, and deferred loading

### Takeaway
Anthropic's guidance is to start with a few consolidated tools, namespace them, add a concise/detailed response option, cap responses, write actionable errors, and treat descriptions as an eval target. For many tools, deferred tool search cuts upfront context. The reported gains are from Anthropic's internal evals.

### Cited Findings
- "More tools don't always lead to better outcomes." Start with "a few thoughtful tools targeting specific high-impact workflows." Consolidate chained operations into one tool. [Anthropic writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Namespacing (`asana_search`, `asana_projects_search`) helps tool selection. Prefix vs suffix naming changed eval results, and the best choice varies by model. [Anthropic writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Add a `response_format` enum (DETAILED, CONCISE). In Anthropic's example, the detailed response used 206 tokens and the concise one used 72. [Anthropic writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Small description changes had large effects. Anthropic reports that a description fix stopped Claude from appending "2025" to web search queries. [Anthropic writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Tool search (deferred loading): an example with 5 MCP servers (58 tools) costs about 55K tokens upfront. Anthropic reports about 8.7K with tool search, and MCP evaluation accuracy rose from 49% to 74% (Opus 4) and 79.5% to 88.1% (Opus 4.5). The numbers are Anthropic's own, and the article describes the feature as beta. [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- Programmatic tool calling: average usage dropped from 43,588 to 27,297 tokens (37%) on complex research tasks, per Anthropic's internal testing. [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- Tool use examples (`input_examples`, 1 to 5 per tool): accuracy on complex parameter handling rose from 72% to 90% in Anthropic's testing. [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)

### Inferences
- A from-scratch CLI can keep its core set small (read, write/edit, search, glob, bash) and defer MCP tools behind a search tool. The savings are large only when MCP servers contribute many tools.
- Anthropic's own numbers suggest the description and example work is worth an eval loop, not a one-time write.
- The 25K-token response cap is a reasonable default for a CLI's tool result limit, but it is Anthropic's default for Claude Code, not a published policy for other harnesses.

### Gaps
- No independent measurement of tool-count effects on coding tasks.
- MCP as a tool source: I did not research MCP specifics (server lifecycle, auth, transport). That is outside the scope of this pass unless the caller asks.

## Key Question 6: Parallel tool calls

### Takeaway
The Anthropic API runs multiple tool calls from one turn concurrently by default, and `disable_parallel_tool_use` turns that off. How a harness classifies read-only vs mutating tools is not documented in an official Claude Code source. Secondary sources describe a concurrency-safe flag per tool, with read tools safe and write/bash tools unsafe.

### Cited Findings
- Anthropic parallel tool use: enabled by default. Calls in one turn are unordered and may run concurrently. Disable with `disable_parallel_tool_use`. [Anthropic parallel tool use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use). Retrieved as a search snippet only, not fetched.
- A third-party write-up describes `isConcurrencySafe()` per tool: read tools (Glob, Grep, FileRead) run in parallel, mutating tools (FileWrite, Bash) run serially. This is unverified and based on a secondary description of the Claude Code codebase. [Claude Code tool system summary](https://www.mintlify.com/killlowkey/claude-code/concepts/tool-system)
- A GitHub-style issue reports that Claude Code does not expose `disable_parallel_tool_use`, and that a failure in a mutating tool (Bash, Edit, Write) cancels its sibling calls. It says a fix for read-only commands shipped in v2.1.147. Unverified. [claudeissues report](https://claudeissues.com/issue/64237-expose-a-way-to-disable-parallel-tool-execution-the-apis-disable-parallel-tool-u)
- A closed issue reports an API 400 "tool use concurrency" error when Claude Code issued parallel Read calls in the VS Code extension, while the terminal CLI worked. Resolution not shown. [claudeissues report](https://claudeissues.com/issue/21110-api-error-400-due-to-tool-use-concurrency-issues-when-reading-multiple-files)
- OpenAI shell tool: concurrency across the commands array is not stated. [OpenAI shell tool guide](https://developers.openai.com/api/docs/guides/tools-shell)

### Inferences
- A safe default for a from-scratch CLI: mark read-only tools concurrency-safe and run them in parallel, serialize anything that writes or runs shell. Let a failed mutating call not cancel reads, since those are independent.
- The reported 400 error suggests that concurrent file reads need the harness to serialize results in order and handle partial failures per call.

### Gaps
- No official Claude Code or Codex document on concurrency classification. The described behavior is secondhand and should be verified against the source before design decisions rely on it.
- No measurement of parallel vs serial tool calls on coding task quality or latency.
- Did not read openai/codex or OpenHands source for their tool-dispatch logic.

## Sources not reached in this pass
- openai/codex source (V4A parser, shell implementation)
- OpenHands and SWE-agent repository source
- Aider polyglot benchmark post on edit format effects, and any 2026 Aider data
- Morph and Relace primary posts (Morph returned HTTP 429; Relace links unresolved)
- Claude Code official docs on tools and concurrency
- 2025-2026 follow-ups on ACI or code-editing research beyond SWE-agent
