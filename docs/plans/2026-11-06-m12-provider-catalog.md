# M12 Provider Catalog, Local Endpoints and Context Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CookCode runs against local OpenAI-compatible servers (Ollama, llama.cpp, LM Studio, vLLM) with no API key, picks the loaded model on its own, knows the context window the server actually loaded, and scales the summarize trigger to it.

**Architecture:** Presets become data in `cookcode.providers.catalog`, replacing the 3-tuple `PRESETS` dict. A new `cookcode.providers.discovery` module asks the server, once at startup, which models it serves and what context it loaded. It reads only the effective window, never the trained maximum. The CLI resolves the window (flag, then settings, then discovery, then preset default), derives the summarize trigger from it when the user gave none, and records both in `RunConfig`. The transport (`http.py`), the adapters and the context builder do not change.

**Tech Stack:** Same as M11. `httpx` is already a runtime dependency. No new dependencies.

**Spec:** this plan carries its own design: decision calls D1 to D7 below, which Task 5 writes up as decision 0054. Background: the Sutra comparison (`cneuralnetwork/sutra`, Apache-2.0), whose provider catalog seeded the preset list.

**Style of this plan:** same as M2. Architecture calls, exact interfaces, invariants and behavioral tests. No implementation code. Every listed test is required. Strings marked binding are binding.

## Global Constraints

- All commands run inside WSL Ubuntu, from `~/code/flagship-1`, on branch `m12-providers`, cut from the current tip of `feat/plan-mode-tui` (7 commits ahead of `main` and `dev`).
- Python `>=3.12`. Runtime dependencies stay `httpx`, `prompt-toolkit`, `pydantic`, `rich`.
- Boundary rules from M0 to M11 hold. `cookcode.core` stays free of I/O. `discovery.py` is the only new module that does network I/O, and it lives in `cookcode.providers`.
- All serializable types are frozen pydantic models.
- Schema changes are additive only (0020): new optional fields with defaults. `SCHEMA_VERSION` stays 1.
- The measured baseline does not move: with a cloud preset and no new flags, every request body, the strategy label and the summarize trigger are identical to M11.
- No paid models. Tests use `httpx.MockTransport`. No test touches the network.
- No filler comments. No em or en dashes anywhere.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Done means `uv run pytest -q`, `uv run ruff check` and `uv run pyright` all pass.

## Decision calls

**D1. Curated catalog, as data.** Seven presets: the existing `nim`, `openrouter` and `gemini`, plus `ollama`, `llamacpp`, `lmstudio` and `vllm`. Sutra ships 24. The other cloud entries are one line each to add, are untested here, and showcase nothing. No `custom` preset: `--base-url` on any OpenAI-protocol preset already covers it.

**D2. Keys are optional for local endpoints.** A key is needed only when the preset names a key variable and the base URL is not local. Local means the host is `localhost`, `127.0.0.1`, `[::1]` or `0.0.0.0`. So `--provider nim --base-url http://localhost:8000/v1` needs no key either.

**D3. Effective window only, never the trained maximum.** The servers report two numbers and they differ by up to 128x:

| Server | Effective window (read) | Trained maximum (never read) | Source |
|---|---|---|---|
| OpenRouter | `GET {base}/models` `data[].context_length` | n/a | OpenRouter API |
| llama.cpp | `GET {root}/props` `default_generation_settings.n_ctx` | `/v1/models` `meta.n_ctx_train` | llama.cpp server README |
| LM Studio | `GET {root}/api/v0/models` `data[].loaded_context_length` | `max_context_length` | LM Studio REST API |
| vLLM | `GET {base}/models` `data[].max_model_len` | n/a | third-party only, **unverified** |
| Ollama | not exposed | `/api/show` `model_info.<arch>.context_length` | Ollama API docs |

`{root}` is the base URL with a trailing `/v1` removed. Reading the trained maximum is the bug this decision avoids: a llama.cpp server started with `-c 4096` on a 128k model would get a 64k trigger and overflow on every long run. Ollama exposes no effective window, so the user sets it (D4), and the preset note says how.

**D4. Precedence:** `--context-window` flag, then `context_window` in settings, then discovery, then the preset's `context_window`. The source is recorded as one of `flag`, `settings`, `server`, `preset` (binding).

**D5. The window drives the trigger only when the user gave none.** For a summarizing strategy (`summarize`, `hybrid`) whose string has no `t` option, `trigger_tokens = min(48_000, window // 2)`. `model_fields_set` on the parsed `Strategy` tells whether `t` was given. The cap keeps cloud runs on the M11 trigger. Half the window leaves room for the output and for the chars/4 estimate running low on code. The resolved strategy is what `RunConfig.strategy` records, so its label carries the derived `t`.

**D6. Replay does not need the window.** Compaction is logged in `compaction_applied` and the builder is a pure function of the log, so the window only decides *when* a summary is made, never what a request contains. `RunConfig.context_window` and `RunConfig.context_source` are provenance. Resume re-resolves the window every run, the way it re-resolves the provider. The cost (0020 precedent): `config_id` changes for new runs, and ids from before M12 are not comparable.

**D7. Local presets wait longer.** Prefill of a long prompt on a 6 GB GPU with partial offload can take minutes before the first byte. Local presets set `read_timeout_s=600`. Cloud presets keep 180.

**Out of scope, recorded in 0054 "What would change it":** pricing and the `max_cost_usd` budget, a truncation detector comparing reported `prompt_tokens` with the estimate (Ollama's prompt count with KV-cache reuse needs a live check first), DeepSeek's `prompt_cache_hit_tokens`, and a setup wizard.

---

### Task 1: Preset catalog and keyless local endpoints

**Files:**
- Create: `src/cookcode/providers/catalog.py`
- Modify: `src/cookcode/cli/app.py` (`PRESETS`, `make_provider`, `apply_provider`, `--provider` choices, the key check near line 457)
- Modify: `src/cookcode/cli/headless.py` (key check near line 113)
- Modify: `src/cookcode/evals/runner.py:170`, `src/cookcode/evals/cli.py:90-92,243`
- Test: `tests/test_catalog.py` (new), `tests/test_cli.py`, `tests/test_headless.py`

**Interfaces:**
- Produces, in `cookcode.providers.catalog`:
  - `WindowProbe = Literal["none", "openrouter_models", "llamacpp_props", "lmstudio_models", "vllm_models"]`
  - `class Preset` (frozen pydantic): `name: str`, `protocol: Literal["openai", "gemini"]`, `base_url: str`, `key_env: str | None`, `default_model: str | None`, `context_window: int`, `window_probe: WindowProbe`, `local: bool`, `read_timeout_s: float = 180.0`, `note: str | None = None`.
  - `PRESETS: dict[str, Preset]`, in this order: `nim`, `openrouter`, `gemini`, `ollama`, `llamacpp`, `lmstudio`, `vllm`.
  - `def is_local_url(url: str) -> bool`
  - `def needs_key(preset: Preset, base_url: str) -> bool`, true iff `preset.key_env is not None and not is_local_url(base_url)`.
- `cookcode.cli.app.PRESETS` is removed. Callers import from the catalog.

Preset values (binding):

| name | protocol | base_url | key_env | default_model | context_window | window_probe | local | read_timeout_s |
|---|---|---|---|---|---|---|---|---|
| nim | openai | `NIM_BASE_URL` | `NVIDIA_API_KEY` | `minimaxai/minimax-m3.1-flash` | 128000 | none | no | 180 |
| openrouter | openai | `OPENROUTER_BASE_URL` | `OPENROUTER_API_KEY` | `nvidia/nemotron-3.5-super-vl:free` | 128000 | openrouter_models | no | 180 |
| gemini | gemini | `GEMINI_BASE_URL` | `GEMINI_API_KEY` | `gemini-3.5-flash` | 1000000 | none | no | 180 |
| ollama | openai | `http://localhost:11434/v1` | none | none | 4096 | none | yes | 600 |
| llamacpp | openai | `http://localhost:8080/v1` | none | none | 4096 | llamacpp_props | yes | 600 |
| lmstudio | openai | `http://localhost:1234/v1` | none | none | 4096 | lmstudio_models | yes | 600 |
| vllm | openai | `http://localhost:8000/v1` | none | none | 4096 | vllm_models | yes | 600 |

The 4096 local default is deliberately low. A wrong low window only summarizes early. A wrong high one overflows.

Notes (binding text):
- ollama: `Ollama loads 4096 tokens of context unless told otherwise, and cuts longer prompts without an error. Start it with OLLAMA_CONTEXT_LENGTH=32768 ollama serve and pass --context-window 32768.`
- llamacpp: `Start llama-server with --jinja so tool calls work, and -c to set the context size.`
- vllm: `Start vllm serve with --enable-auto-tool-choice and --tool-call-parser so tool calls work.`

- [ ] **Step 1: Write the failing tests.** `tests/test_catalog.py`:
  - `test_presets_are_in_order_and_complete`: the seven names in order. Every local preset has `key_env is None`, `default_model is None`, `read_timeout_s == 600`.
  - `test_existing_presets_keep_their_values`: `nim`, `openrouter`, `gemini` have the same URL, key variable and default model as M11's tuples.
  - `test_is_local_url`: true for `http://localhost:11434/v1`, `http://127.0.0.1:8080`, `http://[::1]:1234/v1`, `http://0.0.0.0:8000/v1`. False for `https://localhost.example.com/v1`, `https://openrouter.ai/api/v1`, `http://10.0.0.5:8080/v1`.
  - `test_needs_key`: `nim` on its URL needs one. `nim` on `http://localhost:8000/v1` does not. `ollama` never does.

  In `tests/test_cli.py`, extend the preset test and add `test_local_provider_starts_without_a_key`: `--provider llamacpp --model m` with no key variable set does not print the missing-key message. The existing `test_missing_api_key_is_reported` stays green. Same pair in `tests/test_headless.py`.
- [ ] **Step 2: Run them and see them fail** (`uv run pytest tests/test_catalog.py tests/test_cli.py tests/test_headless.py -q`).
- [ ] **Step 3: Implement.** `make_provider` takes a `Preset`, picks the adapter from `protocol` and passes `read_timeout_s` into the transport policy. The key check becomes `needs_key(preset, args.base_url) and not os.environ.get(...)`. When no key is needed, the provider gets `api_key=None` and sends no `Authorization` header (existing behavior of `OpenAICompatProvider.headers`). `apply_provider` takes `key_env` and `default_model` from the preset. A local preset with `default_model is None` leaves `args.model` as `None` here. Task 3 fills it. Evals keep working with the cloud presets: `runner.py` and `cli.py` read `preset.base_url`, `preset.key_env`, `preset.default_model`, and `--provider` choices stay the cloud names (`nim`, `openrouter`, `gemini`), since eval runs are cloud-only.
- [ ] **Step 4: Run the full suite, ruff and pyright.**
- [ ] **Step 5: Commit** `Providers: a preset catalog with local endpoints that need no key`.

### Task 2: Discovery of models and the effective window

**Files:**
- Create: `src/cookcode/providers/discovery.py`
- Test: `tests/test_discovery.py` (new)

**Interfaces:**
- Consumes: `Preset`, `WindowProbe` from Task 1.
- Produces, in `cookcode.providers.discovery`:
  - `class Discovery` (frozen pydantic): `models: tuple[str, ...] = ()`, `context_window: int | None = None`, `problem: str | None = None`.
  - `async def discover(preset: Preset, *, base_url: str, api_key: str | None, model: str | None, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 3.0) -> Discovery`

Invariants:
- `discover` never raises. Any HTTP error, timeout, bad JSON or missing field becomes `problem` (a one-line reason) with the fields it could not fill left empty.
- It makes at most two requests: `GET {base}/models` for the model list, and the probe's request when the probe is not served by that same response.
- Only the effective-window field from D3 is read. A test fails if `n_ctx_train`, `max_context_length` or any `context_length` under `model_info` would be used.
- The window is looked up for `model` when given. With `model=None` it is looked up for the single listed model, if exactly one is listed.
- `window_probe == "none"` makes only the models request.
- The `Authorization` header is sent when `api_key` is set, and never otherwise.

- [ ] **Step 1: Write the failing tests** with `httpx.MockTransport` handlers built from the documented shapes:
  - `test_llamacpp_reads_n_ctx_not_n_ctx_train`: `/v1/models` returns one model with `meta.n_ctx_train=131072`. `/props` returns `default_generation_settings.n_ctx=4096`. Result: `models=("m",)`, `context_window=4096`. The request for `/props` hits `http://localhost:8080/props` (the `/v1` is removed).
  - `test_lmstudio_reads_loaded_context_length`: `max_context_length=131072`, `loaded_context_length=8192` gives 8192. A model with no `loaded_context_length` (not loaded) gives `None` and a `problem`.
  - `test_openrouter_reads_context_length_for_the_requested_model`: two models in the list, the window is the requested one's.
  - `test_vllm_reads_max_model_len`.
  - `test_ollama_lists_models_and_reports_no_window`: `models` filled, `context_window is None`, exactly one request made.
  - `test_server_down_is_a_problem_not_an_error`: the transport raises `httpx.ConnectError`. Result has empty `models` and a `problem` naming the URL.
  - `test_http_error_and_bad_json_are_problems`: a 401 and a non-JSON 200, each a `problem`.
  - `test_no_auth_header_without_a_key` and `test_auth_header_with_a_key`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** One `httpx.AsyncClient` per call with the given transport and timeout.
- [ ] **Step 4: Run the full suite, ruff and pyright.**
- [ ] **Step 5: Commit** `Providers: discover served models and the window the server loaded`.

### Task 3: Window resolution, the derived trigger and provenance

**Files:**
- Modify: `src/cookcode/core/events.py` (`RunConfig`)
- Modify: `src/cookcode/core/strategy.py` (one helper)
- Modify: `src/cookcode/config.py` (`Settings.context_window`)
- Modify: `src/cookcode/agent/loop.py` (`AgentConfig`, `_run_config`)
- Modify: `src/cookcode/cli/app.py` (flag, resolution in `build_agent`, startup notice), `src/cookcode/cli/headless.py`
- Test: `tests/test_events.py`, `tests/test_context.py` or `tests/test_compaction.py`, `tests/test_cli.py`, `tests/test_headless.py`

**Interfaces:**
- Consumes: `Preset` (Task 1), `discover`, `Discovery` (Task 2).
- Produces:
  - `RunConfig.context_window: int | None = None`, `RunConfig.context_source: Literal["flag", "settings", "server", "preset"] | None = None`.
  - `AgentConfig.context_window: int | None = None`, `AgentConfig.context_source: ... | None = None`, copied into `RunConfig` by `_run_config`.
  - `Strategy.for_window(self, window: int) -> Strategy`: returns `self` unchanged unless `self.summarizes` and `"trigger_tokens" not in self.model_fields_set`. Otherwise returns a copy with `trigger_tokens = min(48_000, window // 2)` and `trigger_tokens` marked as set.
  - `Settings.context_window: int | None = Field(default=None, ge=1)`, included in `Settings.over`.
  - CLI flag `--context-window N` (int, `>= 1`).
  - `async def resolve_window(preset, *, flag, settings, discovery) -> tuple[int, str]` in `cookcode.cli.app`, applying D4.

Behavior:
- `build_agent` (and the headless path, which shares it) calls `discover` once per agent build, unless a test injects a provider. A cloud preset with `window_probe == "none"` and a model already set skips the call entirely, so cloud startup makes no extra request.
- A local preset with no model: exactly one served model is used. Zero or several end startup with exit code 1 and the binding message `cookcode: pick a model with --model; {url} serves: {ids}`, or `cookcode: no model is loaded at {url}` when the list is empty or the server is down (with the discovery `problem` appended after `: `).
- A discovery `problem` with a model already chosen is not fatal. The window falls back per D4.
- Startup notice, printed once to stderr in both clients when the strategy does not summarize and the window is `<= 32_768` (binding): `cookcode: the context window is {window} tokens and the {label} strategy never summarizes; long runs will overflow. Try --strategy hybrid.`

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_events.py`: the `RunConfig` sample gains both fields. An M11 `RunConfig` dict without them loads with both `None`.
  - Strategy: `test_for_window_derives_trigger_only_when_unset`: `hybrid` at window 16384 gives trigger 8192 and label `hybrid:k6:b8:t8192`. `hybrid:k6:b8:t40000` at 16384 keeps 40000. `hybrid` at 1_000_000 gives 48000 (M11 value). `raw` and `mask_batched` come back unchanged.
  - Resolution: `test_window_precedence` covers all four sources, and `flag` beats a server value.
  - CLI: `test_cloud_run_config_is_unchanged_but_for_provenance`: `--provider nim` with an injected provider records the M11 strategy label and trigger, `context_window=128000`, `context_source="preset"`.
  - CLI: `test_local_model_is_picked_when_one_is_served`, `test_several_models_ask_for_one` (exit 1, binding message), `test_server_down_without_model_fails_clearly`.
  - CLI: `test_small_window_notice` for `raw` at 4096, and no notice for `hybrid` at 4096 or for `raw` at 128000.
  - Loop: `test_summary_fires_at_derived_trigger`: a `FakeProvider` run with `hybrid` and `context_window=4096` logs `compaction_applied` once the estimate passes 2048. The same run at 128000 does not.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Resolution happens in the CLI before `AgentConfig` is built. `AgentConfig.strategy` gets `strategy.for_window(window)`. Discovery gets a transport injection point on the runtime object, so tests serve it from `MockTransport` the way they inject providers today.
- [ ] **Step 4: Run the full suite, ruff and pyright.**
- [ ] **Step 5: Commit** `Size the summarize trigger to the context window the server loaded`.

### Task 4: Context fill against the window in the interactive client

**Files:**
- Modify: `src/cookcode/cli/render.py` (live state near line 194), `src/cookcode/cli/inline.py` (mode line near line 321)
- Test: `tests/test_inline.py`

**Interfaces:**
- Consumes: `AgentConfig.context_window` (Task 3).
- Produces: `LiveState.context_window: int | None = None`.

Behavior: when the window is known, the mode line shows the estimate against the window as `~{used} / {window} tokens`, both in `k` with one decimal under 10k and none above (`~3.2k / 4.1k tokens`, `~41k / 128k tokens`). At 80% of the window or more, that segment uses the existing warning style. With no window, the M11 rendering is unchanged.

- [ ] **Step 1: Write the failing tests** for the format at both magnitudes, the 80% style switch and the unchanged no-window line.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite, ruff and pyright.**
- [ ] **Step 5: Commit** `Inline client: show context use against the model's window`.

### Task 5: Decision record, walkthrough, README and credit

**Files:**
- Create: `docs/decisions/0054-provider-catalog-and-context-windows.md` (template `0000`)
- Create: `docs/walkthroughs/m12.md` (same shape as `m11.md`, with cross-questions)
- Create: `THIRD_PARTY.md`
- Modify: `README.md` (Quickstart, Features "Providers" row, Configuration)

Content requirements:
- 0054 covers D1 to D7. "Options considered" covers: reading the trained maximum (rejected, with the llama.cpp 4096 vs 131072 example), a fixed default per local preset (Sutra's approach: wrong whenever the server was started differently), asking the user every time (rejected: discovery is right for 3 of 4 local servers), and a pre-request overflow check on the chars/4 estimate (rejected: false positives). "What would change it" lists the out-of-scope items and the vLLM field verification.
- The walkthrough's cross-questions include at least: why not `n_ctx_train`; why replay does not need the window; why the trigger is capped at 48k; what happens with Ollama when the user forgets `OLLAMA_CONTEXT_LENGTH`; why discovery never raises.
- `THIRD_PARTY.md` credits Sutra (`https://github.com/cneuralnetwork/sutra`, Apache-2.0) as the source of the preset list, the local-server notes and the keyless-local rule. No Sutra code is copied. The port is a Python design of our own.
- README Quickstart gains a local section: start `llama-server --jinja -c 16384 -m <model.gguf>`, then `uv run cookcode --provider llamacpp`.

- [ ] **Step 1: Write the four documents.**
- [ ] **Step 2: Check them:** no em or en dashes (`grep -nP '[\x{2013}\x{2014}]'` finds nothing), every file path they name exists.
- [ ] **Step 3: Commit** `docs: decision 0054, the M12 walkthrough, README and third-party credit`.

### Task 6: Gate

- [ ] **Step 1:** `uv run pytest -q`, `uv run ruff check`, `uv run pyright` all pass. Record the test count for the README badge.
- [ ] **Step 2:** Baseline check: replay one M11 suite cassette (`cookcode replay`) and confirm every request hash still matches.
- [ ] **Step 3: Live check (not blocking for the merge, required before 0054 says "verified"):** start one local server in WSL and run `uv run cookcode --provider <it> --once "list the files here"`. Record in 0054 which server and version were checked, the window discovery reported and whether a tool call worked. Until then, 0054 marks every probe as "tested against documented shapes only".
- [ ] **Step 4:** Update the badge and push the branch. Merge is the user's call.
