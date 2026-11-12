"""The rails job's run-job budget is at least twice each leg's slowest green run.

A leg's run-job step stops at its budget, the entry's ``timeout_min`` minutes less the margin
(``tools/ci/jobs.py``), and the job's ``timeout-minutes`` is that same cap, so the budget is also
the most the job can give its tests. A slow hosted runner runs every rail file 1.1 to 1.8 times
as long as a fast one, so a budget under twice a leg's slowest green run cuts a green head off:
leg 1's slowest green run took 536 s against a 540 s budget, and two of its runs were cut at 541
and 542 s.

The slowest green runs are measured, not derived. :data:`SLOWEST_GREEN_S` holds each leg's slowest
run-job step on a green merge.yml run and the run it came from; a leg that grows is measured again
here, with the cap beside it.
"""

from __future__ import annotations

from ci_catalogue_steps import read

from tools.ci import catalogue, jobs, rails_census

#: Each rails leg's slowest run-job step on a green merge.yml run, in seconds, and that run: the
#: slowest of merge.yml's last 40 runs on 2026-11-11, 24 to 26 green runs a leg.
SLOWEST_GREEN_S: dict[int, tuple[int, int]] = {
    1: (536, 42960874004),
    2: (432, 42834353666),
    3: (422, 42951274039),
}


def _rails_job() -> catalogue.Job:
    return read().job("rails")


def test_each_leg_has_its_slowest_green_run_measured() -> None:
    legs = list(range(1, _rails_job().shards + 1))
    assert len(legs) == len(rails_census.LEGS), (legs, rails_census.LEGS)
    assert sorted(SLOWEST_GREEN_S) == legs, "measure each leg's slowest green run with its cap"


def test_the_budget_is_twice_each_legs_slowest_green_run() -> None:
    cap = _rails_job().timeout_min
    budget = cap * 60 - jobs.MARGIN_S
    short = {
        f"rails ({leg}/{len(SLOWEST_GREEN_S)})": f"{seconds} s (run {run})"
        for leg, (seconds, run) in SLOWEST_GREEN_S.items()
        if budget < 2 * seconds
    }
    assert not short, (
        f"the rails job's {cap}-minute cap leaves its run-job step {budget} s, under twice the"
        f" slowest green run of {short}"
    )
