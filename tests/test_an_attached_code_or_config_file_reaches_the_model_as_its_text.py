"""A code or config file attached in chat, or to an Inbox message, reaches the model as its text.

The library keeps a code file (a script, JSON, YAML, a shell script, TypeScript, …) as a gist,
whose text it reads into the item when the file is uploaded, and a gist's graph hands that text
on: it reads no file. An attachment is only the file, so read through the gist's graph it had no
text to hand on, and the model was told the file had "no extractable text content": the owner
could not attach a script or a config file and ask about it. A file of a text type is read by the
document reader now, as a text file is: by the rule every reader of a file as text shares,
scanned before the model is handed it, fenced as data, masked, and held to the same length.

The content the scan refuses is the upload tests' own example (``OVERRIDE_LIST``): a shopping
list with an invisible right-to-left override character in it.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state
from test_what_a_reader_makes_of_an_upload_is_scanned_and_fenced import (
    OVERRIDE_LIST,
    _scan_rows,
    _with_stray_nul,
)

from personalclaw.knowledge import media
from personalclaw.security import outside_fences
from personalclaw.uploads import content_scan
from personalclaw.uploads.content_scan import scan_upload

#: One small file of each kind an owner attaches to ask about, and a line only its text holds. A
#: ``.toml`` is no kind the library keeps as code: the document reader read it already, and does.
FILES = {
    "pantry.py": ("def total(prices):\n    return sum(prices)\n", "return sum(prices)"),
    "pantry.json": ('{"pantry": ["rice", "lentils"], "shelves": 3}\n', '"shelves": 3'),
    "pantry.yaml": ("pantry:\n  - rice\n  - lentils\nshelves: 3\n", "  - lentils\nshelves: 3"),
    "stock.sh": ("#!/bin/sh\nprintf 'rice and lentils\\n'\n", "printf 'rice and lentils"),
    "pantry.toml": ('[pantry]\nitems = ["rice", "lentils"]\nshelves = 3\n', "shelves = 3"),
    "shelves.ts": ("export const shelves: number = 3;\n", "export const shelves: number"),
}


@pytest.fixture
def extractor(monkeypatch):
    """A fresh attachment extractor behind ``get_extractor()``."""
    from personalclaw.dashboard import attachment_extract

    fresh = attachment_extract.AttachmentExtractor()
    monkeypatch.setattr(attachment_extract, "_INSTANCE", fresh)
    return fresh


@pytest.fixture
def scans(monkeypatch) -> list[bytes]:
    """The content scan's child, answering that nothing is refused, and what it was asked."""
    asked: list[bytes] = []

    async def _ask(window: bytes):
        asked.append(window)
        return {"dangerous": False}

    monkeypatch.setattr(content_scan, "_ask_child", _ask)
    return asked


async def _block(path: Path) -> str:
    """What a chat turn hands the model for *path*, attached."""
    from personalclaw.dashboard.chat_runner import _attachment_text_blocks

    return await _attachment_text_blocks([str(path)])


def _mime(path: Path) -> str | None:
    """The type the upload route and the turn read the file with."""
    return mimetypes.guess_type(str(path))[0]


# ── a chat attachment ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(FILES))
@pytest.mark.asyncio
async def test_an_attached_code_or_config_file_reaches_the_model_as_its_text(
    name, tmp_path, extractor
):
    """🔴 Red before for each kind the library keeps as code: the model was handed "Gist: … — no
    extractable text content". The text is handed on whole, inside the fence."""
    text, line = FILES[name]
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")

    block = await _block(path)

    assert f"### Attached file: {name}" in block
    assert "no extractable text content" not in block.lower(), block
    assert "<untrusted_content source=attachment" in block and line in block, block
    assert line not in outside_fences(block), "the text is inside the fence"
    got = await extractor.get(str(path), _mime(path))
    assert (got.text, got.read, got.unread) == (text.strip(), True, "")


@pytest.mark.asyncio
async def test_every_kind_the_library_keeps_as_code_is_read_and_scanned(tmp_path, scans):
    """🔴 Red before for every one of them. The family: whatever the classifier calls code is read
    as text when it is attached, and that text is what the content scan reads."""
    from personalclaw.knowledge.extract import extract_file

    kinds = sorted(media._CODE_EXT_LANGUAGE)
    assert {".py", ".json", ".yaml", ".sh", ".ts"} <= set(kinds)
    for ext in kinds:
        path = tmp_path / f"shelf{ext}"
        text = f"shelf {ext}: rice and lentils"
        path.write_text(text + "\n", encoding="utf-8")
        assert media.classify(path.name, _mime(path)) == "gist", ext

        got = await extract_file(str(path), _mime(path), name=path.name, surface="attachment")

        assert (got.text, got.read) == (text, True), ext
        assert scans[-1] == text.encode(), f"{ext}: its text is what the scan read"
    assert len(scans) == len(kinds)


@pytest.mark.asyncio
async def test_a_binary_file_named_as_code_is_still_not_read_as_text(tmp_path, extractor, scans):
    """The arm that keeps the change honest: the document reader reads a file as text only when it
    is text, and a NUL byte in its first 8 KB says it is not. A binary file named ``.py`` hands
    the model its name and size and none of its bytes, and with nothing read nothing is scanned."""
    path = tmp_path / "tool.py"
    path.write_bytes(bytes(range(256)) * 4)

    block = await _block(path)

    got = await extractor.get(str(path), _mime(path))
    assert got.read is False and got.text.startswith("Gist: tool.py ("), got
    assert got.text.endswith("no extractable text content."), got
    assert "0123456789" not in block and "ABCDEFGH" not in block, block
    assert scans == []


@pytest.mark.asyncio
async def test_a_code_file_whose_text_fails_the_scan_is_withheld_and_said_so(tmp_path, extractor):
    """🔴 Red before: nothing of a code file was read, so the scan never saw its text and the model
    was told it had none. Now its text is read and scanned, and when it fails none of it is handed
    on and the model is told why, in the words a text file's refusal uses."""
    from personalclaw.knowledge.extract import UNREAD_REFUSED, extract_file

    path = tmp_path / "pantry.py"
    path.write_bytes(_with_stray_nul(f"# {OVERRIDE_LIST}"))
    await scan_upload(path, "document", surface="attachment")  # its bytes pass, as a text's do

    block = await _block(path)

    assert "(Not given to you: its text failed the content safety scan.)" in block, block
    assert "Shopping list" not in block and "Bought this week" not in block
    got = await extract_file(str(path), _mime(path), name="pantry.py", surface="attachment")
    assert (got.text, got.read, got.unread) == ("", False, UNREAD_REFUSED)
    rows = _scan_rows()
    assert rows and {(r["outcome"], r["caller_identity"], r["resources"]) for r in rows} == {
        ("rejected", "uploads.content_scan:attachment", "extracted text")
    }


@pytest.mark.asyncio
async def test_a_long_code_file_is_cut_where_a_long_text_file_is(tmp_path, extractor, scans):
    """🔴 Red before: a long script gave the model nothing. Its text is held to a text attachment's
    length: a chat turn's cap, never past the scan's first window, and an Inbox message's cap."""
    from personalclaw import attachments
    from personalclaw.attachments import Attachment

    body = "".join(f"# shelf {i}: rice and lentils\n" for i in range(30_000))
    assert len(body.encode()) > content_scan.WHOLE_FILE_BYTES
    script, notes = tmp_path / "stock.py", tmp_path / "stock.txt"
    script.write_text(body, encoding="utf-8")
    notes.write_text(body, encoding="utf-8")

    as_code = await extractor.get(str(script), _mime(script))
    as_text = await extractor.get(str(notes), _mime(notes))

    assert as_code.read and as_code == as_text
    assert body.startswith(as_code.text)
    assert len(as_code.text.encode()) <= content_scan.SCAN_WINDOW

    records = attachments.keep(
        "mail_abc_3",
        [
            Attachment(name="stock.py", data=body.encode()),
            Attachment(name="stock.txt", data=body.encode()),
        ],
    )
    code_text, _ = await attachments.text_of("mail_abc_3", records[0])
    plain_text, _ = await attachments.text_of("mail_abc_3", records[1])
    assert code_text == plain_text and body.startswith(code_text.split("\n…[cut at")[0])
    assert code_text.endswith(f"[cut at {attachments.TEXT_CAP:,} characters]"), code_text[-80:]


# ── the sent turn's preview ──────────────────────────────────────────────────────────────────


async def _until(check, what: str, *, within: float = 60.0) -> None:
    """Yield to the loop until *check* holds; fail naming *what* if it never does."""
    deadline = asyncio.get_running_loop().time() + within
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"never happened: {what}")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_the_sent_turns_preview_shows_the_text_the_agent_was_given(tmp_path, monkeypatch):
    """🔴 Red before: an attached config file's preview said it had no text, which was what the
    agent was given. It shows the text now, masked as the agent's copy is: a key the file holds
    reaches neither the agent nor the page."""
    from personalclaw.dashboard import attachment_extract
    from personalclaw.dashboard.handlers.files import api_attachment_extract

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    config = uploads / f"{'2' * 32}_service.yaml"
    config.write_text("service: pantry\napi_key: hunter2hunter2\n", encoding="utf-8")
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    monkeypatch.setattr(attachment_extract, "_INSTANCE", attachment_extract.AttachmentExtractor())
    state = _make_state(tmp_path)
    hints: list[tuple[str, ...]] = []
    monkeypatch.setattr(state, "push_refresh", lambda *kinds: hints.append(kinds))
    app = _api_app(state)
    app.router.add_get("/api/attachment-extract", api_attachment_extract)

    async with TestClient(TestServer(app)) as client:
        first = await (
            await client.get("/api/attachment-extract", params={"path": str(config)})
        ).json()
        if first["pending"]:
            # The real extraction graph and the real content scan: slow on a busy host.
            await _until(lambda: bool(hints), "the config file's read finishing")
        r = await client.get("/api/attachment-extract", params={"path": str(config)})
        assert r.status == 200, await r.text()
        shown = await r.json()
    block = await _block(config)

    assert (shown["name"], shown["pending"], shown["read"]) == ("service.yaml", False, True)
    assert "service: pantry" in shown["text"] and "[REDACTED: credential]" in shown["text"]
    assert "hunter2hunter2" not in shown["text"] and "hunter2hunter2" not in block
    assert shown["text"] in block, "the preview is the text the agent was handed, as it was handed"


# ── an Inbox message's attachment ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["pantry.py", "pantry.yaml"])
@pytest.mark.asyncio
async def test_a_code_file_attached_to_an_inbox_message_reaches_the_agent_as_its_text(name):
    """🔴 Red before: an agent reading the message was told no text could be read from it."""
    from personalclaw import attachments
    from personalclaw.attachments import Attachment

    text, line = FILES[name]
    records = attachments.keep(
        "mail_abc_1",
        [Attachment(name=name, mimetype=mimetypes.guess_type(name)[0] or "", data=text.encode())],
    )

    said = (await attachments.reading("mail_abc_1", records, source="inbox")).text

    assert "its name, its type and its text" in said, said
    assert line in said and line not in outside_fences(said), said


@pytest.mark.asyncio
async def test_an_inbox_code_attachment_whose_text_fails_the_scan_is_withheld_and_said_so():
    """🔴 Red before: the agent was told no text could be read from it, which was not why. An Inbox
    attachment's bytes are not scanned when it is kept, so this scan is the one it gets."""
    from personalclaw import attachments
    from personalclaw.attachments import Attachment

    records = attachments.keep(
        "mail_abc_2",
        [
            Attachment(
                name="pantry.py", mimetype="text/x-python", data=f"# {OVERRIDE_LIST}".encode()
            )
        ],
    )

    said = (await attachments.reading("mail_abc_2", records, source="inbox")).text

    assert "Shopping list" not in said
    assert "its text failed the content safety scan" in said, said


# ── the library's own code upload is unchanged ───────────────────────────────────────────────


def test_a_library_gist_keeps_its_own_graph_and_only_a_file_is_read_as_a_document():
    """A Knowledge gist's content is the code the library read into it when it was uploaded (and
    scanned there), and its graph hands that on, as before. Only a caller that holds the file
    alone is given the document graph; every other type reads its file with its own graph."""
    from personalclaw.knowledge.pipeline import file_graph_for, graph_for
    from personalclaw.knowledge.pipeline.graphs import DocumentGraph, PassthroughGraph

    assert isinstance(graph_for("gist"), PassthroughGraph)
    assert isinstance(file_graph_for("gist"), DocumentGraph)
    for item_type in ("pdf", "document", "sheet", "slides", "image", "audio", "video"):
        assert type(file_graph_for(item_type)) is type(graph_for(item_type)), item_type
