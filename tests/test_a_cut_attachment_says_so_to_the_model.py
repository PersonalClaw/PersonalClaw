"""A chat attachment handed on shorter than it was read says so to the model.

The text read from a file attached in a chat is cut before a turn hands it on: to a turn's cap
(``attachment_extract._MAX_TEXT_CHARS``) and never past what the content scan read
(``content_scan.scanned_head``, the scan's first window for a text too long to read whole). The cut
said nothing, so the model answered about the file as if it had read all of it. A cut file now
carries core's own line after its fenced text, naming how much of it was given and how much the
file held; a file handed on whole carries none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.dashboard import attachment_extract
from personalclaw.security import outside_fences
from personalclaw.uploads import content_scan

pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def extractor(monkeypatch):
    """A fresh attachment extractor behind ``get_extractor()``."""
    fresh = attachment_extract.AttachmentExtractor()
    monkeypatch.setattr(attachment_extract, "_INSTANCE", fresh)
    return fresh


@pytest.fixture(autouse=True)
def scans(monkeypatch) -> None:
    """The content scan's child, answering that nothing is refused."""

    async def _ask(window: bytes):
        return {"dangerous": False}

    monkeypatch.setattr(content_scan, "_ask_child", _ask)


async def _block(path: Path) -> str:
    """What a chat turn hands the model for *path*, attached."""
    from personalclaw.dashboard.chat_runner import _attachment_text_blocks

    return await _attachment_text_blocks([str(path)])


def _cut_line(given: int, held: int) -> str:
    return (
        f"(Only the first {given:,} of {held:,} characters of this file were given to you. The "
        "rest was left out: say so if your answer depends on it.)"
    )


def _log(chars: int) -> str:
    """A plain-text log *chars* characters long, ending on its last line's text (a reader drops a
    file's closing newline)."""
    lines = "".join(f"line {i:06d}: the pantry was restocked\n" for i in range(chars // 30 + 1))
    return lines[: chars - 1] + "."


@pytest.mark.asyncio
async def test_a_text_past_a_turns_cap_names_what_it_was_given(tmp_path) -> None:
    """🔴 Red before: the model got the first 200,000 characters and no word of the rest."""
    path = tmp_path / "pantry.log"
    path.write_text(_log(300_000), encoding="utf-8")

    block = await _block(path)

    assert _cut_line(200_000, 300_000) in outside_fences(block), block[-300:]
    assert block.index(_cut_line(200_000, 300_000)) > block.rindex("</untrusted_content>")


@pytest.mark.asyncio
async def test_a_text_past_the_scans_window_names_the_windows_cut(tmp_path) -> None:
    """🔴 Red before. A text of 3-byte characters past the size the scan reads whole is handed on
    as the scan's first window, far fewer than the turn's 200,000 characters."""
    body = "€" * 200_000
    assert len(body.encode()) > content_scan.WHOLE_FILE_BYTES
    path = tmp_path / "prices.txt"
    path.write_text(body, encoding="utf-8")

    block = await _block(path)

    given = content_scan.SCAN_WINDOW // 3
    assert _cut_line(given, 200_000) in outside_fences(block), block[-300:]


@pytest.mark.asyncio
async def test_a_csv_whose_own_row_cut_is_cut_again_still_says_so(tmp_path) -> None:
    """🔴 Red before. The CSV reader keeps 500 rows and says how many more there were; those 500
    rows run past a turn's cap, so that line is cut away and the new line is all that says so."""
    path = tmp_path / "stock.csv"
    path.write_text(
        "".join(f"{i},{'rice and lentils ' * 29}\n" for i in range(600)), encoding="utf-8"
    )

    block = await _block(path)

    assert "more rows" not in block
    outside = outside_fences(block)
    assert "(Only the first 200,000 of " in outside, block[-300:]


@pytest.mark.asyncio
async def test_a_text_handed_on_whole_says_nothing_of_a_cut(tmp_path) -> None:
    path = tmp_path / "pantry.txt"
    path.write_text(_log(5_000), encoding="utf-8")

    block = await _block(path)

    assert "line 000001: the pantry was restocked" in block
    assert "were given to you" not in block
