"""The desktop shell must DECLARE its install kind (`DC-1` T1.3, DISTRIBUTION C1) — #2673.

`self_update.detect_install_kind()` resolves ``PERSONALCLAW_INSTALL_KIND`` first, then probes
``PERSONALCLAW_PROJECT_DIR`` for a ``.git``, then falls back to ``"pip"``. The ``"desktop"``
member is therefore *produced* by exactly one thing in this repository — the Electron shell's
spawn env — and consumed by three: the Updates panel, ``POST /api/update``, and
``personalclaw update``.

Nothing produced it. `DC-6` shipped the Linux AppImage/.deb to every GitHub Release while
`DC-1`'s install-kind clause was still open, and `desktop/main.js` set ``PROJECT_DIR`` but not
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

    The clause was missable in the first place because the env was an object literal buried in
    ``startGateway``, invisible to every test. Keep the one seam: the builder is executed by
    ``desktop/test/gatewayEnv.test.js``, an inline literal is executed by nothing.
    """
    src = _source("main.js")
    assert 'require("./gatewayEnv")' in src, "main.js must require ./gatewayEnv"
    assert "buildGatewayEnv({" in src, "main.js must build the spawn env through the builder"
    # PERSONALCLAW_DEV_NO_AUTH is the tell: it belongs to the env the builder owns, so an
    # ASSIGNMENT of it in main.js means a second env literal has grown back. Prose about it
    # (the `startGateway` docstring explains the loopback bypass) is not an assignment.
    for lineno, line in enumerate(src.splitlines(), start=1):
        if re.search(r"PERSONALCLAW_DEV_NO_AUTH\s*[:=]", line):
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


# ── the consuming side: no surface may promise an updater the shell lacks ─────


def _has_electron_updater() -> bool:
    """Whether the shell actually ships an auto-updater (the other half of `DC-1`)."""
    pkg = json.loads(_source("package.json"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return "electron-updater" in deps


def test_no_surface_promises_self_update_while_the_updater_is_unbuilt() -> None:
    """Three surfaces tell a desktop user how to update. None may overstate the shell.

    All three said the app "updates itself" — copy written for `DC-1`'s electron-updater,
    which is not in ``desktop/package.json`` and is not called anywhere in the shell. Under
    #2673's install-kind fix that copy became reachable for the first time, so it had to
    become true rather than merely newly visible.

    WHEN `DC-1` LANDS THE UPDATER this test reds, and that is its second job: restoring the
    self-update wording is then correct, and the same commit should relax this rail. A
    premise-checked claim, not a permanent ban.
    """
    if _has_electron_updater():
        raise AssertionError(
            "desktop/package.json now depends on electron-updater — the shell can self-update. "
            "Restore the self-update wording in the three desktop surfaces (updates.py detail, "
            "UpdatesPanel.tsx, cli_server._update_desktop) and relax this rail in the same "
            "commit."
        )

    surfaces = (
        "src/personalclaw/dashboard/handlers/updates.py",
        "web/src/pages/settings/UpdatesPanel.tsx",
        "src/personalclaw/cli_server.py",
    )
    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        # Only the user-facing strings matter; the comments in these files explain the history
        # and legitimately quote the retired wording. Strip nothing — instead require that any
        # surviving "updates itself" sits on a comment line.
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "updates itself" not in line:
                continue
            stripped = line.strip()
            assert stripped.startswith(("#", "//", "*", "/*")), (
                f"{relative}:{lineno} tells a desktop user the app updates itself, but the "
                "shell ships no updater"
            )

    # And the honest answer must actually be there, in all three.
    releases = "github.com/PersonalClaw/PersonalClaw/releases"
    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        if relative.endswith("updates.py"):
            assert "releases page" in text, f"{relative} does not name the releases page"
        else:
            assert releases in text, f"{relative} does not link the releases page"
