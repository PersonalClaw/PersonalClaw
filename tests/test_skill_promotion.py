"""Retroactive run/conversation → skill promotion (LEARN E1.3 — WF2LEA-11).

The clauses of the contract, each pinned against the REAL proposal queue, run store and
SkillsLoader (monkeypatched to a tmp home), not hand-built state:

1. a successful run — or a conversation — promotes to a `Kind.SKILL` proposal in the §2.2 queue,
   carrying the rationale a human reads before deciding;
2. the promotion writes NOTHING to the live skills tree, prompted or unprompted, and the proposing
   agent cannot accept its own row — the skill appears only through the human accept;
3. accepting installs exactly the one skill, so the path is not a queue that ends nowhere;
4. a REJECTED promotion re-attempted is SKIPped and adds no row (the queue's decision memory).
"""

from __future__ import annotations

import pytest

from personalclaw.learning import proposals as P
from personalclaw.learning import skill_promotion as SP
from personalclaw.learning.proposals import Kind, Status
from personalclaw.skills.loader import skills_dir
from personalclaw.workflows import store as run_store
from personalclaw.workflows.models import RunStatus, WorkflowRun

_PROCEDURE = "1. Fetch the feed.\n2. Render the report.\n3. Publish and verify it."


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Point the queue, the run store and the skills tree at a tmp home. NEVER the real one.

    `PERSONALCLAW_HOME` is set (not just the loader symbol patched) because `SkillsLoader` and the
    run store bind `config_dir` at import; only the env var — which `config_dir()` re-reads live on
    every call — isolates every store a promotion touches, not just the proposal queue.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    monkeypatch.setattr(P, "_audit", lambda operation, prop, outcome: None)
    return tmp_path


def _promote(**over):
    kwargs = {
        "name": "publish the nightly report",
        "description": "Build and publish the nightly report end to end",
        "procedure": _PROCEDURE,
        "rationale": "We worked this out from scratch and it recurs nightly",
        "session_key": "s-1",
    }
    kwargs.update(over)
    return SP.promote(**kwargs)


def _completed_run() -> WorkflowRun:
    run = run_store.create(WorkflowRun(id="", workflow_name="nightly"))
    run.status = RunStatus.COMPLETE
    return run_store.save(run)


def _skill_files(root) -> list[str]:
    """Every SKILL.md under the tmp home's skills tree, as home-relative paths."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("SKILL.md"))


# ── clause 1: a promotion reaches the one queue ──────────────────────────────


class TestPromotionFiles:
    def test_a_conversation_promotes_to_a_skill_proposal(self) -> None:
        """The reserved `Kind.SKILL` slot gets a writer: a conversation becomes one PENDING row."""
        result = _promote(transcript=[{"role": "user", "content": "publish tonight's report"}])

        assert result.filed, result.refusal
        prop = result.proposal
        assert prop.kind == Kind.SKILL.value
        assert prop.status == Status.PENDING.value
        # The rationale is the title (what a reviewer reads); the procedure is the body (what an
        # accept writes). Conflated, the rationale would leak into the installed SKILL.md.
        assert prop.title == "We worked this out from scratch and it recurs nightly"
        assert prop.body == _PROCEDURE
        assert SP._decode_target(prop.target) == (
            "publish-the-nightly-report",
            "Build and publish the nightly report end to end",
        )
        assert [p.id for p in P.list_pending(kind=Kind.SKILL.value)] == [prop.id]

    def test_a_completed_run_promotes_and_points_back_at_it(self) -> None:
        run = _completed_run()

        result = _promote(run_id=run.id)

        assert result.filed, result.refusal
        assert result.proposal.run_id == run.id
        assert result.proposal.evidence_refs == [run.id]

    def test_an_unfinished_run_is_refused(self) -> None:
        """ "Promote what worked" is only meaningful if the STORE decides what worked."""
        run = run_store.create(WorkflowRun(id="", workflow_name="nightly"))

        result = _promote(run_id=run.id)

        assert result.refusal == SP.Refusal.RUN_NOT_SUCCESSFUL.value
        assert P.list_pending() == []

    def test_an_unknown_run_is_refused(self) -> None:
        result = _promote(run_id="run-does-not-exist")

        assert result.refusal == SP.Refusal.RUN_NOT_FOUND.value
        assert P.list_pending() == []

    def test_a_promotion_without_a_rationale_is_refused(self) -> None:
        """A row a reviewer cannot weigh is worse than no row — weighing it is the queue's job."""
        result = _promote(rationale="  ")

        assert result.refusal == SP.Refusal.NEEDS_RATIONALE.value
        assert P.list_pending() == []

    def test_an_unusable_name_is_refused_before_filing(self) -> None:
        """The install rail rejects the slug, so filing it would queue an un-acceptable row."""
        result = _promote(name="!!!")

        assert result.refusal == SP.Refusal.UNUSABLE_NAME.value
        assert P.list_pending() == []

    def test_a_failed_write_is_not_reported_as_a_decision(self, monkeypatch) -> None:
        """`enqueue` returns SKIP for a prior decision AND for a write it could not complete.

        Reporting the second as "you already decided this" would send the agent away from a retry
        it should make, so the two refusals are told apart by asking decision memory directly.
        """
        monkeypatch.setattr(P, "_save", lambda prop: False)

        result = _promote()

        assert result.refusal == SP.Refusal.QUEUE_REFUSED.value
        assert P.load_decisions() == {}

    def test_the_row_is_not_flagged_for_a_missing_manifest(self) -> None:
        """`enqueue` flags a manifest-less `skill` proposal, so a promotion must carry one or every
        promotion renders permanently warning in the inbox."""
        prop = _promote().proposal

        assert prop.manifest_valid
        assert prop.manifest_issues == []


# ── clause 2: propose, never write ──────────────────────────────────────────


class TestNeverWrites:
    def test_promotion_writes_no_skill(self, home) -> None:
        before = _skill_files(home)

        assert _promote().filed
        assert _skill_files(home) == before
        assert not (skills_dir() / "auto" / "publish-the-nightly-report").exists()

    def test_an_unprompted_promotion_also_writes_nothing(self, home) -> None:
        """Unprompted is the same call with no transcript: one reviewable row, no skill."""
        result = _promote(transcript=None, session_key="")

        assert result.filed, result.refusal
        assert result.proposal.provenance == "inferred"
        assert _skill_files(home) == []

    def test_the_proposing_agent_cannot_accept_its_own_row(self, home) -> None:
        """The gate, not an absence of callers, is what keeps the agent out of the write path."""
        prop = _promote().proposal

        with pytest.raises(P.AcceptError):
            P.accept(prop.id, installer=_installer, actor="agent")
        assert _skill_files(home) == []
        assert [p.id for p in P.list_pending()] == [prop.id]


def _installer(prop) -> None:
    """The dashboard handler's dispatch, reproduced at its one relevant branch."""
    data = prop.to_dict()
    if SP.is_skill_promotion_proposal(data):
        SP.install_accepted_skill(data)


# ── clause 3: the human accept installs, so the queue is not a dead end ─────


class TestAcceptInstalls:
    def test_accepting_writes_exactly_one_auto_skill(self, home) -> None:
        prop = _promote().proposal

        P.accept(prop.id, installer=_installer, actor="user")

        written = skills_dir() / "auto" / "publish-the-nightly-report" / "SKILL.md"
        assert _skill_files(home) == [str(written.relative_to(home))]
        content = written.read_text(encoding="utf-8")
        # Frontmatter from the existing auto-skill rail — the description is what makes the
        # promoted skill discoverable on a later turn, and `source: auto` is what ages it.
        assert "description: Build and publish the nightly report end to end" in content
        assert "source: auto" in content
        assert "3. Publish and verify it." in content
        # The rationale is review-only; it must not reach the installed file.
        assert "worked this out from scratch" not in content

    def test_a_failed_install_does_not_record_the_decision(self, home) -> None:
        """A name already taken must stay retryable, not silently suppress its own re-proposal.

        The second promotion carries a DIFFERENT procedure on purpose: the same one would be
        suppressed by the first accept's own decision record (the fingerprint is kind + target +
        body), which is the anti-nag path, not the install-collision path this pins.
        """
        first = _promote().proposal
        P.accept(first.id, installer=_installer, actor="user")
        second = _promote(
            procedure="1. A different way to do the same job.",
            rationale="Second attempt at the same name",
        ).proposal
        assert second is not None

        with pytest.raises(P.AcceptError):
            P.accept(second.id, installer=_installer, actor="user")
        assert second.fingerprint not in P.load_decisions()


# ── clause 4: a rejected promotion does not re-surface ──────────────────────


class TestDecisionMemory:
    def test_a_rejected_promotion_is_not_re_proposed(self, home) -> None:
        prop = _promote().proposal
        assert P.reject(prop.id, actor="user")

        again = _promote()

        assert again.proposal is None
        assert again.refusal == SP.Refusal.ALREADY_DECIDED.value
        assert P.list_pending() == []
        assert _skill_files(home) == []
