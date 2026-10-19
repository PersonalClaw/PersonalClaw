"""The security documents name the app-frontend carve-out, and the section the code cites resolves.

The tree half of ``tests/test_app_ui_host_origin_disclosure.py``, which says what the measured
behaviour is and why it is a disclosure rather than a control (#492). It reads the three security
documents and the files that cite them, so it is a rail: it runs once per CI run, in the rails job.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from rails_repo import REPO

_LIMITATIONS = REPO / "docs" / "security" / "limitations.md"
_THREAT_MODEL = REPO / "docs" / "security" / "threat-model.md"
_SECURITY = REPO / "SECURITY.md"

# The section the code's citations name. Kept as a constant because it appears in three
# assertions below AND in two source files — the thing that must not drift.
_SECTION = 4


def _text(path: Path) -> str:
    """A file's text: through the suite's source index where it holds the file (the docs, the
    package, ``web/src``), else read here (``SECURITY.md``)."""
    return source_index.text(path) if source_index.holds(path) else path.read_text("utf-8")


def test_limitations_names_the_frontend_carve_out():
    """The filed defect: the threat model and ``limitations.md`` described the app
    *backend* carve-out in detail and said nothing about the frontend, so a reader who
    had absorbed them would conclude an app's declared permissions bound its UI."""
    text = _text(_LIMITATIONS)
    assert re.search(rf"^## {_SECTION}\. .*frontend", text, re.M | re.I), (
        f"limitations.md must carry a §{_SECTION} about the app frontend — the code cites "
        f"that number"
    )
    low = text.lower()
    for claim in ("dashboard page", "same-origin", "loadcontributedmodule", "no iframe"):
        assert claim in low, f"limitations.md §{_SECTION} must name {claim!r}"
    # The consequence, not just the mechanism: what the api allowlist does NOT bound.
    assert (
        "not** its page code" in text
        or "not* its page code" in text
        or ("bounds the app's backend and its SDK calls" in text)
    ), "the section must say the api allowlist does not bound page code"
    # And the control that IS in force, so the entry is not pure alarm.
    assert "quarantine → scan → consent → install" in text


def test_threat_model_deliberately_does_not_defend_the_host_page():
    """The out-of-scope list had the ``network`` bullet and no frontend bullet — the
    asymmetry the issue named. This is the list a security reader reads."""
    text = _text(_THREAT_MODEL)
    section = text[text.index("## What we deliberately don't defend against") :]
    assert re.search(
        r"frontend", section, re.I
    ), "the deliberately-undefended list must name the app frontend"
    assert "same-origin" in section.lower()
    assert "limitations.md" in section


def test_security_md_scopes_an_app_frontend_report_out():
    """SECURITY.md sets what is worth a private advisory. Reaching the host from app UI is
    the documented design, so a reporter must learn that here rather than from a closed
    report — and the thing that IS in scope (installing past the gate) must stay named."""
    text = _text(_SECURITY)
    section = text[text.index("### Out of scope") :]
    assert re.search(r"frontend", section, re.I)
    assert "install an app past that gate" in section


def test_the_section_number_the_code_cites_resolves():
    """Both ``appSdk.tsx`` and ``dashboard/server.py`` send a reader to
    ``limitations.md`` §4. A renumbered page leaves those citations pointing at an
    unrelated limitation, which reads as sourced and is therefore worse than no citation
    at all. Rail on the citation, not on the prose around it."""
    citers = [
        REPO / "web" / "src" / "app" / "appSdk.tsx",
        REPO / "src" / "personalclaw" / "dashboard" / "server.py",
        REPO / "src" / "personalclaw" / "apps" / "catalog.py",
        REPO / "web" / "src" / "pages" / "apps" / "installConsent.tsx",
    ]
    headings = re.findall(r"^## (\d+)\. (.+)$", _text(_LIMITATIONS), re.M)
    numbers = [int(n) for n, _ in headings]
    assert numbers == list(range(1, len(numbers) + 1)), f"non-contiguous sections: {numbers}"
    frontend = [n for n, t in headings if "frontend" in t.lower()]
    assert frontend == [str(_SECTION)], f"expected exactly one frontend section, got {frontend}"
    # Counted PER FILE, not as one total: a total alone stays green while one file loses
    # its pointer and another grows a second, which is how a citation rots unnoticed.
    # Every citation in these files must also RESOLVE — `installConsent.tsx` legitimately
    # cites §2 for the network row, and that one has to keep resolving too.
    per_file = {}
    for path in citers:
        cited = re.findall(r"limitations\.md[`)\]\s]*§(\d+)", _text(path))
        for n in cited:
            assert int(n) in numbers, f"{path.name} cites §{n}, which does not exist"
        per_file[path.name] = cited.count(str(_SECTION))
    missing = [name for name, n in per_file.items() if n == 0]
    assert (
        not missing
    ), f"these files carry the behaviour and must point a reader at §{_SECTION}: {missing}"
