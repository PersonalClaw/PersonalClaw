"""EI-7 (EXECUTION-ISOLATION §4.1/§4.2) — the second-opinion handoff's two safety properties.

Success criterion 6 has three clauses that can each be faked, so each is asserted at its CALL
SITE with a vacuity floor:

1. **"a DIFFERENT cataloged runner"** — not "some runner ran". The assertion is that the runner
   that stalled is *unreachable*: when it is the ONLY healthy runner in the catalog, selection
   returns nothing and the service degrades to the ``subagent`` backend. A test that only checked
   "a runner was chosen" would pass against a selector with no exclusion at all, so the vacuity
   floor is that the same catalog with a DIFFERENT exclusion does yield that runner — proving the
   catalog was populated and the filter, not an empty universe, is what dropped it.

2. **"accepted only when the disk re-diff confirms the edits"** — asserted as the NEGATIVE. A
   proposer that claims an edit it did not make is REJECTED. The vacuity floor is the paired
   positive: the same proposer, same claim, with the bytes actually written, is accepted. One
   without the other proves nothing (an always-reject gate passes the negative alone).

3. **"SEL-audited"** — a row is read back out of the log, not merely "the code path exists".

Every filesystem touch is under ``tmp_path``; the runner catalog's sidecar reads/writes are
redirected with ``PERSONALCLAW_HOME`` (the safe lever — ``monkeypatch.setattr`` on
``config.loader.config_dir`` misses import-bound stores and is not undoable once a consumer has
imported), and the redirect is asserted to have bound before anything depends on it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.proposer.backends import (
    SUBAGENT_BACKEND,
    RunnerProposerBackend,
    SubagentProposerBackend,
    normalise,
)
from personalclaw.proposer.brief import build_brief
from personalclaw.proposer.contract import CLAIM_MARKER, ProposerBackend, parse_claimed_paths
from personalclaw.proposer.selection import select_target
from personalclaw.proposer.service import (
    SEL_OP_FIRE,
    SEL_OP_VERDICT,
    choose_backend,
    run_second_opinion,
)
from personalclaw.proposer.verify import rediff, snapshot_workspace

_ALL_RUNNERS = ("claude-code", "codex", "gemini-cli", "kiro")


@pytest.fixture()
def isolated_home(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect PERSONALCLAW_HOME and PROVE the redirect bound before any test depends on it."""
    home = Path(str(tmp_path)) / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    from personalclaw.agents import runners

    bound = runners.user_catalog_dir()
    assert str(home) in str(bound), (
        f"PERSONALCLAW_HOME redirect did not bind: sidecars would resolve to {bound}, "
        "which is outside the test home"
    )
    return home


def _mark_healthy(runner_id: str, *, ok: bool = True) -> None:
    """Write the health sidecar the selector reads. Uses the module's own writer, so a change
    to the sidecar format cannot make this fixture silently stop being read."""
    from datetime import datetime, timezone

    from personalclaw.agents.runners import HealthEvidence, record_evidence

    record_evidence(
        runner_id,
        HealthEvidence(
            ok=ok,
            probe="version",
            checked_at=datetime.now(tz=timezone.utc).isoformat(),
            version="1.2.3",
            latency_ms=12,
            error=None if ok else "not found on PATH",
        ),
    )


# ── 1. the DIFFERENT-runner requirement ───────────────────────────────────────


def test_stalled_runner_is_never_selected_even_as_the_only_healthy_one(isolated_home: Path) -> None:
    """The exclusion is structural: the runner that stalled cannot serve its own second opinion."""
    _mark_healthy("gemini-cli")

    # Vacuity floor FIRST: with a different exclusion, this catalog DOES yield gemini-cli. So the
    # catalog is populated and the health sidecar is being read — a later empty result is the
    # exclusion doing work, not an empty universe.
    floor = select_target(exclude_runner="codex")
    assert floor.runner_id == "gemini-cli", (
        "vacuity floor failed: gemini-cli is the only healthy runner and must be selectable "
        f"when it is not the excluded one (considered={[c.runner_id for c in floor.considered]})"
    )

    excluded = select_target(exclude_runner="gemini-cli")
    assert (
        excluded.target is None
    ), f"the stalled runner was selected to review its own stall: {excluded.runner_id}"
    reasons = {c.runner_id: c.reason for c in excluded.considered}
    assert "stalled" in reasons["gemini-cli"], reasons["gemini-cli"]


def test_runtime_id_spelling_cannot_dodge_the_exclusion(isolated_home: Path) -> None:
    """``acp:gemini-cli`` and ``gemini-cli`` name the same runner, so either spelling excludes."""
    _mark_healthy("gemini-cli")
    assert select_target(exclude_runner="acp:gemini-cli").target is None
    # Floor: an unrelated runtime id does NOT exclude it.
    assert select_target(exclude_runner="acp:codex").runner_id == "gemini-cli"


def test_choose_backend_degrades_to_subagent_when_the_exclusion_empties_the_catalog(
    isolated_home: Path,
) -> None:
    """The degradation path is the ``subagent`` backend — never a re-ask of the stalled runner."""
    _mark_healthy("gemini-cli")
    brief = build_brief(
        goal="g", stuck_at="s", workspace=str(isolated_home), origin_runner="gemini-cli"
    )
    backend, selection = choose_backend(brief)
    assert backend.name == SUBAGENT_BACKEND
    assert selection.target is None
    # Floor: a stall in a runner that is NOT the healthy one picks the runner backend instead.
    other_brief = build_brief(
        goal="g", stuck_at="s", workspace=str(isolated_home), origin_runner="codex"
    )
    other_backend, other_selection = choose_backend(other_brief)
    assert isinstance(other_backend, RunnerProposerBackend)
    assert other_selection.runner_id == "gemini-cli"


def test_unhealthy_and_unprobed_runners_are_not_credible_second_opinions(
    isolated_home: Path,
) -> None:
    """No probe on record means NOT MEASURED, and a failed probe means unhealthy — neither is
    silently treated as available."""
    # Nothing written at all: every runner is unprobed.
    assert select_target(exclude_runner="codex").target is None
    _mark_healthy("gemini-cli", ok=False)
    assert select_target(exclude_runner="codex").target is None
    # Floor: flipping the SAME runner healthy makes it selectable, so the filter is the
    # discriminator rather than a blanket refusal.
    _mark_healthy("gemini-cli", ok=True)
    assert select_target(exclude_runner="codex").runner_id == "gemini-cli"


def test_every_cataloged_runner_is_considered(isolated_home: Path) -> None:
    """The considered set covers the whole catalog — a runner silently missing from the census
    would be one the exclusion never had to exclude."""
    considered = {c.runner_id for c in select_target(exclude_runner="").considered}
    assert set(_ALL_RUNNERS) <= considered, considered


# ── 2. the disk-re-diff acceptance gate ───────────────────────────────────────


def test_claimed_edit_not_on_disk_is_rejected_and_the_same_claim_landed_is_accepted(
    tmp_path: Path,
) -> None:
    """The gate, both directions, over ONE baseline — the negative and its vacuity floor."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "app.py"
    target.write_text("original\n", encoding="utf-8")
    baseline = snapshot_workspace(str(ws), paths=("app.py",))

    lying = rediff(baseline, ("app.py",))
    assert not lying.verified, "a claim with no write behind it was accepted"
    assert lying.missing == ("app.py",)
    assert "not on disk" in lying.reason

    # Vacuity floor: the identical claim over the identical baseline, with the bytes actually
    # written, verifies. Without this the negative above would also pass an always-reject gate.
    target.write_text("patched\n", encoding="utf-8")
    landed = rediff(baseline, ("app.py",))
    assert landed.verified, landed.reason
    assert landed.verified_paths == ("app.py",)


def test_a_proposer_that_claims_nothing_is_not_accepted(tmp_path: Path) -> None:
    """Advice is not a patch. Zero claims → nothing to confirm → not accepted, even when other
    files did change (so this is not "we saw no changes")."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x\n", encoding="utf-8")
    baseline = snapshot_workspace(str(ws), paths=("a.py",))
    (ws / "a.py").write_text("y\n", encoding="utf-8")
    verdict = rediff(baseline, ())
    assert not verdict.verified
    assert verdict.changed == ("a.py",), "the change WAS observed; the rejection is about claims"


def test_a_partially_true_claim_set_is_rejected_whole(tmp_path: Path) -> None:
    """One false claim poisons the handoff — a half-landed patch is not a safe thing to accept."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x\n", encoding="utf-8")
    (ws / "b.py").write_text("x\n", encoding="utf-8")
    baseline = snapshot_workspace(str(ws), paths=("a.py", "b.py"))
    (ws / "a.py").write_text("y\n", encoding="utf-8")
    verdict = rediff(baseline, ("a.py", "b.py"))
    assert not verdict.verified
    assert verdict.verified_paths == ("a.py",)
    assert verdict.missing == ("b.py",)


def test_a_created_file_can_be_proven(tmp_path: Path) -> None:
    """An added file has baseline digest "" (absent) and so is provable — otherwise a proposer
    that correctly creates a file would be rejected for telling the truth."""
    ws = tmp_path / "ws"
    ws.mkdir()
    baseline = snapshot_workspace(str(ws), paths=("new.py",))
    assert not rediff(baseline, ("new.py",)).verified
    (ws / "new.py").write_text("hello\n", encoding="utf-8")
    assert rediff(baseline, ("new.py",)).verified


def test_absolute_and_dotted_claim_spellings_normalise(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x\n", encoding="utf-8")
    baseline = snapshot_workspace(str(ws), paths=("a.py",))
    (ws / "a.py").write_text("y\n", encoding="utf-8")
    for spelling in ("a.py", "./a.py", str(ws / "a.py")):
        assert rediff(baseline, (spelling,)).verified, spelling


def test_normalise_computes_diff_verified_from_disk_not_from_the_message(tmp_path: Path) -> None:
    """``normalise`` is the single place ``diff_verified`` is set. A message that says "done"
    and lists a file it never touched is ok=True, diff_verified=False — recorded honestly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x\n", encoding="utf-8")
    baseline = snapshot_workspace(str(ws), paths=("a.py",))
    text = f"All done, I fixed it.\n{CLAIM_MARKER} a.py\n"

    lying = normalise(backend="t", runner_id="t", ok=True, text=text, baseline=baseline)
    assert lying.ok and not lying.diff_verified
    assert lying.missing_paths == ("a.py",)

    (ws / "a.py").write_text("y\n", encoding="utf-8")
    honest = normalise(backend="t", runner_id="t", ok=True, text=text, baseline=baseline)
    assert honest.ok and honest.diff_verified
    assert honest.verified_paths == ("a.py",)


def test_prose_claims_are_not_claims() -> None:
    """Only the marker counts. "I edited app.py" is prose, not a verifiable claim."""
    assert parse_claimed_paths("I edited app.py and also src/main.py, trust me") == ()
    assert parse_claimed_paths(f"- {CLAIM_MARKER} `src/main.py`") == ("src/main.py",)
    assert parse_claimed_paths(f"{CLAIM_MARKER} a\n{CLAIM_MARKER} a\n") == ("a",)


# ── 3. the service: one definition of "accepted", and a SEL row for it ─────────


class _FakeManager:
    """A stand-in SubagentManager: the two methods the fallback backend actually calls."""

    def __init__(self, workspace: Path, *, claim: str, write: bool) -> None:
        self.workspace = workspace
        self.claim = claim
        self.write = write
        self.spawned: list[dict] = []
        self._info = None

    def spawn(self, **kwargs):
        self.spawned.append(kwargs)
        if self.write:
            (self.workspace / self.claim).write_text("patched by the proposer\n", encoding="utf-8")

        class _Info:
            id = "sub-1"
            done = True
            error = ""

        _Info.result = f"Fixed it.\n{CLAIM_MARKER} {self.claim}\n"
        self._info = _Info()
        return self._info

    def get(self, agent_id: str):
        return self._info if agent_id == "sub-1" else None


def _run_handoff(workspace: Path, *, write: bool, home: Path) -> tuple[object, _FakeManager]:
    manager = _FakeManager(workspace, claim="app.py", write=write)
    backend = SubagentProposerBackend(timeout_secs=5.0, poll_secs=0.01, manager=manager)
    import personalclaw.proposer.service as service_mod

    original = service_mod.SubagentProposerBackend
    service_mod.SubagentProposerBackend = lambda **_kw: backend  # type: ignore[assignment]
    try:
        outcome = asyncio.run(
            run_second_opinion(
                goal="make the suite green",
                stuck_at="the same assertion fails every cycle",
                workspace=str(workspace),
                origin_runner="gemini-cli",
                sandbox="none",
                session_key="loop-42",
                consumer="loop_watchdog",
                attempts=("AssertionError: expected 3, got 4",),
                files_touched=("app.py",),
                brief_dir=str(home / "briefs"),
                timeout_secs=5.0,
            )
        )
    finally:
        service_mod.SubagentProposerBackend = original  # type: ignore[assignment]
    return outcome, manager


def test_service_rejects_an_unverified_handoff_and_accepts_a_verified_one(
    isolated_home: Path, tmp_path: Path
) -> None:
    """End to end through ``run_second_opinion``: the SAME fake proposer making the SAME claim is
    rejected when it did not write and accepted when it did. That pairing is the vacuity floor —
    an always-reject service passes the first half alone."""
    _mark_healthy("gemini-cli")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("original\n", encoding="utf-8")

    rejected, manager = _run_handoff(ws, write=False, home=isolated_home)
    assert manager.spawned, "the fallback backend never fired"
    assert not rejected.accepted  # type: ignore[attr-defined]
    assert "not on disk" in rejected.rejection  # type: ignore[attr-defined]
    assert rejected.result.claimed_paths == ("app.py",)  # type: ignore[attr-defined]

    accepted, _ = _run_handoff(ws, write=True, home=isolated_home)
    assert accepted.accepted, accepted.rejection  # type: ignore[attr-defined]
    assert accepted.result.diff_verified  # type: ignore[attr-defined]


def test_the_handoff_writes_a_sel_row_for_the_fire_and_the_verdict(
    isolated_home: Path, tmp_path: Path
) -> None:
    """ "SEL-audited" means a row is readable back out of the log — not that a call site exists."""
    from personalclaw.sel import sel

    _mark_healthy("gemini-cli")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("original\n", encoding="utf-8")
    before = len(sel().recent(limit=500))

    outcome, _ = _run_handoff(ws, write=False, home=isolated_home)

    rows = sel().recent(limit=500)
    assert len(rows) > before, "no SEL row was written for the handoff at all"
    fires = [r for r in rows if r.get("operation") == SEL_OP_FIRE]
    verdicts = [r for r in rows if r.get("operation") == SEL_OP_VERDICT]
    assert fires, f"no {SEL_OP_FIRE} row: {[r.get('operation') for r in rows]}"
    assert verdicts, f"no {SEL_OP_VERDICT} row: {[r.get('operation') for r in rows]}"
    verdict = verdicts[-1]
    assert verdict["outcome"] == "denied", verdict
    assert verdict["metadata"]["origin_runner"] == "gemini-cli"
    assert verdict["metadata"]["diff_verified"] == "False"
    assert verdict["metadata"]["missing"] == "app.py"
    assert not outcome.accepted  # type: ignore[attr-defined]


def test_the_brief_is_written_and_carries_a_fenced_verbatim_error(
    isolated_home: Path, tmp_path: Path
) -> None:
    """The brief file exists, names the claim protocol, and fences the transcript excerpt."""
    _mark_healthy("gemini-cli")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("original\n", encoding="utf-8")
    outcome, _ = _run_handoff(ws, write=True, home=isolated_home)
    brief_path = Path(outcome.brief_path)  # type: ignore[attr-defined]
    assert brief_path.is_file(), brief_path
    body = brief_path.read_text(encoding="utf-8")
    assert "AssertionError: expected 3, got 4" in body
    assert CLAIM_MARKER in body
    # The excerpt is fenced, i.e. it is not pasted in as bare instructions.
    assert body.index("AssertionError") > body.index("## What was already tried")
    assert "untrusted" in body.lower() or "data" in body.lower()


def test_the_prepared_invocation_inherits_the_stalled_runs_sandbox_class(
    isolated_home: Path, tmp_path: Path
) -> None:
    """ "inside the same sandbox class": the sandbox name flows brief → prepared → spawn kwargs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    brief = build_brief(
        goal="g", stuck_at="s", workspace=str(ws), origin_runner="codex", sandbox="docker"
    )
    manager = _FakeManager(ws, claim="app.py", write=True)
    backend = SubagentProposerBackend(timeout_secs=1.0, poll_secs=0.01, manager=manager)
    prepared = asyncio.run(backend.prepare(brief))
    assert prepared.sandbox == "docker"
    asyncio.run(backend.invoke(prepared))
    assert manager.spawned[0]["sandbox"] == "docker", manager.spawned[0]


def test_both_backends_satisfy_the_four_member_contract() -> None:
    """The Protocol is runtime-checkable, so the contract is asserted, not just documented."""
    from personalclaw.agents.runners import catalog

    subagent = SubagentProposerBackend()
    runner = RunnerProposerBackend(catalog()["gemini-cli"])
    for backend in (subagent, runner):
        assert isinstance(backend, ProposerBackend), backend
        for member in ("name", "prepare", "invoke", "collect"):
            assert hasattr(backend, member), f"{backend} is missing {member}"


def test_an_undeclared_dialect_refuses_to_prepare_instead_of_guessing_a_flag() -> None:
    """``kiro`` has no declared non-interactive form, so its backend refuses rather than firing
    an interactive process that would block on a TTY and time out."""
    from personalclaw.agents.runners import catalog
    from personalclaw.proposer.backends import ProposerUnavailable
    from personalclaw.proposer.dialects import declared_dialects, one_shot

    assert one_shot("", "kiro") is None
    # Floor: the dialect table is not empty — three runners DO have a declared form.
    assert set(declared_dialects()) == {"claude-code", "codex", "gemini-cli"}
    brief = build_brief(goal="g", stuck_at="s", workspace=".", origin_runner="codex")
    with pytest.raises(ProposerUnavailable):
        asyncio.run(RunnerProposerBackend(catalog()["kiro"]).prepare(brief))


def test_result_records_round_trip_as_json() -> None:
    """The normalised record is what the cockpit and the ledger read, so it must serialise."""
    result = normalise(backend="b", runner_id="r", ok=True, text="x", baseline=None)
    assert json.loads(json.dumps(result.to_dict()))["diff_verified"] is False
