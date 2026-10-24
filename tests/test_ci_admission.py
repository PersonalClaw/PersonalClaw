"""A local tier run starts a job only when the machine has the memory it needs (#3920).

``tools/ci/admission.py`` reckons a job's need and waits for it. Each test here gives it a
planted machine, whose free memory, total, clock and sleep the test sets, so nothing waits on
this machine's own memory or a real clock. ``tests/test_ci_jobs.py`` runs whole tiers through it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.ci import admission


class _Machine:
    """A planted machine: its free memory is read from *free* in turn, the last value staying,
    and each sleep moves its clock on."""

    def __init__(self, *free: int | None, total: int | None = 16_384) -> None:
        self.free = list(free)
        self.total = total
        self.now = 0.0
        self.sleeps: list[float] = []
        self.reads = 0

    def memory(self, ledger: Path | None = None) -> admission.Memory:
        """Its memory; with *ledger*, the machine-wide ledger at that path, on its clock."""
        return admission.Memory(
            free=self._free,
            total=lambda: self.total,
            sleep=self._sleep,
            clock=lambda: self.now,
            ledger=None if ledger is None else admission.Ledger(ledger, now=lambda: self.now),
        )

    def _free(self) -> int | None:
        self.reads += 1
        return self.free.pop(0) if len(self.free) > 1 else self.free[0]

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _admit(machine: _Machine, need: int, memory: admission.Memory | None = None, **kw):
    said: list[str] = []
    refused = admission.admit("job", need, "how", memory or machine.memory(), say=said.append, **kw)
    return refused, said


def test_a_job_measured_here_needs_its_workers_times_its_peak():
    assert admission.need_mb(workers=4, peak_mb=300, declared_gb=8) == (
        1200,
        "4 worker(s) × 300 MB, its peak here",
    )


def test_a_job_never_measured_here_needs_what_the_catalogue_declares():
    assert admission.need_mb(workers=4, peak_mb=None, declared_gb=1.5) == (
        1536,
        "the catalogue's mem_gb = 1.5",
    )


def test_a_job_neither_measured_nor_declared_is_taken_to_need_a_large_share():
    need, how = admission.need_mb(workers=3, peak_mb=None, declared_gb=None)
    assert need == 3 * admission.UNKNOWN_WORKER_MB
    assert "never measured here" in how


def test_a_job_the_free_memory_holds_starts_at_once():
    machine = _Machine(4096)
    refused, said = _admit(machine, 1024)
    assert refused is None
    assert machine.sleeps == [] and said == []


def test_a_job_waits_until_the_free_memory_holds_it_then_starts():
    machine = _Machine(512, 900, 2048)
    refused, said = _admit(machine, 1024)
    assert refused is None
    assert machine.sleeps == [admission.POLL_S, admission.POLL_S]
    assert said == [
        "admission: [job] waits: it needs 1024 MB (how), 512 MB is free",
        "admission: [job] starts: 2048 MB free of the 1024 MB it needs",
    ]


def test_a_job_still_waiting_after_the_bound_is_not_run():
    machine = _Machine(512)
    refused, said = _admit(machine, 1024, wait_s=60)
    assert refused == "not run: it needs 1024 MB (how), and 512 MB was free after 60s of waiting"
    assert sum(machine.sleeps) == 60
    assert len(said) == 1  # it said it waits once, not at every reading


def test_a_job_needing_more_than_the_machine_holds_is_not_run_and_waits_for_nothing():
    machine = _Machine(8192, total=8192)
    refused, _ = _admit(machine, 9000)
    assert refused == "not run: it needs 9000 MB (how), and this machine has 8192 MB in all"
    assert machine.reads == 0 and machine.sleeps == []


def test_a_machine_whose_memory_cannot_be_read_admits_every_job_and_says_so_once():
    machine = _Machine(None, total=None)
    memory = machine.memory()
    first, said_first = _admit(machine, 10**9, memory)
    second, said_second = _admit(machine, 10**9, memory)
    assert first is None and second is None
    assert said_first == ["admission: this machine's free memory cannot be read; every job starts"]
    assert said_second == []


def test_this_machine_s_memory_reads_as_a_free_share_of_its_total():
    free, total = admission.free_mb(), admission.total_mb()
    assert isinstance(free, int) and isinstance(total, int), (free, total)
    assert 0 < free <= total


def test_a_peak_is_kept_for_the_next_run(tmp_path: Path):
    path = tmp_path / admission.PEAKS_FILE
    admission.Peaks(path).record("unit", 412)
    assert admission.Peaks(path).get("unit") == 412
    assert json.loads(path.read_text()) == {"unit": 412}


def test_a_peaks_file_that_cannot_be_read_counts_every_job_as_never_measured(tmp_path: Path):
    path = tmp_path / admission.PEAKS_FILE
    path.write_text("not json")
    assert admission.Peaks(path).get("unit") is None
    path.write_text(json.dumps({"unit": "big", "web": True, "lint": -3, "e2e": 700}))
    peaks = admission.Peaks(path)
    assert [peaks.get(j) for j in ("unit", "web", "lint", "e2e")] == [None, None, None, 700]


# The ledger: jobs just started count before the free reading shows them


def test_a_second_admission_waits_for_the_memory_the_first_just_took(tmp_path: Path):
    """The plant: two runs (two clones) admit back to back against a free reading that does not
    drop, since the first job has not grown yet. The first starts; the second waits until the
    first's entry is past the ramp, then starts."""
    machine = _Machine(3000)
    ledger = tmp_path / admission.LEDGER_FILE
    first, said_first = _admit(machine, 2000, machine.memory(ledger))
    assert first is None and said_first == [] and machine.sleeps == []

    second, said_second = _admit(machine, 2000, machine.memory(ledger))
    assert second is None
    assert sum(machine.sleeps) == admission.RAMP_S
    assert said_second == [
        "admission: [job] waits: it needs 2000 MB (how), 3000 MB is free (2000 MB of it held by"
        " jobs just started)",
        "admission: [job] starts: 3000 MB free of the 2000 MB it needs",
    ]


def test_a_second_admission_never_given_the_memory_says_what_held_it(tmp_path: Path):
    machine = _Machine(3000)
    ledger = tmp_path / admission.LEDGER_FILE
    _admit(machine, 2000, machine.memory(ledger))
    refused, _ = _admit(machine, 2000, machine.memory(ledger), wait_s=30)
    assert refused == (
        "not run: it needs 2000 MB (how), and 3000 MB was free (2000 MB of it held by jobs just"
        " started) after 30s of waiting"
    )


def test_a_job_that_has_ended_holds_nothing(tmp_path: Path):
    machine = _Machine(3000)
    ledger = tmp_path / admission.LEDGER_FILE
    first = machine.memory(ledger)
    _admit(machine, 2000, first)
    admission.release(first)
    second, said = _admit(machine, 2000, machine.memory(ledger))
    assert second is None and said == [] and machine.sleeps == []
    assert json.loads(ledger.read_text()) and len(json.loads(ledger.read_text())) == 1


def test_an_entry_whose_run_is_gone_holds_nothing(tmp_path: Path):
    """A run that died without releasing its entry: its process is gone, so its entry is."""
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    ledger = tmp_path / admission.LEDGER_FILE
    entry = {"id": "x", "pid": gone.pid, "need_mb": 2000, "start": 0.0, "job": "dead"}
    ledger.write_text(json.dumps([entry]))
    machine = _Machine(3000)
    refused, said = _admit(machine, 2000, machine.memory(ledger))
    assert refused is None and said == []
    assert [e["job"] for e in json.loads(ledger.read_text())] == ["job"]


def test_a_ledger_holding_what_is_not_an_entry_holds_nothing(tmp_path: Path):
    ledger = tmp_path / admission.LEDGER_FILE
    machine = _Machine(3000)
    for text in ("not json", json.dumps({"id": "x"}), json.dumps([{"pid": True}, 7])):
        ledger.write_text(text)
        assert _admit(machine, 2000, machine.memory(ledger)) == (None, [])
        ledger.unlink()


def test_a_ledger_that_cannot_be_used_counts_nothing_and_says_so_once(tmp_path: Path):
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    machine = _Machine(3000)
    memory = machine.memory(blocked / admission.LEDGER_FILE)
    first, said = _admit(machine, 2000, memory)
    assert first is None and len(said) == 1
    assert said[0].startswith("admission: the machine's ledger ") and said[0].endswith(
        "jobs other runs just started are not counted"
    )
    assert memory.ledger is None
    assert _admit(machine, 2000, memory) == (None, [])


def test_the_ledger_is_in_the_user_s_cache_folder_outside_every_clone():
    home = Path("/home/someone")
    tail = Path("personalclaw-ci") / admission.LEDGER_FILE
    assert admission.ledger_path({}, home, "darwin") == home / "Library" / "Caches" / tail
    assert admission.ledger_path({}, home, "linux") == home / ".cache" / tail
    assert admission.ledger_path({"XDG_CACHE_HOME": "/c"}, home, "linux") == Path("/c") / tail
    # A relative XDG_CACHE_HOME names no folder, as the spec reads it.
    assert admission.ledger_path({"XDG_CACHE_HOME": "c"}, home, "linux") == home / ".cache" / tail
    assert admission.Memory.this().ledger == admission.Ledger(admission.ledger_path())
