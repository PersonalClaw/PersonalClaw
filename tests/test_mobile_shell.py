"""The Capacitor shell wraps the SERVED companion route — rails that keep it a wrapper.

``mobile/`` (MOBILE-COMPANION ``MC-7``) is a Capacitor app whose entire job is to hand a WebView
one URL: ``<gateway>/#/companion``, the route ``web/src/app/App.tsx`` already serves. Its failure
mode is not a crash — it is **drift**, in three directions that a green ``node --test`` cannot see
because each one spans two files this repo builds separately:

1. the shell's route string drifting from the route the SPA registers;
2. the shell's navigation rail drifting from the ``allowNavigation`` list the native platforms
   actually read out of ``capacitor.config.json``;
3. the shell's persisted registry drifting from ``web/src/lib/endpoints.ts``, which owns that
   format and says so — *"two shells that disagree about the format are two shells that cannot
   share a registry."*

Parsed from source, never executed: node is not a test dependency of the Python suite (the
convention ``tests/test_desktop_seam.py`` set for the same reason — ``desktop/``'s shell-side
modules cannot import core's Python either, so their shared vocabulary is held by a rail). The
behavioural half lives in ``mobile/test/*.test.mjs`` and runs in CI as ``npm run test:mobile``,
which ``tests/test_ci_tier_enforcement.py`` requires to exist.

**What no rail here can prove:** that a real iPhone or Android device renders the companion inside
its safe area. Those two keys are read by native code, so the assertions below establish only that
they are shipped. See the dated PARTIAL in ``docs/roadmap/plans/MOBILE-COMPANION.md``.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MOBILE = REPO_ROOT / "mobile"
SHELL = MOBILE / "www" / "shell"
CAP_CONFIG = MOBILE / "capacitor.config.json"
NETWORK_MJS = SHELL / "network.mjs"
REGISTRY_MJS = SHELL / "registry.mjs"
ENDPOINTS_TS = REPO_ROOT / "web" / "src" / "lib" / "endpoints.ts"
APP_TSX = REPO_ROOT / "web" / "src" / "app" / "App.tsx"


def _config() -> dict:
    return json.loads(CAP_CONFIG.read_text(encoding="utf-8"))


def _quoted_list(source: str, const: str) -> list[str]:
    """Every single-quoted literal inside ``export const <const> = ...([ ... ])``.

    A deliberately dumb scan rather than a JS parse: the shape being asserted is "this array
    holds exactly these string literals", which the raw text carries. The literals are written
    out one per line in ``network.mjs`` precisely so this stays a scan — a generated list would
    force this rail to re-implement the generator, which is how the two sides would come to
    disagree while both looking right.
    """
    match = re.search(rf"export const {const}\s*=[^[]*\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"could not find `export const {const}` as an array literal"
    return re.findall(r"'([^']*)'", match.group(1))


# ── the served route ────────────────────────────────────────────────────────


def test_the_shell_points_at_the_route_the_spa_actually_serves():
    """The one assertion the whole atom rests on.

    If these two drift, the shell opens a gateway page that does not exist — and the "no forked
    UI" property has nothing holding it up, because a wrapper that cannot reach the real UI is
    one commit away from someone copying a component into ``mobile/``.
    """
    source = NETWORK_MJS.read_text(encoding="utf-8")
    match = re.search(r"export const COMPANION_ROUTE = '([^']+)'", source)
    assert match, "network.mjs no longer exports COMPANION_ROUTE as a literal"
    route = match.group(1)
    assert route == "#/companion"

    app = APP_TSX.read_text(encoding="utf-8")
    slug = route.removeprefix("#/")
    assert f"route === '{slug}'" in app, (
        f"web/src/app/App.tsx does not register a {slug!r} route, so the shell's "
        f"{route!r} opens nothing."
    )


def test_the_shell_targets_the_pair_page_the_gateway_serves():
    source = NETWORK_MJS.read_text(encoding="utf-8")
    match = re.search(r"export const PAIR_ROUTE = '([^']+)'", source)
    assert match, "network.mjs no longer exports PAIR_ROUTE as a literal"
    route = match.group(1)
    assert route == "/pair"

    devices = (
        REPO_ROOT / "src" / "personalclaw" / "dashboard" / "handlers" / "devices.py"
    ).read_text(encoding="utf-8")
    assert f'add_get("{route}"' in devices, f"the gateway does not serve GET {route}"


# ── the navigation rail ─────────────────────────────────────────────────────


def test_the_host_pattern_scan_is_not_vacuous():
    """Vacuity floor: an empty scan would make every rail below pass silently."""
    patterns = _quoted_list(NETWORK_MJS.read_text(encoding="utf-8"), "PRIVATE_HOST_PATTERNS")
    assert len(patterns) == 22, f"expected 22 host patterns, scanned {len(patterns)}: {patterns}"
    assert "localhost" in patterns
    assert "192.168.*" in patterns


def test_the_native_config_carries_every_pattern_the_shell_validates():
    """Two sides of one policy: the shell refuses a host, and Capacitor refuses to navigate to it.

    Only one of those is enforced by native code. If ``capacitor.config.json`` were missing a
    pattern the shell accepts, the shell would compute a URL that Capacitor then kicks out to the
    system browser — the companion opening in Safari instead of the app, with no session cookie.
    """
    patterns = _quoted_list(NETWORK_MJS.read_text(encoding="utf-8"), "PRIVATE_HOST_PATTERNS")
    allowed = _config()["server"]["allowNavigation"]
    assert set(patterns) == set(allowed), (
        "network.mjs and capacitor.config.json disagree about which hosts the shell may reach; "
        f"only in network.mjs: {sorted(set(patterns) - set(allowed))}; "
        f"only in the config: {sorted(set(allowed) - set(patterns))}"
    )


def test_the_navigation_rail_is_not_a_wildcard():
    """``allowNavigation: ['*']`` is the default a hurry reaches for.

    It would let a link in rendered content steer the shell onto any origin at all, which is a
    phishing surface wearing the app's own chrome. The companion is a LAN/tailnet surface, so the
    rail is the private ranges; a public reverse proxy is added deliberately, in a diff.
    """
    allowed = _config()["server"]["allowNavigation"]
    for pattern in allowed:
        assert pattern not in {"*", "*.*", "**"}, f"{pattern!r} is not a rail"
    assert "172.*" not in allowed, "172.* covers most of a PUBLIC /8; spell out 16..31"
    # RFC1918's 172 block, exactly — one below and one above must be absent.
    for octet in range(16, 32):
        assert f"172.{octet}.*" in allowed, f"172.{octet}.* missing from allowNavigation"
    assert "172.15.*" not in allowed
    assert "172.32.*" not in allowed


def test_the_gateway_url_is_not_baked_into_the_build():
    """``server.url`` would make the gateway address a BUILD constant.

    Every owner's gateway lives at a different private address, so a baked URL means one store
    build per owner. The shell learns the address at runtime instead (``mobile/www/shell/``),
    which is the only reason the bootstrap document exists at all.
    """
    server = _config()["server"]
    assert "url" not in server, "a baked server.url means one store build per owner"
    assert "hostname" not in server, "a baked server.hostname has the same effect as server.url"
    assert _config()["webDir"] == "www"
    assert (MOBILE / "www" / "index.html").is_file()


# ── native safe areas ───────────────────────────────────────────────────────


def test_the_native_safe_area_keys_are_shipped():
    """The served companion's insets are a NATIVE concern, and this is the only rail on them.

    ``web/`` declares no ``env(safe-area-inset-*)`` anywhere and no ``viewport-fit=cover`` (see
    the companion assertion below), so nothing in the served document insets itself. Both keys
    below are read by native code, so this test proves they are shipped and nothing more; a real
    device is the only proof they render.
    """
    config = _config()
    assert config["ios"]["contentInset"] == "always"
    assert config["android"]["adjustMarginsForEdgeToEdge"] == "force"


def test_the_bootstrap_document_opts_into_real_insets():
    """``env(safe-area-inset-*)`` resolves to 0px without ``viewport-fit=cover``.

    So the meta tag is load-bearing rather than boilerplate: without it the shell's own screen
    would sit under the notch while every inset it read came back as zero.
    """
    html = (MOBILE / "www" / "index.html").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in html
    css = (SHELL / "shell.css").read_text(encoding="utf-8")
    for edge in ("top", "right", "bottom", "left"):
        assert f"--pc-safe-{edge}: env(safe-area-inset-{edge}" in css, (
            f"shell.css must resolve env(safe-area-inset-{edge}) into --pc-safe-{edge}; "
            "safeArea.mjs reads the custom property, because env() is not readable from script."
        )


def test_the_served_companion_still_relies_on_the_shell_for_insets():
    """A premise check, so this atom's reasoning fails loudly if it stops being true.

    The native keys above exist because the served document handles no insets itself. If `web/`
    grows real safe-area handling, that is good news — but the shell's native insets would then
    be doubling it, and someone has to look.
    """
    web_src = REPO_ROOT / "web" / "src"
    hits = subprocess.run(
        ["git", "grep", "-lF", "safe-area-inset", "--", str(web_src)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert hits.stdout.strip() == "", (
        "web/src now handles safe-area insets itself, so the shell's native contentInset / "
        "adjustMarginsForEdgeToEdge may now be double-insetting the companion:\n"
        f"{hits.stdout}"
    )


# ── the registry contract ───────────────────────────────────────────────────


def test_the_shell_reuses_the_endpoint_registry_contract():
    """``endpoints.ts`` names mobile as a consumer; this asserts the shell really is one.

    Before ``MC-7`` that module had zero production importers, so its format was declared and
    unconsumed — exactly the state in which a second shell quietly invents its own key. The three
    literals below are the whole shared vocabulary.
    """
    endpoints = ENDPOINTS_TS.read_text(encoding="utf-8")
    registry = REGISTRY_MJS.read_text(encoding="utf-8")

    key = re.search(r"export const REGISTRY_STORAGE_KEY = '([^']+)'", endpoints)
    assert key, "endpoints.ts no longer exports REGISTRY_STORAGE_KEY as a literal"
    assert f"export const REGISTRY_STORAGE_KEY = '{key.group(1)}'" in registry, (
        f"the shell must persist under endpoints.ts's key ({key.group(1)!r}); a second key is a "
        "second registry contract."
    )

    # `CompanionEndpoint`'s declared fields, read off the interface body.
    interface = re.search(r"export interface CompanionEndpoint \{(.*?)\n\}", endpoints, re.DOTALL)
    assert interface, "endpoints.ts no longer declares interface CompanionEndpoint"
    declared = re.findall(r"^\s{2}(\w+)[?]?:", interface.group(1), re.MULTILINE)
    assert set(declared) == {"id", "label", "base_url", "kind", "device_session_ref"}, declared
    assert set(_quoted_list(registry, "ENDPOINT_FIELDS")) == set(declared), (
        "the shell's ENDPOINT_FIELDS no longer matches endpoints.ts's CompanionEndpoint: "
        f"shell={sorted(_quoted_list(registry, 'ENDPOINT_FIELDS'))} vs ts={sorted(declared)}"
    )

    registry_interface = re.search(
        r"export interface EndpointRegistry \{(.*?)\n\}", endpoints, re.DOTALL
    )
    assert registry_interface, "endpoints.ts no longer declares interface EndpointRegistry"
    declared_registry = re.findall(r"^\s{2}(\w+)[?]?:", registry_interface.group(1), re.MULTILINE)
    assert set(_quoted_list(registry, "REGISTRY_FIELDS")) == set(declared_registry)


def test_the_shell_mints_endpoint_ids_the_way_endpoints_ts_does():
    """A shell whose ids look different is a shell whose rows another shell cannot recognize."""
    endpoints = ENDPOINTS_TS.read_text(encoding="utf-8")
    registry = REGISTRY_MJS.read_text(encoding="utf-8")
    for source, name in ((endpoints, "endpoints.ts"), (registry, "registry.mjs")):
        assert "'abcdefghijklmnopqrstuvwxyz0123456789'" in source, f"{name}: id alphabet changed"
        assert "'ep_'" in source, f"{name}: id prefix changed"


# ── no forked UI ────────────────────────────────────────────────────────────


def _tracked_mobile_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "mobile"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert len(out) >= 10, f"git ls-files mobile found only {len(out)} files — the scan broke"
    return out


def test_the_shell_holds_no_copy_of_the_companion_ui():
    """ "No forked UI" is the load-bearing clause of this atom, so it gets a rail.

    A component file under ``mobile/`` is the unambiguous form of the failure: the moment the
    shell renders a loop list of its own, the companion has two implementations and the gateway
    stops being the single source of what the phone shows.
    """
    forked = [f for f in _tracked_mobile_files() if f.endswith((".tsx", ".jsx", ".vue", ".svelte"))]
    assert (
        forked == []
    ), f"component files under mobile/ mean the companion UI has been forked: {forked}"


def test_the_shell_does_not_reach_into_the_web_bundle():
    """A cross-workspace source import is the subtler form of the same fork.

    It would also break the shell outright — ``mobile/www`` is copied verbatim into the native
    app with no bundler, so a ``../../web/src`` specifier resolves to nothing on a device.
    """
    for name in _tracked_mobile_files():
        path = REPO_ROOT / name
        if path.suffix not in {".mjs", ".js", ".html", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for offender in ("web/src", "@capacitor/core'", "from 'web"):
            if offender == "web/src" and name == "mobile/README.md":
                continue
            assert f"import {offender}" not in text and f"from '{offender}" not in text, (
                f"{name} imports {offender!r} — mobile/www is copied verbatim into the native "
                "app with no bundler, so a bare or cross-workspace specifier resolves to nothing."
            )


def test_the_shell_never_redeems_a_pairing_code_itself():
    """The device-session contract on ``main`` is a cookie, and there must stay one of it.

    ``POST /api/devices/pair/complete`` answers with an httponly ``Set-Cookie``, so a native
    redemption would hold a session the WebView could not use — and would be a second
    device-session mechanism beside the served ``/pair`` page's.
    """
    for name in _tracked_mobile_files():
        path = REPO_ROOT / name
        if path.suffix not in {".mjs", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        # Comments in these files DISCUSS the route on purpose, so the rail looks for a call,
        # not for the string: a text scan for `pair/complete` alone would fire on the docstring
        # that explains why the shell does not call it.
        assert not re.search(r"fetch\s*\(\s*[`'\"][^`'\"]*pair/complete", text), (
            f"{name} calls pair/complete directly; the shell hands redemption to the served "
            "/pair page so the httponly cookie lands in the WebView's own jar."
        )


def test_the_generated_native_projects_are_not_committed():
    """``ios/`` and ``android/`` are Capacitor templates, rewritten by ``cap sync``.

    A committed copy is a fork of the template that drifts from the Capacitor version in
    ``package.json`` — the class of bug where a store build works from one checkout and nowhere
    else.
    """
    tracked = _tracked_mobile_files()
    for name in tracked:
        assert not name.startswith(
            ("mobile/ios/", "mobile/android/")
        ), f"{name} is generated output"
        assert "node_modules" not in name, f"{name} is an installed dependency"
        assert not name.endswith(".xcodeproj"), f"{name} is generated output"

    ignored = (MOBILE / ".gitignore").read_text(encoding="utf-8")
    for entry in ("/ios/", "/android/", "Pods/"):
        assert entry in ignored, f"mobile/.gitignore does not ignore {entry}"


def test_the_mobile_tier_is_wired_the_way_the_ci_rail_requires():
    """A companion to ``tests/test_ci_tier_enforcement.py``, stated from this atom's side.

    That file enforces the general rule; this one records that ``mobile`` is deliberately a
    workspace member *because* of it. Keeping the shell outside the workspace graph would have
    kept 37 node cases out of every gate — the exact gap that rail was written to close.
    """
    root = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert "mobile" in root["workspaces"]
    assert root["scripts"]["test:mobile"] == "npm run test --workspace=mobile"
    manifest = json.loads((MOBILE / "package.json").read_text(encoding="utf-8"))
    assert manifest["scripts"]["test"] == "node --test test/*.test.mjs"
    # The native builds are NOT a CI tier: they need Xcode and the Android SDK.
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm run test:mobile" in ci
    assert "cap build" not in ci, "a native build in CI would need Xcode and the Android SDK"
