# Sub-agents, Planning, and Verification-First Coding Agents

Research date: 2026-10-19. Budget: 15 tool calls. Primary pages were read through WebFetch, which runs a small model over the page, so exact wording needs a check against the original before quoting. Items tagged [snippet] came only from search results and were not opened. Items tagged [unverified] could not be confirmed in this pass. Not covered: Codex, Amp, OpenHands, and Cursor designs, and 2026 TDD-style agents (see Gaps).

## Key Question 1: How do production coding agents use sub-agents (context isolation, return values, parallelism), and what does the evidence say for and against multi-agent designs?

### Takeaway
Production harnesses use sub-agents mainly as context firewalls for read-heavy exploration that returns a summary. Cognition argues that parallel sub-agents without shared decision context produce conflicting work, and recommends a single-threaded agent by default. Anthropic reports a large gain for breadth-first research and says coding is a poor fit. I found no controlled single-vs-multi-agent comparison on SWE-bench-style coding tasks.

### Cited Findings
- Claude Code sub-agents each run in their own context window. They start without the main conversation history, but get their own system prompt, CLAUDE.md, a git status snapshot, and preloaded skills. Only the final summary returns to the parent; tool calls and verbose output stay in the sub-agent. [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents)
- Claude Code parallelism: foreground sub-agents block the parent, background sub-agents run concurrently, and several can run at once. Default concurrency is 20 (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Nesting defaults to 3 layers below the main conversation (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`); at the limit the Agent tool is withheld from sub-agents, except forks. [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents)
- Claude Code guidance: use sub-agents when output is verbose, when tool restrictions are needed, or when the work is self-contained and returns a summary. Stay in the main conversation for back-and-forth, multi-phase work sharing context, quick targeted edits, or latency-sensitive work. The docs warn that many sub-agents returning detailed results can consume significant context. [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents)
- Claude Code sub-agent config: frontmatter fields include `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, `memory`, `background`, `isolation`, `effort`. Model resolution order: per-invocation parameter, frontmatter, `CLAUDE_CODE_SUBAGENT_MODEL`, then the main model. [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents)
- Anthropic multi-agent research system: orchestrator-worker pattern. A LeadResearcher plans, spawns sub-agents that each have their own context window, tools, and prompt, and receives condensed findings back. The lead saves its plan to memory because context beyond 200k tokens gets truncated. A separate CitationAgent attaches sources to claims. [Anthropic, How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic: a Claude Opus 4 lead with Sonnet 4 sub-agents beat single-agent Opus 4 by 90.2% on their internal research eval. The gain was largest on breadth-first queries. The eval query set is not described in the fetched summary. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic cost: agents use about 4x the tokens of chat and multi-agent systems about 15x. On BrowseComp, token usage alone explains 80% of performance variance, with tool calls and model choice explaining most of the rest. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic parallelism: the lead runs 3-5 sub-agents in parallel, and sub-agents use 3+ tools in parallel. The post reports this "cut research time by up to 90% for complex queries." [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic on fit: coding "has fewer parallelizable subtasks than research, and agents are still weak at real-time coordination and delegation." Domains needing shared context or many inter-agent dependencies are a poor fit. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic early failure modes: spawning 50 sub-agents for a simple query, endless web scouring for nonexistent sources, sub-agents distracting each other with excessive updates. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Cognition, "Don't Build Multi-Agents" (Walden Yan, published June 12, 2025). Two principles: (1) share context, passing full agent traces rather than individual messages; (2) actions carry implicit decisions, and conflicting assumptions produce bad results. Recommends a single-threaded linear agent by default, and for very long tasks a dedicated model that compresses history into key decisions and events, which the author calls hard and possibly needing fine-tuning. Example: a Flappy Bird clone split across two sub-agents produced mismatched styles even when the second run shared the original task. The evidence is illustrative, with no benchmarks. [Cognition, Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) (cognition.ai redirects here)
- Cognition describes Claude Code sub-agents as answering well-defined questions and not running in parallel with the main agent, and presents that as a deliberately simple design. [Cognition post](https://cognition.com/blog/dont-build-multi-agents)
- Cognition follow-ups: a piece titled "Multi-Agents: What's Actually Working" [snippet], and an April 2026 reflection saying the landscape has changed and that specific multi-agent flows are implemented in Devin [snippet]. Neither was opened.

### Inferences
- Both sources agree on the same boundary. Read-only or research sub-agents that return summaries work. Parallel writing sub-agents without a shared decision record are where coordination breaks.
- For a from-scratch agent: make sub-agents read/search-only by default, give each a specific question and a bounded return schema, and keep edits in one thread. Sub-agents earn their cost on large exploration, not on edits.
- Anthropic's roughly 15x token multiplier means a sub-agent design needs task breadth to pay for itself. By Anthropic's own account, coding gets less of that benefit.
- Cognition's principle 1 and Anthropic's lead-plan memory point at one implementation need: an explicit shared artifact (plan plus decision log) that every agent reads, instead of assuming implicit context transfer.

### Gaps
- No primary measurement of single vs multi-agent on SWE-bench-style coding. The 90.2% number is research, not coding.
- Codex, Amp oracle/subagents, OpenHands delegation, and Cursor background agents were not researched. Their designs are unverified here.
- The 90.2%, 15x, and 80%-variance figures come from a summarizing fetch. Check the original post before quoting.

## Key Question 2: Planning (todo tools, plan mode, explicit plan steps): any measured benefit?

### Takeaway
I found no measured benefit from explicit planning or todo tools for coding agents. The only documented planning mechanism with an operational role is Anthropic's lead-agent plan, and it is part of a research system whose gain was not ablated against planning alone.

### Cited Findings
- Anthropic lead agent plans its research process and saves the plan to memory because long contexts get truncated. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Anthropic embeds effort rules in the lead's prompt: simple fact-finding gets 1 agent with 3-10 tool calls, direct comparisons get 2-4 sub-agents with 10-15 calls each, complex research gets more than 10 sub-agents. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- Cognition's single-threaded default implies planning is carried in the agent's own trace, not in separate planner agents. [Cognition post](https://cognition.com/blog/dont-build-multi-agents)

### Inferences
- The 90.2% result combines planning with sub-agents and parallel search. It does not isolate planning.
- For a from-scratch agent, a plan is cheap to write and log, but its value is unmeasured. Run an ablation (plan vs no plan, same budget, SWE-bench-style tasks) before building a planning mode around it.

### Gaps
- No ablation of plan vs no plan on coding tasks found.
- Claude Code plan mode, Codex, and Cursor todo or plan features were not checked against primary docs.

## Key Question 3: Reproduction test generation before fixing (SWT-Bench, Otter, Issue2Test, Agentless, SWE-agent follow-ups, TDD-style agents): success rates and failure modes

### Takeaway
Writing a fail-to-pass reproduction test from an issue is hard. On SWT-Bench-Lite (276 instances, GPT-4-era models), the best agent reached 18.5% success, and the authors' most-cited failure is a test that passes without reproducing the issue. Newer papers report higher numbers, but on different benchmarks, so the figures are not comparable. Red-to-green alone does not establish that a test reproduces the reported bug.

### Cited Findings
- SWT-Bench (arXiv 2406.12952, June 2024): converts SWE-bench into a test-generation task. Fail-to-pass means the test fails before the golden patch and passes after it. Full benchmark has 1,983 instances after filtering from 2,294; experiments use SWT-Bench-Lite (276). [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- SWT-Bench Table 2, success S (%) on Lite, GPT-4 unless noted: ZeroShot 3.6; ZeroShotPlus 9.4; LIBRO 14.1; AutoCodeRover 9.1; aider 12.7; SWE-Agent 15.9; SWE-Agent+ (told to execute its tests) 18.5; Pass@5 oracle 20.3 (not practical). [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- SWT-Bench Table 3, change coverage of the golden patch: golden tests 72.0; SWE-Agent+ 27.6 overall and 69.4 on successful instances; ZeroShot 7.6. Existing repo test suites cover 70.1% of lines on average. [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- SWT-Bench model comparison for SWE-Agent S: Mistral Large 2 16.3; GPT-4 15.9; Claude 3.5 Sonnet 12.3; GPT-4o mini 9.8; Claude 3.0 Haiku 2.5; Mixtral 0.7. Claude 3.5 Sonnet has the highest change coverage (30.3) despite lower S. [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- SWT-Bench failure modes: (a) passing tests that do not reproduce the issue, common even in SWE-Agent+; (b) loops after edit commands that fail to apply; (c) failing to set up or run the test environment; (d) invalid test code (syntax errors, invalid variables); (e) malformed unified diffs, with ZeroShot producing well-formed patches for only 48.6%; (f) tests that fail for reasons unrelated to the bug; (g) total failure on astropy, requests, and flask. All pylint tests were pass-to-pass, correct but unhelpful. [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- SWT-Bench agent limits are inconsistent: Section 5.1 gives 20 API calls for SWE-Agent, 3 reflection steps for aider, and 10 rounds for AutoCodeRover, while Appendix C.2 says 20 is the default for interaction rounds. [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- Issue2Test (arXiv 2503.16320, March 2025): LLM generates a reproducing test refined with compilation and runtime feedback. Reproduces 32.9% of SWT-bench-Lite issues, a 16.3% relative gain over the best prior technique. [snippet, arXiv 2503.16320](https://arxiv.org/abs/2503.16320v3)
- Otter (arXiv 2502.05368, Feb 2025, revised May 2025): pipeline of a localizer, a self-reflective action planner, and a test generator, with rule-based analysis that checks and repairs LLM output. Introduces TDD-Bench-Verified. Authors claim it outperforms prior systems on generating tests from issues. The abstract gives no reproduction counts; the detailed results are in the HTML version, which I did not open. [arXiv 2502.05368](https://arxiv.org/abs/2502.05368)
- e-Otter++ (arXiv 2508.06365, Aug 2025; ICSE 2026 per snippet): uses execution feedback to address cases where the code to run is missing or wrong. Reports an average fail-to-pass rate of 63% on TDD-Bench Verified. [snippet, arXiv 2508.06365](https://arxiv.org/pdf/2508.06365v1). Different benchmark from SWT-Bench, so not comparable.
- SWE-Tester (arXiv 2601.13713, Jan 2026): trains open models for issue reproduction on a 41K-instance training set. Reports up to 10% gain in success rate and 21% in change coverage on SWT-Bench Verified. [snippet, arXiv 2601.13713](https://arxiv.org/abs/2601.13713v1). Baseline not checked.
- Agentless (arXiv 2407.01489, July 2024): localization, repair, and patch validation phases, no free-form agent actions. Reports 32.00% (96) on SWE-bench Lite at $0.70 per issue in the later version; an earlier abstract reports 27.33%. The search did not confirm a reproduction-test step in the paper. Repo: github.com/OpenAutoCoder/Agentless (not opened). [arXiv 2407.01489](https://www.arxiv.org/pdf/2407.01489)
- Kimi-Dev (arXiv 2509.23045) describes Agentless as a pre-defined pipeline that includes test composition. [snippet, arXiv 2509.23045](https://arxiv.org/pdf/2509.23045)
- SWE-RL (arXiv 2502.18449): in Agentless Mini, scaling both repair samples and generated reproduction tests raises SWE-bench Verified resolve rate. [snippet, arXiv 2502.18449](https://arxiv.org/pdf/2502.18449)

### Inferences
- A verify-first mode needs more than red-to-green. SWT-Bench's authors report many tests that pass without reproducing the bug, and a fail-to-pass check accepts any failure. Require the failure to match the symptom in the issue (exception type, message, or output), and check that the traceback enters the changed code path.
- SWE-Agent+ (told to execute its tests) improved S from 15.9 to 18.5, a 2.6-point gain from one setup. Letting the agent run and read its own test is the cheapest step, and it is supported by one data point only.
- The gap between S and F→P (SWE-Agent 15.9 vs 16.7) is small. I did not confirm the paper's exact definitions of S and F→P columns, so check Section 4 before relying on them.
- Hard repos (astropy, requests, flask) fail outright. The mode should fall back to a weaker check or a skip rule rather than block the fix.
- Modern models will score higher, but I have no 2026 SWT-Bench-Lite numbers with current frontier models.

### Gaps
- No 2025-2026 SWT-Bench-Lite numbers with current models.
- Full-text results for Otter and Issue2Test not extracted.
- No quantitative data on the share of generated tests that fail for the right reason in newer work. SWT-Bench discusses this only qualitatively.
- 2024-2026 TDD-style agent papers (write test first inside the agent loop) were not searched.
- Agentless reproduction-test step not confirmed.

## Key Question 4: Verification loops (running tests and linters after edits, self-review, critic models, best-of-n with test-based selection): measured gains

### Takeaway
Test-based selection across rollouts helps, but the verifier caps the gain. R2E-Gym reports each test-based verifier plateauing around 42-43% on SWE-bench Verified and 51% combined. SWE-ZERO/SWE-HERO reports the selected result trailing the Pass@K ceiling, with the gap widening as K grows.

### Cited Findings
- R2E-Gym (arXiv 2504.07164, NeurIPS 2025): a testing agent writes reproduction tests, and a regression-test filter removes patches that break existing behavior. Test-based verifiers show low distinguishability; each verifier alone plateaus around 42-43%; combined reaches 51% on SWE-bench Verified. [snippet, arXiv 2504.07164](https://arxiv.org/pdf/2504.07164)
- Satori-SWE / EvoScale (arXiv 2505.23604): best-of-N style pipeline; Best@50 of 41.6; combines a reward model with unit tests for selection. [snippet, arXiv 2505.23604](https://arxiv.org/abs/2505.23604v1)
- SWE-ZERO to SWE-HERO (arXiv 2604.01496, April 2026): with K=32 rollouts and open generative verifiers, the verifier-selected result trails Pass@K. Authors attribute the gap to verifiers lacking discriminative precision, and report a 15% gap at K=16. [snippet, arXiv 2604.01496](https://arxiv.org/pdf/2604.01496)
- SWE-World (arXiv 2602.03419): SWR-32B rises from 55.0 at K=1 to 68.2 at test-time scaling with 8 rollouts and a verifier. [snippet, arXiv 2602.03419](https://arxiv.org/pdf/2602.03419)
- SWE-RL (arXiv 2502.18449): see Key Question 3; scaling repair samples and test samples both raises resolve rate. [snippet]
- Anthropic research eval used an LLM judge scoring factual accuracy, citation accuracy, completeness, source quality, and tool efficiency, returning 0.0-1.0 and pass/fail. Human testing caught edge cases the judge missed. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- SWT-Bench: self-executing tests improved test-generation success (SWE-Agent+ 18.5 vs SWE-Agent 15.9). [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- Linter loops, self-review passes, and critic models in Claude Code, Codex, or OpenHands: not checked.

### Inferences
- Verifier quality is the bottleneck. For a from-scratch agent, invest in the verifier (reproduction test plus regression suite) before adding rollouts.
- R2E-Gym's result that verifiers are weak discriminators, and that combining them beats any one alone, supports a layered check: reproduction test, then regression tests, then a critic only as a tiebreaker.
- SWE-ZERO's 15% gap at K=16 and the SWE-World curve both suggest diminishing returns past about 8 rollouts. Set a rollout budget and measure the marginal gain.

### Gaps
- No measured gain for linter-after-edit or self-review on SWE-bench in this pass.
- Snippet figures for R2E-Gym, Satori, SWE-World, and SWE-ZERO were not opened. Verify before citing.
- Critic-model studies beyond these snippets not reviewed.

## Key Question 5: Long-horizon autonomy (done detection, max-turn policies, stuck-loop detection)

### Takeaway
Little measured evidence was found on these. Documented failure modes are agents that keep searching after results are sufficient, loops after edits that fail to apply, and over-spawning. Claude Code exposes `maxTurns` per sub-agent and a nesting cap. I did not find a documented done-detection policy in any production harness.

### Cited Findings
- Anthropic failure modes include continuing to search after results were sufficient. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)
- SWT-Bench: agents loop after issuing edits that fail to apply. [arXiv 2406.12952](https://arxiv.org/html/2406.12952v2)
- Claude Code sub-agent frontmatter supports `maxTurns` and `effort`; default nesting cap is 3 layers. [Claude Code sub-agents docs](https://code.claude.com/docs/en/sub-agents)
- Anthropic reports that token usage explains 80% of performance variance on BrowseComp, which argues for explicit budgets. [Anthropic post](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- Three controls have support in these sources, though none was measured here as a performance gain: a turn cap per task, repeated-action detection (same edit or same failing command N times in a row), and a forced verification step before the agent may declare done.
- Done should mean the reproduction test passes and the regression suite is unchanged, not that the model says so.

### Gaps
- Done-detection and stuck-loop policies in Claude Code, Codex, and OpenHands not researched.
- No measured effect of stuck-loop detection found.
- Max-turn defaults in production harnesses not checked.
