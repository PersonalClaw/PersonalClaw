"""Accepting a learning proposal INSTALLS the thing, or refuses (#677).

**Measured on ``origin/main`` @ ``f292be7b1``** before writing any of this — one proposal per kind
enqueued through the real queue into an isolated ``PERSONALCLAW_HOME``, then accepted through the
real ``POST /api/learning/proposals/{id}/accept`` on a real gateway:

* ``skill`` → 200 ``accepted``, and ``skills/auto/rebuild-the-spa/SKILL.md`` was written. The
  headline of #677 was already fixed by the time the issue was worked.
* ``knowledge_draft`` → 200 ``accepted``, ``workspace/knowledge/knowledge.db`` ``items`` = 0 rows.
* ``retirement`` → 200 ``accepted``, and the ``skills/auto/doomed/SKILL.md`` it proposed retiring
  was still there.
* ``tier_migration`` / ``template`` → 200 ``accepted``, nothing on disk.

All four recorded an ``accepted`` decision, after which re-filing the identical proposal returned
``SKIP`` / ``"already accepted"`` — the change was suppressed forever having never been applied.
So the SAME defect the issue names was live for four kinds beside the one it names.

The durable half is why the installer was injectable at all: ``accept(pid, installer=None)`` SKIPPED
the install when nobody passed one, and the dispatch was spelled twice (the dashboard handler and a
local default inside ``proposals_contract``, already divergent). This suite pins both halves:

* the store RESOLVES its installer (``personalclaw.learning.installers``), so an un-injected accept
  installs — :class:`TestAnUninjectedAcceptInstalls`;
* a kind nothing writes is a REFUSAL that records no decision — :class:`TestAnUninstallableKind`;
* a failed install still reports failure — :class:`TestAFailedInstall`;
* and the vacuity floor: reject writes nothing, a nothing-to-install kind writes nothing, so an
  "install on every path" implementation reds here — :class:`TestVacuityFloor`.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import learning as L
from personalclaw.learning import installers
from personalclaw.learning import proposals as P
from personalclaw.learning import skill_promotion as SP
from personalclaw.skills.loader import skills_dir

_PROCEDURE = "1. Run make web-build.\n2. Restart the gateway.\n3. Reload the dashboard."

#: The kinds with a live producer and no installer anywhere in the tree. Each one is a proposal a
#: user can see in the inbox today and click Accept on.
UNINSTALLABLE_KINDS = ("retirement", "tier_migration", "template", "knowledge_draft")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The queue AND every store an install writes, under tmp_path. NEVER the real home.

    `PERSONALCLAW_HOME` is set rather than only patching the loader symbol, because `SkillsLoader`
    binds `config_dir` at import; only the env var — re-read live on every `config_dir()` call —
    isolates the skills tree an accepted promotion actually writes into.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    monkeypatch.setattr(P, "_audit", lambda operation, prop, outcome: None)
    return tmp_path


def _skill_proposal(**over):
    """One PENDING `Kind.SKILL` row, through the real producer rather than a hand-built record."""
    kwargs = {
        "name": "rebuild the spa",
        "description": "How to rebuild the SPA before serving",
        "procedure": _PROCEDURE,
        "rationale": "We worked this out twice and it recurs on every frontend change",
        "session_key": "s-1",
        "transcript": [{"role": "user", "content": "rebuild the spa"}],
    }
    kwargs.update(over)
    result = SP.promote(**kwargs)
    assert result.filed, result.refusal
    return result.proposal


def _filed(kind: str, *, title: str = "a change", body: str = "the change body", **over):
    """One PENDING row of *kind*, through the queue's own public filing path."""
    fields = {"provenance": "human", "target": "something"}
    fields.update(over)
    verdict, prop = P.enqueue(kind=kind, title=title, body=body, **fields)
    assert prop is not None, verdict
    return prop


def _installed_skill() -> "object":
    return skills_dir() / "auto" / "rebuild-the-spa" / "SKILL.md"


def _tree(root) -> list[str]:
    """Every INSTALLED file under *root*, home-relative and sorted.

    The queue's own bookkeeping (`learning/proposals/`) is excluded: deciding a proposal always
    unlinks its row and rewrites `decisions.json`, and counting that as an install would make the
    vacuity floor tautologically red. Everything a real installer writes is elsewhere.
    """
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "learning/proposals/" not in p.relative_to(root).as_posix()
    )


def _req(pid: str, *, user: str | None = "me", app: str | None = None):
    request = make_mocked_request(
        "POST",
        f"/api/learning/proposals/{pid}/accept",
        match_info={"id": pid},
        app=web.Application(),
    )
    if user:
        request["user"] = user
    if app:
        request["app"] = app
    return request


def _route(pid: str, **kw):
    import asyncio

    resp = asyncio.run(L.api_learning_proposal_accept(_req(pid, **kw)))
    return resp.status, json.loads(resp.body.decode())


# ── the reported defect: nobody has to pass an installer ─────────────────────


class TestAnUninjectedAcceptInstalls:
    """`accept(pid)` with NO installer must WRITE. This is the whole issue."""

    def test_accepting_a_skill_writes_the_skill_file(self, home) -> None:
        """The artifact, not a return value: #677's own measurement was a 200 beside an empty tree.

        Asserted with no `installer=` argument at all, which is exactly the call the dashboard
        route made when the accept installed nothing.
        """
        prop = _skill_proposal()
        assert not _installed_skill().exists()

        P.accept(prop.id, actor="user")

        written = _installed_skill()
        assert written.exists(), _tree(home)
        content = written.read_text(encoding="utf-8")
        assert "3. Reload the dashboard." in content
        assert "source: auto" in content

    def test_the_dashboard_route_writes_the_skill_file(self, home) -> None:
        """End to end through the real handler — the surface the issue was filed against."""
        prop = _skill_proposal()

        status, body = _route(prop.id)

        assert status == 200 and body["ok"] is True
        assert body["proposal"]["status"] == "accepted"
        assert _installed_skill().exists(), _tree(home)

    def test_the_dashboard_installer_delegates_to_the_one_owner(self, home) -> None:
        """`_installer_for` must carry no dispatch of its own.

        It used to spell the four branches itself, and `proposals_contract` spelled three of them
        again — so which entity Approve wrote depended on which surface was clicked. This reds if a
        second dispatch grows back in the handler.
        """
        seen: list[tuple[str, object]] = []
        monkeypatched = pytest.MonkeyPatch()
        monkeypatched.setattr(
            installers, "install", lambda prop, *, service=None: seen.append((prop.kind, service))
        )
        try:
            L._installer_for(_req("p1"))(_skill_proposal())
        finally:
            monkeypatched.undo()

        assert [kind for kind, _svc in seen] == ["skill"]

    def test_the_self_model_branch_is_refused_without_a_memory_store(self, home) -> None:
        """A principle nobody could write must not record an accept.

        The handler used to call this a best-effort deferral. A deferral that records a decision
        suppresses its own retry forever, which is the same bug in a smaller blast radius — so it
        is a refusal, and the row survives.
        """
        prop = _filed("lesson_batch", source_cadence="self_model", body="Answer briefly by default")

        with pytest.raises(P.AcceptError, match="no memory store is reachable"):
            P.accept(prop.id, actor="user")
        assert P.get(prop.id) is not None
        assert prop.fingerprint not in P.load_decisions()


# ── the whole class: four sibling kinds nothing can install ──────────────────


class TestAnUninstallableKind:
    @pytest.mark.parametrize("kind", UNINSTALLABLE_KINDS)
    def test_it_is_refused_rather_than_recorded(self, home, kind) -> None:
        """Measured on main: each of these answered 200 `accepted` and wrote nothing."""
        prop = _filed(kind, title=f"a {kind} change")

        with pytest.raises(P.NoProposalInstallerError, match=kind):
            P.accept(prop.id, actor="user")

        assert P.get(prop.id) is not None
        assert P.get(prop.id).status == "pending"
        assert prop.fingerprint not in P.load_decisions()

    @pytest.mark.parametrize("kind", UNINSTALLABLE_KINDS)
    def test_the_refusal_leaves_the_change_re_filable(self, home, kind) -> None:
        """The harm #677 names: an accept that installed nothing blocked every future re-file.

        `_prior_decision_blocks` returned "already accepted" for a change that never happened, so
        the proposer could never raise it again. Retryability is the property, not the status code.
        """
        prop = _filed(kind, title=f"a {kind} change")
        with pytest.raises(P.NoProposalInstallerError):
            P.accept(prop.id, actor="user")

        assert P._prior_decision_blocks(prop.fingerprint, P.load_decisions()) == ""

    @pytest.mark.parametrize("kind", UNINSTALLABLE_KINDS)
    def test_the_route_answers_409_and_not_200(self, home, kind) -> None:
        """409, not 403: a reviewer told "forbidden" goes hunting for a setting to open."""
        prop = _filed(kind, title=f"a {kind} change")

        status, body = _route(prop.id)

        assert status == 409, body
        assert "ok" not in body
        assert kind in body["error"] and "still pending" in body["error"]

    def test_the_gate_still_outranks_the_installer_check(self, home) -> None:
        """An agent accepting an uninstallable kind is a PERMISSION refusal (403), not a 409.

        Order matters: reporting the actor problem as "unsupported kind" would tell an app-scoped
        token that the route would have worked for it once an installer landed.
        """
        prop = _filed("retirement")

        status, body = _route(prop.id, user=None, app="acme-app")

        assert status == 403 and "never accept" in body["error"]
        assert P.get(prop.id).status == "pending"


# ── a failed install never reports success ──────────────────────────────────


class TestAFailedInstall:
    def test_a_skill_that_cannot_be_written_is_not_recorded(self, home) -> None:
        """The installer raises (name already taken); accept must not claim it landed."""
        first = _skill_proposal()
        P.accept(first.id, actor="user")
        second = _skill_proposal(
            procedure="1. A different route to the same result.",
            rationale="A second attempt at the same skill name entirely",
        )

        with pytest.raises(P.AcceptError, match="install failed"):
            P.accept(second.id, actor="user")

        assert second.fingerprint not in P.load_decisions()
        assert P.get(second.id) is not None

    def test_the_route_does_not_report_ok_for_a_failed_install(self, home) -> None:
        first = _skill_proposal()
        P.accept(first.id, actor="user")
        second = _skill_proposal(
            procedure="1. A different route to the same result.",
            rationale="A second attempt at the same skill name entirely",
        )

        status, body = _route(second.id)

        assert status != 200 and "ok" not in body
        assert "install failed" in body["error"]

    def test_a_failed_install_is_distinguishable_from_an_unsupported_kind(self, home) -> None:
        """Both leave the row pending, but only one is worth retrying unchanged.

        A `NoProposalInstallerError` says "come back when an installer exists"; a plain
        `AcceptError` says "this write broke". Collapsing them would make the 409 unreadable.
        """
        assert issubclass(P.NoProposalInstallerError, P.AcceptError)
        assert not isinstance(P.AcceptError("x"), P.NoProposalInstallerError)


# ── the other caller of accept ───────────────────────────────────────────────


class TestTheInboxApplyPathInstallsToo:
    def test_a_skill_promotion_applied_from_the_inbox_writes_the_skill(self, home) -> None:
        """`proposals_contract` reaches the same `accept`, so it must install the same thing.

        Called with no installer, which is the shape a caller that forgot one produces — the
        resolved dispatch is what makes that harmless now.
        """
        import asyncio

        from personalclaw import proposals_contract as pc

        prop = _skill_proposal()
        proposal = pc.Proposal(
            title="Install the promoted skill",
            provenance="learning",
            apply={"skill_promotion": {"pid": prop.id}},
        )

        outcome = asyncio.run(pc.apply_proposal(proposal, item_id="i-1", actor="user"))

        assert outcome.ok, outcome.error
        assert _installed_skill().exists(), _tree(home)


# ── vacuity floor ───────────────────────────────────────────────────────────


class TestVacuityFloor:
    """A fix that installed on every path would pass every test above. These red on it."""

    def test_rejecting_a_skill_proposal_installs_nothing(self, home) -> None:
        """Reject is the sibling action, and #676/#677 sit on the same row of the same UI."""
        prop = _skill_proposal()
        before = _tree(home)

        assert P.reject(prop.id, actor="user") is True

        assert not _installed_skill().exists()
        assert _tree(home) == before
        assert P.load_decisions()[prop.fingerprint].verdict == "rejected"

    def test_a_refused_kind_writes_nothing_at_all(self, home) -> None:
        """Not just "no skill" — no file anywhere. An install-everything dispatch reds here."""
        prop = _filed("retirement", title="retire the unused thing")
        before = _tree(home)

        with pytest.raises(P.NoProposalInstallerError):
            P.accept(prop.id, actor="user")

        assert _tree(home) == before

    def test_a_lesson_batch_accept_writes_no_skill_and_no_context_file(self, home) -> None:
        """`lesson_batch` is claimed and writes NOTHING, by design: a correction-derived lesson is
        already in the lesson store. A dispatch that fell through to the skill writer for every
        unmatched kind would write `skills/auto/...` here."""
        prop = _filed("lesson_batch", title="prefer uv", body="uv resolves lockfiles determinist.")

        assert P.accept(prop.id, actor="user").status == "accepted"

        assert list((home / "skills" / "auto").glob("*")) == []

    def test_the_nothing_to_install_set_is_explicit(self) -> None:
        """Pinned so a kind cannot join it by accident.

        `NOTHING_TO_INSTALL` is the one place "accepting this writes nothing" is a correct answer;
        every other unmatched kind refuses. A regression that widened it would turn four honest
        409s back into four silent 200s.
        """
        assert installers.NOTHING_TO_INSTALL == frozenset({"lesson_batch", "template_diff"})
        for kind in UNINSTALLABLE_KINDS:
            assert installers.branch_for({"kind": kind}) == ""
