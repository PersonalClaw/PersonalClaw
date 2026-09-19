"""An app's FRONTEND runs in the dashboard's origin, and every surface must say so (#492).

**The measured behaviour.** ``appSdk.loadContributedModule`` fetches an app's UI bundle,
rewrites its bare specifiers to host blob shims and ``import()``s it into the dashboard
page; ``ContributedPage`` mounts the exported ``mount`` in the host React tree with
``createRoot``. There is no iframe on that path. So app UI code holds the host DOM, the
owner's session cookie and authenticated same-origin ``/api/*`` reach.

**Why it is a disclosure and not a control.** ``app_permission_middleware`` acts only on a
request carrying an app identity (the SDK client's app-scoped Bearer token). A bare
``fetch`` from app UI carries none, so it arrives as an owner request —
:func:`tests.test_app_api_permission_scope.test_an_owner_request_is_untouched` pins that
rule from the other side, and it is deliberate: the owner's own dashboard is the caller
the allowlist must not touch. A same-origin request from an app bundle is indistinguishable
from the dashboard's own, so nothing server-side can separate them; only a distinct ORIGIN
for app UI can, which is why #492 is closed as an honest limitation plus a consent row.

**What this suite pins**, being the half that lives in Python:

1. the catalog wire carries the two facts the consent surface needs BEFORE install, under
   the same field names the installed-app wire already uses;
2. a registry pointer carries neither, so "ships no browser code" is never asserted about
   a manifest nobody has read;
3. the three security documents name the carve-out — the documentation-honesty defect the
   issue was actually filed about;
4. the ``limitations.md`` section NUMBER the code cites still resolves. Both
   ``appSdk.tsx`` and ``server.py`` point a reader at "§4"; a renumbered page would leave
   those citations pointing at someone else's limitation, which is a worse failure than
   silence because it reads as sourced.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from personalclaw.apps import catalog as C

_ROOT = Path(__file__).resolve().parents[1]
_LIMITATIONS = _ROOT / "docs" / "security" / "limitations.md"
_THREAT_MODEL = _ROOT / "docs" / "security" / "threat-model.md"
_SECURITY = _ROOT / "SECURITY.md"

# The section the code's citations name. Kept as a constant because it appears in three
# assertions below AND in two source files — the thing that must not drift.
_SECTION = 4


def _write_app(root: Path, name: str, manifest: dict) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": name, "version": "1.0.0", **manifest}))


def _scan(tmp_path, monkeypatch) -> list:
    monkeypatch.setattr(C, "list_local_sources", lambda: [str(tmp_path)])
    monkeypatch.setattr(C, "_installed_names", lambda: set())
    monkeypatch.setattr(C, "first_party_sources", lambda: set())
    return C._scan_local_sources()


# ── 1. the wire carries the fact consent needs ───────────────────────────────


def test_a_scanned_ui_app_reaches_the_store_as_one(tmp_path, monkeypatch):
    """The pre-install card must be able to SAY "this runs in your dashboard page". The
    permission block cannot: there is no manifest field for host-page authority, which is
    exactly why the consent screen was silent about the app that has it."""
    _write_app(
        tmp_path,
        "ui-app",
        {"ui": {"entry": "dist/index.mjs", "pages": [{"route": "ui-app", "label": "UI App"}]}},
    )
    (e,) = _scan(tmp_path, monkeypatch)
    assert e.consentKnown is True
    assert e.hasUI is True
    assert e.to_dict()["hasUI"] is True


def test_a_components_module_is_carried_separately_from_a_page(tmp_path, monkeypatch):
    """``ui.components`` is the BROADER fact and must not collapse into ``hasUI``: the
    shell loads that module for every ENABLED declaring app, so it runs without the user
    ever opening the app's page. A single boolean would let the consent row understate the
    one case the user cannot avoid by not visiting."""
    _write_app(
        tmp_path,
        "widget-app",
        {
            "ui": {"components": "components.mjs"},
            "uiCapabilities": ["generative-component"],
        },
    )
    (e,) = _scan(tmp_path, monkeypatch)
    assert e.hasUI is False, "it declares no page"
    assert e.uiComponents == "components.mjs"
    assert e.to_dict()["uiComponents"] == "components.mjs"


def test_a_backend_only_app_ships_no_browser_code(tmp_path, monkeypatch):
    """The honest negative. It is a CLAIM the consent row makes ("nothing of it runs in
    the dashboard"), so it has to be false for a UI app and true for this one."""
    _write_app(tmp_path, "quiet-app", {"backend": {"entryPoint": "server.py"}})
    (e,) = _scan(tmp_path, monkeypatch)
    assert e.hasUI is False
    assert e.uiComponents == ""


# ── 2. a pointer asserts nothing ─────────────────────────────────────────────


def test_a_registry_pointer_carries_no_ui_claim():
    """Same trap issue 614 found for permissions: ``to_dict`` is ``asdict``, so a pointer
    ships ``hasUI: False`` for a manifest nobody read. ``consentKnown`` is what tells the
    two silences apart, and the UI row rides inside ``PermissionList``, which every caller
    already gates on it (``consentHostUi`` returns undefined for an absent entry)."""
    p = C.RegistryPointer(name="remote-thing", repo="https://example.invalid/repo.git")
    e = C._pointer_to_entry("https://example.invalid/source.git", p, is_git=True)
    assert e.consentKnown is False
    assert e.hasUI is False
    assert e.uiComponents == ""


def test_every_manifest_backed_builder_answers_the_ui_question():
    """A COUNT, not a spot check. Three builders construct a ``CatalogEntry`` from a
    scanned manifest (git scan, local/first-party scan, native scan); a fourth added later
    that forgets ``hasUI`` would ship a card that silently claims an app runs no browser
    code. Counting the two together is what makes this rail non-vacuous: asserting only
    "some site sets hasUI" would stay green with two of three sites missing."""
    src = (_ROOT / "src" / "personalclaw" / "apps" / "catalog.py").read_text()
    known = len(re.findall(r"^\s+consentKnown=True,$", src, re.M))
    has_ui = len(re.findall(r"^\s+hasUI=bool\(m\.ui\.pages\),$", src, re.M))
    components = len(re.findall(r"^\s+uiComponents=m\.ui\.components,$", src, re.M))
    assert known == 3, f"expected 3 manifest-backed builders, found {known}"
    assert has_ui == known, f"{known} builders read a manifest, {has_ui} report hasUI"
    assert (
        components == known
    ), f"{known} builders read a manifest, {components} report uiComponents"


# ── 3. the security docs name the carve-out (the issue's actual ask) ─────────


def test_limitations_names_the_frontend_carve_out():
    """The filed defect: the threat model and ``limitations.md`` described the app
    *backend* carve-out in detail and said nothing about the frontend, so a reader who
    had absorbed them would conclude an app's declared permissions bound its UI."""
    text = _LIMITATIONS.read_text()
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
    text = _THREAT_MODEL.read_text()
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
    text = _SECURITY.read_text()
    section = text[text.index("### Out of scope") :]
    assert re.search(r"frontend", section, re.I)
    assert "install an app past that gate" in section


def test_the_section_number_the_code_cites_resolves():
    """Both ``appSdk.tsx`` and ``dashboard/server.py`` send a reader to
    ``limitations.md`` §4. A renumbered page leaves those citations pointing at an
    unrelated limitation, which reads as sourced and is therefore worse than no citation
    at all. Rail on the citation, not on the prose around it."""
    citers = [
        _ROOT / "web" / "src" / "app" / "appSdk.tsx",
        _ROOT / "src" / "personalclaw" / "dashboard" / "server.py",
        _ROOT / "src" / "personalclaw" / "apps" / "catalog.py",
        _ROOT / "web" / "src" / "pages" / "apps" / "installConsent.tsx",
    ]
    headings = re.findall(r"^## (\d+)\. (.+)$", _LIMITATIONS.read_text(), re.M)
    numbers = [int(n) for n, _ in headings]
    assert numbers == list(range(1, len(numbers) + 1)), f"non-contiguous sections: {numbers}"
    frontend = [n for n, t in headings if "frontend" in t.lower()]
    assert frontend == [str(_SECTION)], f"expected exactly one frontend section, got {frontend}"
    # Counted PER FILE, not as one total: a total alone stays green while one file loses
    # its §4 pointer and another grows a second, which is how a citation rots unnoticed.
    # Every citation in these files must also RESOLVE — `installConsent.tsx` legitimately
    # cites §2 for the network row, and that one has to keep resolving too.
    per_file = {}
    for path in citers:
        cited = re.findall(r"limitations\.md[`)\]\s]*§(\d+)", path.read_text())
        for n in cited:
            assert int(n) in numbers, f"{path.name} cites §{n}, which does not exist"
        per_file[path.name] = cited.count(str(_SECTION))
    missing = [name for name, n in per_file.items() if n == 0]
    assert (
        not missing
    ), f"these files carry the behaviour and must point a reader at §{_SECTION}: {missing}"
