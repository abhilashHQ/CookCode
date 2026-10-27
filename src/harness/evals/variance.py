"""How much a score moves between identical runs, and the smallest difference it can resolve."""

import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

Z_ALPHA = 1.959964  # two-sided 5%
Z_POWER = 0.841621  # 80% power
BOOTSTRAP_SAMPLES = 10_000


@dataclass(frozen=True)
class Trial:
    task: str
    attempt: int
    reward: float


@dataclass(frozen=True)
class Summary:
    tasks: int
    attempts: int
    mean: float
    round_scores: tuple[float, ...]
    ci95: tuple[float, float]
    within_sd: float
    mde_paired: float
    per_task: dict[str, tuple[float, int]]

    @property
    def round_min(self) -> float:
        return min(self.round_scores)

    @property
    def round_max(self) -> float:
        return max(self.round_scores)


def _by_task(trials: Sequence[Trial]) -> dict[str, list[Trial]]:
    grouped: dict[str, list[Trial]] = defaultdict(list)
    for trial in trials:
        grouped[trial.task].append(trial)
    for runs in grouped.values():
        runs.sort(key=lambda trial: trial.attempt)
    return dict(sorted(grouped.items()))


def summarize(trials: Sequence[Trial], *, seed: int = 0) -> Summary:
    """Score = mean over tasks of each task's mean reward.

    round_scores: the score of attempt round r alone (task i's r-th attempt), which is what a
    single-attempt benchmark run would have reported.
    ci95: bootstrap over tasks, then over attempts within each drawn task.
    mde_paired: the smallest true difference between two configs, run on the same tasks with the
    same attempt count, that a paired test detects with 80% power at 5%. Pairing by task cancels
    task difficulty, so only attempt-to-attempt noise is left; this ignores task-by-config
    interaction, so it is a floor.
    """
    if not trials:
        raise ValueError("no trials")
    grouped = _by_task(trials)
    rewards = {task: [trial.reward for trial in runs] for task, runs in grouped.items()}
    attempts = min(len(values) for values in rewards.values())
    task_means = [statistics.fmean(values) for values in rewards.values()]
    mean = statistics.fmean(task_means)
    rounds = tuple(
        statistics.fmean(values[r] for values in rewards.values()) for r in range(attempts)
    )
    rng = random.Random(seed)
    tasks = list(rewards.values())
    samples: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        drawn = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        samples.append(
            statistics.fmean(
                statistics.fmean(values[rng.randrange(len(values))] for _ in values)
                for values in drawn
            )
        )
    samples.sort()
    low = samples[int(0.025 * BOOTSTRAP_SAMPLES)]
    high = samples[int(0.975 * BOOTSTRAP_SAMPLES) - 1]
    within = [statistics.variance(values) for values in tasks if len(values) > 1]
    within_var = statistics.fmean(within) if within else 0.0
    n, k = len(tasks), max(attempts, 1)
    mde = (Z_ALPHA + Z_POWER) * math.sqrt(2 * within_var / (k * n))
    return Summary(
        tasks=n,
        attempts=attempts,
        mean=mean,
        round_scores=rounds,
        ci95=(low, high),
        within_sd=math.sqrt(within_var),
        mde_paired=mde,
        per_task={
            task: (statistics.fmean(values), len(values)) for task, values in rewards.items()
        },
    )


def render(summary: Summary, label: str) -> str:
    lines = [
        f"{label}: {summary.tasks} tasks x {summary.attempts} attempts",
        f"  score        {summary.mean:.3f}  (95% CI {summary.ci95[0]:.3f} to "
        f"{summary.ci95[1]:.3f})",
        f"  per round    min {summary.round_min:.3f}  max {summary.round_max:.3f}  "
        f"[{', '.join(f'{s:.3f}' for s in summary.round_scores)}]",
        f"  within-task  sd {summary.within_sd:.3f}",
        f"  paired MDE   {summary.mde_paired:.3f} (80% power, 5% two-sided)",
    ]
    lines += [
        f"    {task:<40} {score:.2f} over {count}"
        for task, (score, count) in summary.per_task.items()
    ]
    return "\n".join(lines)


def suite_trials(run_dir: Path) -> list[Trial]:
    trials: list[Trial] = []
    for line in (run_dir / "results.jsonl").read_text().splitlines():
        row = json.loads(line)
        trials.append(Trial(row["task"], row["attempt"], 1.0 if row["passed"] else 0.0))
    return trials


def harbor_trials(job_dir: Path) -> list[Trial]:
    """One Trial per Harbor trial; attempts are numbered per task by start time. A trial that
    raised before the verifier ran scores 0, as Harbor's own mean does."""
    rows: list[tuple[str, str, float]] = []
    for path in job_dir.glob("*/result.json"):
        data = cast(dict[str, Any], json.loads(path.read_text()))
        verifier = cast(dict[str, Any] | None, data.get("verifier_result")) or {}
        rewards = cast(dict[str, Any], verifier.get("rewards") or {})
        reward = rewards.get("reward")
        rows.append((data["task_name"], data["started_at"] or "", float(reward or 0.0)))
    rows.sort()
    counts: dict[str, int] = defaultdict(int)
    trials: list[Trial] = []
    for task, _, reward in rows:
        counts[task] += 1
        trials.append(Trial(task, counts[task], reward))
    return trials
