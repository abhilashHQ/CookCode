# Coding Agent Benchmarks and Eval Harness Mechanics (as of 2026-10-19)

Method: WebFetch on primary pages where reachable (tbench.ai, Harbor docs, Scale leaderboard, GitHub repos, arXiv abstracts), WebSearch for secondary snapshots. Dates are the fetch date unless the source states one. Anything marked UNVERIFIED was only seen in search snippets or third-party blogs. The tbench.ai leaderboard and the Harbor docs do not show dates per row, so every leaderboard number here is a snapshot with unknown exact date.

Biggest correction to the brief: the tbench.ai homepage and leaderboard fetched today headline Terminal-Bench 4.0, not 2.0. A June 2026 blog also refers to Terminal-Bench 2.1. Terminal-Bench 2.0 still exists as a dataset (`terminal-bench@2.0`), but it is no longer the newest version. Pin the version explicitly in every run and write it in the results.

## Terminal-Bench 2.0: what it is, task count, Harbor integration, custom agent interface, pinning, CLI

### Takeaway
Terminal-Bench 2.0 is a containerized, test-verified CLI task suite (89 tasks per secondary sources) run through the Harbor framework. A custom agent plugs in as a Python class (`BaseInstalledAgent` preferred, `BaseAgent` for host-side loops) that Harbor selects with `-a module:Class`. Dataset pinning is done with `name@version` syntax, but the Harbor docs I could read do not explain pinning semantics in detail.

### Cited Findings
- Terminal-Bench paper on arXiv: 2601.11868. The ICLR 2026 proceedings title is "Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command Line Interfaces", [ICLR 2026 proceedings](https://proceedings.iclr.cc/paper_files/paper/2026/hash/444a3737adaee10d86ad2ef5f74468e6-Abstract-Conference.html); [arXiv abstract](https://arxiv.org/abs/2601.11868v1).
- Each task is a containerized environment with an instruction, verification tests, and a manually written reference solution, [arXiv 2601.11868 (via search summary)](https://arxiv.org/abs/2601.11868v1). The abstract body was not fetched, so treat the structure as secondhand.
- Task count 89 and per-task Docker containers with automated verification, [Morph Terminal-Bench 2.0 page](https://www.morphllm.com/terminal-bench-2) (UNVERIFIED against primary).
- Headline claim in the paper (secondhand): frontier agents resolve under 65 percent of tasks at launch; the study ran six agents across 16 models, over 32,000 trials, with Terminus 2 as a neutral baseline. UNVERIFIED against paper text. Source: search summary only.
- Harbor: "a framework for running any agent with any model on any task in any sandbox in parallel." Installs via `uv tool install harbor` or `pip install harbor`, [Harbor GitHub](https://github.com/laude-institute/harbor); [Harbor docs](https://docs.harborframework.com/).
- Canonical run command shown on the Harbor GitHub page: `harbor run --dataset terminal-bench@2.0 --agent claude-code --model anthropic/claude-opus-5-5 --n-concurrent 4` with `ANTHROPIC_API_KEY` set. Cloud execution via `--env daytona` plus `DAYTONA_API_KEY`, [Harbor GitHub](https://github.com/laude-institute/harbor). The model string is copied as shown; not verified against Anthropic's model list.
- Terminal-Bench repo README (`harbor-framework/terminal-bench`) uses `terminal-bench/terminal-bench@latest`, not `@2.0`. Example runs: `harbor run -d terminal-bench/terminal-bench@latest -k 5 --agent oracle --n-concurrent 500 --env modal`, and the same with `--agent claude-code --model anthropic/claude-fable-5 --ak reasoning_effort=max --n-concurrent 100 --env modal`. The README says tasks are developed on Modal and the oracle should be run "to confirm all tasks work as expected", [terminal-bench repo](https://github.com/harbor-framework/terminal-bench). The repo does not say whether it holds the 1.0, 2.0, or 4.0 task set.
- Harbor docs, custom agents page: base classes are `BaseInstalledAgent` at `harbor.agents.installed.base` (preferred, Harbor installs and runs the agent CLI inside the task container) and `BaseAgent` at `harbor.agents.base` (for loops that must stay outside the container), [Custom agents](https://docs.harborframework.com/agents/custom-agents.md).
- `BaseInstalledAgent` required methods: `@staticmethod name() -> str`, `async install(self, environment)`, and `@with_prompt_template async run(self, instruction, environment, context)`. Optional override: `populate_context_post_run`, which runs after logs sync, [Custom agents](https://docs.harborframework.com/agents/custom-agents.md).
- `BaseAgent` required methods: `@staticmethod name()`, `version() -> str | None`, `async setup(environment)`, and `async run(instruction, environment, context)`. External agents "act through environment.exec(...)", [Custom agents](https://docs.harborframework.com/agents/custom-agents.md).
- Command execution inside the container: installed agents call `await self.exec_as_agent(environment, command=...)`, and `exec_as_root` for system packages. The docs do not describe tmux, per-command timeouts, the full `exec` signature, or working directory handling, [Custom agents](https://docs.harborframework.com/agents/custom-agents.md).
- Selection: `harbor run -t hello-world/hello-world -a examples.agents.marker_agent:MarkerAgent`. The module must be importable from the Harbor process. Related flags: `-m/--model`, `--ak/--agent-kwarg`, `--ae/--agent-env`. A `harbor agent auth module.path:MyAgent --json` command inspects a declaration, [Custom agents](https://docs.harborframework.com/agents/custom-agents.md). The `--agent-import-path` flag I expected does not appear on the pages I read.
- Full run flags from the run-a-job page: `-p <local path>`, `-d <org/dataset>@<ref>` for Hub datasets, `-d <dataset>@<version> --registry-path <path>` for custom registries, `-a`, `-m`, `-n` (concurrent trials), `-k` (attempts), `-r` (retries), `-e` (sandbox, not env vars), `-o <jobs-dir>`, `--job-name`, `--effort`, `--one-off-secret`, `--print-config`, `--launch` (run on Harbor Hub), `-l/-i/-x` (task filters). The job config is saved to `<job>/config.json`, [Run a job](https://docs.harborframework.com/jobs/run-a-job.md).
- Trajectory format is ATIF, documented at `docs.harborframework.com/agents/atif.md`. Not fetched. Loading trajectories is at `docs.harborframework.com/jobs/loading-trajectories.md`. Not fetched, [Harbor docs index](https://docs.harborframework.com/llms.txt).
- Harbor docs also list pre-integrated agents (`agents/pre-integrated-agents.md`), an ACP path (`agents/acp.md`), and agent auth resolution (`agents/agent-auth-resolution.md`). None were fetched, [Harbor docs index](https://docs.harborframework.com/llms.txt).
- Harbor's `--allow-agent-host` flag appears in the run-a-job flag list, [Run a job](https://docs.harborframework.com/jobs/run-a-job.md). Its exact semantics were not documented in what I read.

### Inferences
- Terminal-Bench 2.0 runs through Harbor. Treat the Harbor CLI as the only supported runner for a leaderboard-style submission. I did not find a standalone Terminal-Bench 2.0 runner.
- A from-scratch Python agent has two integration paths. `BaseAgent` runs the loop on the host and calls `environment.exec` into the task container. This fits a Python bridge on `127.0.0.1:4100` because the loop, not the container, calls the model. `BaseInstalledAgent` installs the agent inside the container, so the bridge would have to be reachable from inside Docker (`--allow-agent-host` looks relevant here). This is an inference from the flags, not a documented recipe.
- Pinning: the only explicit version syntax I saw is `name@version` (for example `terminal-bench@2.0`). The README's `@latest` means results drift when the dataset changes. Record the exact version string and the Harbor version (`harbor --version`, not verified) in every published result. Confirm pinning behavior with `harbor datasets list` and the registry docs before publishing.
- Installed-agent path looks mandatory for anything that needs tmux-style interactive sessions, but tmux is not documented. Confirm before designing around it.

### Gaps
- Terminal-Bench 2.0 vs 4.0 vs 2.1 differences: task sets, versioning, and whether 2.0 results are still accepted on the leaderboard. Not confirmed from a primary source.
- Per-command and per-trial timeouts in Harbor: not in any page I read.
- Where trajectories and job outputs are written on disk: not stated on the run-a-job page.
- Exact `exec` signature, working directory, and tmux behavior in `BaseInstalledAgent`.
- Windows and WSL support for Harbor: not documented on any page I read.
- Harbor's own docs on pinning semantics (`registries.md`, `git-repos.md`, `news/harbor-registry.md`): linked, not fetched.

## Terminal-Bench 2.0 leaderboard and evidence that harness matters on the same model

### Takeaway
The Terminal-Bench 2.0 leaderboard I could reach through search is third-party snapshots. The top of those snapshots sits in the low 80s percent, with GPT-5.5 near the top. The strongest evidence that harness matters comes from third-party blog comparisons on the same model, not from a controlled ablation by the benchmark authors.

### Cited Findings
- Codesota (cites the official leaderboard, date not confirmed): Codex / GPT-5.5 at 82.0, ForgeCode / GPT-5.4 at 81.8, TongAgents / Gemini 3.1 Pro at 80.2. GPT-5.5 submission dated April 2026, [Codesota Terminal-Bench 2](https://www.codesota.com/benchmark/terminal-bench-2) (UNVERIFIED against tbench.ai, which I could not read for 2.0).
- A snapshot dated 2026-09-09 lists GPT-5.5 at 82.7, Claude Mythos Preview at 82.0, and Claude Sonnet 5 at 80.4, [Morph Terminal-Bench 2.0](https://www.morphllm.com/terminal-bench-2) (UNVERIFIED; search summary only, not fetched).
- A March 2026 snapshot had ForgeCode with Gemini 3.1 Pro at 78.4 at #1, [Codesota Terminal-Bench 2](https://www.codesota.com/benchmark/terminal-bench-2) (UNVERIFIED, dated secondhand).
- Snorkel analysis: 142 published submissions, median score 28.2 percent, and the top 10 fall within about 7 points of each other, [Snorkel Terminal-Bench 2.0 blog](https://www.snorkel.ai/blog/terminal-bench-2-0/) (search summary only, not fetched).
- tbench.ai leaderboard table headers: RANK, MODEL, AGENT, RESOLUTION RATE, COST, TOKENS. No rows were populated at fetch time, and the page is for Terminal-Bench 4.0, [tbench.ai leaderboard](https://www.tbench.ai/leaderboard).
- Same model, different harness: Claude Opus 4.6 at 58.0 percent inside Claude Code and 74.7 percent inside Terminus-KIRA, a 16.7-point gap. ForgeCode reports 81.8 percent on Opus 4.6, [Codex harness performance blog, 2026-04-09](https://codex.danielvaughan.com/2026/04/09/harness-performance-terminal-bench/) (secondary blog, UNVERIFIED against leaderboard).
- Codex CLI with GPT-5.5 at 83.4 percent vs GPT-5.5 under Terminus 2 at 76.40 percent. This is on Terminal-Bench 2.1, not 2.0, [Codex blog, 2026-06-11](https://codex.danielvaughan.com/2026/06/11/terminal-bench-2-1-june-2026-benchmark-landscape-codex-cli-harness-engineering-model-scores/) (secondary blog).
- GPT-5.3-Codex across harnesses: Droid (Factory) 77.3, Simple Codex (OpenAI) 75.1, Mux (Coder) 74.6, CodeBrain-1 (Feeling AI) 70.3, search summary only; the exact source page was not identified (UNVERIFIED).
- Anthropic's internal Terminal-Bench 2.0 experiments reportedly found a six-point spread from infrastructure configuration alone. Source not identified; search summary only (UNVERIFIED). Worth finding the primary Anthropic post.
- Adjacent-rank pairs on the leaderboard mostly differ by less than noise (24 of 25 pairs, per one analysis). Source not identified (UNVERIFIED).
- LemonHarness Technical Report (arXiv 2606.24311): a GPT-5.3-CodeX submission with 5 jobs and 445 trials. Job accuracy ranged 80.90 to 89.89 percent, mean 84.49. The authors attribute the spread to model sampling, task-path choices, and interface stability, [arXiv 2606.24311](https://arxiv.org/pdf/2606.24311) (self-reported technical report, not peer reviewed).
- Survey on agent system and harness design (arXiv 2606.20683) exists and may cover harness ablations. Not fetched, [arXiv 2606.20683](https://arxiv.org/pdf/2606.20683).

### Inferences
- Harness is a first-class variable. Public comparisons show spreads of 7 to 17 points on the same or similar model. Those spreads are the same order as the run-to-run variance in the LemonHarness report, so single-run comparisons are weak evidence.
- The leaderboard is not a clean model ranking. Reported rows mix harness, model, budget, and version. Report your own numbers with the full config (model, reasoning effort, harness commit, dataset version, attempts, timeouts).
- Terminus 2 is the neutral baseline the paper uses, per the secondhand summary. Running your custom agent alongside Terminus 2 on the same model and budget is the cleanest ablation to publish.

### Gaps
- No primary tbench.ai 2.0 leaderboard page was reachable. All 2.0 rankings above are secondary.
- No controlled, primary-source harness ablation on Terminal-Bench 2.0 was found.
- No cost or token figures per leaderboard row were populated on the pages I reached.

## SWE-bench Pro (Scale AI), SWE-bench Verified, SWE-bench Live, SWE-rebench, Multi-SWE-bench

### Takeaway
SWE-bench Pro is the benchmark OpenAI now recommends for reporting coding results, after it dropped SWE-bench Verified. Scale publishes a public split and keeps a held-out split off the public board. Scores on the public leaderboard are reported with mini-swe-agent for the top rows, which means the harness is a confound in the table.

### Cited Findings
- Dataset composition from the Scale leaderboard page: public set of 731 instances, held-out set of 858 instances that "will not be published on the public leaderboards," private set of 276 instances from 18 proprietary startup codebases with results on a separate leaderboard, and 1,865 tasks across 41 repositories total. Reproducible Docker environments per problem, [Scale Labs SWE-bench Pro public](https://labs.scale.com/leaderboard/swe_bench_pro_public).
- Public leaderboard rows (score ± CI, harness as marked): Muse Spark 1.1 61.50±3.10 (mini-swe-agent, marked NEW); gpt-5.4 (xHigh) 59.10±3.56 (mini-swe-agent); Muse Spark 55.00±3.60 (mini-swe-agent); claude-opus-4-6 (thinking) 51.90±3.61 (mini-swe-agent); gemini-3.1-pro (thinking) 46.10±3.60 (mini-swe-agent); claude-opus-4-5-20251101 45.89±3.60 (harness not stated). Page has no per-row date, [Scale Labs SWE-bench Pro public](https://labs.scale.com/leaderboard/swe_bench_pro_public).
- Harness notes on the Scale page: mini-swe-agent is the harness for starred rows, earlier charts used the SWE-Agent scaffold, grayed-out rows used a capped cost limit and a 50-turn limit, and other rows used uncapped cost and a 250-turn limit, [Scale Labs SWE-bench Pro public](https://labs.scale.com/leaderboard/swe_bench_pro_public). The page does not say which rows are grayed out in the text I received.
- Rank definition on the Scale page: 1 plus the number of models whose lower CI bound exceeds this model's upper CI bound, [Scale Labs](https://labs.scale.com/leaderboard/swe_bench_pro_public).
- SWE-bench Pro is listed in the Harbor hub as `scale-ai/swe-bench-pro`, [Harbor hub](https://hub.harborframework.com/datasets/scale-ai/swe-bench-pro) (listing seen in search; not fetched).
- Harness repo for running it yourself: `github.com/scaleapi/SWE-bench_Pro-os`, named on the Scale page; not fetched.
- OpenAI stopped reporting SWE-bench Verified and recommends SWE-bench Pro. The post is dated February 2026 in search summaries; one blog dates the decision to September 2025, [OpenAI, "Why we no longer evaluate SWE-bench Verified"](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) (URL seen in search results, not fetched; date conflict unresolved, lean February 2026).
- Secondhand summary of OpenAI's audit: every frontier model tested could reproduce verbatim gold patches or problem statement details for some Verified tasks, and 59.4 percent of the hardest unsolved problems had flawed tests (UNVERIFIED; third-party summary only).
- SWE-bench Live: 1,319 tasks from GitHub issues created since 2024, across 93 repositories; live-updating design, [arXiv 2505.23419](https://arxiv.org/abs/2505.23419) (2025 paper; results predate 2026 models). Live site `swe-bench-live.github.io` named in search, not fetched.
- SWE-rebench (Nebius): built from issue-PR pairs collected after training cutoffs, 860 curated Python tasks per a Harbor dataset listing, [Harbor hub SWE-rebench leaderboard](https://hub.harborframework.com/datasets/swe-rebench/swe-rebench-leaderboard/latest?page=174) (listing). BenchLM tracker dated 2026-07-29 puts Claude Opus 4.6 at 65.3, GLM-5 at 62.8, GLM-5.1 at 62.7, [BenchLM SWE-rebench](https://www.benchlm.ai/benchmarks/sweRebench) (third-party aggregator, UNVERIFIED). Official site is swe-rebench.com, not fetched.
- Multi-SWE-bench: cited as 1,632 instances across 39 repositories with manual curation in the 2025 paper's text, [arXiv 2505.23419 HTML v1](https://arxiv.org/html/2505.23419v1) (UNVERIFIED that this figure belongs to Multi-SWE-bench; the search summary attributed it that way). No 2026 Multi-SWE-bench leaderboard standings found.
- Morph's SWE-Bench Pro page is titled "Why 46% Beats 81%," implying a score conflict that I did not resolve. Likely a different split or harness. Unresolved, [Morph SWE-Bench Pro](https://www.morphllm.com/swe-bench-pro) (not fetched).
- Old search snippet claimed top public scores around 23 percent. This is stale: the fetched Scale page shows 61.50 at the top, so I dropped that figure.

### Inferences
- For a custom agent, SWE-bench Pro public scores are only comparable within the same harness and turn/cost budget. The Scale table shows the budget difference explicitly. Publish your number with the budget (turn limit, cost cap), the harness, and the split (public vs held-out).
- Held-out results are not on the public board, so a from-scratch agent claiming "SWE-bench Pro" numbers should say public split only, unless Scale publishes held-out results for you.
- SWE-bench Verified should not be the headline number. Use it only as a secondary comparison, labeled contaminated.

### Gaps
- Scale's own run instructions for SWE-bench Pro harness (scaleapi/SWE-bench_Pro-os): not read. Required evaluation harness version, Docker image sizes, and how to submit to the public leaderboard are unknown.
- Current official public-board scores as of 2026-10-19: the Scale page has no date, so the snapshot is undated.
- Which rows are grayed out (capped cost, 50 turns): unknown from the text I received.
- SWE-bench Live, SWE-rebench, and Multi-SWE-bench current leaderboards from primary sites were not read.
- OpenAI's audit numbers: not confirmed from the primary post.

## Cost of running these evals (tokens, wall-clock, Docker, Windows/WSL vs Linux)

### Takeaway
I could not find primary-source cost figures for a Terminal-Bench 2.0 or SWE-bench Pro run. Harbor needs Docker for the default local run. The Terminal-Bench team runs their own tasks on Modal. I found no confirmed Windows or WSL support statement for Harbor.

### Cited Findings
- Harbor default local run uses Docker. No version or setup requirements given, [Harbor GitHub](https://github.com/laude-institute/harbor).
- Harbor cloud sandboxes named on the GitHub page include Daytona, Modal, LangSmith, Blaxel, Novita Sandbox, Tensorlake, Runta, and Sail, [Harbor GitHub](https://github.com/laude-institute/harbor).
- The terminal-bench repo says tasks are developed on Modal in CI and leaderboard experiments, [terminal-bench repo](https://github.com/harbor-framework/terminal-bench).
- Each Terminal-Bench 2.0 task runs in its own Docker container, [Morph Terminal-Bench 2.0](https://www.morphllm.com/terminal-bench-2) (UNVERIFIED against primary).
- SWE-bench Pro environments are Docker-based, built by engineers to be reproducible, [Scale Labs](https://labs.scale.com/leaderboard/swe_bench_pro_public).
- tbench.ai leaderboard has COST and TOKENS columns, but no populated rows at fetch time, [tbench.ai leaderboard](https://www.tbench.ai/leaderboard).
- LemonHarness submission used 5 jobs and 445 trials total, [arXiv 2606.24311](https://arxiv.org/pdf/2606.24311). No cost or wall-clock reported in the excerpt I received.

### Inferences
- Trial count math (not sourced): Terminal-Bench 2.0 at 89 tasks with k attempts is 89k trials per configuration. At `-k 5` that is 445 trials, which matches the LemonHarness job size. Budget tokens per trial before running the full sweep.
- Windows: Docker Desktop with the WSL2 backend is the likely path. Harbor's Windows support is undocumented on the pages I read, so run a single-task smoke test first. Native Linux or a cloud sandbox (Modal, Daytona) is the lower-risk path for reproducibility.
- Concurrency flags (`-n`) are the main wall-clock lever. The docs show values from 4 to 500, [Harbor GitHub](https://github.com/laude-institute/harbor), [terminal-bench repo](https://github.com/harbor-framework/terminal-bench).

### Gaps
- Dollar and token cost per full Terminal-Bench 2.0 run: not found in any primary source.
- Wall-clock per trial or per full run: not found.
- Windows and WSL support for Harbor: not confirmed. Searched only the pages listed above.
- Minimum Docker resource requirements per task: not found.

## Trace formats and observability (ATIF, OpenTelemetry GenAI semconv, how papers report ablations)

### Takeaway
Harbor uses ATIF as its trajectory format, but I did not read the ATIF spec. The OpenTelemetry GenAI semantic conventions moved out of the docs site I tried, and I did not read the current attribute list or stability status. Papers in this space report harness ablations mostly as third-party comparisons, not as controlled component ablations.

### Cited Findings
- Harbor documents ATIF as its trajectory format at `docs.harborframework.com/agents/atif.md`. Loading trajectories at `docs.harborframework.com/jobs/loading-trajectories.md`. Artifact collection at `docs.harborframework.com/jobs/artifact-collection.md`. Neither was fetched, [Harbor docs index](https://docs.harborframework.com/llms.txt).
- OpenTelemetry GenAI semantic conventions: the old page at `opentelemetry.io/docs/specs/semconv/gen-ai/` says it "has moved and is no longer maintained in this repository." Current location: `github.com/open-telemetry/semantic-conventions-genai`, [OpenTelemetry GenAI semconv (moved notice)](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
- The Harbor docs index says the agent integration pages cover auth resolution (models, secrets, endpoints, API formats), [Harbor docs index](https://docs.harborframework.com/llms.txt).
- The LemonHarness report and the harness-performance blog compare harnesses as single-run snapshots, not component ablations, [arXiv 2606.24311](https://arxiv.org/pdf/2606.24311); [Codex blog](https://codex.danielvaughan.com/2026/04/09/harness-performance-terminal-bench/).

### Inferences
- Plan for trajectories to be stored in Harbor's job directory and loaded with the Harbor loader. Confirm the ATIF schema before writing the agent, since `populate_context_post_run` is where usage and trajectory data get parsed.
- For your own observability, emit OpenTelemetry GenAI spans from the agent. The attribute names and content-capture opt-in need confirmation from the new repo before I would rely on them. From memory, the conventions use `gen_ai.*` keys for operation, provider, model, and usage. That memory is not verified against the current spec.
- Component ablations in papers usually replace one component (tool set, compaction, prompt) and rerun the same suite. Public Terminal-Bench 2.0 comparisons do not do this. A publishable ablation should fix the model, budget, and dataset version and vary one harness component, with repeats.

### Gaps
- ATIF spec content: not read.
- OTel GenAI current attribute names, stability status, and content-capture policy: not read. The current repo is `github.com/open-telemetry/semantic-conventions-genai`.
- Papers reporting component ablations on Terminal-Bench 2.0: none confirmed from primary text. The arXiv 2606.20683 survey may cover this.

## Pitfalls: contamination, reward hacking in tests, flaky tasks, variance across runs, repeat counts

### Takeaway
Contamination is the most documented pitfall, and OpenAI's move off SWE-bench Verified is the clearest public signal. Variance is large: a single five-job configuration on Terminal-Bench 2.0 spanned roughly nine points, and the leaderboard top-10 spans about seven points. Reporting one run is not defensible. Reward hacking and flaky tests are mentioned in secondary summaries, but I did not find a primary-source audit for Terminal-Bench 2.0.

### Cited Findings
- SWE-bench Verified contamination: OpenAI stopped reporting it and recommends SWE-bench Pro, citing training-time exposure, [OpenAI post](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) (URL seen, not fetched; date conflict noted above).
- Secondhand audit: verbatim gold-patch reproduction for some tasks, and 59.4 percent of hardest unsolved problems with flawed tests, UNVERIFIED, search summary only.
- SWE-bench Pro held-out split is kept off the public board to act as a control for training exposure, [Scale Labs](https://labs.scale.com/leaderboard/swe_bench_pro_public) (control framing from a secondary explainer, UNVERIFIED).
- LemonHarness: five jobs of the same submission scored between 80.90 and 89.89 percent, mean 84.49, over 445 trials, [arXiv 2606.24311](https://arxiv.org/pdf/2606.24311).
- Scale reports "±" confidence intervals on each score (confidence level not stated) (±3.1 to ±3.6 points on the public rows) and defines ranking by non-overlapping CIs, [Scale Labs](https://labs.scale.com/leaderboard/swe_bench_pro_public).
- Adjacent leaderboard ranks differ by noise in most pairs (24 of 25, one analysis, UNVERIFIED source).
- Harbor docs recommend running the oracle agent first to confirm every task works, [terminal-bench repo](https://github.com/harbor-framework/terminal-bench). This is the only flaky-task control I found documented.

### Inferences
- Repeat count: a minimum of 5 attempts per configuration is the lower bound the public submissions use (LemonHarness used 5 jobs). Report the mean with the min and max across jobs, and the attempt count (`-k`).
- Reward hacking: check that the verifier results are not produced by reading test files or writing outputs that bypass the task. The Harbor oracle run plus a manual trace review of a sample of passing trials is the minimum sanity check. Primary-source audit for Terminal-Bench 2.0 still needed.
- Flaky tasks: run the oracle and your agent on the same tasks and drop or flag tasks where the oracle fails or the verifier is nondeterministic. Keep a list and publish it with the results.
- Version drift: 2.0, 2.1, and 4.0 coexist, and `@latest` changes under you. Pin the dataset version string and record it with every number.
- Harness confound: the 16.7-point Claude Code vs Terminus-KIRA gap (secondhand) is larger than the per-job spread. Pin the harness commit and the budget before claiming any model comparison.

### Gaps
- Primary-source reward hacking audit for Terminal-Bench 2.0 or SWE-bench Pro: not found.
- Official Terminal-Bench 2.0 variance or CI numbers from tbench.ai: not found.
- Official SWE-bench Pro contamination analysis beyond the held-out split: not found.
- Terminal-Bench 2.0 flaky-task list: not found.
