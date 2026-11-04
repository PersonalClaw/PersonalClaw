"""The desktop shell must DECLARE its install kind — #2673.

`self_update.detect_install_kind()` resolves ``PERSONALCLAW_INSTALL_KIND`` first, then asks
whether the process is FROZEN, then whether the running package comes from a git checkout, then
falls back to ``"pip"``. The ``"desktop"`` member has two producers: the Electron shell's spawn
env (asserted here) and the frozen artefact answering for itself
(``tests/test_frozen_bundle_runtime.py``) — a second, independent producer added on 2026-09-23
so the correct refusal no longer depends on an environment variable being present. It is
consumed by three: the Updates panel, ``POST /api/update``, and ``personalclaw update``.

Nothing produced it. The Linux AppImage/.deb shipped to every GitHub Release while
the install kind was still unwired, and `desktop/main.js` set ``PROJECT_DIR`` but not
``INSTALL_KIND`` — so inside the bundle the project dir is ``…/resources`` (no ``.git``) and the
gateway classified itself as a **pip install**. The Updates panel then offered an in-app apply
that runs ``<installer> install -U personalclaw==<tag>`` against ``sys.executable``, which in a
packaged app is the frozen PyInstaller backend: there is no interpreter there to upgrade.

Every existing test of the desktop kind sets the env var ITSELF
(``tests/test_self_update.py::test_desktop_env_wins``,
``test_update_apply_kind.py::test_desktop_returns_instructions``,
``test_cli_update_kinds.py::test_desktop_kind_delegates_to_the_app_and_exits_zero``) — a suite
that stayed green over a value no shipped code emitted. These are the rails that read the
producing side.

Parsed from source rather than executed, the ``test_desktop_seam.py`` precedent: node is not a
test dependency, and `desktop/test/gatewayEnv.test.js` already executes the builder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from personalclaw.self_update import INSTALL_KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP = REPO_ROOT / "desktop"


def _source(relative: str) -> str:
    path = DESKTOP / relative
    assert path.is_file(), f"desktop/{relative} is missing"
    return path.read_text(encoding="utf-8")


# ── the producing side ────────────────────────────────────────────────────────


def test_the_shell_declares_the_desktop_install_kind() -> None:
    """🔴 THE #2673 REGRESSION. The env the shell hands the gateway must carry the kind.

    Asserted against the Python taxonomy, not against a bare string: a shell setting
    ``PERSONALCLAW_INSTALL_KIND=app`` would be junk that ``detect_install_kind`` ignores,
    falling straight back through to ``pip`` — the very outcome this pins shut.
    """
    src = _source("gatewayEnv.js")
    m = re.search(r'const INSTALL_KIND = "([^"]+)";', src)
    assert m, 'desktop/gatewayEnv.js must declare `const INSTALL_KIND = "…"`'
    assert m.group(1) == "desktop", f"the shell declares {m.group(1)!r}"
    assert m.group(1) in INSTALL_KINDS, (
        f"{m.group(1)!r} is not a self_update.INSTALL_KINDS member {INSTALL_KINDS} — "
        "detect_install_kind() would ignore it and classify the app as a pip install"
    )
    assert re.search(
        r"PERSONALCLAW_INSTALL_KIND:\s*INSTALL_KIND", src
    ), "gatewayEnv.js declares the kind but does not put it in the child environment"


def test_the_declared_kind_wins_over_an_inherited_one() -> None:
    """The explicit keys must be spread AFTER the inherited env.

    A ``PERSONALCLAW_INSTALL_KIND=container`` in the user's shell profile would otherwise make
    a desktop install describe itself as a container and print `docker compose` instructions
    to somebody running an .app. Ordering is the whole mechanism, so it is read as ordering.
    """
    src = _source("gatewayEnv.js")
    body = re.search(r"return \{(.*?)\n  \};", src, re.S)
    assert body, "buildGatewayEnv's returned object literal not found"
    literal = body.group(1)
    assert "...inherited" in literal, "buildGatewayEnv no longer spreads the inherited env"
    assert "PERSONALCLAW_INSTALL_KIND" in literal, "the returned env carries no install kind"
    assert literal.index("...inherited") < literal.index("PERSONALCLAW_INSTALL_KIND"), (
        "the inherited env must be spread BEFORE the explicit keys, or an inherited "
        "PERSONALCLAW_INSTALL_KIND would win"
    )


def test_main_js_builds_the_spawn_env_through_the_shared_builder() -> None:
    """main.js must not reconstruct the env inline again.

    The install kind was missable in the first place because the env was an object literal buried in
    ``startGateway``, invisible to every test. Keep the one seam: the builder is executed by
    ``desktop/test/gatewayEnv.test.js``, an inline literal is executed by nothing.
    """
    src = _source("main.js")
    assert 'require("./gatewayEnv")' in src, "main.js must require ./gatewayEnv"
    assert "buildGatewayEnv({" in src, "main.js must build the spawn env through the builder"
    # The keys the builder sets are the tell: an ASSIGNMENT of one in main.js means a second env
    # literal has grown back.
    for lineno, line in enumerate(src.splitlines(), start=1):
        if re.search(r"PERSONALCLAW_(INSTALL_KIND|PROJECT_DIR)\s*[:=]", line):
            raise AssertionError(
                f"desktop/main.js:{lineno} sets a spawn-env key directly again — those belong "
                "in gatewayEnv.js, where a test can see them"
            )


def test_the_shipped_bundle_carries_the_env_builder() -> None:
    """A module absent from ``build.files`` is absent from the packaged app.

    ``desktop/test/packaging.test.js`` walks main.js's requires transitively and would catch
    this too; asserted here as well because the consequence is silent — the app fails at
    launch inside the bundle only, which is the one place nothing runs a test.
    """
    pkg = json.loads(_source("package.json"))
    assert "gatewayEnv.js" in pkg["build"]["files"]


# ── the consuming side: every surface says the updater the shell has ─────────


def _has_electron_updater() -> bool:
    """Whether the shell depends on electron-updater (it does not: its updater is its own)."""
    pkg = json.loads(_source("package.json"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return "electron-updater" in deps


def _ships_its_own_updater() -> bool:
    """Whether the packaged shell carries its updater and starts it: ``appUpdate.js`` in
    ``build.files`` and required by ``main.js``, with Check for Updates… in its menu."""
    pkg = json.loads(_source("package.json"))
    main = _source("main.js")
    return (
        "appUpdate.js" in pkg["build"]["files"]
        and 'require("./appUpdate")' in main
        and 'label: "Check for Updates…"' in main
    )


def test_every_surface_says_the_app_updates_when_asked_and_names_the_releases_page() -> None:
    """Four surfaces tell a desktop user how to update. Each says what the shell does.

    They once said the app "updates itself", copy written for an electron-updater that the
    shell never had (#2673): a user told the app self-updated simply never updated. The rail
    then held them to the re-download, until the updater landed. It has: ``desktop/appUpdate.js``
    checks on launch and once a day, and installs only when its owner presses for it, from
    Check for Updates… in the app's menu or Settings › Updates › This app. So each surface names
    that way, and the releases page beside it, which is where every copy the app cannot replace
    (a package, a copy run from its disk image) and every failure is sent.

    The gateway's answer and the CLI's are read where the app is not (a browser tab,
    ``personalclaw update``), so they name the app's menu; the done screen's sentence and the
    panel name This app.
    """
    assert _ships_its_own_updater(), (
        "the shell no longer ships its updater (appUpdate.js in build.files, required by "
        "main.js, Check for Updates… in its menu): say the re-download again on every surface"
    )
    assert (
        not _has_electron_updater()
    ), "desktop/package.json depends on electron-updater: one updater, not two (appUpdate.js)"

    releases = "github.com/PersonalClaw/PersonalClaw/releases"
    # The web keeps the address once (`lib/repoDocs.RELEASES_PAGE_URL`); a page links that.
    shared = (REPO_ROOT / "web/src/lib/repoDocs.ts").read_text(encoding="utf-8")
    assert releases in shared, "lib/repoDocs no longer holds the releases page's address"
    surfaces = {
        "src/personalclaw/dashboard/handlers/updates.py": ("Check for Updates…", "releases page"),
        "src/personalclaw/cli_server.py": ("Check for Updates…", releases),
        "web/src/lib/updateRoute.ts": ("Settings › Updates › This app", ""),
        "web/src/pages/settings/UpdatesPanel.tsx": ("This app updates itself", "RELEASES_PAGE_URL"),
    }
    for relative, (asked, page) in surfaces.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert asked in text, f"{relative} does not name how the app is asked to update"
        if page:
            assert page in text, f"{relative} does not name the releases page"
    # Nothing installs without a press, so no surface may say it does.
    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            said = re.search(r"(?<!for )\bupdates (itself )?(on its own|automatically)", line)
            if said and "never" not in line:
                raise AssertionError(f"{relative}:{lineno} says the desktop app updates on its own")
