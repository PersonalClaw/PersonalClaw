"""A dashboard read answers at once while the model it would ask is still thinking.

A browser holds six HTTP/1.1 connections to the gateway, for every tab together. A GET that
waits on a model holds one of them for as long as the model takes, and a busy local background
model takes tens of seconds per chore, one chore at a time. The chat's organize chip re-read its
suggestion after every turn, each read waited for its own model answer, and seven of them held
every connection: the chat's own send then sat 138 s inside the browser (the gateway answered it
in 10 ms once it arrived), and every other panel in every tab waited too.

So each read here is held to the same rule. With the model scripted to hang until the test lets
it answer, the read must come back within a small bound, repeated reads during one model call
must ask the model once, and when the answer lands the page is told once, after which the read
returns it at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

#: How long a read may take while the model hangs. Generous for a loaded CI host, and far
#: below the model's own time, which here is "until the test says so".
READ_BOUND_SECS = 2.0

FOLDERS = [{"id": "f-res", "name": "Research"}, {"id": "f-inf", "name": "Infra"}]
TAGS = [{"id": "t-bug", "name": "bug"}, {"id": "t-done", "name": "done", "status": True}]


async def _until(check: Callable[[], bool], what: str, *, within: float = 5.0) -> None:
    """Yield to the loop until *check* holds; fail naming *what* if it never does."""
    deadline = asyncio.get_running_loop().time() + within
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"never happened: {what}")
        await asyncio.sleep(0.01)


async def _read(client: TestClient, url: str, **params: str) -> dict:
    """One GET, held to the bound."""
    response = await asyncio.wait_for(client.get(url, params=params), timeout=READ_BOUND_SECS)
    assert response.status == 200, await response.text()
    return await response.json()


class _HangingModel:
    """A scripted model: records each question, answers only once the test releases it."""

    def __init__(self, answer: object) -> None:
        self.asked: list[tuple[object, ...]] = []
        self.answer = answer
        self.release = asyncio.Event()

    async def __call__(self, *args: object, **kwargs: object) -> object:
        self.asked.append(args)
        await self.release.wait()
        return self.answer


@pytest.fixture
def decline_store(tmp_path, monkeypatch):
    """The organize decline memory (``entity_settings/session_organize.json``) in a scratch home."""
    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    return tmp_path


def _app(state, method_path: str, handler) -> web.Application:
    app = _api_app(state)
    app.router.add_get(method_path, handler)
    return app


@pytest.mark.asyncio
async def test_the_organize_read_answers_while_the_model_is_still_sorting_the_chat(
    tmp_path, monkeypatch, decline_store
):
    from personalclaw.dashboard.handlers.session_organize import api_session_organize_suggest

    model = _HangingModel("FOLDER: Research  TAGS: bug")
    monkeypatch.setattr("personalclaw.chores.run_chore", model)
    state = _make_state(tmp_path)
    state._folders, state._tags = list(FOLDERS), list(TAGS)
    chat = state.get_or_create_session("chat-1-1790000000")
    # Titled as the auto-titler leaves it. No word of it names a folder or a tag, so only the
    # model could sort it.
    chat.title, chat._titled = "quarterly planning cadence", True
    frames: list[tuple[str, object]] = []
    monkeypatch.setattr(state, "broadcast_ws", lambda kind, data: frames.append((kind, data)))
    path = "/api/chat/sessions/{session}/organize"

    async with TestClient(TestServer(_app(state, path, api_session_organize_suggest))) as client:
        url = path.format(session=chat.key)
        for _ in range(3):
            assert await _read(client, url) == {"proposal": None, "pending": True}
        assert len(model.asked) == 1, "three reads during one model call asked it more than once"
        assert frames == [], "the chat was told of an answer the model has not given"

        model.release.set()
        await _until(lambda: bool(frames), "the chat hearing that its proposal is ready")
        assert frames == [("chat_organize", {"session": chat.key})]

        body = await _read(client, url)
        assert body["pending"] is False
        assert body["proposal"]["source"] == "llm"
        assert (body["proposal"]["folder_id"], body["proposal"]["tags"]) == ("f-res", ["bug"])
        await asyncio.sleep(0.05)
        assert len(model.asked) == 1, "an answered question was asked again"
        assert len(frames) == 1, "one answer was announced more than once"


@pytest.mark.asyncio
async def test_a_chat_whose_title_moved_on_during_the_ask_is_asked_once_more(
    tmp_path, monkeypatch, decline_store
):
    from personalclaw.dashboard.handlers.session_organize import api_session_organize_suggest

    model = _HangingModel("FOLDER: Infra  TAGS: -")
    monkeypatch.setattr("personalclaw.chores.run_chore", model)
    state = _make_state(tmp_path)
    state._folders, state._tags = list(FOLDERS), list(TAGS)
    chat = state.get_or_create_session("chat-2-1790000000")
    chat.title, chat._titled = "quarterly planning cadence", True
    frames: list[tuple[str, object]] = []
    monkeypatch.setattr(state, "broadcast_ws", lambda kind, data: frames.append((kind, data)))
    path = "/api/chat/sessions/{session}/organize"

    async with TestClient(TestServer(_app(state, path, api_session_organize_suggest))) as client:
        url = path.format(session=chat.key)
        await _read(client, url)
        chat.title = "staging cluster upgrade window"
        for _ in range(2):
            assert (await _read(client, url))["pending"] is True
        assert len(model.asked) == 1, "a read during the ask started a second one beside it"

        model.release.set()
        await _until(lambda: bool(frames), "the answer to the chat's newest question")
        assert len(model.asked) == 2, "the newer question was never asked, or asked twice"
        assert "staging cluster upgrade window" in str(model.asked[1])
        assert frames == [("chat_organize", {"session": chat.key})]
        assert (await _read(client, url))["proposal"]["folder_id"] == "f-inf"


@pytest.mark.asyncio
async def test_the_suggestions_read_answers_while_the_model_is_still_writing_them(
    tmp_path, monkeypatch
):
    from personalclaw import suggestions

    model = _HangingModel(["Plan the release notes"])
    monkeypatch.setattr("personalclaw.suggestions.generate_suggestions", model)
    state = _make_state(tmp_path)
    hints: list[tuple[str, ...]] = []
    monkeypatch.setattr(state, "push_refresh", lambda *kinds: hints.append(kinds))

    app = _app(state, "/api/suggestions", suggestions.api_suggestions)
    async with TestClient(TestServer(app)) as client:
        first = await _read(client, "/api/suggestions")
        assert first["suggestions"] == suggestions._FALLBACK_SUGGESTIONS
        assert (first["generated_at"], first["refreshing"]) == (0.0, True)
        # The widget's Refresh, while that first generation is still out.
        forced = await _read(client, "/api/suggestions", force="1")
        assert forced["refreshing"] is True
        assert len(model.asked) == 1, "a read during a generation started a second one"
        assert hints == []

        model.release.set()
        await _until(lambda: bool(hints), "the pages hearing the suggestions changed")
        assert hints == [("suggestions",)]
        after = await _read(client, "/api/suggestions")
        assert after["suggestions"] == ["Plan the release notes"]
        assert after["generated_at"] > 0 and after["refreshing"] is False
        assert len(model.asked) == 1


@pytest.mark.asyncio
async def test_the_attachment_read_answers_while_a_model_is_still_reading_the_image(
    tmp_path, monkeypatch
):
    from personalclaw.dashboard import attachment_extract
    from personalclaw.dashboard.handlers.files import api_attachment_extract
    from personalclaw.knowledge.extract import Extracted

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    image = uploads / f"{'0' * 32}_receipt.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    model = _HangingModel(Extracted("INVOICE 42", True))
    monkeypatch.setattr("personalclaw.knowledge.extract.extract_file", model)
    monkeypatch.setattr(attachment_extract, "_INSTANCE", attachment_extract.AttachmentExtractor())
    state = _make_state(tmp_path)
    hints: list[tuple[str, ...]] = []
    monkeypatch.setattr(state, "push_refresh", lambda *kinds: hints.append(kinds))

    app = _app(state, "/api/attachment-extract", api_attachment_extract)
    async with TestClient(TestServer(app)) as client:
        for _ in range(2):
            pending = await _read(client, "/api/attachment-extract", path=str(image))
            assert pending["pending"] is True and pending["text"] == ""
            assert pending["name"] == "receipt.png"
        assert len(model.asked) == 1, "two reads of one image read it twice"
        assert hints == []

        model.release.set()
        await _until(lambda: bool(hints), "the pages hearing the image was read")
        assert hints == [("attachments",)]
        done = await _read(client, "/api/attachment-extract", path=str(image))
        assert (done["pending"], done["text"], done["read"]) == (False, "INVOICE 42", True)
        assert len(model.asked) == 1


@pytest.mark.asyncio
async def test_an_ordinary_document_still_reads_back_its_text(tmp_path, monkeypatch):
    """The rule changes when the answer comes, not what it says: a text file's read lands
    and reads back whole, with no model anywhere in it."""
    from personalclaw.dashboard import attachment_extract
    from personalclaw.dashboard.handlers.files import api_attachment_extract

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    notes = uploads / f"{'1' * 32}_notes.txt"
    notes.write_text("Ship the beta on Friday.\n", encoding="utf-8")
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    monkeypatch.setattr(attachment_extract, "_INSTANCE", attachment_extract.AttachmentExtractor())
    state = _make_state(tmp_path)
    hints: list[tuple[str, ...]] = []
    monkeypatch.setattr(state, "push_refresh", lambda *kinds: hints.append(kinds))

    app = _app(state, "/api/attachment-extract", api_attachment_extract)
    async with TestClient(TestServer(app)) as client:
        first = await _read(client, "/api/attachment-extract", path=str(notes))
        if first.get("pending"):
            # The real extraction graph, whose first run imports its readers: slow on a busy host.
            await _until(lambda: bool(hints), "the text file's read finishing", within=30.0)
        done = await _read(client, "/api/attachment-extract", path=str(notes))
    assert not done.get("pending") and done["read"] is True
    assert "Ship the beta on Friday." in done["text"]
