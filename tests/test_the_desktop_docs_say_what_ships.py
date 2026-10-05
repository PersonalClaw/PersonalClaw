"""The desktop app's docs say what is built and what every release ships.

Two sentences had fallen behind the code:

* ``docs/guides/desktop.md`` said the tray's **Quick Capture Note** was a plain shortcut to the
  Inbox because "the note-writing half is not built yet". It is built: the tray deep-links
  ``#/inbox?capture=1``, the Inbox turns that flag into its note composer, and the composer saves
  through ``POST /api/inbox/notes``.
* ``docs/vision.md`` said CI neither built nor released the desktop shell. Every release builds a
  macOS dmg and a Linux AppImage and ``.deb``, and the GitHub Release waits on both jobs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from personalclaw.dashboard import handlers_inbox

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def _guide_quick_capture_row() -> str:
    guide = _read("docs/guides/desktop.md")
    row = re.search(r"^- \*\*Quick Capture Note\*\*.*?(?=^- \*\*|\n\n)", guide, re.M | re.S)
    assert row, "the desktop guide no longer describes the tray's Quick Capture Note row"
    return row.group(0)


def test_the_guide_describes_quick_capture_as_the_note_composer_it_opens() -> None:
    row = _guide_quick_capture_row()
    tray = _read("desktop/trayPresence.js")
    shell = _read("desktop/main.js")
    inbox = _read("web/src/pages/inbox/InboxPage.tsx")
    # The tray row the guide names, and the flag it deep-links with.
    assert 'label: "Quick Capture Note…"' in tray
    assert "?capture=1" in shell
    # The Inbox reads that flag into its composer, and its header control carries the label
    # the guide names.
    assert "useQueryFlag(query, setQuery, 'capture')" in inbox
    header = re.search(r'label="(Capture a note)"', inbox)
    assert header and f"**{header.group(1)}**" in row
    # The size the guide states is the size the server accepts.
    cap = handlers_inbox._NOTE_MAX_CHARS
    assert f"{cap:,} characters" in row


def test_the_vision_names_the_desktop_app_every_release_ships() -> None:
    workflow = yaml.safe_load(_read(".github/workflows/release.yml"))
    jobs = workflow["jobs"]
    publishing = [job for job in jobs.values() if "desktop-mac" in (job.get("needs") or [])]
    assert publishing, "no release job waits on the macOS desktop build"
    assert all("desktop-linux" in job["needs"] for job in publishing)
    vision = _read("docs/vision.md")
    surfaces = re.search(r"^\*\*Delivery surfaces\*\*.*?(?=\n\n)", vision, re.M | re.S)
    assert surfaces, "the vision no longer lists its delivery surfaces"
    text = " ".join(surfaces.group(0).split())
    assert "the desktop app, which every release ships" in text
    assert "dmg" in text and "AppImage" in text
