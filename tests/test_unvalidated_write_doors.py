"""Six write doors that let an unvalidated request field reach a write.

All six share one root — **a field is read off the body and acted on without ever being
checked** — but they split into two behaviours, and the split is worth stating because only
one of them was ever visible as a bug report.

**Group A — the refusal exists but names nothing** (#3001's remainder). ``lexicon``,
``chat_folders`` and ``providers`` each read a required name with a bare ``.strip()``. A
non-string raises ``AttributeError``, which
:mod:`personalclaw.dashboard.request_boundary` catches as the *unguarded* fault family and
answers with a generic ``bad_request`` naming no field — and in any app that mounts these
routes WITHOUT that middleware it is an unhandled 500. Nothing is stored, so this never looked
like data corruption; it is a refusal that cannot say what it refused.

**Group B — the repr IS stored** (#3001's headline shape, at three doors it was never measured
at). ``knowledge``'s source create/update twins and ``artifacts``' create read their name
through ``str(...)``, which *cannot fail*: ``{"name": {"a": "b"}}`` becomes the Python repr
``"{'a': 'b'}"`` — single quotes and all — and is persisted at 200/201 as a user-visible name.
``artifacts`` is the sharpest of the three because that name reaches a FILENAME on disk, so the
repr outlives the request as a file nobody meant to create.

**On the envelope, because it is the reason these doors do not simply raise.** The shared
validator in :mod:`personalclaw.request_validation` decides WHAT is malformed; each door keeps
its own envelope for SAYING so. Measured per handler, every one of these six sits in a district
that answers the flat ``{"error": "<sentence>"}`` — 10 sibling refusals in ``api_artifacts_create``,
8 in ``create_watched_source``, 12 in ``update_watched_source``, 4 in ``api_provider_create`` —
and **none of the five modules calls ``json_error`` even once**. Letting
``RequestValidationError`` travel to ``request_boundary`` would therefore make ONE field answer
a different shape from its neighbours: one endpoint replying in two envelopes depending on which
field the caller got wrong. This repo has two envelopes deliberately, so the fix keeps each
door's and re-emits inside it. A useful side effect: the refusal no longer depends on the
middleware being installed, which is precisely what made Group A a bare 500.

Each test asserts the REACHABLE refusal — the response a caller receives, its status, that it
names the offending field, and that the write never happened — rather than the presence of a
validator call in the source, which would pass with the guard deleted.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The bodies that must be refused. A dict is the #3001 repr case; the rest cover the scalar
# family `str()` also swallows, including `True` (a `bool` IS an `int` in Python, which is how
# an allowlist of `(str, int, float)` shipped a bug once already).
WRONG_TYPES = [{"a": "b"}, ["x"], 5, 1.5, True]


def _request(body, *, match=None, app=None):
    """A request double carrying ``body`` as its parsed JSON.

    The same shape ``tests/test_write_path_4xx.py`` uses for this defect class: the real
    handler body runs, which is where the defect lives. No middleware is installed, which is
    deliberate — it proves the door answers its own 400 rather than relying on the gateway to
    render one for it.
    """
    req = MagicMock()
    req.method = "POST"
    req.json = AsyncMock(return_value=body)
    req.read = AsyncMock(return_value=b"{}")
    req.match_info = match or {}
    req.headers = {}
    req.query = {}
    req.app = app if app is not None else {"state": MagicMock()}
    return req


def _assert_named_refusal(resp, field: str) -> None:
    """A 400 that NAMES the offending field, in this door's own envelope.

    Naming it is the whole point: the generic ``bad_request`` these doors produced before is
    also a 400, so asserting the status alone would pass unchanged against the defect.
    """
    assert resp.status == 400, resp.status
    err = json.loads(resp.body)["error"]
    assert isinstance(err, str), f"this door's envelope is flat; got {err!r}"
    assert field in err, err


# ── Group A: the refusal that named nothing (#3001 remainder) ──────────────────


class TestLexiconAddTermRefusesAWrongTypedCanonical:
    """``lexicon/handlers.py`` — ``(body.get("canonical") or "").strip()``."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", WRONG_TYPES)
    async def test_a_non_string_canonical_is_a_named_400(self, bad):
        from personalclaw.lexicon.handlers import api_lexicon_add_term

        with patch("personalclaw.lexicon.handlers.get_lexicon_service") as svc:
            resp = await api_lexicon_add_term(_request({"canonical": bad}))
            _assert_named_refusal(resp, "canonical")
            # The service is the next statement after the field reads, so never reaching it
            # proves the handler aborted before doing any work.
            svc.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_real_canonical_still_adds_the_term(self):
        """The refusal must not cost the feature."""
        from personalclaw.lexicon.handlers import api_lexicon_add_term

        with patch("personalclaw.lexicon.handlers.get_lexicon_service") as svc:
            svc.return_value.add_manual_term.return_value = "t-1"
            resp = await api_lexicon_add_term(
                _request({"canonical": " Kubernetes ", "aliases": "k8s"})
            )
            assert resp.status == 200
            # Stripped by `require_string`, so the handler never re-strips (and cannot forget to).
            svc.return_value.add_manual_term.assert_called_once_with("Kubernetes", aliases=["k8s"])


class TestChatFolderCreateRefusesAWrongTypedName:
    """``dashboard/chat_folders.py`` — ``(body.get("name") or "").strip()[:100]``."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", WRONG_TYPES)
    async def test_a_non_string_name_is_a_named_400(self, bad):
        from personalclaw.dashboard.chat_folders import api_chat_folder_create

        state = MagicMock()
        state._folders = []
        resp = await api_chat_folder_create(_request({"name": bad}, app={"state": state}))
        _assert_named_refusal(resp, "name")
        # No folder was minted, and nothing was persisted.
        assert state._folders == []
        state.save_folders.assert_not_called()


class TestProviderCreateRefusesAWrongTypedName:
    """``dashboard/handlers/providers.py`` — ``body.get("name", "").strip()``.

    This door already guarded the body SHAPE (``isinstance(body, dict)``); only the fields
    were unchecked, so this is a field fix and the reader is left alone.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["name", "type"])
    async def test_a_non_string_field_is_a_named_400(self, field):
        from personalclaw.dashboard.handlers.providers import api_provider_create

        body = {"name": "ollama", "type": "ollama"}
        body[field] = {"a": "b"}
        _assert_named_refusal(await api_provider_create(_request(body)), field)


# ── Group B: the doors that STORED the repr (#3001's headline shape) ───────────


class TestKnowledgeSourceCreateDoesNotStoreARepr:
    """``dashboard/handlers/knowledge.py`` — the door #3001 itself flagged as untested."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", WRONG_TYPES)
    async def test_a_non_string_name_is_refused_not_stringified(self, bad):
        from personalclaw.dashboard.handlers.knowledge import create_watched_source

        with patch("personalclaw.dashboard.handlers.knowledge._source_providers") as provs:
            resp = await create_watched_source(_request({"name": bad, "provider": "web"}))
            _assert_named_refusal(resp, "name")
            # `_source_providers()` is the statement immediately after the field reads, so an
            # uncalled registry proves nothing downstream of it ran.
            provs.assert_not_called()


class TestKnowledgeSourceUpdateDoesNotStoreARepr:
    """The update twin. An update door that does not re-ask what the create door asked is how
    the same value the POST refused gets in through the PATCH (#2992/#456)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", WRONG_TYPES)
    async def test_a_non_string_name_is_refused_not_stringified(self, bad):
        from personalclaw.dashboard.handlers.knowledge import update_watched_source

        store = MagicMock()
        store.get_source.return_value = {"id": "s-1", "name": "Real name"}
        app = {"state": MagicMock(knowledge_store=store)}
        resp = await update_watched_source(_request({"name": bad}, match={"id": "s-1"}, app=app))
        _assert_named_refusal(resp, "name")
        # The existing row keeps the name it had.
        store.update_source.assert_not_called()


class TestArtifactCreateDoesNotWriteAReprFilename:
    """``artifacts/handlers.py`` — the highest-severity of the three, because this name
    reaches a FILENAME on disk, so the repr outlives the request as a real file."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", WRONG_TYPES)
    async def test_a_non_string_name_is_refused_and_nothing_is_written(self, bad):
        from personalclaw.artifacts import handlers as ah

        prov = MagicMock()
        prov.readonly = False
        prov.name = "native"
        with (
            patch.object(ah, "_provider", return_value=prov),
            patch.object(ah, "_is_restricted_session", return_value=False),
            patch.object(ah, "_audit"),
        ):
            resp = await ah.api_artifacts_create(_request({"name": bad, "content": "x"}))
            _assert_named_refusal(resp, "name")
            # No artifact row, and therefore no file named after a Python repr.
            prov.create.assert_not_called()
            prov.update.assert_not_called()
