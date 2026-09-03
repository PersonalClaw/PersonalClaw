"""Artifact investigate resolver + @-artifact chat references (ARTIFACTS-EVOLUTION S3).

Two halves of one loop. The RESOLVER stages an artifact so the agent can *iterate* on
it — the only resolver that suggests `agent` mode, because iterating means calling
`artifact_update` on that one slug and (owner ruling 2026-07-29) legitimately needs the
wider toolset: web search, knowledge reads, commands, project investigation. A narrower
mode would produce a panel where the agent cannot do the thing the panel is for.

The INJECTION grounds a chat turn in an artifact's CURRENT version — referencing an
artifact means "what it is now", not a pinned snapshot — and records a `referenced`
event so the artifact's timeline shows where it was used.
"""

from __future__ import annotations

import pytest

from personalclaw import investigate as inv


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A real native artifact provider rooted in tmp_path.

    Note the seam: `registry._providers` is a MODULE-LEVEL cache, so patching
    `registry.get_provider` is not enough — the code under test imports the module and
    calls through it, and a provider registered by an earlier test in the same worker
    would win (it did: one test read another's artifact and the assertion caught it).
    Replacing the cache entry itself is the correct isolation, and monkeypatch.setitem
    restores the previous entry afterwards.
    """
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.native import NativeArtifactProvider

    # Pass the root EXPLICITLY. NativeArtifactProvider resolves `config_dir()` eagerly
    # in __init__, so relying on the patched config_dir is fragile — and a provider
    # constructed by an earlier test in the same xdist worker has already cached its
    # own root. Being explicit removes the ordering dependency entirely.
    prov = NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setitem(registry._providers, "native", prov)
    return prov


class _State:
    """The resolver only reads the provider via the registry, so state is unused —
    but it must still accept one (the registry signature is (entity_id, state))."""


# ── the resolver ──────────────────────────────────────────────────────────────


def test_the_artifact_resolver_is_registered():
    assert "artifact" in inv._RESOLVERS


def test_it_resolves_an_artifact_into_an_envelope(provider):
    provider.create(name="Sales dashboard", content="<div>chart</div>", kind="widget")
    ctx = inv._resolve_artifact("sales-dashboard", _State())
    assert ctx is not None
    assert ctx.kind == "artifact"
    assert ctx.id == "sales-dashboard"
    assert "Sales dashboard" in ctx.title
    assert ctx.back_link == "#/artifacts/sales-dashboard"


def test_the_snapshot_carries_the_current_body(provider):
    provider.create(name="Doc", content="the actual body text", kind="document")
    ctx = inv._resolve_artifact("doc", _State())
    assert "the actual body text" in ctx.snapshot


def test_it_suggests_agent_mode(provider):
    """The one resolver that does. Owner ruling: iteration needs the wider toolset —
    web search, knowledge, commands, investigate — not just artifact_update."""
    provider.create(name="Doc", content="x", kind="document")
    ctx = inv._resolve_artifact("doc", _State())
    assert ctx.suggested_task_mode == "agent"


def test_every_other_resolver_still_defaults_to_read_only():
    """Guard the exception: `agent` must stay special-cased to artifacts, not become
    the platform default by drift."""
    from dataclasses import fields

    default = next(f for f in fields(inv.InvestigateContext) if f.name == "suggested_task_mode")
    assert default.default == "ask"


def test_the_opening_prompt_names_the_slug_and_the_update_tool(provider):
    """A vaguer prompt produces a near-duplicate artifact instead of a new version —
    the exact failure this wording exists to prevent."""
    provider.create(name="Sales dashboard", content="x", kind="widget")
    ctx = inv._resolve_artifact("sales-dashboard", _State())
    assert "sales-dashboard" in ctx.opening_prompt
    assert "artifact_update" in ctx.opening_prompt


def test_it_reports_the_version(provider):
    provider.create(name="Doc", content="v1 body", kind="document")
    provider.update("doc", content="v2 body", actor="agent")
    ctx = inv._resolve_artifact("doc", _State())
    assert "v2" in ctx.snapshot


def test_a_file_backed_artifact_names_its_live_source(provider, tmp_path):
    """The agent must edit the workspace file, not the snapshot — otherwise the next
    read reverts its work."""
    provider.create(name="Doc", content="body", kind="document", source_path="notes/doc.md")
    ctx = inv._resolve_artifact("doc", _State())
    assert "notes/doc.md" in ctx.snapshot


def test_a_binary_artifact_never_puts_bytes_in_the_snapshot(provider):
    """An image body is a raw URL reference; inlining bytes would blow the turn budget
    for no benefit."""
    provider.create_binary(
        name="Chart", data=b"\x89PNG\r\n\x1a\nfake", kind="image", mime="image/png"
    )
    ctx = inv._resolve_artifact("chart", _State())
    assert "\x89PNG" not in ctx.snapshot
    assert "Binary artifact" in ctx.snapshot


def test_a_missing_artifact_resolves_to_none(provider):
    assert inv._resolve_artifact("no-such-slug", _State()) is None


def test_a_provider_failure_degrades_to_none(monkeypatch, tmp_path):
    """A resolver must never raise into the investigate endpoint."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    from personalclaw.artifacts import registry

    def _boom(name=None):
        raise OSError("disk gone")

    monkeypatch.setattr(registry, "get_provider", _boom)
    assert inv._resolve_artifact("anything", _State()) is None
    # And the injection path must degrade the same way rather than killing the turn.
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    assert _inject_artifact_content(None, _Session(_msg(["x"])), "go") == "go"


# ── @-artifact chat references ────────────────────────────────────────────────


class _Session:
    def __init__(self, messages):
        self.key = "chat-1-test"
        self.messages = messages


def _msg(slugs):
    return [{"role": "user", "content": "look at this", "meta": {"artifacts": slugs}}]


def test_injection_prepends_the_current_body(provider):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Sales dashboard", content="<div>the chart</div>", kind="widget")
    out = _inject_artifact_content(None, _Session(_msg(["sales-dashboard"])), "make it blue")
    assert "the chart" in out
    assert "make it blue" in out
    assert "sales-dashboard" in out


def test_injection_uses_the_LATEST_version(provider):
    """Referencing an artifact means "what it is now"."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Doc", content="old body", kind="document")
    provider.update("doc", content="new body", actor="agent")
    out = _inject_artifact_content(None, _Session(_msg(["doc"])), "summarize")
    assert "new body" in out
    assert "old body" not in out


def test_no_mention_leaves_the_message_untouched(provider):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    session = _Session([{"role": "user", "content": "hi", "meta": {}}])
    assert _inject_artifact_content(None, session, "hi") == "hi"


def test_a_missing_slug_is_skipped_without_failing_the_turn(provider):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Real", content="real body", kind="document")
    out = _inject_artifact_content(None, _Session(_msg(["real", "ghost"])), "go")
    assert "real body" in out
    assert out.endswith("go")


def test_injection_records_a_referenced_event_with_the_session_id(provider):
    """The artifact's timeline is how a user sees where it was used."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Doc", content="body", kind="document")
    _inject_artifact_content(None, _Session(_msg(["doc"])), "go")
    art = provider.get("doc")
    refs = [e for e in art.events if e.type == "referenced"]
    assert len(refs) == 1
    assert refs[0].session_id == "chat-1-test"


def test_referencing_twice_in_one_session_records_one_impression(provider):
    """A long conversation about one artifact must not flood its timeline."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Doc", content="body", kind="document")
    session = _Session(_msg(["doc"]))
    _inject_artifact_content(None, session, "first")
    _inject_artifact_content(None, session, "second")
    art = provider.get("doc")
    assert len([e for e in art.events if e.type == "referenced"]) == 1


def test_a_binary_artifact_reference_does_not_inline_bytes(provider):
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create_binary(
        name="Chart", data=b"\x89PNG\r\n\x1a\nfake", kind="image", mime="image/png"
    )
    out = _inject_artifact_content(None, _Session(_msg(["chart"])), "describe it")
    assert "\x89PNG" not in out
    assert "Binary artifact" in out


def test_the_header_tells_the_model_to_update_in_place(provider):
    """Without this the model creates a near-duplicate artifact instead of a version."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(name="Doc", content="body", kind="document")
    out = _inject_artifact_content(None, _Session(_msg(["doc"])), "change it")
    assert "artifact_update" in out
    assert "same slug" in out


def test_credentials_in_an_artifact_body_are_redacted_on_the_way_in(provider):
    """An artifact body is agent-authored and can contain anything it was told."""
    from personalclaw.dashboard.chat_runner import _inject_artifact_content

    provider.create(
        name="Doc",
        content="const key = 'sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'",
        kind="document",
    )
    out = _inject_artifact_content(None, _Session(_msg(["doc"])), "review")
    # Assert the body REACHED the prompt first — otherwise "the key is absent" would
    # pass just as well if nothing were injected at all, proving nothing.
    assert "const key" in out
    assert "sk-ant-api03-AAAA" not in out


# ── create() echoes the PERSISTED body, not the raw input (#781) ──────────────


def test_create_response_content_matches_the_persisted_cap(provider):
    """An over-cap create must return what reached disk, not the full input.

    _write_text slices the body to MAX_CONTENT_BYTES; create() used to set
    art.content to the un-sliced input, so a >1 MiB save reported success at full
    size in the create response while the next get() returned only the capped body.
    The create echo and the subsequent read must agree.
    """
    from personalclaw.artifacts.models import MAX_CONTENT_BYTES

    oversize = "x" * (MAX_CONTENT_BYTES + 4096)
    art = provider.create(name="Big doc", content=oversize, kind="document")
    assert len(art.content) == MAX_CONTENT_BYTES

    fetched = provider.get(art.slug)
    assert fetched is not None
    assert fetched.content == art.content
    assert len(fetched.content) == MAX_CONTENT_BYTES


def test_create_under_cap_round_trips_unchanged(provider):
    """The opposite failure mode: a normal (under-cap) body must not be mangled."""
    body = "the whole body text"
    art = provider.create(name="Small doc", content=body, kind="document")
    assert art.content == body
    assert provider.get(art.slug).content == body


# ── save / snapshot / event triad tracks a REAL change (#692, #291) ──────────


def _event_types(art):
    return [e.type for e in (art.events or [])]


def test_snapshot_of_unchanged_content_is_a_noop(provider):
    """#692: Snapshot on a clean buffer must not cut a duplicate version or log an event."""
    provider.create(name="Report", content="the body", kind="document")
    v1 = provider.get("report")
    assert v1.version == 1
    # Snapshot with the SAME bytes the artifact already holds.
    provider.update("report", content="the body", snapshot=True, event_type="iterated")
    after = provider.get("report")
    assert after.version == 1  # no duplicate version
    assert _event_types(after) == _event_types(v1)  # no phantom 'iterated' event


def test_snapshot_of_changed_content_still_cuts_a_version(provider):
    """Opposite failure mode: a real snapshot must still cut a version + its event."""
    provider.create(name="Report", content="v1 body", kind="document")
    provider.update("report", content="v2 body", snapshot=True, event_type="iterated")
    after = provider.get("report")
    assert after.version == 2
    assert after.events[-1].type == "iterated"


def test_plain_save_records_an_edited_event_without_a_new_version(provider):
    """#291: a content edit (no snapshot) must log its event_type, not silently drop it."""
    provider.create(name="Note", content="original", kind="document")
    before = provider.get("note")
    assert _event_types(before) == ["created"]
    provider.update("note", content="edited body", event_type="edited")
    after = provider.get("note")
    assert after.version == before.version  # Save edits live content; it does not cut a version
    assert _event_types(after) == ["created", "edited"]  # the edit now appears in the history
