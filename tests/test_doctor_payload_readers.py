"""Every field the doctor's remediation payload carries must have a reader on a doctor surface.

🔴 THE DEFECT CLASS THIS CLOSES. ``/api/doctor/remediation`` measures deficits, scores them, and
ships the result. The score had readers; parts of the evidence behind it did not, and an unread
field is indistinguishable from a field that does not exist. Measured against a live gateway on a
seeded home (25 knowledge items, no embedder bound):

    score 100.0   target_score 90
    deficits      knowledge_missing_embeddings ×25  penalty 12.5  reachable:false
    plan          []

The score is RIGHT — an unreachable deficit is at its floor and excluded, because penalising it
would keep the score permanently red for something no maintenance run can improve. What reached
the screen was ``Knowledge missing embeddings ×25 · not fixable yet`` under a green 100, and that
"yet" promises a later pass for a deficit no pass will ever touch: the missing thing is an
embedding model, not a tick of the clock. The engine knew that exactly where it computed
``reachable`` and dropped it one line later. Same shape in the ledger: ``score 88→100 · 1 job``
dropped ``ts`` and every ``jobs[].status``, so a pass whose every job threw rendered identically
to one that did the work.

🔑 SO THE RAIL IS DERIVED FROM THE PAYLOAD'S OWN SHAPE, NOT FROM A LIST OF FIELD NAMES. It builds
the REAL projection (``handlers.doctor.remediation_snapshot``, which is why that is module-level
rather than a closure), walks it for leaf field names, and requires each one to be read by a doctor
surface. Adding a field to the projection with no reader reds this; so does deleting a field an
exemption still names, because a stale exemption is how a census quietly goes vacuous.

The surfaces are the panel AND the CLI, deliberately: ``personalclaw doctor`` printed no part of
this payload at all, so the identical gap existed twice and only one copy was visible from the
dashboard.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PANEL = REPO / "web" / "src" / "pages" / "settings" / "DoctorPanel.tsx"
CLI = REPO / "src" / "personalclaw" / "cli_doctor.py"

#: Payload fields with NO reader, each with the reason it carries no evidence. Every entry is
#: re-checked against the derived field set below, so an exemption cannot outlive its field.
#:
#: Only ONE entry, and it is self-invalidating: ``cost`` is ``0.0`` on every row the engine can
#: currently emit, because every registered job is in the deterministic lane (the judgment lane's
#: inputs are future flywheel/knowledge infra and no job is registered in it). Rendering a column
#: of zeros is not evidence. ``test_the_cost_exemption_expires_when_a_priced_job_appears`` reds the
#: moment a judgment-lane job registers, which is the moment cost starts carrying information.
UNREAD_WITH_REASON = {
    "cost": (
        "every registered job is deterministic ($0) and a dry-run row is 0.0 by construction, so "
        "this is a constant, not evidence — see the judgment-lane rail below"
    ),
}


def _seeded_snapshot(tmp_path: Path) -> dict:
    """The real projection, on a home seeded so no branch of it is empty.

    A payload whose lists are empty derives no nested field names at all, which would let this
    census pass while checking nothing — the exact vacuity the repo's other rails call out. So:
    eight stale locks make ``orphan_locks`` cross the schedulability gate (score 88 < target 90)
    and produce a non-empty ``plan``; a hand-written ledger row carries one ``ok`` job with a
    ``detail`` and one ``error`` job with an ``error``, which are the only two shapes
    ``run_remediation`` appends.
    """
    from personalclaw.dashboard.handlers.doctor import remediation_snapshot

    locks = tmp_path / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    old = time.time() - 3 * 86400
    for i in range(8):
        p = locks / f"stale-{i}.lock"
        p.write_text("")
        os.utime(p, (old, old))

    doctor_dir = tmp_path / "doctor"
    doctor_dir.mkdir(parents=True, exist_ok=True)
    (doctor_dir / "remediation.jsonl").write_text(
        json.dumps(
            {
                "ts": old,
                "score_before": 76.0,
                "score_after": 88.0,
                "jobs": [
                    {
                        "id": "serving-fs.prune-orphans",
                        "status": "ok",
                        "cost": 0.0,
                        "detail": "Removed 4 stale locks",
                    },
                    {
                        "id": "memory.rebuild-fts",
                        "status": "error",
                        "cost": 0.0,
                        "error": "database is locked",
                    },
                ],
                "stopped_reason": "plan exhausted",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return remediation_snapshot()


def _leaves(node: object, path: str = "") -> dict[str, str]:
    """Every leaf in the payload as ``dotted.path[] -> field name``.

    Containers count as leaves too (``plan``, ``jobs``): a list nobody reads is as dropped as a
    string nobody reads. Lists are walked in full rather than by first element, so a field that
    only some rows carry (``detail`` on an ok job, ``error`` on a failed one) is still derived.
    """
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            out[child] = str(key)
            out.update(_leaves(value, child))
    elif isinstance(node, list):
        for item in node:
            out.update(_leaves(item, f"{path}[]"))
    return out


def _reads(source: str, field: str) -> bool:
    """Does ``source`` read ``field``? Attribute access or a string subscript.

    ``\\b`` after the name is load-bearing: without it ``.score`` would match ``.score_before``
    and ``.key`` would match ``.keys()``, and the census would report readers it does not have.
    """
    name = re.escape(field)
    return bool(re.search(rf"\.{name}\b", source) or re.search(rf"\[['\"]{name}['\"]\]", source))


@pytest.fixture()
def snapshot(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return _seeded_snapshot(tmp_path)


# ── the census ────────────────────────────────────────────────────────────────


def test_the_seeded_payload_exercises_every_branch(snapshot):
    """NON-VACUITY, first. A census over an empty payload is a passing test that checks nothing.

    Each assertion here is a branch the derivation needs: a planned job, a ledger row, and both
    job outcomes. If the seeding stops working (a weight change moves the schedulability gate, the
    ledger format changes), this reds instead of the census silently going green on three fields.
    """
    assert snapshot["plan"], "no dry-run plan derived — the seeded locks no longer cross the gate"
    assert snapshot[
        "recent_runs"
    ], "no ledger row derived — the seeded remediation.jsonl was not read"
    statuses = {j["status"] for j in snapshot["recent_runs"][0]["jobs"]}
    assert statuses == {"ok", "error"}, f"both job outcomes must be present, got {statuses}"
    assert snapshot["deficits"], "no deficits measured — every measure branch swallowed"

    derived = _leaves(snapshot)
    # The deep paths, named, so a walker that stopped at the top level cannot pass.
    for path in (
        "deficits[].blocked_by",
        "plan[].id",
        "recent_runs[].ts",
        "recent_runs[].jobs[].status",
        "recent_runs[].jobs[].detail",
        "recent_runs[].jobs[].error",
    ):
        assert path in derived, f"{path} not derived — the payload walk did not reach it"


def test_every_payload_field_has_a_reader(snapshot):
    """The rail itself: no field of the doctor payload is shipped without a surface reading it."""
    surfaces = PANEL.read_text(encoding="utf-8") + "\n" + CLI.read_text(encoding="utf-8")
    derived = _leaves(snapshot)
    unread = sorted(
        {
            f"{path} ({field})"
            for path, field in derived.items()
            if field not in UNREAD_WITH_REASON and not _reads(surfaces, field)
        }
    )
    assert unread == [], (
        "doctor payload field(s) with no reader on any doctor surface:\n  "
        + "\n  ".join(unread)
        + "\n\nEither render it (web/src/pages/settings/DoctorPanel.tsx or "
        "src/personalclaw/cli_doctor.py) or stop projecting it. A third option — adding it to "
        "UNREAD_WITH_REASON — is only honest when the value is a constant that carries no "
        "information, and it needs the reason written next to it."
    )


def test_no_exemption_outlives_its_field(snapshot):
    """A stale exemption is how a census goes quietly vacuous, so name-checking runs both ways."""
    fields = set(_leaves(snapshot).values())
    stale = sorted(set(UNREAD_WITH_REASON) - fields)
    assert stale == [], (
        f"UNREAD_WITH_REASON names field(s) the payload no longer carries: {stale}. "
        "Delete the entry — an exemption for a field that does not exist exempts nothing and "
        "hides the next one that shares its name."
    )
    for field, reason in UNREAD_WITH_REASON.items():
        assert reason.strip(), f"exemption {field!r} has no recorded reason"


def test_the_rail_catches_an_unread_field(snapshot):
    """🐤 CANARY. The census must red on a field nobody reads — otherwise the green above means
    only that the walk found nothing. A synthetic field is used rather than a real one so the
    canary cannot pass by accident on a name some surface happens to mention."""
    surfaces = PANEL.read_text(encoding="utf-8") + "\n" + CLI.read_text(encoding="utf-8")
    poisoned = dict(snapshot)
    poisoned["deficits"] = [{**d, "zz_never_read_anywhere": 1} for d in snapshot["deficits"]]
    caught = [
        path
        for path, field in _leaves(poisoned).items()
        if field not in UNREAD_WITH_REASON and not _reads(surfaces, field)
    ]
    assert caught == [
        "deficits[].zz_never_read_anywhere"
    ], f"the census did not isolate the injected unread field, it reported {caught}"


# ── the deficit list is the evidence, so BOTH surfaces render all of it ───────


def test_every_deficit_field_is_read_by_both_the_panel_and_the_cli(snapshot):
    """The deficits are the actionable payload — the reason a reader opens either surface — so
    "at least one reader" is too weak a bar for them specifically. ``personalclaw doctor`` used to
    print none of it: on a home with 25 unembedded items it said ``embeddings: ⏹ disabled``,
    naming the missing prerequisite while saying nothing about its consequence.

    Derived from the projection's own deficit keys, so a new deficit field has to reach both."""
    panel = PANEL.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    fields = sorted(snapshot["deficits"][0])
    assert len(fields) >= 5, f"deficit projection shrank to {fields} — is the census still real?"
    missing = {
        "panel": [f for f in fields if not _reads(panel, f)],
        "cli": [f for f in fields if not _reads(cli, f)],
    }
    assert not missing["panel"] and not missing["cli"], (
        f"deficit field(s) missing a reader: {missing}. Both surfaces present the deficit list; "
        "a field on one and not the other is the same evidence gap, half-fixed."
    )


# ── the invariant that makes `blocked_by` trustworthy ─────────────────────────


def test_blocked_by_is_present_exactly_when_a_deficit_is_unreachable(tmp_path, monkeypatch):
    """``reachable=False`` without a reason re-creates the original defect: a surface that can say
    only "not fixable yet". And a reason on a reachable deficit would render a blocker for
    something the next Run now clears. So the two fields move together, in both directions."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.resilience.remediation import measure_deficits

    measured = measure_deficits()
    assert measured, "no deficits measured — every branch swallowed its exception"
    for d in measured:
        if d.reachable:
            assert d.blocked_by == "", (
                f"{d.key} is reachable but names a blocker {d.blocked_by!r} — the next Run now "
                "clears it, so a prerequisite sentence is a lie"
            )
        else:
            assert d.blocked_by.strip(), (
                f"{d.key} is unreachable and cannot say why. That is the whole defect: the "
                "surfaces fall back to 'not fixable yet', which reads as 'later' for something "
                "no maintenance pass will ever reach."
            )


def test_the_unreachable_reason_names_a_next_step(tmp_path, monkeypatch):
    """A blocker sentence that only restates the block ("no embedder") leaves a reader exactly
    where "not fixable yet" did. Each one has to point somewhere — a page, a person, an action."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.resilience.remediation import measure_deficits

    blocked = [d for d in measure_deficits() if not d.reachable]
    assert blocked, "no unreachable deficit measured — skills_tampered is always one of them"
    for d in blocked:
        assert re.search(
            r"Settings|page|review|reinstall|remove|pick", d.blocked_by
        ), f"{d.key}'s blocker {d.blocked_by!r} states the problem without a next step"


# ── the CLI reads it by EXECUTION, not by containing the source ───────────────


def test_personalclaw_doctor_prints_the_deficit_and_its_blocker(tmp_path, monkeypatch, capsys):
    """Runs the real ``_doctor()``. The census above greps source text, which a dead function
    would satisfy — so the CLI half is also pinned by execution: drop the ``_doctor_maintenance()``
    call from ``_doctor`` and this reds while the grep stays green.

    Seeded to reproduce the reported home: knowledge items with text, no embedder bound. The one
    line the CLI used to print about this was ``embeddings: ⏹ disabled``.
    """
    import urllib.error
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.cli_doctor import _doctor
    from personalclaw.knowledge import get_knowledge_store

    store = get_knowledge_store()
    for i in range(25):
        store.create_typed_item(item_type="note", title=f"seeded {i}", content="body text here")
    assert store.count_items_missing_embedding() == 25, "seeding did not produce the backlog"

    (tmp_path / "personalclaw.json").write_text(
        json.dumps({"tools": [], "allowedTools": [], "mcpServers": {}}), encoding="utf-8"
    )
    mock_run = MagicMock(returncode=0, stdout="v22.12.0", stderr="")
    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
        patch("subprocess.run", return_value=mock_run),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        patch("personalclaw.cli_doctor.ensure_ffmpeg_in_path"),
    ):
        # `_doctor` only raises when it collected issues; a clean run returns.
        code: object = 0
        try:
            _doctor()
        except SystemExit as exit_signal:  # pragma: no cover - only on a dirty probe env
            code = exit_signal.code
    out = capsys.readouterr().out
    # A maintenance backlog must not fail the setup check — the deficit is a backlog, not a
    # broken install, and a fresh home with unembedded notes must still pass its own doctor.
    assert code in (0, None), f"a deficit failed the doctor (exit {code}):\n{out}"
    assert "✅ PersonalClaw is ready!" in out, f"the doctor did not pass:\n{out}"
    assert "Maintenance" in out, "the doctor grew no maintenance section"
    assert "knowledge missing embeddings ×25" in out, f"the measured backlog is not printed:\n{out}"
    assert "no embedding model is bound" in out, f"the blocker is not printed:\n{out}"
    assert "Settings → Models" in out, "the next step is not printed"


def test_the_cost_exemption_expires_when_a_priced_job_appears():
    """The one exemption's premise, pinned. ``cost`` is exempt because it is always ``0.0`` —
    true only while every registered job is deterministic. Register a judgment-lane job and this
    reds, which is correct: at that moment cost starts carrying information and has to be shown."""
    from personalclaw.resilience.remediation import all_jobs

    jobs = all_jobs()
    assert jobs, "no jobs registered — the exemption's premise is unverifiable, not satisfied"
    priced = sorted(j.id for j in jobs if j.lane != "deterministic")
    assert priced == [], (
        f"job(s) {priced} run in a priced lane, so `cost` is no longer a constant 0.0. Render it "
        "on the doctor surfaces and delete its UNREAD_WITH_REASON entry."
    )
