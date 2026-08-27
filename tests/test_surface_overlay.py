"""The L2 surface-overlay producer (AMBIENT-SURFACES §6 / AS-6).

Before this atom's remainder, L2 was a **declared layer with no producer**: the ceiling,
the refusals and `LayerBoundary` were layer-generic and L2 was exercised in tests, but
nothing WROTE a user/agent overlay. `personalclaw/surface_overlay.py` is that producer,
and it is built to the owner's threat posture (recorded in full in that module's
docstring). This suite is the enforcement, clause by clause:

* clause 1 — an overlay is DATA: a closed key set, typed values, nothing that executes.
* clause 2 — an unknown component / bad prop is refused, not dropped (the FE half owns
  this one; `web/src/ui/surfaces/overlay.test.tsx` asserts it against the real registry).
* clause 3 — shadowing goes through the SAME `registerLayerComponent` (FE half too).
* clause 5 — **path containment, asserted with a really planted symlink.**

Every refusal here carries a **vacuity leg**: the accepting case goes through the same
code path, so a suite of green refusals cannot be green because the loader is never
reached. `test_the_home_is_redirected` is the file-wide vacuity assertion — if it fails,
every other test in this file was reading the owner's real ``~/.personalclaw/surfaces``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import surface_overlay as so
from personalclaw.errors import ERROR_CODES

CODE_PATH = "ERR_SURFACE_OVERLAY_PATH"
CODE_INVALID = "ERR_SURFACE_OVERLAY_INVALID"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``config_dir`` at BOTH bindings and return the surfaces dir.

    ``config/__init__.py`` does ``from .loader import config_dir``, so patching only
    ``config.loader.config_dir`` leaves the package-level name bound to the original
    function object — and ``surfaces_dir()`` imports it from the package. Both, always.
    """
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    monkeypatch.setattr("personalclaw.config.config_dir", lambda: root)
    surfaces = root / so.OVERLAY_DIRNAME
    surfaces.mkdir()
    return surfaces


def _write(surfaces: Path, name: str, doc: object) -> Path:
    path = surfaces / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


def _ok(surface: str = "dashboard", **extra: object) -> dict:
    doc: dict = {
        "surface": surface,
        "title": "Mine",
        "body": 'a = Callout(tone: "info", text: "hi")',
    }
    doc.update(extra)
    return doc


# ── the file-wide vacuity assertion ────────────────────────────────────────────


def test_the_home_is_redirected(home: Path, tmp_path: Path):
    """`surfaces_dir()` resolves under the TMP home, never the real one.

    🪤 This is the load-bearing test of the file. The loader reads a directory under
    ``$PERSONALCLAW_HOME``; if the redirect did not take, every refusal below would be
    measured against the owner's actual overlays and the accepting legs would silently
    depend on that directory being empty.
    """
    assert so.surfaces_dir() == home
    assert str(tmp_path) in str(so.surfaces_dir())
    assert ".personalclaw" not in str(so.surfaces_dir())


# ── the accepting path (the vacuity leg every refusal below leans on) ───────────


def test_a_valid_overlay_loads(home: Path):
    _write(home, "mine.json", _ok())
    accepted, refused = so.load_overlays()
    assert refused == []
    assert [o.file for o in accepted] == ["mine.json"]
    assert accepted[0].surface == "dashboard"
    assert accepted[0].title == "Mine"
    assert "Callout" in accepted[0].body


def test_a_missing_directory_is_no_overlays_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "bare"
    root.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: root)
    monkeypatch.setattr("personalclaw.config.config_dir", lambda: root)
    assert not (root / so.OVERLAY_DIRNAME).exists()
    assert so.load_overlays() == ([], [])


def test_non_json_files_are_ignored_not_refused(home: Path):
    """A README beside the overlays is not a broken overlay."""
    (home / "README.md").write_text("notes", encoding="utf-8")
    _write(home, "mine.json", _ok())
    accepted, refused = so.load_overlays()
    assert [o.file for o in accepted] == ["mine.json"]
    assert refused == []


def test_load_order_is_by_file_name(home: Path):
    _write(home, "b.json", _ok())
    _write(home, "a.json", _ok())
    accepted, _ = so.load_overlays()
    assert [o.file for o in accepted] == ["a.json", "b.json"]


# ── clause 5: path containment, with a really planted symlink ──────────────────


def test_a_symlink_out_of_surfaces_is_refused_and_the_same_content_inline_is_not(
    home: Path, tmp_path: Path
):
    """🪤 The containment clause, proved by PLANTING a symlink — and its vacuity leg.

    `outside/evil.json` is a perfectly VALID overlay document. Linked in from
    ``surfaces/`` it is refused on the PATH code; copied in as a real file it is accepted.
    So the refusal is measuring containment, not content — which is the only way this test
    says anything.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = json.dumps(_ok())
    (outside / "evil.json").write_text(payload, encoding="utf-8")

    link = home / "linked.json"
    link.symlink_to(outside / "evil.json")
    assert link.is_symlink()  # the plant took

    accepted, refused = so.load_overlays()
    assert accepted == []
    assert [r.file for r in refused] == ["linked.json"]
    assert refused[0].error.code == CODE_PATH
    assert "outside surfaces/" in refused[0].error.what

    # Vacuity leg: identical BYTES, real file, inside the directory ⇒ accepted.
    link.unlink()
    _write(home, "linked.json", payload)
    accepted, refused = so.load_overlays()
    assert refused == []
    assert [o.file for o in accepted] == ["linked.json"]


def test_a_symlink_INSIDE_surfaces_still_loads(home: Path):
    """Containment, not a symlink ban: a link whose target is also inside is fine."""
    _write(home, "real.json", _ok())
    (home / "alias.json").symlink_to(home / "real.json")
    accepted, refused = so.load_overlays()
    assert refused == []
    assert sorted(o.file for o in accepted) == ["alias.json", "real.json"]


@pytest.mark.parametrize("name", ["../escape.json", "sub/inner.json", "..", ""])
def test_resolve_overlay_path_refuses_traversal(home: Path, name: str):
    with pytest.raises(ValueError):
        so.resolve_overlay_path(name)


def test_resolve_overlay_path_refuses_an_absolute_path(home: Path, tmp_path: Path):
    with pytest.raises(ValueError):
        so.resolve_overlay_path(str(tmp_path / "elsewhere.json"))


def test_resolve_overlay_path_accepts_a_bare_name(home: Path):
    """The vacuity leg for the four refusals above."""
    assert so.resolve_overlay_path("mine.json") == (home / "mine.json").resolve()


def test_a_directory_named_like_an_overlay_is_refused(home: Path):
    (home / "notafile.json").mkdir()
    accepted, refused = so.load_overlays()
    assert accepted == []
    assert refused[0].error.code == CODE_PATH
    assert "not a regular file" in refused[0].error.what


# ── clause 1: an overlay is DATA with a closed key set ─────────────────────────


def test_an_unknown_top_level_key_is_refused_and_removing_it_loads(home: Path):
    doc = _ok()
    doc["onLoad"] = "alert(1)"
    _write(home, "mine.json", doc)
    accepted, refused = so.load_overlays()
    assert accepted == []
    assert refused[0].error.code == CODE_INVALID
    assert "onLoad" in refused[0].error.what

    # Vacuity: the SAME document without the extra key is accepted.
    del doc["onLoad"]
    _write(home, "mine.json", doc)
    accepted, refused = so.load_overlays()
    assert refused == [] and len(accepted) == 1


def test_a_body_that_is_not_a_string_is_refused(home: Path):
    _write(home, "mine.json", {"surface": "dashboard", "body": {"component": "Callout"}})
    _, refused = so.load_overlays()
    assert refused[0].error.code == CODE_INVALID
    assert '"body" must be a string' in refused[0].error.what


def test_a_non_object_top_level_is_refused(home: Path):
    _write(home, "mine.json", [1, 2, 3])
    _, refused = so.load_overlays()
    assert refused[0].error.code == CODE_INVALID
    assert "top level must be a JSON object" in refused[0].error.what


def test_invalid_json_is_refused(home: Path):
    _write(home, "mine.json", "{not json")
    _, refused = so.load_overlays()
    assert refused[0].error.code == CODE_INVALID
    assert "not valid JSON" in refused[0].error.what


def test_an_empty_overlay_contributes_nothing_and_says_so(home: Path):
    _write(home, "mine.json", {"surface": "dashboard", "body": "  "})
    _, refused = so.load_overlays()
    assert refused[0].error.code == CODE_INVALID
    assert "must contribute something" in refused[0].error.what


def test_an_unknown_surface_id_is_refused_and_a_known_one_loads(home: Path):
    _write(home, "mine.json", _ok(surface="settings"))
    accepted, refused = so.load_overlays()
    assert accepted == []
    assert refused[0].error.code == CODE_INVALID
    assert "'settings'" in refused[0].error.what
    # Vacuity: the one declared id goes through.
    _write(home, "mine.json", _ok(surface="dashboard"))
    accepted, refused = so.load_overlays()
    assert refused == [] and len(accepted) == 1


def test_the_size_ceiling_refuses_over_and_accepts_under(home: Path):
    doc = _ok()
    doc["title"] = "x" * (so.MAX_OVERLAY_BYTES + 1)
    _write(home, "big.json", doc)
    _, refused = so.load_overlays()
    assert refused[0].error.code == CODE_INVALID
    assert "overlay ceiling" in refused[0].error.what
    # Vacuity: a document just under the ceiling loads through the same code path.
    doc["title"] = "x" * 100
    _write(home, "big.json", doc)
    accepted, refused = so.load_overlays()
    assert refused == [] and len(accepted) == 1


def test_one_bad_file_does_not_hide_a_good_one(home: Path):
    """🪤 The refusals are DATA beside the accepted overlays, never an early return."""
    _write(home, "a-good.json", _ok())
    _write(home, "b-bad.json", "{nope")
    accepted, refused = so.load_overlays()
    assert [o.file for o in accepted] == ["a-good.json"]
    assert [r.file for r in refused] == ["b-bad.json"]


# ── clause 3's data half: the `define` block ────────────────────────────────────


def test_a_valid_define_loads(home: Path):
    _write(
        home,
        "mine.json",
        _ok(define=[{"name": "MyPanel", "body": 'x = Callout(tone: "info", text: "y")'}]),
    )
    accepted, refused = so.load_overlays()
    assert refused == []
    assert accepted[0].define[0]["name"] == "MyPanel"
    assert accepted[0].define[0]["description"] == ""


@pytest.mark.parametrize(
    "entry,fragment",
    [
        ({"name": "lowercase", "body": "x = Callout()"}, "CamelCase"),
        ({"name": "My-Panel", "body": "x = Callout()"}, "CamelCase"),
        ({"name": "MyPanel", "body": ""}, "non-empty DSL string"),
        ({"name": "MyPanel", "body": {"k": 1}}, "non-empty DSL string"),
        ({"name": "MyPanel", "body": "x = Callout()", "component": "() => 1"}, "unknown key"),
        (
            {"name": "MyPanel", "body": "x = Callout()", "description": 7},
            "description must be a string",
        ),
    ],
)
def test_a_malformed_define_entry_is_refused(home: Path, entry: dict, fragment: str):
    _write(home, "mine.json", _ok(define=[entry]))
    accepted, refused = so.load_overlays()
    assert accepted == []
    assert refused[0].error.code == CODE_INVALID
    assert fragment in refused[0].error.what


def test_define_refuses_a_duplicate_name(home: Path):
    body = 'x = Callout(tone: "info", text: "y")'
    _write(
        home, "mine.json", _ok(define=[{"name": "P", "body": body}, {"name": "P", "body": body}])
    )
    _, refused = so.load_overlays()
    assert "twice" in refused[0].error.what


def test_define_must_be_a_list(home: Path):
    _write(home, "mine.json", _ok(define={"MyPanel": "x = Callout()"}))
    _, refused = so.load_overlays()
    assert '"define" must be a list' in refused[0].error.what


# ── the envelope + the wire shape ──────────────────────────────────────────────


def test_every_code_this_module_emits_is_a_registered_error_code():
    """A refusal carries the platform's typed envelope, not an ad-hoc string."""
    for code in (CODE_PATH, CODE_INVALID, "ERR_SURFACE_OVERLAY_COMPONENT"):
        assert code in ERROR_CODES, f"{code} is missing from errors.ERROR_CODES"


def test_a_refusal_carries_what_why_fix(home: Path):
    _write(home, "mine.json", "{nope")
    _, refused = so.load_overlays()
    err = refused[0].error.to_dict()
    assert err["code"] == CODE_INVALID
    assert err["what"] and err["why"] and err["fix"]
    assert "surfaces" in refused[0].error.why or "DATA" in refused[0].error.why


def test_overlay_payload_is_the_wire_shape(home: Path):
    _write(home, "good.json", _ok())
    _write(home, "zbad.json", "{nope")
    payload = so.overlay_payload()
    assert [o["file"] for o in payload["overlays"]] == ["good.json"]
    assert [r["file"] for r in payload["refusals"]] == ["zbad.json"]
    assert payload["refusals"][0]["error"]["code"] == CODE_INVALID
    assert payload["dir"] == str(home)


def test_the_endpoint_returns_the_payload(home: Path):
    """The BACKEND call site: the handler the router points at returns the loader's answer."""
    import asyncio

    from personalclaw.dashboard.handlers.surfaces import api_surface_overlays

    _write(home, "good.json", _ok())
    # The handler reads nothing off the request, so `None` is the honest argument here — a
    # fabricated aiohttp Request would only be scaffolding.
    response = asyncio.run(api_surface_overlays(None))  # type: ignore[arg-type]
    body = json.loads(response.body.decode("utf-8"))
    assert [o["file"] for o in body["overlays"]] == ["good.json"]


def test_the_route_is_registered_in_the_shipped_reference():
    """The route reaches the router — proved through the generated route inventory.

    `reference/routes.md` is rendered by a STATIC scan of the registration calls in
    `dashboard/server.py`, so its containing the path is evidence the router does too.
    """
    import personalclaw

    routes = (Path(personalclaw.__file__).parent / "reference" / "routes.md").read_text(
        encoding="utf-8"
    )
    assert "/api/surfaces/overlays" in routes


def test_every_overlayable_surface_has_a_frontend_call_site():
    """🪤 A surface id nothing renders would be an invisible failure.

    The closed `OVERLAYABLE_SURFACES` set is only honest if each id has a
    ``<SurfaceOverlay surface="…" />`` on a real page. Adding an id without the call site
    reddens here rather than accepting overlays that render nowhere.
    """
    web = Path(__file__).resolve().parents[1] / "web" / "src"
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in web.rglob("*.tsx")
        if not p.name.endswith(".test.tsx")
    )
    assert "<SurfaceOverlay" in sources, "the band has no call site at all"
    for surface in sorted(so.OVERLAYABLE_SURFACES):
        assert (
            f'<SurfaceOverlay surface="{surface}"' in sources
        ), f"OVERLAYABLE_SURFACES declares {surface!r} but no page renders a band for it"
