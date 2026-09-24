"""#3473 — the last two unclaimed paths a fresh install reports, decided per path.

On a newly installed home ``durability.inventory.audit_home()`` reported::

    2 unclaimed path(s) … these are in NO snapshot

naming ``footprint.json`` and ``trigger-claims/``. That is an honest report of a real gap, and
after #3468 it was the ONLY remaining doctor fault a new user sees (``worst: "durability"``), so
the first thing a fresh install showed in Doctor was this and nothing else.

🔑 THE DECISION IS PER PATH, AND BOTH LANDED ON "DELIBERATELY NOT STATE" — for two different
reasons, which is why it could not be one blanket rule:

* ``footprint.json`` is a MEASUREMENT SERIES of this machine's disk, not user state. Its samples
  are bytes-on-disk per store, re-measured by the maintenance tick, and its only non-reproducible
  content is the trend. A snapshot is restored onto another machine or the same one after a wipe,
  where those byte counts describe a filesystem that no longer exists — so a restored series
  would hand ``growth()`` two samples straddling a discontinuity and it would report a rate that
  never happened. The module's own rule is that one sample reports NO rate rather than a
  fabricated zero; restoring a foreign series fabricates something worse than a zero.

* ``trigger-claims/`` is RUNTIME COORDINATION, and restoring it is actively harmful. A claim
  answers "which trigger is running right now" and names ``owner_pid``. Restore it and every
  claim names a pid that is dead or belongs to an unrelated process on the target machine; until
  read-time expiry elapses the trigger reads as in flight, so ``overlap: skip`` suppresses fires
  that should happen and ``is_running`` asserts a process that does not exist. That is exactly
  the "resurrect a claim whose owner is gone" hazard, and `reaper.terminalize_orphans` exists to
  clean these up at boot — a restore that re-plants them creates work for the reaper at best.

🪤 THE FIXTURE DRIVES THE REAL PRODUCERS, NOT HAND-TYPED FILENAMES. An ``IGNORED`` row is a
STRING, and a test that writes ``home / "footprint.json"`` itself proves only that the string
matches itself. Both paths are also invisible to the static census in
``test_durability_inventory_census.py`` — they are built from a runtime value
(``home / _STATE_FILENAME``, ``root / "trigger-claims"`` where ``root`` defaults to
``config_dir()``), which is why they land in that file's bounded blind spot and why
``audit_home()`` against a real home was the only thing that could see them. So these tests call
``footprint.record`` and ``claims.write_claim`` and let the producers name their own paths: if a
producer is renamed, the row stops matching and this file says so.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw.durability import footprint, inventory
from personalclaw.triggers import claims
from personalclaw.triggers.scheduling import Claim


def _fresh_home(tmp_path: Path) -> Path:
    """A home with one declared store on disk, so the audit has something to claim.

    Without it ``claimed`` is 0 and "no unclaimed paths" is true of an empty directory — the
    vacuity floor for every assertion below.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({}))
    return home


def test_the_footprint_series_is_accounted_for(tmp_path: Path) -> None:
    """Driven through ``footprint.record``, which is what actually names the file."""
    home = _fresh_home(tmp_path)
    before = inventory.audit_home(home)
    assert before.unclaimed == [], f"the fixture was already dirty: {before.unclaimed}"

    footprint.record(home)

    written = footprint.state_path(home)
    assert written.is_file(), "the producer wrote nothing, so this test proves nothing"
    rel = written.relative_to(home).as_posix()
    assert inventory.is_accounted(rel), f"{rel} is neither claimed nor deliberately ignored"
    after = inventory.audit_home(home)
    assert after.unclaimed == [], f"the footprint series reads as unclaimed: {after.unclaimed}"


def test_a_live_trigger_claim_is_accounted_for(tmp_path: Path) -> None:
    """Driven through ``claims.write_claim``, which owns the directory name."""
    home = _fresh_home(tmp_path)
    before = inventory.audit_home(home)
    assert before.unclaimed == [], f"the fixture was already dirty: {before.unclaimed}"

    claims.write_claim(Claim(trigger_id="t-1", holder="tick", claimed_at=1.0), base_dir=home)

    assert claims.running_ids(now=2.0, base_dir=home) == ["t-1"], "no claim was persisted"
    after = inventory.audit_home(home)
    assert after.unclaimed == [], f"the claim store reads as unclaimed: {after.unclaimed}"


def test_both_together_leave_the_audit_clean(tmp_path: Path) -> None:
    """The issue's own state: a fresh install where both have appeared.

    Asserting ``ok`` rather than just ``unclaimed``, because that is what the Doctor probe reads
    — and `undeclared_dbs` must stay empty too, or the fault comes back wearing the other half of
    the sentence.
    """
    home = _fresh_home(tmp_path)
    footprint.record(home)
    claims.write_claim(Claim(trigger_id="t-1", holder="tick", claimed_at=1.0), base_dir=home)

    res = inventory.audit_home(home)
    assert res.unclaimed == [], res.unclaimed
    assert res.undeclared_dbs == [], res.undeclared_dbs
    assert res.ok is True
    assert res.claimed >= 1, "nothing was claimed, so `ok` is true of an empty home"


def test_neither_is_declared_as_state() -> None:
    """The other half of the decision, asserted so "ignored" cannot quietly become "claimed".

    The two postures are NOT interchangeable. A declared entry is CAPTURED by a snapshot — that
    is what ``secret=True`` is for, a store excluded from exports but backed up on purpose. These
    two must not travel at all, which is the same posture as ``machine_id``,
    ``gateway.runtime.json`` and ``update_run.json``: ignored, never declared.
    """
    for rel in ("footprint.json", "trigger-claims"):
        assert inventory.is_ignored(rel), f"{rel} must be ignored, not merely unclaimed"
        assert inventory.claim_for(rel) is None, f"{rel} must not be a declared, captured store"
        assert not inventory.claims_within(rel), f"nothing may claim a path inside {rel}"


def test_no_snapshot_contents_change_in_either_direction() -> None:
    """The state-shape question, answered: there is no state-shape change.

    ``IGNORED`` is read by ``audit_home``/``is_accounted`` and by NOTHING else — a snapshot walks
    ``backup_entries()`` and an export walks ``export_entries()``, both projections of the declared
    entries. An unclaimed path was therefore already absent from every archive, which is what
    "unclaimed" means. So an archive taken before this change and one taken after contain the same
    paths, and restoring either behaves identically; what changed is that a deliberate absence is
    now recorded as one instead of reported as a gap. Asserted rather than argued, because "this
    needs no migration" is exactly the claim a reader should not have to take on trust.
    """
    for projection in (
        inventory.backup_entries(),
        inventory.backup_entries(include_derived=True),
        inventory.export_entries(),
    ):
        paths = {e.path for e in projection}
        tops = {p.split("/", 1)[0] for p in paths}
        assert "footprint.json" not in paths and "footprint.json" not in tops
        assert "trigger-claims" not in tops
    assert "footprint.json" not in inventory.secret_paths()
    assert not any(p.startswith("trigger-claims") for p in inventory.secret_paths())
    # The floor: these projections are non-empty, or the four assertions above are vacuous.
    assert len(inventory.backup_entries()) > 10


def test_the_ignore_rows_carry_their_reasoning() -> None:
    """The issue's condition on this outcome, as a test.

    "Mark deliberately unclaimed so the probe stops reporting it" is only honest if the reasoning
    is written where the next reader finds it — which for this manifest is beside the row, the
    way every other machine-local row here is justified. A row with no argument is
    indistinguishable from someone silencing a probe.
    """
    src = Path(inventory.__file__).read_text()
    block = src[src.index("IGNORED: tuple[str, ...] = (") :]
    lines = block[: block.index("\n)\n")].splitlines()

    def argument_for(rel: str) -> str:
        """The contiguous run of comment lines immediately above the row.

        🪤 LINE-BASED, NOT A CHARACTER WINDOW SPLIT ON A QUOTE. The first version took the 2000
        characters before the row and `rsplit('",', 1)` to find the previous row's boundary — and
        the `trigger-claims` argument quotes a phrase (`"a process on THIS machine holds this
        trigger"`), so the split landed inside the prose and read 116 characters where the
        argument is over a thousand. A reader that cannot handle a quotation mark in prose will
        eventually report a written argument as missing, which is the one verdict this test must
        not get wrong.
        """
        at = next(i for i, ln in enumerate(lines) if ln.strip() == f'"{rel}",')
        out: list[str] = []
        for ln in reversed(lines[:at]):
            if not ln.strip().startswith("#"):
                break
            out.append(ln)
        return "\n".join(reversed(out))

    for rel, must_mention in (
        ("footprint.json", ("measurement", "3473")),
        ("trigger-claims", ("owner_pid", "3473")),
    ):
        argument = argument_for(rel)
        assert len(argument.strip()) > 200, f"{rel} is ignored without a written argument"
        for token in must_mention:
            # Case-insensitive: this manifest SHOUTS the load-bearing noun ("MACHINE-LOCAL",
            # "MEASUREMENT"), and an assertion about which concept the argument names has no
            # business also asserting its capitalisation.
            assert token.lower() in argument.lower(), f"{rel}'s argument does not mention {token!r}"
