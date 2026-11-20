"""A trigger's claim, and the reaper's deadline, age by the time the machine was awake.

🔴 The defect (Backlog: "An automation running while the computer sleeps loses its claim, so a
second run starts"). A running fire holds a claim so the next fire of the same automation cannot
start beside it, and the claim's age was read from the wall clock, which runs on while a laptop
sleeps. A machine that slept more than half an hour through a run woke to the reaper calling the
run overdue (a `timeout` row for a run still going), and one that slept more than an hour to the
claim reading as expired, so the next due fire started a second run beside the first.

Each case here stubs `claims._clocks`, the one place the clocks are read, so "slept two hours,
awake one minute" is exact: sleep moves the wall clock and the clock that counts sleep, and not the
awake clock; a wall-clock step moves only the wall clock; a new boot starts both boot clocks again.
The last two cases read the real clocks: a claim another process wrote is read here as just taken,
and the premise (the clock that counts sleep never runs behind the awake one, and both name one
boot) holds on the platform the suite runs on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personalclaw.triggers import claims, reaper
from personalclaw.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim, claim_fire

#: When the claim is taken: by the wall clock, by the awake clock, and by the clock that counts
#: sleep. The boot began SINCE_BOOT0 before T0.
T0 = 1_800_000_000.0
AWAKE0 = 50_000.0
SINCE_BOOT0 = 60_000.0
TRIGGER = "clock:nightly"
MINUTE = 60.0
HOUR = 3600.0


class _Clocks:
    """`claims._clocks`, stubbed, moved as the machine moves them."""

    def __init__(self) -> None:
        self.wall, self.awake, self.since_boot = T0, AWAKE0, SINCE_BOOT0

    def __call__(self) -> tuple[float, float, float]:
        return self.wall, self.awake, self.since_boot

    def run(self, secs: float) -> None:
        """The machine is awake for *secs*: every clock moves."""
        self.wall += secs
        self.awake += secs
        self.since_boot += secs

    def sleep(self, secs: float) -> None:
        """The machine sleeps for *secs*: the awake clock stops."""
        self.wall += secs
        self.since_boot += secs

    def step(self, secs: float) -> None:
        """The wall clock is set by *secs* (an NTP correction, or by hand): nothing else moves."""
        self.wall += secs

    def reboot(self, *, down: float, up: float, asleep: float = 0.0) -> None:
        """The machine restarts: *down* seconds off, then *up* seconds into its new boot, *asleep*
        of them asleep."""
        self.wall += down + up
        self.since_boot = up
        self.awake = up - asleep


@pytest.fixture
def clocks(monkeypatch: pytest.MonkeyPatch) -> _Clocks:
    stub = _Clocks()
    monkeypatch.setattr(claims, "_clocks", stub)
    return stub


def _take(base: Path) -> None:
    """The tick's grant at T0, persisted as the tick persists it."""
    claims.write_claim(Claim(trigger_id=TRIGGER, holder="tick", claimed_at=T0), base_dir=base)


def _held_for(base: Path, clocks: _Clocks) -> float | None:
    return claims.held_for(TRIGGER, now=clocks.wall, base_dir=base)


def test_a_claim_the_machine_slept_through_is_still_held(tmp_path: Path, clocks: _Clocks) -> None:
    """🔴 Two hours by the wall clock, a minute of it awake: the run has held its claim a minute."""
    _take(tmp_path)
    clocks.run(30)
    clocks.sleep(2 * HOUR - MINUTE)
    clocks.run(30)
    held = claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path)
    assert held is not None, "a claim one awake minute old read as expired after two hours asleep"
    assert held.slept_secs == pytest.approx(2 * HOUR - MINUTE)
    assert held.claimed_at == T0, "the run began when it began, by the wall clock"
    assert _held_for(tmp_path, clocks) == pytest.approx(MINUTE)


def test_the_next_fire_after_a_sleep_does_not_start_beside_the_run(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """🔴 The duplicate run: the tick reads the claim and decides the next fire (`claim_fire`)."""
    _take(tmp_path)
    clocks.sleep(2 * HOUR - MINUTE)
    clocks.run(MINUTE)
    existing = claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path)
    granted, refusal = claim_fire(
        existing, trigger_id=TRIGGER, holder="tick", now=clocks.wall, overlap="skip"
    )
    assert granted is None, "a second run of the automation started beside the first"
    assert refusal.startswith("held by tick"), refusal


def test_the_reaper_does_not_call_a_run_overdue_for_time_it_slept(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """🔴 45 minutes by the wall clock, 5 of them awake: no `timeout` row for a run still going."""
    _take(tmp_path)
    clocks.run(5 * MINUTE)
    clocks.sleep(40 * MINUTE)
    assert reaper.overdue(now=clocks.wall, base_dir=tmp_path) == []


def test_a_run_awake_past_its_deadline_is_still_overdue(tmp_path: Path, clocks: _Clocks) -> None:
    """The deadline still holds for a run that is awake and stuck, measured in awake time."""
    _take(tmp_path)
    clocks.run(20 * MINUTE)
    clocks.sleep(10 * MINUTE)
    clocks.run(20 * MINUTE)
    [(trigger_id, elapsed)] = reaper.overdue(now=clocks.wall, base_dir=tmp_path)
    assert trigger_id == TRIGGER
    assert elapsed == pytest.approx(40 * MINUTE)


def test_a_claim_awake_past_its_max_duration_expires(tmp_path: Path, clocks: _Clocks) -> None:
    """Read-time expiry still frees a run that crashed, once it has been awake its hour."""
    _take(tmp_path)
    clocks.sleep(2 * HOUR)
    clocks.run(CLAIM_MAX_DURATION_SECS + 1)
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None


@pytest.mark.parametrize(
    ("step", "held_for"),
    [
        # Within a minute the reading names this boot, and the sleep is still taken off; the step
        # counts in the claim's age, as every second of the wall clock did before.
        pytest.param(40.0, 10 * MINUTE + 40, id="forwards-40s"),
        pytest.param(-40.0, 10 * MINUTE - 40, id="backwards-40s"),
        # Past a minute the reading names another boot, and the claim ages by the wall clock alone:
        # what it did before, and what the step alone gives. Two hours on, it has expired.
        pytest.param(2 * HOUR, None, id="forwards-2h"),
        # Two hours back, the wall clock says the run has not begun: held, for no time yet.
        pytest.param(-2 * HOUR, 0.0, id="backwards-2h"),
    ],
)
def test_a_wall_clock_step_is_not_read_as_sleep(
    tmp_path: Path, clocks: _Clocks, step: float, held_for: float | None
) -> None:
    """A wall clock set forwards or backwards moves neither boot clock, so no step reads as sleep:
    50 minutes asleep and 10 awake, around a step, leaves 10 awake minutes plus the step."""
    _take(tmp_path)
    clocks.run(5 * MINUTE)
    clocks.sleep(50 * MINUTE)
    clocks.step(step)
    clocks.run(5 * MINUTE)
    got = _held_for(tmp_path, clocks)
    if held_for is None:
        assert got is None
    else:
        assert got == pytest.approx(held_for)


def test_the_sleep_measured_is_never_below_zero(tmp_path: Path, clocks: _Clocks) -> None:
    """Where the clock that counts sleep reads a little behind the awake one (two clocks, each read
    on its own), the sleep is 0: a claim never ages faster than the wall clock."""
    _take(tmp_path)
    clocks.run(10 * MINUTE)
    clocks.since_boot -= 5
    held = claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path)
    assert held is not None
    assert held.slept_secs == 0.0
    assert _held_for(tmp_path, clocks) == pytest.approx(10 * MINUTE)


def test_a_claim_from_another_boot_ages_by_the_wall_clock(tmp_path: Path, clocks: _Clocks) -> None:
    """A claim the last boot wrote is aged by the wall clock, as every claim was before: the
    restart's clocks measure nothing about the old boot's sleep. Its gateway is gone, and the claim
    expires an hour after it was taken. Here the new boot's clocks read behind the claim's."""
    _take(tmp_path)
    clocks.run(10 * MINUTE)
    clocks.reboot(down=CLAIM_MAX_DURATION_SECS, up=5 * MINUTE)
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None


def test_a_claim_from_a_boot_that_began_at_another_time_ages_by_the_wall_clock(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """A claim taken two minutes after its boot, whose gateway then crashed, read on a quick
    restart that has since slept more than an hour: the new boot's clocks read ahead of the
    claim's, and taken as its boot they would say it slept 4,000 s and hold the claim past its
    hour. The new boot began 4 minutes after the old one, and that is what names it."""
    clocks.awake = clocks.since_boot = 2 * MINUTE
    _take(tmp_path)
    clocks.run(MINUTE)
    clocks.reboot(down=MINUTE, up=5000.0, asleep=4000.0)
    assert clocks.wall - T0 > CLAIM_MAX_DURATION_SECS
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None


def test_a_boot_whose_start_reads_as_the_old_ones_is_still_another_boot(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """A restart whose wall clock came up set back, so its start reads within a minute of the old
    boot's: its clocks read behind the claim's, which no clock of one boot does, and the claim
    carries no sleep (taken as the claim's boot, its clocks would say 10,000 s)."""
    _take(tmp_path)
    clocks.reboot(down=0.0, up=40_000.0, asleep=20_000.0)
    clocks.wall = T0 - SINCE_BOOT0 + clocks.since_boot
    held = claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path)
    assert held is not None, "by the wall clock it was set back to, the run has not begun"
    assert held.slept_secs == 0.0


def test_a_claim_written_before_the_stamps_ages_by_the_wall_clock(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """A claim a gateway wrote before claims carried the stamps is read as it was then."""
    _take(tmp_path)
    path = claims._claim_path(TRIGGER, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["claimed_awake"], raw["claimed_since_boot"], raw["boot_at"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    clocks.sleep(2 * HOUR - MINUTE)
    clocks.run(MINUTE)
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None


def test_where_no_clock_counts_sleep_a_claim_ages_by_the_wall_clock(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """A platform with no clock that counts sleep can't measure it: the claim carries no stamps,
    and one that does is aged by the wall clock where it's read."""
    clocks.since_boot = 0.0
    _take(tmp_path)
    raw = json.loads(claims._claim_path(TRIGGER, tmp_path).read_text(encoding="utf-8"))
    assert not {"claimed_awake", "claimed_since_boot", "boot_at"} & raw.keys(), raw
    clocks.wall += 2 * HOUR
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None

    clocks.wall, clocks.awake, clocks.since_boot = T0, AWAKE0, SINCE_BOOT0
    _take(tmp_path)
    clocks.sleep(2 * HOUR - MINUTE)
    clocks.run(MINUTE)
    clocks.since_boot = 0.0
    assert claims.read_claim(TRIGGER, now=clocks.wall, base_dir=tmp_path) is None


def test_a_claim_written_a_moment_after_its_grant_back_dates_its_stamps(
    tmp_path: Path, clocks: _Clocks
) -> None:
    """The stamps are the boot clocks at the grant, so time between grant and write is not lost."""
    clocks.run(2)
    _take(tmp_path)
    raw = json.loads(claims._claim_path(TRIGGER, tmp_path).read_text(encoding="utf-8"))
    assert raw["claimed_awake"] == pytest.approx(AWAKE0)
    assert raw["claimed_since_boot"] == pytest.approx(SINCE_BOOT0)
    assert raw["boot_at"] == pytest.approx(T0 - SINCE_BOOT0)


_HAS_SLEEP_CLOCK = pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="the clock that counts sleep is macOS's and Linux's",
)

_WRITE_IN_A_CHILD = """
import sys, time
from personalclaw.triggers import claims
from personalclaw.triggers.scheduling import Claim
claim = Claim(trigger_id=sys.argv[2], holder="tick", claimed_at=time.time())
claims.write_claim(claim, base_dir=sys.argv[1])
"""


@_HAS_SLEEP_CLOCK
def test_a_claim_another_process_wrote_is_read_on_its_boots_clocks(tmp_path: Path) -> None:
    """The boot clocks are one clock for every process on a boot: a claim a child process wrote is
    read here as one taken a moment ago, with no sleep since."""
    src = Path(claims.__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    env["PYTHONPATH"] = str(src)
    # A home of the child's own: it writes only into tmp_path, and the suite's guard holds every
    # PersonalClaw child to its own home.
    env["PERSONALCLAW_HOME"] = str(tmp_path / "home")
    subprocess.run(
        [sys.executable, "-c", _WRITE_IN_A_CHILD, str(tmp_path / "claims"), TRIGGER],
        check=True,
        env=env,
        timeout=60,
    )
    base = tmp_path / "claims"
    raw = json.loads(claims._claim_path(TRIGGER, base).read_text(encoding="utf-8"))
    assert {"claimed_awake", "claimed_since_boot", "boot_at"} <= raw.keys(), raw
    wall, _, _ = claims._clocks()
    held = claims.read_claim(TRIGGER, now=wall, base_dir=base)
    assert held is not None
    assert held.slept_secs < 1.0
    assert 0.0 <= (claims.held_for(TRIGGER, now=wall, base_dir=base) or 0.0) < 60.0


@_HAS_SLEEP_CLOCK
def test_each_platform_reads_the_clock_that_counts_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Which clock is which, pinned on the platform the suite runs on (CI's macOS shards pin the
    one half, its Linux shards the other). The sleep is read from the clock that counts it
    (`CLOCK_MONOTONIC` on macOS, `CLOCK_BOOTTIME` on Linux), and the awake clock is Python's
    monotonic one, which stops in sleep. A clock that doesn't count sleep in its place turns the
    fix off without a sound: the sleep always reads 0. On a machine that has never slept the two
    read alike, so the row pins the clock `_clocks` asks for, not only what it reads."""
    if sys.platform == "darwin":
        counts_sleep, awake = time.CLOCK_MONOTONIC, "mach_absolute_time()"
    else:
        counts_sleep, awake = time.CLOCK_BOOTTIME, "clock_gettime(CLOCK_MONOTONIC)"
    assert time.get_clock_info("monotonic").implementation == awake

    asked: list[int] = []
    real = time.clock_gettime

    def spy(clock: int) -> float:
        asked.append(clock)
        return real(clock)

    monkeypatch.setattr(time, "clock_gettime", spy)
    before = real(counts_sleep)
    _, _, since_boot = claims._clocks()
    after = real(counts_sleep)
    assert asked == [counts_sleep]
    assert before <= since_boot <= after


@_HAS_SLEEP_CLOCK
def test_the_real_clocks_count_sleep_on_one_and_name_one_boot() -> None:
    """The premise, on the clocks themselves: the clock that counts sleep never reads behind the
    awake one, and two readings of when the boot began agree."""
    wall, awake, since_boot = claims._clocks()
    again_wall, _, again_since_boot = claims._clocks()
    assert since_boot > 0 and awake > 0
    assert since_boot >= awake - 1.0
    assert abs((wall - since_boot) - (again_wall - again_since_boot)) < 1.0
