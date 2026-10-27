import asyncio
import re
from pathlib import Path

import pytest

from cookcode.core.features import Features
from cookcode.tools import default_tools
from cookcode.tools.jobs import job_tools
from tool_helpers import make_ctx, run_tool

START, CHECK = job_tools()


async def start(tmp_path: Path, command: str, cwd: str = ".", timeout_s: float = 10.0) -> str:
    out = await run_tool(START, make_ctx(tmp_path, cwd=cwd, timeout_s=timeout_s), command=command)
    assert out.status == "ok", out.content
    match = re.search(r"started job (j[0-9a-f]{8})", out.content)
    assert match is not None
    return match.group(1)


def test_features_parse_and_label() -> None:
    assert Features.parse("base") == Features() and Features().label == "base"
    assert Features.parse("jobs").jobs and Features.parse(" jobs, ").label == "jobs"
    with pytest.raises(ValueError, match="unknown feature nope"):
        Features.parse("jobs,nope")


def test_job_tools_come_only_with_the_feature() -> None:
    assert not {"job_start", "job_check"} & {t.name for t in default_tools()}
    assert {"job_start", "job_check"} <= {t.name for t in default_tools(jobs=True)}


async def test_a_job_outlives_the_call_that_started_it_and_reports_its_exit(
    tmp_path: Path,
) -> None:
    job = await start(tmp_path, "sleep 1; echo done; exit 3", timeout_s=0.5)
    running = await run_tool(CHECK, make_ctx(tmp_path), job_id=job)
    assert f"[job {job}: running" in running.content
    finished = await run_tool(CHECK, make_ctx(tmp_path), job_id=job, wait_s=5)
    assert f"[job {job}: exited with code 3" in finished.content
    assert finished.content.endswith("done\n") and finished.status == "ok"


async def test_a_job_runs_in_the_tracked_directory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    job = await start(tmp_path, "pwd -P", cwd="sub")
    out = await run_tool(CHECK, make_ctx(tmp_path), job_id=job, wait_s=5)
    assert out.content.rstrip().endswith(str((tmp_path / "sub").resolve()))


async def test_kill_stops_the_job_and_its_children(tmp_path: Path) -> None:
    marker = tmp_path / "late"
    job = await start(tmp_path, f"(sleep 2; touch {marker}) & sleep 30")
    out = await run_tool(CHECK, make_ctx(tmp_path), job_id=job, kill=True)
    assert "killed (by job_check)" in out.content
    await asyncio.sleep(2.5)
    assert not marker.exists()


async def test_the_lifetime_cap_kills_a_job(tmp_path: Path) -> None:
    out = await run_tool(START, make_ctx(tmp_path), command="sleep 30", max_s=1)
    job = out.content.split()[2].rstrip(".")
    checked = await run_tool(CHECK, make_ctx(tmp_path), job_id=job, wait_s=5)
    assert "killed (lifetime cap of 1s)" in checked.content


async def test_wait_is_capped_below_the_tool_timeout(tmp_path: Path) -> None:
    job = await start(tmp_path, "sleep 30")
    out = await run_tool(CHECK, make_ctx(tmp_path, timeout_s=6), job_id=job, wait_s=600)
    assert "running" in out.content
    await run_tool(CHECK, make_ctx(tmp_path), job_id=job, kill=True)


async def test_long_output_shows_the_tail(tmp_path: Path) -> None:
    job = await start(tmp_path, "seq 1 20000")
    out = await run_tool(CHECK, make_ctx(tmp_path, max_output_bytes=500), job_id=job, wait_s=5)
    assert "[... last 300 bytes ...]" in out.content and out.content.endswith("20000\n")


async def test_unknown_and_malformed_ids_are_errors(tmp_path: Path) -> None:
    missing = await run_tool(CHECK, make_ctx(tmp_path), job_id="j00000000")
    assert (missing.status, missing.content) == ("error", "no job j00000000")
    bad = await run_tool(CHECK, make_ctx(tmp_path), job_id="../etc")
    assert bad.status == "error" and "not a job id" in bad.content


async def test_a_missing_directory_does_not_start_a_job(tmp_path: Path) -> None:
    out = await run_tool(START, make_ctx(tmp_path, cwd="gone"), command="echo hi")
    assert out.status == "error" and "no longer exists" in out.content
