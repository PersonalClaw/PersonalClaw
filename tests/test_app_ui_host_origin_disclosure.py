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
   issue was actually filed about — and the ``limitations.md`` section NUMBER the code cites
   still resolves. Both read the docs, so they are the rail
   ``tests/rails/test_app_ui_host_origin_disclosure_tree.py``, once per CI run in the rails job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from personalclaw.apps import catalog as C

_ROOT = Path(__file__).resolve().parents[1]


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
    "some site sets hasUI" would stay green with two of three sites missing.

    Since #3608 no builder spells ``hasUI`` itself: each splats ``disclosure.describe(m)``,
    the one projection the install dialog reads too. So the count is of that splat, and the
    UI answer is asserted on ``describe`` for a manifest with a page, one with only a
    components module, and one with neither."""
    from personalclaw.apps.disclosure import describe
    from personalclaw.apps.manifest import AppManifest

    src = (_ROOT / "src" / "personalclaw" / "apps" / "catalog.py").read_text()
    known = len(re.findall(r"^\s+consentKnown=True,$", src, re.M))
    projected = len(re.findall(r"^\s+\*\*describe\(m\),$", src, re.M))
    assert known == 3, f"expected 3 manifest-backed builders, found {known}"
    assert projected == known, f"{known} builders read a manifest, {projected} splat describe(m)"
    assert not re.search(
        r"^\s+(hasUI|uiComponents)=", src, re.M
    ), "a builder sets a UI field by hand beside the projection"

    def ui(manifest: dict) -> tuple[bool, str]:
        d = describe(AppManifest.from_dict({"name": "a", "version": "1.0.0", **manifest}))
        return d["hasUI"], d["uiComponents"]

    assert ui({"ui": {"entry": "i.mjs", "pages": [{"route": "a", "label": "A"}]}}) == (True, "")
    assert ui({"ui": {"components": "c.mjs"}}) == (False, "c.mjs")
    assert ui({}) == (False, "")
