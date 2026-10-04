"""A proposal about any skill can be reviewed: listed, opened, accepted and rejected.

A skill is named by its folder in the library, so most names hold a slash: an imported skill is
``imported/<source>/<name>``, an auto-created one ``auto/<name>``, a skill kept in a folder of its
own ``<folder>/<name>``, and a folder can be named with any characters at all. The proposal id was
the skill's name with a digest on the end, so a refinement of any of those skills got an id with
slashes in it. Its record was written into a subfolder the queue's listing never reads, and every
route that takes an id refused the id: the Skills queue never showed the proposal, its Inbox row
could not be opened, accepted or rejected, and the anti-flood rail, which reads the same listing,
let the next stumble on that skill file another one.

The id is now built for the store, from the letters and digits of the name plus a digest of the
whole name, and the record keeps the name. Every read and write goes through the store's one path
builder, so no name, however it is spelled, puts a record anywhere but the proposals folder.

These drive the product's own paths: the import's install gate puts the skill in the library, the
stumble arm files the proposal, and the dashboard's routes, behind the gate that refuses an unsafe
id, review it. The Inbox row is answered through the Inbox's own handler.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.record_ids import is_safe_record_id
from personalclaw.skills import overlays, proposals, refine
from personalclaw.skills.loader import SkillsLoader
from personalclaw.skills.marketplace import verify_skill_integrity

SOURCE = "other-tool"
NAME = "incident-writeup"
IMPORTED = f"imported/{SOURCE}/{NAME}"
SKILL_MD = (
    "---\nname: incident-writeup\ndescription: Write up an incident for the team\n---\n\n"
    "Start from the template, then fill in every section.\n"
)
CORRECTION = "No, put the timeline before the summary."
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home for the library, the proposal queue, the overlays and the inbox."""
    import personalclaw.config.loader as cfg
    import personalclaw.skills.loader as sl
    import personalclaw.skills.marketplace as mp

    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(sl, "config_dir", lambda: root)
    monkeypatch.setattr(cfg, "config_dir", lambda: root)
    monkeypatch.setattr(mp, "skill_discovery_paths", lambda: [])
    assert root in proposals._proposals_dir().parents, "the proposal queue was not redirected"
    assert root in overlays.overlays_dir().parents, "the overlays were not redirected"
    return root


def _import(home: Path) -> Path:
    """Bring a skill over the way the import does: through the install gate, into
    ``imported/<source>/<name>/``, with the lock file that baselines its bytes."""
    from personalclaw.onboarding_import.sources.common import ImportedSkillMarketplace
    from personalclaw.onboarding_import.writers import imported_skills_dir
    from personalclaw.skills.marketplace import install_scanned

    src = home.parent / "other-tool-skills" / NAME
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    target = imported_skills_dir(SOURCE)
    target.mkdir(parents=True, exist_ok=True)
    install_scanned(ImportedSkillMarketplace(src), f"import:{SOURCE}", NAME, target)
    skill_dir = target / NAME
    assert skill_dir == home / "skills" / "imported" / SOURCE / NAME
    assert verify_skill_integrity(skill_dir).ok and not verify_skill_integrity(skill_dir).unlocked
    return skill_dir


def _place(home: Path, name: str) -> None:
    """A skill the user keeps at ``skills/<name>/``, nested or not."""
    d = home / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a skill of the user's own\n---\n\nDo it well.\n",
        encoding="utf-8",
    )


def _stumble(skill: str, *, said: str = CORRECTION, session: str = "chat-1"):
    """A turn that used *skill* and was corrected, through the real stumble arm."""
    return refine.propose_refinement(
        trigger="correction", skill=skill, user_message=said, session_key=session, now=NOW
    )


def _load(name: str) -> str | None:
    return SkillsLoader(install_builtins=False).load_skill(name)


def _app() -> web.Application:
    """The Skills queue's four routes behind the gate that answers an unsafe id with a 400."""
    from personalclaw.dashboard.handlers import skills as h
    from personalclaw.dashboard.invalid_id_gate import invalid_id_middleware

    app = web.Application(middlewares=[invalid_id_middleware()])
    app.router.add_get("/api/skills/proposals", h.api_skill_proposals_list)
    app.router.add_get("/api/skills/proposals/{id}", h.api_skill_proposal_detail)
    app.router.add_post("/api/skills/proposals/{id}/accept", h.api_skill_proposal_accept)
    app.router.add_delete("/api/skills/proposals/{id}", h.api_skill_proposal_reject)
    return app


def _url(pid: str) -> str:
    """The proposal's URL as the dashboard builds it (``encodeURIComponent``)."""
    return f"/api/skills/proposals/{quote(pid, safe='')}"


def _live(monkeypatch: pytest.MonkeyPatch):
    """A running gateway's inbox: the store the Inbox page reads and every writer must use."""
    from personalclaw import inbox as ibx
    from personalclaw.inbox_providers import native_source as ns

    class Svc:
        def __init__(self) -> None:
            self.inbox = ibx.InboxStore()
            self.inbox.load()
            self.state = ibx.InboxState()
            self.state.load()

    class State:
        def __init__(self) -> None:
            self._inbox_svc = Svc()
            self.frames: list[tuple[str, dict]] = []

        def notify(self, *a, **k) -> None:
            pass

        def broadcast_ws(self, frame: str, payload: dict) -> None:
            self.frames.append((frame, payload))

    state = State()
    monkeypatch.setattr(ns, "get_dashboard_state", lambda: state)
    return state, state._inbox_svc.inbox


class _Req:
    """What the Inbox handler reads off a request."""

    def __init__(self, state, item_id: str, body: dict) -> None:
        self.app = {"state": state}
        self.match_info = {"id": item_id}
        self._body = body

    async def json(self) -> dict:
        return self._body


def _row_for(store, pid: str):
    rows = [i for i in store.items.values() if i.refs.get("skill_proposal") == pid]
    assert len(rows) == 1, rows
    return rows[0]


# ── listed and opened, whatever the skill is called ────────────────────────────


@pytest.mark.parametrize(
    "name",
    [IMPORTED, "auto/loop-worker", "Team Notes/weekly review"],
    ids=["imported", "auto-created", "a folder of the user's own"],
)
def test_a_refinement_of_any_skill_is_listed_and_opens(home: Path, name: str) -> None:
    _place(home, name)
    prop = _stumble(name)
    assert prop is not None, "the stumble arm filed nothing"

    assert is_safe_record_id(prop.id), prop.id
    assert proposals._path(prop.id).is_file()
    assert proposals._path(prop.id).parent == proposals._proposals_dir()
    assert [p.id for p in proposals.list_pending()] == [prop.id]

    opened = proposals.get(prop.id)
    assert opened is not None
    # The id is the store's; the name the user knows stays on the record.
    assert (opened.kind, opened.slug, opened.refine_target) == ("refine", name, name)


def test_a_refinement_of_a_top_level_skill_still_reads_as_its_name(home: Path) -> None:
    """The control: a skill at the top of the library worked before, and still does."""
    _place(home, "release-flow")
    prop = _stumble("release-flow")
    assert prop is not None and prop.id.startswith("release-flow-")
    assert [p.id for p in proposals.list_pending()] == [prop.id]
    assert proposals.accept(prop.id).name == "release-flow"
    assert "> No, put the timeline before the summary." in (_load("release-flow") or "")


# ── accept refines the imported skill where it lives; reject changes nothing ──


def test_accepting_it_refines_the_imported_skill_where_it_lives(home: Path) -> None:
    skill_dir = _import(home)
    files_before = {p.name: p.read_bytes() for p in skill_dir.iterdir()}
    prop = _stumble(IMPORTED)
    assert prop is not None

    result = proposals.accept(prop.id)

    assert (result.name, result.version) == (IMPORTED, 1)
    body = _load(IMPORTED) or ""
    assert "Start from the template" in body
    assert "> No, put the timeline before the summary." in body
    # The refinement rides beside the imported skill, so its own files and its lock are as the
    # import left them, and reverting it is the deletion of one file.
    assert {p.name: p.read_bytes() for p in skill_dir.iterdir()} == files_before
    assert verify_skill_integrity(skill_dir).ok
    assert overlays.overlay_path(IMPORTED) == (
        home / "skills" / ".overlays" / "imported" / SOURCE / f"{NAME}.json"
    )
    assert overlays.overlay_path(IMPORTED).is_file()
    assert not (home / "skills" / "auto").exists(), "accept minted a skill instead of refining"
    assert proposals.list_pending() == []


@pytest.mark.asyncio
async def test_the_queue_opens_and_accepts_it(home: Path) -> None:
    _import(home)
    prop = _stumble(IMPORTED)
    assert prop is not None
    async with TestClient(TestServer(_app())) as client:
        listed = await (await client.get("/api/skills/proposals")).json()
        assert [p["id"] for p in listed["proposals"]] == [prop.id]
        assert listed["proposals"][0]["refine_target"] == IMPORTED

        opened = await client.get(_url(prop.id))
        assert opened.status == 200, await opened.text()
        detail = await opened.json()
        assert detail["refine_target"] == IMPORTED and detail["version"] == 1
        assert f"+++ {IMPORTED}/SKILL.md" in detail["diff"]
        assert "+> No, put the timeline before the summary." in detail["diff"]

        accepted = await client.post(f"{_url(prop.id)}/accept", json={})
        assert accepted.status == 200, await accepted.text()
        assert await accepted.json() == {"ok": True, "name": IMPORTED, "version": 1}

        listed = await (await client.get("/api/skills/proposals")).json()
        assert listed["proposals"] == []
    assert "> No, put the timeline before the summary." in (_load(IMPORTED) or "")


@pytest.mark.asyncio
async def test_the_queue_rejects_it_and_nothing_changes(home: Path) -> None:
    skill_dir = _import(home)
    files_before = {p.name: p.read_bytes() for p in skill_dir.iterdir()}
    body_before = _load(IMPORTED)
    prop = _stumble(IMPORTED)
    assert prop is not None
    async with TestClient(TestServer(_app())) as client:
        rejected = await client.delete(_url(prop.id))
        assert rejected.status == 200, await rejected.text()
        listed = await (await client.get("/api/skills/proposals")).json()
        assert listed["proposals"] == []
        assert (await client.get(_url(prop.id))).status == 404

    assert _load(IMPORTED) == body_before
    assert {p.name: p.read_bytes() for p in skill_dir.iterdir()} == files_before
    assert overlays.overlay_path(IMPORTED) is not None
    assert not overlays.overlay_path(IMPORTED).exists()


@pytest.mark.parametrize(
    ("target", "minted"),
    [(IMPORTED, "auto/imported-other-tool-incident-writeup"), ("auto/loop-worker", None)],
    ids=["an imported skill", "an auto-created skill"],
)
def test_a_refinement_whose_skill_was_removed_adds_it_as_a_new_skill(
    home: Path, target: str, minted: str | None
) -> None:
    """The review surface says so ("no longer installed, so accepting this would add it as a new
    skill instead"); the new skill could not be named after a slug with slashes in it. An
    auto-created skill that was deleted comes back under its own name."""
    from personalclaw.atomic_write import atomic_write

    prop = proposals.SkillProposal(
        id="a-removed-skill-0123456789ab",
        slug=target,
        description="Refined after you corrected this turn",
        triggers="",
        procedure_md="When this skill applies, put the timeline first.",
        session_key="chat-1",
        created_at="2026-09-30T09:00:00+00:00",
        kind="refine",
        refine_target=target,
        trigger="correction",
    )
    atomic_write(proposals._path(prop.id), json.dumps(prop.to_dict(), indent=2))
    assert _load(target) is None, "the precondition: the skill is gone"

    result = proposals.accept(prop.id)

    assert (result.name, result.version) == (minted or target, 0)
    assert "put the timeline first" in (_load(result.name) or "")
    assert proposals.list_pending() == []


# ── the Inbox row opens it, and answering the row answers it ─────────────────


@pytest.mark.asyncio
async def test_its_inbox_row_opens_and_accepts_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _state, store = _live(monkeypatch)
    _import(home)
    prop = _stumble(IMPORTED)
    assert prop is not None
    row = _row_for(store, prop.id)
    assert row.message.startswith("Refine a skill")

    # The Inbox panel opens and installs the proposal by the id its row carries.
    pid = row.refs["skill_proposal"]
    async with TestClient(TestServer(_app())) as client:
        opened = await client.get(_url(pid))
        assert opened.status == 200, await opened.text()
        accepted = await client.post(f"{_url(pid)}/accept", json={})
        assert accepted.status == 200, await accepted.text()

    assert row.status == "handled"
    assert "> No, put the timeline before the summary." in (_load(IMPORTED) or "")


def test_dismissing_its_inbox_row_rejects_it(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from personalclaw.dashboard import handlers_inbox

    state, store = _live(monkeypatch)
    _import(home)
    body_before = _load(IMPORTED)
    prop = _stumble(IMPORTED)
    assert prop is not None
    row = _row_for(store, prop.id)

    resp = asyncio.run(
        handlers_inbox.api_inbox_update(_Req(state, row.id, {"status": "dismissed"}))
    )

    assert resp.status == 200
    assert proposals.get(prop.id) is None, "the row was answered and the proposal was not"
    assert proposals.list_pending() == []
    assert _load(IMPORTED) == body_before


# ── the anti-flood rail sees it ──────────────────────────────────────────────


def test_a_second_stumble_on_the_skill_waits_for_the_first(home: Path) -> None:
    _import(home)
    first = _stumble(IMPORTED)
    second = _stumble(IMPORTED, said="Also name who was paged.", session="chat-2")

    assert first is not None
    assert second is None, "a second review was filed while the first is still waiting"
    assert [p.id for p in proposals.list_pending()] == [first.id]


# ── no name puts a record anywhere but the proposals folder ──────────────────


@pytest.mark.parametrize(
    "slug",
    ["../incident-writeup", "<root>/elsewhere/incident-writeup", "w" * 400, "Notes: week 2?"],
    ids=["names a parent folder", "absolute", "longer than a file name", "unusual characters"],
)
def test_no_name_puts_a_record_outside_the_proposals_folder(home: Path, slug: str) -> None:
    slug = slug.replace("<root>", str(home.parent))
    prop = proposals.enqueue(
        slug=slug,
        description="Write up an incident",
        triggers="",
        procedure_md="Lead with the timeline.",
        session_key="chat-1",
        created_at="2026-09-30T09:00:00+00:00",
    )

    assert prop is not None, "the proposal was dropped"
    assert is_safe_record_id(prop.id), prop.id
    written = sorted(p for p in home.parent.rglob("*.json") if prop.id[-12:] in p.name)
    assert written == [proposals._proposals_dir() / f"{prop.id}.json"]
    assert proposals.get(prop.id).slug == slug


# ── a proposal filed under the old id is moved, and its row follows ──────────


def _legacy(home: Path, *, n: int, created_at: str):
    """A refine proposal exactly as the old store left it: named ``<skill name>-<digest>``, in
    the subfolders that name made, and raised in the Inbox under that id."""
    import hashlib

    from personalclaw.atomic_write import atomic_write

    session = f"chat-{n}"
    digest = hashlib.sha1(f"{IMPORTED}|{session}|{created_at}".encode()).hexdigest()[:12]
    prop = proposals.SkillProposal(
        id=f"{IMPORTED}-{digest}",
        slug=IMPORTED,
        description="Refined after you corrected this turn",
        triggers="",
        procedure_md=f"When this skill applies, put the timeline first ({n}).",
        session_key=session,
        created_at=created_at,
        kind="refine",
        refine_target=IMPORTED,
        trigger="correction",
    )
    where = proposals._proposals_dir() / f"{prop.id}.json"
    where.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(where, json.dumps(prop.to_dict(), indent=2))
    proposals._surface_in_inbox(prop)
    return prop, where


def test_a_proposal_filed_under_the_old_id_is_moved_and_its_row_follows(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, store = _live(monkeypatch)
    _import(home)
    old, old_file = _legacy(home, n=1, created_at="2026-09-29T08:00:00+00:00")
    row = _row_for(store, old.id)
    row.status = "seen"

    pending = proposals.list_pending()

    assert len(pending) == 1
    moved = pending[0]
    assert is_safe_record_id(moved.id) and moved.id != old.id
    assert proposals._path(moved.id).is_file()
    assert (moved.slug, moved.refine_target, moved.procedure_md, moved.created_at) == (
        old.slug,
        old.refine_target,
        old.procedure_md,
        old.created_at,
    )
    assert not old_file.exists()
    assert not (proposals._proposals_dir() / "imported").exists(), "an emptied folder was left"
    # The SAME row, now pointing at the moved record: still where the user left it, no second
    # row and no second notification.
    assert row.refs["skill_proposal"] == moved.id
    assert row.refs["dedup_key"] == f"skill_proposal:{moved.id}"
    assert row.status == "seen"
    assert [i.id for i in store.items.values() if i.refs.get("skill_proposal")] == [row.id]
    assert ("inbox_item_updated", row.id) in [(f, p.get("id")) for f, p in state.frames]

    # Idempotent: a second read moves nothing and raises nothing.
    frames = len(state.frames)
    assert [p.id for p in proposals.list_pending()] == [moved.id]
    assert len(state.frames) == frames
    assert len(store.items) == 1

    # And the moved proposal is one the user can now act on.
    assert proposals.accept(moved.id).name == IMPORTED
    assert row.status == "handled"


def test_several_old_proposals_all_become_reviewable(home: Path) -> None:
    """A home the anti-flood gap already filled: every stumble on the skill filed one more."""
    _import(home)
    olds = [_legacy(home, n=n, created_at=f"2026-09-2{n}T08:00:00+00:00")[0] for n in range(1, 4)]

    pending = proposals.list_pending()

    assert len(pending) == 3
    assert all(is_safe_record_id(p.id) for p in pending)
    assert sorted(p.procedure_md for p in pending) == sorted(o.procedure_md for o in olds)
    assert sorted(p.name for p in proposals._proposals_dir().iterdir()) == sorted(
        f"{p.id}.json" for p in pending
    )
    # Now visible to the rail: the next stumble waits instead of filing a fourth.
    assert _stumble(IMPORTED, session="chat-9") is None
