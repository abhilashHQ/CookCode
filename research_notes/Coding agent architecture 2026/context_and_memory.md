# Context Management, Compaction and Persistent Memory in Coding Agents

Research date: 2026-10-19. Coverage is partial: about 19 tool calls against the researcher budget of ~15. Items I could not verify are listed under Gaps in each section and in the consolidated list at the end.

## 1. Compaction in production systems

### Takeaway
Anthropic and OpenAI now both ship server-side compaction. Anthropic's public docs define two modes (on-demand and at a token threshold) plus a lighter rule-based tool-result clearing. OpenAI exposes compaction as a Responses API parameter and a stateless `/responses/compact` endpoint that returns an opaque encrypted item. The only published, measured comparison of summarization vs. simpler masking in an agent loop is JetBrains' Complexity Trap paper (Section 2). Vendor claims about summarization quality are not backed by published numbers.

### Cited Findings
- Anthropic's engineering post says compaction "preserves architectural decisions, unresolved bugs, and implementation details" and discards "redundant tool outputs or messages"; it also keeps the five most recently accessed files: [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Same post: tuning advice is "Start by maximizing recall," then iterate "to improve precision by eliminating superfluous content": [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Same post calls tool result clearing "One of the safest lightest touch forms of compaction": [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Same post on sub-agents: each returns "a condensed, distilled summary," typically about 1,000 to 2,000 tokens, after using tens of thousands internally: [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- The post reports no benchmark numbers for compaction, tool clearing, or note-taking. The only performance claim is qualitative ("a substantial improvement over single-agent systems"): [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic compaction docs (current page) describe server-side summarization that "replaces the older turns of a conversation with a summary that Claude writes on the server." Two modes: on-demand (you send a request; you swap the returned block in for the summarized messages) and at a token threshold (one parameter on ordinary requests; the API drops everything before the compaction block): [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- The same page lists options: keep recent turns verbatim ("compaction that keeps recent turns"), compaction in the background, a custom summarization prompt ("Your prompt replaces the default one"), and "compact again" for long conversations that already start with a compaction block: [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- On-demand compaction beta header is `compact-2026-09-04` with a top-level `compaction` parameter. Threshold mode is a separate beta. Note: a secondary search snippet cited `compact-2026-01-12` with a `compact_20260112` edit under `context_management`; that appears to be an earlier version, so check the current reference before coding against it: [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- The docs state that on several current models (listed as Claude Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5), thinking blocks in re-inserted turns must be removed or dropped under threshold compaction. On-demand compaction keeps thinking under stated conditions: [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- Context editing (clearing old tool results or old thinking blocks by rule, not summary) is a separate feature documented at `platform.claude.com/docs/en/build-with-claude/context-editing`. I did not fetch that page. Secondary sources cite the beta flag `context-management-2025-06-27`, which I have not verified against the primary doc: [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction) (links to context editing)
- OpenAI Responses API compaction: set `context_management` with a `compact_threshold` on a create request; the server compacts when the rendered token count crosses it, and the stream includes an encrypted compaction item. Standalone `POST /responses/compact` is described as stateless and ZDR-friendly; you send the full window and pass the returned window to the next call: [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction)
- OpenAI says the returned window is the canonical next input and advises against pruning it. The item is "opaque and not intended to be human-interpretable": [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction)
- Gateway docs disagree with OpenAI on the standalone output shape. Bifrost describes the output as original user messages plus a final `response.compaction` item with `encrypted_content`; TrueFoundry says only OpenAI supports it. Treat these as secondary: [Bifrost compaction](https://docs.getbifrost.ai/api-reference/compaction/compact-context.md), [TrueFoundry compaction](https://www.truefoundry.com/docs/ai-gateway/compaction.md)
- OpenHands context condenser (blog, dated April 9, 2025 per secondary coverage): summarizes older interactions past a threshold, keeps recent exchanges intact, and claims "up to 2x per-turn API cost reduction" with "equivalent or better performance." These are vendor claims. I did not fetch the blog itself: [OpenHands blog](https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents)
- OpenHands SDK architecture: `LLMSummarizingCondenser` keeps the first N and last M events, sends the middle to a dedicated LLM, and records the result as a Condensation event. The SDK docs say the default condenser is LLM summarization: [OpenHands condenser arch](https://docs.openhands.dev/sdk/arch/condenser), [OpenHands condenser guide](https://docs.openhands.dev/sdk/guides/context-condenser)
- Claude Code auto-compact and Codex's own compaction implementation: not verified. I did not find primary sources for either in this session.
- Amp handoff/threads: not researched in this session.

### Inferences
- Anthropic's documented kept/dropped split (decisions, unresolved bugs, recent files kept; redundant tool output dropped) is a prompt-level policy, not a fixed rule. The "maximize recall, then tighten precision" advice implies the summary prompt is the main quality lever, and it is tunable through the custom-prompt option.
- The OpenAI encrypted item means the summary is not auditable by the application. Any cross-session or debugging value must come from your own logging, not from the compacted window.
- Vendor-published compaction results (OpenHands "2x") are the only numeric claims for production summarizers I found. They are not independently replicated in what I read.

### Gaps
- Claude Code auto-compact: exact trigger threshold, prompt, and what is preserved. No primary source found.
- Codex compaction prompt and behavior: no primary source found in this session. OpenAI's Codex repo was not checked.
- Context editing doc (tool-result clearing rules and thresholds): not fetched.
- OpenHands blog's underlying benchmark setup for the "2x" claim: not fetched.
- Amp handoff: not researched.

## 2. Research on context and compaction for agents (2025-2026)

### Takeaway
The one controlled comparison I could fully verify (JetBrains Research and TUM, "The Complexity Trap," arXiv 2508.21433) finds simple observation masking matches LLM summarization on solve rate and is cheaper, within a SWE-agent setup on SWE-bench Verified. Chroma's "context rot" report shows degradation with input length in simple tasks, but it is an input-length study, not an agent-compaction study. ACE (arXiv 2510.04618) reports gains from evolving-playbook contexts, but I could not verify its baselines or per-model numbers from the abstract.

### Cited Findings

Complexity Trap (arXiv 2508.21433; full HTML v3 read):
- Setup: SWE-agent, SWE-bench Verified, 500 instances in main experiments, turn limit 250. Five configurations: Qwen3-32B, Qwen3-32B (thinking), Qwen3-Coder 480B-A35B-Instruct-FP8, Gemini 2.5 Flash, Gemini 2.5 Flash (thinking): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Observation masking: rolling window M = 10. LLM summary: summarizes N = 21 turns at a time, keeps last M = 10 unaltered, first summary after N + M = 31 turns. Hybrid: N = 43 with masking window W = 10: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Main results (solve rate %, cost per instance $):
  - Qwen3-32B: raw 17.0 / 1.12; masking 15.0 (-11.8%) / 0.55 (-50.9%); LLM-summary 16.0 (-5.9%) / 0.50 (-55.4%): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
  - Qwen3-32B (thinking): raw 23.0 / 0.51; masking 24.6 (+7.0%) / 0.46 (-9.8%); summary 24.8 (+7.3%) / 0.51 (0.0%): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
  - Qwen3-Coder 480B: raw 53.4 / 1.29; masking 54.8 (+2.6%) / 0.61 (-52.7%); summary 53.8 (+0.7%) / 0.64 (-50.4%): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
  - Gemini 2.5 Flash: raw 32.8 / 0.41; masking 35.6 (+8.5%) / 0.18 (-56.1%); summary 36.0 (+9.8%) / 0.24 (-41.5%): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
  - Gemini 2.5 Flash (thinking): raw 40.4 / 0.56; masking 36.4 (-9.9%) / 0.24 (-57.1%); summary 31.4 (-22.3%) / 0.25 (-55.4%): [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Hybrid (Qwen3-Coder 480B, Verified-50 subset): +2.6 points solve rate vs raw; 7% cheaper than masking and 11% cheaper than summary. Absolute hybrid numbers are not in the text I read: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Observation tokens are about 84% of an average SWE-agent turn: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Summary-generation API cost as a share of total instance cost: Qwen3-Coder 7.20%, Gemini 2.5 Flash 6.71%, Qwen3-32B 2.86%: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Trajectory length: LLM-summary trajectories on Qwen3-Coder are about 15% longer than raw and about 13% longer than masking: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Authors note Alibaba's Qwen3-32B pricing does not distinguish cache hits from misses, which inflates its reported cost: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Conflict note: an earlier search snippet described masking as moving Qwen3-Coder from 53.8% to 54.8%. The table shows 53.4 raw, 54.8 masking, 53.8 summary. The snippet mislabeled the baseline. I use the table: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Scope limit from the abstract: SWE-agent is the main scaffold; OpenHands is "initial evidence" only: [Complexity Trap](https://arxiv.org/abs/2508.21433)

Chroma "Context Rot" (Hong, Troynikov, Huber; technical report; date from secondary coverage as July 14, 2025):
- 18 LLMs: 5 Anthropic, 7 OpenAI, 3 Google, 3 Alibaba. Tasks: extended Needle-in-a-Haystack with semantic distractors, LongMemEval (focused ~300-token prompts vs full ~113k-token prompts; 306 prompts), and Repeated Words: [Chroma](https://www.trychroma.com/research/context-rot)
- "Performance declined as input length grew in every experiment." Lower needle-question similarity degraded faster: [Chroma](https://www.trychroma.com/research/context-rot)
- Structured haystacks hurt: "Structural coherence consistently hurts model performance." Shuffled haystacks scored better across all 18 models: [Chroma](https://www.trychroma.com/research/context-rot)
- Needle placement: 11 positions tested; no notable variation by position in the NIAH task. This contradicts the popular "lost in the middle" framing that secondary blogs repeat: [Chroma](https://www.trychroma.com/research/context-rot)
- Distractors: one high-similarity distractor lowered performance vs. baseline; four lowered it further: [Chroma](https://www.trychroma.com/research/context-rot)
- Repeated Words: 12 word counts (25 to 10,000), accuracy fell with length across all models tested: [Chroma](https://www.trychroma.com/research/context-rot)
- Caveats stated by the report: only two haystack topics; distractor experiment used one needle; LLM judge (GPT-4.1) calibrated to over 99% agreement on roughly 500 NIAH and 600 LongMemEval outputs; the authors say they do not explain the mechanism: [Chroma](https://www.trychroma.com/research/context-rot)
- Vendor caution: Chroma sells a vector database. Secondary blogs claim "every single one got worse" and specific accuracy drops; those claims are not in the primary report and should be ignored: [Chroma](https://www.trychroma.com/research/context-rot)

ACE: Agentic Context Engineering (Zhang, Hu et al.; arXiv 2510.04618; later ICLR 2026 per secondary search):
- Problem framing: "brevity bias" (summaries drop domain detail) and "context collapse" (iterative rewriting "erodes details over time"): [ACE abstract](https://arxiv.org/abs/2510.04618)
- Method: generation, reflection, curation loop with incremental updates; a grow-and-refine step manages redundancy. Contexts are "evolving playbooks": [ACE abstract](https://arxiv.org/abs/2510.04618)
- Reported gains: +10.6% on agent benchmarks, +8.6% on finance; "significantly reducing adaptation latency and rollout cost": [ACE abstract](https://arxiv.org/abs/2510.04618)
- AppWorld: ACE matches the top-ranked production agent on overall average and surpasses it on the test-challenge split, using a smaller open-source model. No exact scores in abstract: [ACE abstract](https://arxiv.org/abs/2510.04618)
- Code: github.com/ace-agent/ace (from search results, not fetched).

Memory tool (Anthropic docs, not a research result):
- Client-side file memory under `/memories`; the application executes view/create/edit operations against storage it controls. Docs recommend restricting operations to `/memories` with path traversal protection. No benefit measurement is published there: [Claude memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### Inferences
- Complexity Trap's cost savings are mostly from removing observation tokens (84% of turns). Masking is the cheap default. Summarization's extra cost (7% of spend on summary calls for Qwen3-Coder) buys no solve-rate gain in that setup, and in one config (Gemini thinking) summarization hurt more than masking.
- Chroma's result argues for keeping active context small, but its tasks are input-length probes. Extrapolating to agent compaction thresholds is an inference, not a finding.
- ACE's brevity-bias and collapse framing suggests incremental delta updates beat full rewrite summaries. This is plausible, but I have no per-baseline numbers to confirm the size of the effect.

### Gaps
- MemGPT/Letta (arXiv 2310.08560 and later Letta posts): not researched in this session.
- ReSum and other long-horizon memory papers: not researched.
- ACE baselines (ICL, MIPRO, GEPA, Dynamic Cheatsheet), per-model numbers, and offline vs. online split: not in abstract. Full paper at arxiv.org/html/2510.04618v3 not fetched.
- Tool-output pruning papers: not searched.
- Complexity Trap hybrid absolute numbers: not in the text read.

## 3. Context packing (what repo context is loaded up front)

### Takeaway
Aider's repo map is the only mechanism here I checked against a primary doc. It sends a ranked symbol summary to the LLM, sized by a token budget, and lets the model request files. Anthropic's post recommends a hybrid: pre-load a small instruction file (CLAUDE.md), then explore with glob/grep. No measured benefit for either is published in what I read.

### Cited Findings
- Aider repo map: a concise summary of the git repo listing files and key symbols with signatures; it is sent with each request, and the LLM can ask for specific files to be added: [Aider repo map](https://aider.chat/docs/repomap.html)
- For large repos, Aider ranks the map using "a graph ranking algorithm," where "each source file is a node and edges connect files which have dependencies": [Aider repo map](https://aider.chat/docs/repomap.html)
- Token budget is set by `--map-tokens`, default 1k tokens; Aider can expand the map beyond that when no files are in the chat: [Aider repo map](https://aider.chat/docs/repomap.html)
- Anthropic recommends "pre-load some context (e.g., CLAUDE.md) and explore the rest on demand with glob and grep," plus just-in-time retrieval (keep identifiers, load with tools at runtime): [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic also recommends "minimal tool sets" and "diverse canonical examples" instead of long rule lists: [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Inferences
- The 1k default for Aider's map is small. For a from-scratch agent, budget choice is a tunable and should be ablated, not copied.
- Graph ranking by dependency is an inference from the page's wording; the exact tree-sitter and PageRank details are from Aider's blog, which I did not fetch.

### Gaps
- Tree-sitter tag extraction, PageRank personalization toward chat files, and the blog post on repo-map construction: not verified.
- AGENTS.md standard contents and adoption: not fetched (agents.md not checked).
- Skills and progressive disclosure: Anthropic skills mechanism not researched in this session.
- No measured solve-rate delta for repo maps or instruction files found.

## 4. Persistent repo memory across sessions

### Takeaway
Formats in use are plain Markdown instruction files (CLAUDE.md, AGENTS.md) that are loaded up front, and agent-written notes or files under a memory directory (Anthropic memory tool, NOTES.md). I found primary docs for the Anthropic memory tool and the NOTES.md pattern, but no measured evidence that persistent memory improves task outcomes.

### Cited Findings
- Anthropic describes structured note-taking: "the agent regularly writes notes persisted to memory outside of the context window." Examples: a to-do list in Claude Code and "your custom agent maintaining a NOTES.md file": [Anthropic engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Memory tool: Claude "checks its memory directory before starting a task, and stores what it learns in files under /memories." The application runs each operation against storage it controls: [Claude memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- Memory tool data under zero data retention is not stored after the response returns: [Claude memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### Inferences
- Memory writes are model-authored. Nothing in the docs I read validates their quality or prunes stale facts. That makes a periodic consolidation pass a likely requirement, but I have no published measurement of it.

### Gaps
- Codex memories, Cursor rules/memories, Letta memory blocks: not researched.
- AGENTS.md spec: not fetched.
- Evidence of benefit from CLAUDE.md or memory files: none found.
- Format of memory writes in Claude Code's CLAUDE.md flow: not verified.

## 5. Prompt caching interactions

### Takeaway
I did not verify cache-specific behavior for compaction or context editing. The one relevant fact I found is that Anthropic's compaction docs have a thinking-block constraint, and the Complexity Trap authors note cache-hit pricing distorts costs for one provider.

### Cited Findings
- Compaction docs mention thinking block removal and preservation rules, which affect re-inserted turns: [Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- Complexity Trap: Alibaba API pricing for Qwen3-32B does not separate cache hits from misses, inflating reported cost: [Complexity Trap](https://arxiv.org/html/2508.21433v3)

### Inferences
- Any rewrite of earlier messages (summary replacement, masking that edits old tool results) changes the prefix and invalidates cache from the edit point forward. Append-only designs preserve it. This is generic prompt-caching behavior, not verified against the compaction docs. It needs a test.

### Gaps
- Exact cache-breaking behavior of Anthropic compaction and context editing: not verified. The cache section of the compaction docs was not extracted.
- OpenAI compaction effect on prompt cache: not verified.
- Cost numbers for cache-aware compaction: none found.

## 6. Measuring context management

### Takeaway
The published setups I could verify use SWE-agent or OpenHands on SWE-bench Verified with solve rate and $ cost per instance as metrics, and use fixed windows (masking M, summary N/M). Chroma's setup isolates input length with needle and LongMemEval tasks. Ablations should vary the trigger and window while holding model and scaffold fixed.

### Cited Findings
- Metrics in Complexity Trap: solve rate (%), cost per instance ($), trajectory length (turns), summary-call cost share, observation token share: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Setup details to copy: 500 instances, turn limit 250, five model configs including thinking variants, masking window M = 10, summary N = 21 with M = 10 kept verbatim: [Complexity Trap](https://arxiv.org/html/2508.21433v3)
- Chroma judge calibration: GPT-4.1 judge with over 99% agreement with human labels on sampled outputs: [Chroma](https://www.trychroma.com/research/context-rot)
- Chroma's Repeated Words task: 1,090 variations per word combination, 7 combinations, 12 word counts: [Chroma](https://www.trychroma.com/research/context-rot)

### Inferences
- Single-run solve-rate deltas on 500 instances are noisy. Complexity Trap reports standard deviations and CIs in Table 1 and Appendix C, which I did not read. Several deltas in its table (for example, +2.6%, +0.7%) are within plausible noise, so claims of "matching" should be read with the CIs.
- A from-scratch measurement layer should log per-turn token counts split by observation vs. reasoning vs. user, so masking-style and summary-style choices can be compared on the same traces.

### Gaps
- Complexity Trap Table 1 standard deviations and confidence intervals: not read.
- ACE evaluation protocol: not read.
- No ablation of CLAUDE.md or repo-map on solve rate found.

## Open problems (from sources above)
- Summarization cost vs. benefit: Complexity Trap finds summaries add 7% or more to spend without a solve-rate gain in most configs. Whether this holds on other scaffolds (OpenHands) is only "initial evidence."
- Context collapse under iterative rewriting (ACE): quantified only as a qualitative claim.
- Summary quality tuning: Anthropic says start with recall, then precision. No published precision/recall numbers for summaries.
- Opaque server-side summaries (OpenAI encrypted item) limit auditability.
- Input-length degradation without a known mechanism (Chroma).
- Memory write quality and staleness: no measured evidence.

## Consolidated gaps (not verified in this session)
- Claude Code auto-compact internals, Codex compaction, Amp handoff.
- Anthropic context editing doc (`platform.claude.com/docs/en/build-with-claude/context-editing`).
- OpenHands blog benchmark details behind the "2x" claim.
- MemGPT/Letta, ReSum, tool-output pruning papers.
- AGENTS.md spec, Cursor rules, Codex memories.
- Cache-breaking behavior of compaction and context editing.
- ACE full-paper baselines and per-model numbers.
- Complexity Trap CIs and the hybrid's absolute numbers.

## Sources
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://platform.claude.com/docs/en/build-with-claude/compaction
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- https://developers.openai.com/api/docs/guides/compaction
- https://arxiv.org/abs/2508.21433 and https://arxiv.org/html/2508.21433v3
- https://www.trychroma.com/research/context-rot
- https://arxiv.org/abs/2510.04618
- https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents
- https://docs.openhands.dev/sdk/arch/condenser
- https://docs.openhands.dev/sdk/guides/context-condenser
- https://aider.chat/docs/repomap.html
- https://docs.getbifrost.ai/api-reference/compaction/compact-context.md (secondary)
- https://www.truefoundry.com/docs/ai-gateway/compaction.md (secondary)
