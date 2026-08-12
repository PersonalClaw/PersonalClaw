"""Share a chat as a redacted, read-only artifact (SESSION-MANAGEMENT SM-9 / T3.3's
second half).

Three claims are load-bearing here, and each has a test that would fail if it stopped
being true:

* **Redacted** — the artifact body is ``session_export.render_markdown``'s output
  verbatim, so the share inherits the only redaction the ``user``/``system`` roles ever
  get (the dashboard write path skips them). ``test_shared_body_is_the_export_verbatim``
  is what stops a future "share renderer" from redacting slightly less.
* **Read-only** — enforced in the STORE, so the MCP tools and workflow actions hit the
  same refusal the HTTP route does. All three content-mutating methods are covered.
* **Never auto-published** — an AST census of every ``share_session`` call site in
  ``src/personalclaw``. ``test_the_call_site_census_has_teeth`` feeds that same census a
  poisoned source and asserts it flags it, because a structural assertion that cannot
  fail is decoration.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

import personalclaw
from personalclaw import session_search
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.dashboard import session_export as se
from personalclaw.dashboard import session_share as sh
from personalclaw.dashboard import session_starters as ss

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"

MESSAGES = [
    {"role": "user", "content": f"deploy with {AWS_KEY} please", "ts": "2026-08-11T10:00:00Z"},
    {"role": "assistant", "content": "On it.", "ts": "2026-08-11T10:00:01Z"},
    {"role": "stop", "content": "ignored bookkeeping"},
]
META = {"agent": "personalclaw", "model": "claude-opus-5", "created_at": "2026-08-11T09:59:00Z"}


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    """A native store rooted in tmp_path — never the real artifacts dir."""
    return NativeArtifactProvider(root=tmp_path / "artifacts")


# ── the artifact share_session produces ────────────────────────────────────────


def test_shared_body_is_the_export_verbatim(provider):
    """One redaction implementation, two callers. If the bodies ever diverge, one of them
    is redacting less than the other and nobody would notice from the outside."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    expected = se.render_markdown(
        title="Deploy chat", key="dashboard:s1", meta=META, messages=MESSAGES
    )
    stored = provider.get(art.slug)
    assert stored is not None
    assert stored.content == expected


def test_share_redacts_a_user_typed_credential(provider):
    """The security claim. The write path stores `user` content RAW, so if the share path
    skipped redaction the owner's own pasted key would land in a durable artifact."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    stored = provider.get(art.slug)
    assert stored is not None
    assert AWS_KEY not in stored.content
    assert "[REDACTED" in stored.content


def test_share_redacts_the_title_into_the_artifact_name(provider):
    """An auto-titled chat can carry the secret in its title — the artifact NAME is shown
    in the library list, so it leaks in a place the body redaction never covers."""
    art = sh.share_session(
        provider, key="dashboard:s1", title=f"key {AWS_KEY}", meta=META, messages=MESSAGES
    )
    assert AWS_KEY not in art.name
    assert art.name.endswith("(shared chat)")


def test_shared_artifact_declares_what_it_is(provider):
    """Kind, source, tag and event provenance. A markdown TEXT kind (never executed by a
    renderer), sourced `manual` (an owner action, not an automated producer), and an event
    that records which chat it came from and that the body is redacted."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    assert art.kind == "markdown"
    assert art.source == "manual"
    assert sh.SHARE_TAG in art.tags
    assert art.readonly is True
    ev = art.events[0]
    assert ev.type == "created"
    assert ev.metadata["shared_session"] == "dashboard:s1"
    assert ev.metadata["redacted"] is True
    assert "redacted" in art.description.lower()


def test_resharing_writes_a_new_record_not_a_new_version(provider):
    """Deliberately not an upsert: an upsert would have to MUTATE a readonly artifact, and
    two shares taken at different points are two records, not two versions of one."""
    a = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    b = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    assert a.slug != b.slug
    assert a.version == b.version == 1


# ── read-only is enforced by the STORE ─────────────────────────────────────────


def test_update_refuses_a_readonly_artifact(provider):
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    with pytest.raises(PermissionError, match="read-only"):
        provider.update(art.slug, content="rewritten history")
    stored = provider.get(art.slug)
    assert stored is not None
    assert "rewritten history" not in (stored.content or "")


def test_metadata_only_update_is_refused_too(provider):
    """One rule, not a half-mutable artifact: a record whose name/tags can be rewritten is
    a record whose provenance can be rewritten."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    with pytest.raises(PermissionError, match="read-only"):
        provider.update(art.slug, name="Verbatim transcript", tags=["not-redacted"])


def test_revert_refuses_a_readonly_artifact(provider):
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    with pytest.raises(PermissionError, match="read-only"):
        provider.revert(art.slug, 1)


def test_update_binary_refuses_a_readonly_artifact(provider, tmp_path):
    """``create_binary`` has no ``readonly`` parameter (no writer needs one today), so the
    flag is set on disk here — the point is that the guard is in place BEFORE a future
    writer exists, not that today's callers can reach it."""
    art = provider.create_binary(name="pic", data=b"\x89PNG", mime="image/png")
    meta_file = tmp_path / "artifacts" / art.slug / "meta.json"
    meta_file.write_text(meta_file.read_text().replace('"readonly": false', '"readonly": true'))
    assert provider.get(art.slug).readonly is True  # the flip landed
    with pytest.raises(PermissionError, match="read-only"):
        provider.update_binary(art.slug, data=b"\x89PNGnew", mime="image/png")


def test_readonly_artifact_can_still_be_deleted(provider):
    """Read-only is not undeletable. Trapping the owner's own artifact in their library
    would be a worse bargain than letting them remove a record they created."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    assert provider.delete(art.slug) is True
    assert provider.get(art.slug) is None


def test_readonly_survives_the_meta_roundtrip(provider):
    """The flag is persisted, not just returned from create(): a store restart must not
    silently thaw a frozen artifact."""
    art = sh.share_session(
        provider, key="dashboard:s1", title="Deploy chat", meta=META, messages=MESSAGES
    )
    fresh = NativeArtifactProvider(root=provider._root)
    reread = fresh.get(art.slug)
    assert reread is not None and reread.readonly is True
    assert reread.to_dict()["readonly"] is True


def test_an_ordinary_artifact_is_still_editable(provider):
    """The guard must not have frozen the whole library — a rail that refuses everything
    looks identical to a rail that refuses the right things."""
    art = provider.create(name="scratch", content="v1", kind="markdown")
    assert art.readonly is False
    assert provider.update(art.slug, content="v2") is not None


# ── the route ──────────────────────────────────────────────────────────────────


def _make_app(state, provider) -> web.Application:
    app = web.Application()
    app["state"] = state
    ss.register_routes(app)
    return app


@pytest.fixture
def routed(tmp_path, monkeypatch, provider):
    """A client over the real share/export routes, with the artifact store in tmp_path.

    ``get_provider`` is redirected rather than the registry mutated: the registry is
    process-global, so registering a tmp-rooted provider into it would leak into every
    later test in the session.
    """
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss.registry, "get_provider", lambda name=None: provider)
    state = _make_state(tmp_path)
    log = state.conversation_log
    log.append("dashboard:s1", "user", f"deploy with {AWS_KEY} please")
    log.append("dashboard:s1", "assistant", "On it.")
    log.update_metadata("dashboard:s1", {"title": "Deploy chat", "agent": "personalclaw"})
    return state, provider


@pytest.mark.asyncio
async def test_post_share_creates_the_artifact(routed):
    state, provider = routed
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        resp = await client.post("/api/chat/sessions/s1/share")
        assert resp.status == 201
        body = await resp.json()
    assert body["ok"] is True and body["readonly"] is True and body["redacted"] is True
    stored = provider.get(body["slug"])
    assert stored is not None
    assert AWS_KEY not in stored.content


@pytest.mark.asyncio
async def test_share_is_a_post_not_a_get(routed):
    """A GET that creates durable state is a side effect on a method browsers, prefetchers
    and link previews treat as safe — hovering a link would publish a conversation."""
    state, provider = routed
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        resp = await client.get("/api/chat/sessions/s1/share")
        assert resp.status == 405


@pytest.mark.asyncio
async def test_share_404s_on_an_unknown_conversation(routed):
    state, provider = routed
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        resp = await client.post("/api/chat/sessions/nope/share")
        assert resp.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", sorted(session_search._RESTRICTED_MODES))
async def test_share_refuses_every_restricted_mode(routed, mode):
    """An incognito chat promises to leave nothing durable behind; an artifact is durable
    library state. Parametrized over the WHOLE closed set, read from the source of truth,
    so adding a third restricted mode without wiring it here reds rather than silently
    shipping a mode that can be shared.
    """
    state, provider = routed
    state.conversation_log.update_metadata("dashboard:s1", {"memory_mode": mode})
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        resp = await client.post("/api/chat/sessions/s1/share")
        assert resp.status == 403
        assert provider.list() == []
        # The export/share asymmetry is deliberate — a download is the user holding their
        # own text for a moment — so assert it rather than leaving it to a reader.
        assert (await client.get("/api/chat/sessions/s1/export?format=md")).status == 200


@pytest.mark.asyncio
async def test_a_persistent_chat_is_not_swept_up_by_the_restriction_gate(routed):
    """The vacuity check on the gate above: a normal chat must still share."""
    state, provider = routed
    state.conversation_log.update_metadata("dashboard:s1", {"memory_mode": "persistent"})
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        assert (await client.post("/api/chat/sessions/s1/share")).status == 201


@pytest.mark.asyncio
async def test_share_reports_unavailable_artifacts_instead_of_500ing(routed, monkeypatch):
    state, provider = routed
    monkeypatch.setattr(ss.registry, "get_provider", lambda name=None: None)
    async with TestClient(TestServer(_make_app(state, provider))) as client:
        resp = await client.post("/api/chat/sessions/s1/share")
        assert resp.status == 503


# ── never auto-published: an AST census of every call site ─────────────────────


def _share_call_sites(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``{(file, enclosing function)}`` for every call to ``share_session``.

    Counts both ``session_share.share_session(...)`` and a bare ``share_session(...)``, so
    an ``from ... import share_session`` cannot slip past the attribute form.
    """
    found: set[tuple[str, str]] = set()
    for label, src in sources.items():
        tree = ast.parse(src)
        parents: dict[ast.AST, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    parents.setdefault(node, fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = (
                f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            )
            if name == "share_session":
                found.add((label, parents.get(node, "<module>")))
    return found


def _core_sources() -> dict[str, str]:
    root = Path(personalclaw.__file__).resolve().parent
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))
    }


def test_share_has_exactly_one_call_site():
    """The "never auto-published" proof. A behavioural test only covers the paths it
    drives; this covers the ones it doesn't — a heartbeat tick, a post-turn hook or a
    "share it while we're here" convenience reds HERE rather than quietly publishing
    conversations in production.
    """
    assert _share_call_sites(_core_sources()) == {
        ("dashboard/session_starters.py", "api_session_share")
    }


def test_the_call_site_census_has_teeth():
    """Prove the census can fail. Run it against a poisoned source with a second caller
    hidden in an automated path — if this returns only the legitimate site, the test above
    is decoration."""
    poisoned = {
        "dashboard/session_starters.py": (
            "async def api_session_share(request):\n"
            "    return session_share.share_session(p, key='k')\n"
        ),
        "heartbeat.py": (
            "def _tick():\n"
            "    for s in sessions:\n"
            "        session_share.share_session(p, key=s)\n"
        ),
        "chat_runner.py": "from x import share_session\ndef after_turn():\n    share_session(p)\n",
    }
    assert _share_call_sites(poisoned) == {
        ("dashboard/session_starters.py", "api_session_share"),
        ("heartbeat.py", "_tick"),
        ("chat_runner.py", "after_turn"),
    }


def test_share_is_not_in_the_auth_bypass_allowlist():
    """Share must not be reachable without auth. "Share" means into the owner's OWN
    library; a public/tokenless route is EXTERNAL-ACCESS's decision, not this atom's.

    Asserts against the real ``_BYPASS_EXACT`` set rather than grepping the file for
    "share", so the test names the actual mechanism and cannot be reddened by an unrelated
    comment (nor pass because the allowlist moved fields).
    """
    from personalclaw.dashboard.token_auth import _BYPASS_EXACT

    assert _BYPASS_EXACT, "the bypass allowlist is empty — this rail matches nothing"
    assert not [p for p in _BYPASS_EXACT if "share" in p or "session" in p]
