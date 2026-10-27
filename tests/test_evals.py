import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cookcode.cli.app import make_provider
from cookcode.core.events import (
    Event,
    EventBody,
    ItemEvent,
    ModelRequestFinished,
    ModelRequestStarted,
    ProviderRetry,
    RunConfig,
    RunStarted,
    ToolCallFinished,
)
from cookcode.core.items import ToolCall, ToolResult
from cookcode.core.usage import Usage
from cookcode.evals import runner
from cookcode.evals.cli import eval_command, stats_command
from cookcode.evals.runner import AttemptResult, run_suite
from cookcode.evals.stats import Price, cost, repeat_streaks, session_stats
from cookcode.evals.suite import chains, load_suite, suite_sha256
from cookcode.evals.variance import Trial, harbor_trials, summarize
from cookcode.providers.base import Provider
from cookcode.providers.cassette import Cassette, RecordingTransport, ReplayTransport
from openai_helpers import Script, ok, provider, text_stream, tool_stream

T0 = datetime(2026, 10, 26, tzinfo=UTC)


def event(seq: int, body: EventBody, seconds: float = 0.0) -> Event:
    return Event(seq=seq, id=f"e{seq}", ts=T0 + timedelta(seconds=seconds), layer=type(body).LAYER,
                 body=body)


# --- stats ------------------------------------------------------------------------------


def test_repeat_streaks_count_runs_of_three_identical_call_and_result() -> None:
    a, b = ("call-a", "out"), ("call-b", "out")
    assert repeat_streaks([a, a, a]) == 1
    assert repeat_streaks([a, a, b, a, a]) == 0
    assert repeat_streaks([a, a, a, a, b, b, b]) == 2
    assert repeat_streaks([("call-a", "1"), ("call-a", "2"), ("call-a", "3")]) == 0


def test_cost_charges_fresh_input_cache_reads_and_output_separately() -> None:
    usage = Usage(input=1_000_000, cache_read=2_000_000, output=500_000)
    assert cost(usage, Price(input=1.0, cache_read=0.1, output=4.0)) == pytest.approx(3.2)


def _session() -> list[Event]:
    config = RunConfig(provider="nim", model="m", harness_version="0", tools=("read",))
    call = ToolCall(id="c1", provider_call_id="p1", name="read", arguments={"path": "a"})
    usage = Usage(input=40, cache_read=60, output=10, reasoning=4)
    bodies: list[tuple[EventBody, float]] = [
        (RunStarted(config=config, config_id="x"), 0),
        (ModelRequestStarted(request_id="r1", view="raw", token_estimate=1), 1),
        (ProviderRetry(request_id="r1", attempt=1, delay_s=1, reason="429", status=429), 2),
        (ItemEvent(item=call, request_id="r1"), 4),
        (ModelRequestFinished(request_id="r1", stop_reason="tool_use", usage=usage), 4),
        (
            ToolCallFinished(
                call_id="c1", exit_status="ok", duration_s=0.5, output_bytes=3, truncated=False
            ),
            5,
        ),
        (ItemEvent(item=ToolResult(call_id="c1", status="error", content="no")), 5),
    ]
    return [event(i, body, ts) for i, (body, ts) in enumerate(bodies)]


def test_session_stats_reads_tokens_time_and_errors_from_the_log() -> None:
    stats = session_stats(_session(), {"m": Price(input=1_000_000)})
    assert (stats.requests, stats.retries, stats.tool_calls, stats.tool_errors) == (1, 1, 1, 1)
    assert stats.usage.total_input == 100 and stats.cache_read_share == pytest.approx(0.6)
    assert (stats.model_s, stats.tool_s, stats.wall_s) == (3.0, 0.5, 5.0)
    assert stats.cost_usd == pytest.approx(40.0)
    assert (stats + stats).requests == 2


# --- variance ---------------------------------------------------------------------------


def trials(rewards: dict[str, list[float]]) -> list[Trial]:
    return [
        Trial(task, attempt, reward)
        for task, values in rewards.items()
        for attempt, reward in enumerate(values, 1)
    ]


def test_summary_of_a_deterministic_suite_has_no_spread() -> None:
    summary = summarize(trials({"a": [1, 1, 1], "b": [0, 0, 0]}))
    assert summary.mean == 0.5 and summary.round_scores == (0.5, 0.5, 0.5)
    assert summary.within_sd == 0 and summary.mde_paired == 0


def test_summary_rounds_ci_and_paired_mde() -> None:
    summary = summarize(trials({"a": [1, 0, 1, 0], "b": [1, 1, 1, 1]}))
    assert summary.mean == 0.75
    assert summary.round_scores == (1.0, 0.5, 1.0, 0.5)
    assert (summary.round_min, summary.round_max) == (0.5, 1.0)
    within = (1 / 3 + 0) / 2
    assert summary.mde_paired == pytest.approx(2.801585 * math.sqrt(2 * within / 8), rel=1e-5)
    assert 0.25 <= summary.ci95[0] < 0.75 < summary.ci95[1] <= 1.0
    assert summary == summarize(trials({"a": [1, 0, 1, 0], "b": [1, 1, 1, 1]}))


def harbor_trial(
    job: Path,
    name: str,
    task: str,
    started: str,
    reward: float | None,
    *,
    agent: str = "harness",
    exception: tuple[str, str] | None = None,
    session_error: str | None = None,
) -> None:
    (job / name).mkdir(parents=True)
    result: dict[str, Any] = {
        "task_name": task,
        "started_at": started,
        "agent_info": {"name": agent},
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None
        if exception is None
        else {"exception_type": exception[0], "exception_message": exception[1]},
    }
    (job / name / "result.json").write_text(json.dumps(result))
    (job / "result.json").write_text("{}")
    if session_error is not None:
        sessions = job / name / "agent" / "sessions"
        sessions.mkdir(parents=True)
        body = {"type": "error", "kind": "provider", "retryable": False, "message": session_error}
        (sessions / "s.jsonl").write_text(json.dumps({"body": body}) + "\n")


def test_harbor_trials_number_attempts_by_start_and_score_agent_exceptions_zero(
    tmp_path: Path,
) -> None:
    harbor_trial(tmp_path, "x__2", "x", "2026-10-26T10:05", 0.0)
    harbor_trial(tmp_path, "x__1", "x", "2026-10-26T10:00", 1.0)
    harbor_trial(tmp_path, "y__1", "y", "2026-10-26T10:01", None,
                 exception=("AgentTimeoutError", "timed out after 900.0 seconds"))
    [run] = harbor_trials(tmp_path)
    assert run.agent == "harness" and run.excluded == {}
    assert run.trials == [Trial("x", 1, 1.0), Trial("x", 2, 0.0), Trial("y", 1, 0.0)]


def test_infrastructure_failures_are_excluded_and_reruns_merge_by_agent(tmp_path: Path) -> None:
    first, rerun = tmp_path / "job1", tmp_path / "job2"
    harbor_trial(first, "x__a", "x", "2026-10-26T10:00", 1.0,
                 session_error="HTTP 429: busy after 10 attempts")
    harbor_trial(first, "x__b", "x", "2026-10-26T10:01", 0.0,
                 session_error="ConnectError: [Errno -3] Temporary failure in name resolution")
    harbor_trial(first, "t__a", "x", "2026-10-26T10:00", None, agent="terminus-2",
                 exception=("InternalServerError", "OpenAIException - Connection error."))
    harbor_trial(first, "t__b", "x", "2026-10-26T10:02", 1.0, agent="terminus-2")
    harbor_trial(rerun, "x__c", "x", "2026-10-26T12:00", 1.0)
    harness, terminus = harbor_trials(first, rerun)
    assert harness.agent == "harness" and harness.trials == [Trial("x", 1, 1.0)]
    assert sorted(harness.excluded) == ["x__a", "x__b"]
    assert "name resolution" in harness.excluded["x__b"]
    assert terminus.trials == [Trial("x", 1, 1.0)] and list(terminus.excluded) == ["t__a"]


# --- sequenced replay -------------------------------------------------------------------


async def test_sequenced_replay_serves_a_drifted_request_and_notes_where() -> None:
    from cookcode.core.cancel import CancelToken
    from cookcode.core.items import text_message
    from cookcode.providers.base import ModelRequest, StreamEnd

    cassette = Cassette()
    recorder = RecordingTransport(cassette, Script([ok(text_stream("x"))]).transport)
    request = ModelRequest(model="m", system="s", items=(text_message("user", "first"),))
    _ = [e async for e in provider(Script([]), transport=recorder).stream(request, CancelToken())]
    replay = ReplayTransport(cassette, strict=False)
    drifted = request.model_copy(update={"items": (text_message("user", "second"),)})
    events = [
        e async for e in provider(Script([]), transport=replay).stream(drifted, CancelToken())
    ]
    end = events[-1]
    assert isinstance(end, StreamEnd) and end.stop_reason == "end_turn"
    assert replay.drift == [
        "request #1 body differs at $.messages[1].content: expected 'first', got 'second'"
    ]


# --- suite runner -----------------------------------------------------------------------

CHECK = """\
import pathlib, sys
text = pathlib.Path(sys.argv[1], "greet.py").read_text()
if "moon" not in text:
    print("NAME is not moon")
    sys.exit(1)
"""


def make_suite(root: Path, prompt: str = "Set NAME in greet.py to moon.") -> Path:
    task = root / "suite" / "feature-moon"
    (task / "repo").mkdir(parents=True)
    (task / "repo" / "greet.py").write_text('NAME = "world"\n')
    (task / "task.toml").write_text(
        f'kind = "feature"\nprompt = "{prompt}"\nmax_turns = 5\nmax_wall_s = 60\n'
    )
    (task / "check.py").write_text(CHECK)
    (task / "solution.sh").write_text("sed -i s/world/moon/ greet.py\n")
    return root / "suite"


@pytest.fixture(autouse=True)
def work_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "WORK_ROOT", tmp_path / "work")


MOON = 'NAME = "moon"\n'


def scripted(cassette: Cassette) -> Any:
    script = Script(
        [
            ok(tool_stream("write", json.dumps({"path": "greet.py", "content": MOON}))),
            ok(text_stream("Set NAME to moon.")),
        ]
    )

    def factory(preset: str, **kwargs: Any) -> Provider:
        kwargs.pop("transport", None)
        return make_provider(
            preset,
            **{**kwargs, "api_key": "test"},
            transport=RecordingTransport(cassette, script.transport),
        )

    return factory


def test_load_suite_validates_and_filters(tmp_path: Path) -> None:
    suite = make_suite(tmp_path)
    tasks = load_suite(suite)
    assert [task.id for task in tasks] == ["feature-moon"]
    assert tasks[0].spec.max_turns == 5
    with pytest.raises(ValueError, match="unknown task"):
        load_suite(suite, ["nope"])
    before = suite_sha256(tasks)
    (suite / "feature-moon" / "cassette.json").write_text("{}")
    assert suite_sha256(tasks) == before


def add_follower(suite: Path) -> None:
    task = suite / "memory-moon-b"
    task.mkdir()
    (task / "task.toml").write_text(
        'kind = "memory"\nprompt = "Again."\nmax_turns = 5\nmax_wall_s = 60\n'
        'after = "feature-moon"\n'
    )
    (task / "check.py").write_text(CHECK)
    (task / "solution.sh").write_text("test ! -e marker && sed -i s/world/moon/ greet.py\n")
    (suite / "feature-moon" / "solution.sh").write_text(
        "touch marker && sed -i s/world/moon/ greet.py\n"
    )


async def test_a_follower_runs_after_its_task_on_a_fresh_copy_of_its_fixture(
    tmp_path: Path,
) -> None:
    suite = make_suite(tmp_path)
    add_follower(suite)
    tasks = load_suite(suite)
    first, follower = tasks
    assert follower.repo == first.repo
    assert [[t.id for t in chain] for chain in chains(tasks)] == [
        ["feature-moon", "memory-moon-b"]
    ]
    assert [[t.id for t in chain] for chain in chains([follower])] == [["memory-moon-b"]]
    results = await run_suite(tasks, tmp_path / "run", mode="oracle", preset="nim", model="m",
                              attempts=2, concurrency=4)
    assert all(r.passed for r in results)
    order = [(r.attempt, r.task) for r in results]
    for attempt in (1, 2):
        assert order.index((attempt, "feature-moon")) < order.index((attempt, "memory-moon-b"))


async def test_oracle_mode_runs_the_solution_and_the_check(tmp_path: Path) -> None:
    tasks = load_suite(make_suite(tmp_path))
    results = await run_suite(tasks, tmp_path / "run", mode="oracle", preset="nim", model="m")
    assert [(r.passed, r.status) for r in results] == [(True, "oracle")]
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    assert manifest["suite_sha256"] == suite_sha256(tasks) and manifest["mode"] == "oracle"


def test_prepared_fixtures_are_byte_and_time_identical(tmp_path: Path) -> None:
    import subprocess

    [task] = load_suite(make_suite(tmp_path))

    def snapshot() -> tuple[str, list[tuple[str, float]]]:
        work = runner.prepare(task, 1)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True,
                              text=True, check=True).stdout
        return head, sorted((str(p.relative_to(work)), p.lstat().st_mtime)
                            for p in work.rglob("*"))

    assert snapshot() == snapshot()


def test_drift_in_a_long_string_shows_where_it_starts() -> None:
    from cookcode.providers.cassette import first_difference

    before, after = "x" * 300 + "abc", "x" * 300 + "abd"
    assert first_difference(before, after) == (
        f"$ at char 302: expected ...{'x' * 38 + 'abc'!r}, got ...{'x' * 38 + 'abd'!r}"
    )


async def test_a_relative_suite_path_runs_from_any_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_suite(tmp_path)
    monkeypatch.chdir(tmp_path)
    results = await run_suite(load_suite(Path("suite")), tmp_path / "run", mode="oracle",
                              preset="nim", model="m")
    assert [r.passed for r in results] == [True]


async def test_an_unsolved_task_fails_its_check(tmp_path: Path) -> None:
    suite = make_suite(tmp_path)
    (suite / "feature-moon" / "solution.sh").write_text("true\n")
    results = await run_suite(load_suite(suite), tmp_path / "run", mode="oracle", preset="nim",
                              model="m")
    assert [(r.passed, r.reason) for r in results] == [(False, "NAME is not moon")]


async def test_a_live_run_replays_offline_without_drift(tmp_path: Path) -> None:
    suite = make_suite(tmp_path)
    tasks = load_suite(suite)
    cassette = Cassette()
    live = await run_suite(tasks, tmp_path / "live", mode="live", preset="nim", model="m",
                           provider_factory=scripted(cassette))
    assert [(r.passed, r.status, r.turns) for r in live] == [(True, "done", 2)]
    cassette.save(tasks[0].cassette)

    replayed = await run_suite(tasks, tmp_path / "replay", mode="replay", preset="nim", model="m")
    assert [(r.passed, r.status, r.drift) for r in replayed] == [(True, "done", [])]
    rows = [json.loads(line) for line in (tmp_path / "replay" / "results.jsonl").open()]
    assert AttemptResult.model_validate(rows[0]) == replayed[0]
    assert (tmp_path / "replay" / "answers" / "feature-moon-1.txt").read_text() == (
        "Set NAME to moon."
    )


async def test_replay_after_a_prompt_change_still_runs_and_flags_drift(tmp_path: Path) -> None:
    suite = make_suite(tmp_path)
    tasks = load_suite(suite)
    cassette = Cassette()
    await run_suite(tasks, tmp_path / "live", mode="live", preset="nim", model="m",
                    provider_factory=scripted(cassette))
    cassette.save(tasks[0].cassette)
    changed = load_suite(make_suite(tmp_path / "v2", prompt="Please set NAME to moon."))
    tasks[0].cassette.replace(changed[0].cassette)
    [result] = await run_suite(changed, tmp_path / "replay", mode="replay", preset="nim",
                               model="m")
    assert result.passed
    assert result.drift and all("$.messages[1].content" in d for d in result.drift)


async def test_record_and_replay_run_a_single_attempt(tmp_path: Path) -> None:
    tasks = load_suite(make_suite(tmp_path))
    with pytest.raises(ValueError, match="one attempt"):
        await run_suite(tasks, tmp_path / "r", mode="replay", preset="nim", model="m", attempts=2)
    with pytest.raises(ValueError, match="no cassette"):
        await run_suite(tasks, tmp_path / "r", mode="replay", preset="nim", model="m")


# --- commands ---------------------------------------------------------------------------


def test_eval_oracle_and_report_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = make_suite(tmp_path)
    out = tmp_path / "run"
    assert eval_command(["run", "--suite", str(suite), "--mode", "oracle", "-o", str(out)]) == 0
    assert "1/1 passed" in capsys.readouterr().out
    assert eval_command(["report", str(out)]) == 0
    report = capsys.readouterr().out
    assert "1 tasks x 1 attempts" in report and "score        1.000" in report
    assert eval_command(["report", str(tmp_path)]) == 1


async def test_stats_command_totals_every_session_under_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks = load_suite(make_suite(tmp_path))
    await run_suite(tasks, tmp_path / "live", mode="live", preset="nim", model="m",
                    provider_factory=scripted(Cassette()))
    assert stats_command([str(tmp_path / "live")]) == 0
    out = capsys.readouterr().out
    assert "sessions        1" in out and "requests        2 (0 retries)" in out
    assert "tool calls      1 (0% errors)" in out
    assert stats_command([str(tmp_path / "missing-session")]) == 1


async def test_a_verify_first_run_is_judged_against_fixture_and_solution(tmp_path: Path) -> None:
    from cookcode.core.features import Features
    from cookcode.evals.repro import judge
    from cookcode.evals.repro import render as render_repro

    suite = make_suite(tmp_path)
    tasks = load_suite(suite)
    script = Script(
        [
            ok(tool_stream("write", json.dumps({"path": "check_moon.sh",
                                                "content": "grep -q moon greet.py\n"}))),
            ok(tool_stream("repro", json.dumps({"command": "sh check_moon.sh"}))),
            ok(tool_stream("write", json.dumps({"path": "greet.py", "content": MOON}))),
            ok(text_stream("Fixed.")),
        ]
    )

    def factory(preset: str, **kwargs: Any) -> Provider:
        kwargs.pop("transport", None)
        return make_provider(preset, **{**kwargs, "api_key": "test"}, transport=script.transport)

    [result] = await run_suite(tasks, tmp_path / "run", mode="live", preset="nim", model="m",
                               features=Features(verify_first=True), provider_factory=factory)
    assert (result.passed, result.turns) == (True, 4)
    verdict = judge(tasks[0], result, tmp_path / "run" / "sessions")
    assert (verdict.command, verdict.fails_before, verdict.passes_after) == (
        "sh check_moon.sh", True, True
    )
    assert render_repro([verdict]).endswith(
        "1/1 attempts registered a reproduction; 1 failed for the right reason (100%); 1/1 solved"
    )
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    assert manifest["features"] == "verify_first"
