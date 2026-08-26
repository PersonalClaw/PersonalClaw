"""The rail over BA-2's behavioural proof: it must be *reachable*, not merely present.

``BROWSE-AUTOMATION`` `BA-2` has two ``done_when`` clauses that only a running browser can
satisfy — the injected safety script making ``fetch()`` / ``media.play()`` /
``navigator.bluetooth`` throw or return blocked, and client-side redirects being re-evaluated per
``Page.frameNavigated``. Both are proven by ``tests/test_browse_safety_script.py`` (layer 2) and
``tests/test_browse_cdp_live.py``, and both files ``pytest.skip`` when no browser is found.

**A skip is counted as a pass.** So those two files can stop proving anything at all without a
single red appearing anywhere — which is exactly what their previous browser lookup arranged. It
was two absolute literals naming one Playwright revision (``chromium_headless_shell-1234``) and
one OS/CPU (``chrome-headless-shell-mac-arm64``). Consequently:

* a ``playwright`` bump (``package.json`` says ``^1.62.1``) renames the revision directory and all
  22 behavioural tests silently stop running;
* on Linux — where the CI pytest job runs — the literals could never match, so on the machine
  that gates every merge the behavioural layer had *never executed once*. Its green was 22 skips.

This file is the rail that would have caught both. The load-bearing case asks the question the
literals answered wrongly — *"a Chromium is installed on this machine; can we find it?"* — and it
establishes "installed" from Playwright's own ``INSTALLATION_COMPLETE`` marker, which
:func:`browse_chrome.find_chrome` never reads. That independence is the point: a floor computed
from the value it is meant to pin cannot pin it.

The synthetic-layout cases below are that rail's **vacuity floor**. The installed-browser case can
only run where a browser is installed, so on a bare machine it skips — and a rail whose only case
skips is no rail. The synthetic cases run everywhere, and they pin the two properties the literals
got wrong (revision-agnostic, and reaching the Linux layout CI would use) plus the negative
direction, so ``find_chrome`` cannot pass them by returning a constant.
"""

from __future__ import annotations

import pathlib
import stat

import browse_chrome
import pytest

_TESTS = pathlib.Path(__file__).resolve().parent

#: The two modules whose whole behavioural value depends on the lookup this file rails.
_PROOF_MODULES = (
    _TESTS / "test_browse_safety_script.py",
    _TESTS / "test_browse_cdp_live.py",
)


def _install(root: pathlib.Path, revision_dir: str, binary: str) -> pathlib.Path:
    """Lay out a Playwright-shaped install and return the executable's path."""
    target = root / revision_dir / binary
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"#!/bin/sh\nexit 0\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    (root / revision_dir / browse_chrome.INSTALL_MARKER).write_text("")
    return target


# ── the load-bearing case: installed must mean discoverable ───────────────────


def test_an_installed_chromium_is_discoverable() -> None:
    """THE rail. If Playwright says a Chromium is installed, we must be able to launch it.

    ``installed_revision_dirs`` reads Playwright's ``INSTALLATION_COMPLETE`` marker and nothing
    else; ``find_chrome`` never looks at that marker. So this compares two independent answers to
    "is there a browser here", and the disagreement it catches — *installed, but our lookup is
    blind to it* — is the precise state in which BA-2's behavioural clauses stop being tested
    while the suite stays green.
    """
    installed = browse_chrome.installed_revision_dirs()
    if not installed:
        pytest.skip(
            "no Playwright Chromium installed here, so there is nothing for the lookup to be "
            "blind to. The synthetic cases below carry this rail on such a machine. Install one "
            "with `npx playwright install chromium` to exercise this case for real."
        )

    found = browse_chrome.find_chrome()
    assert found is not None, (
        "Playwright reports an installed Chromium at "
        f"{[str(path) for path in installed]} but find_chrome() returned None — BA-2's "
        "behavioural proof is skipping on a machine that CAN run it, and a skip reads as a pass."
    )
    assert pathlib.Path(found).is_file()


# ── the vacuity floor: the lookup discriminates, and on the axes that broke ───


def test_discovery_is_revision_agnostic(tmp_path: pathlib.Path) -> None:
    """The first failure mode: a revision bump must not silently unplug the proof."""
    binary = _install(
        tmp_path,
        "chromium_headless_shell-999999",
        "chrome-headless-shell-mac-arm64/chrome-headless-shell",
    )

    assert browse_chrome.find_chrome((tmp_path,)) == str(binary)


def test_discovery_reaches_the_linux_layout(tmp_path: pathlib.Path) -> None:
    """The second failure mode: CI runs pytest on Linux, where the old literals never matched."""
    binary = _install(
        tmp_path,
        "chromium_headless_shell-1181",
        "chrome-headless-shell-linux64/chrome-headless-shell",
    )

    assert browse_chrome.find_chrome((tmp_path,)) == str(binary)


def test_discovery_reaches_a_full_chromium_app_bundle(tmp_path: pathlib.Path) -> None:
    """The headless shell is preferred, but a full Chromium is still a usable browser."""
    binary = _install(
        tmp_path, "chromium-1181", "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium"
    )

    assert browse_chrome.find_chrome((tmp_path,)) == str(binary)


def test_the_headless_shell_is_preferred_over_the_full_browser(tmp_path: pathlib.Path) -> None:
    """Ordering, asserted — otherwise "prefer the shell" is a comment, not a behaviour."""
    shell = _install(
        tmp_path,
        "chromium_headless_shell-1181",
        "chrome-headless-shell-mac-arm64/chrome-headless-shell",
    )
    _install(tmp_path, "chromium-1181", "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium")

    assert browse_chrome.find_chrome((tmp_path,)) == str(shell)


def test_an_empty_root_is_not_a_false_positive(tmp_path: pathlib.Path) -> None:
    """NEGATIVE control for every case above: they are not passing on a constant.

    Without this, ``find_chrome`` could ``return "/anything"`` and the four cases above would
    still be green — the assertions compare against a path this test builds, but a lookup that
    ignored its argument entirely would be caught only here.
    """
    (tmp_path / "ffmpeg-1011").mkdir()

    assert browse_chrome.find_chrome((tmp_path,)) is None
    assert browse_chrome.installed_revision_dirs((tmp_path,)) == ()


def test_a_non_executable_candidate_is_rejected(tmp_path: pathlib.Path) -> None:
    """A same-named plain file is not a browser. ``exists()`` would have accepted it."""
    target = tmp_path / "chromium_headless_shell-1181" / "chrome-linux" / "chrome-headless-shell"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not a browser")
    target.chmod(0o644)

    assert browse_chrome.find_chrome((tmp_path,)) is None


def test_a_directory_named_like_the_binary_is_rejected(tmp_path: pathlib.Path) -> None:
    """Directories are executable-by-default, so ``X_OK`` alone would let one through."""
    (tmp_path / "chromium_headless_shell-1181" / "chrome-linux" / "chrome-headless-shell").mkdir(
        parents=True
    )

    assert browse_chrome.find_chrome((tmp_path,)) is None


def test_an_explicit_override_wins(tmp_path: pathlib.Path, monkeypatch) -> None:
    """The escape hatch for a browser that did not come from Playwright."""
    override = tmp_path / "my-chrome"
    override.write_bytes(b"#!/bin/sh\nexit 0\n")
    override.chmod(override.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(browse_chrome.CHROME_ENV, str(override))

    assert browse_chrome.find_chrome((tmp_path / "empty",)) == str(override)


def test_a_bogus_override_does_not_shadow_a_real_install(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """An override pointing at nothing must fall through, not poison the search."""
    binary = _install(
        tmp_path, "chromium_headless_shell-1181", "chrome-linux/chrome-headless-shell"
    )
    monkeypatch.setenv(browse_chrome.CHROME_ENV, str(tmp_path / "does-not-exist"))

    assert browse_chrome.find_chrome((tmp_path,)) == str(binary)


# ── the skip is allowed, but it must be *declarable* as a failure ─────────────


def test_a_missing_browser_skips_by_default(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Absence skips: a developer with no browser must still be able to run the suite.

    Also the counterpart the next test needs — without it, "REQUIRE_ENV makes it fail" could be
    measuring a lookup that fails unconditionally.
    """
    monkeypatch.delenv(browse_chrome.CHROME_ENV, raising=False)
    monkeypatch.delenv(browse_chrome.REQUIRE_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception) as caught:
        browse_chrome.chrome_or_skip("PROBE", (tmp_path,))

    assert str(tmp_path) in str(caught.value), "the skip must name where it looked"


def test_requiring_the_proof_turns_a_missing_browser_into_a_failure(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """The lever that lets a gate demand the behavioural clauses actually ran."""
    monkeypatch.delenv(browse_chrome.CHROME_ENV, raising=False)
    monkeypatch.setenv(browse_chrome.REQUIRE_ENV, "1")

    with pytest.raises(pytest.fail.Exception) as caught:
        browse_chrome.chrome_or_skip("PROBE", (tmp_path,))

    assert browse_chrome.REQUIRE_ENV in str(caught.value)


def test_the_require_lever_also_covers_the_cdp_transport(monkeypatch) -> None:
    """Half a rail is not a rail: no ``websockets`` skips the proof just as silently.

    Simulated by making the import fail, because ``websockets`` IS installed here — asserting
    "it did not raise" would pass for a function that never checks anything.
    """
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if name == "websockets":
            raise ImportError("simulated: no websockets")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _refuse)

    monkeypatch.delenv(browse_chrome.REQUIRE_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        browse_chrome.websockets_or_skip("PROBE")

    monkeypatch.setenv(browse_chrome.REQUIRE_ENV, "1")
    with pytest.raises(pytest.fail.Exception):
        browse_chrome.websockets_or_skip("PROBE")


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_falsey_require_values_still_skip(value: str, tmp_path: pathlib.Path, monkeypatch) -> None:
    """``REQUIRE=0`` must mean off. An env var read as "set at all" is a trap for CI matrices."""
    monkeypatch.delenv(browse_chrome.CHROME_ENV, raising=False)
    monkeypatch.setenv(browse_chrome.REQUIRE_ENV, value)

    with pytest.raises(pytest.skip.Exception):
        browse_chrome.chrome_or_skip("PROBE", (tmp_path,))


# ── the fix must stay un-forked ───────────────────────────────────────────────


def test_neither_proof_module_carries_its_own_browser_lookup() -> None:
    """One lookup, not three. A second copy is how the first one drifted out of reach.

    Both modules had their own hard-coded copy, so hardening one would have left the other
    blind. Pinning the absence of the marker literal keeps that from re-growing quietly.
    """
    for module in _PROOF_MODULES:
        source = module.read_text()
        assert "ms-playwright" not in source, (
            f"{module.name} spells a Playwright path itself again. That lookup belongs in "
            "tests/browse_chrome.py, where one rail covers it."
        )
        assert (
            "browse_chrome" in source
        ), f"{module.name} no longer routes through browse_chrome, so nothing rails its skip."


def test_the_roots_are_platform_appropriate() -> None:
    """The default roots must include this platform's, or every skip here is unexplained."""
    roots = browse_chrome.browsers_roots()

    assert roots, "no candidate root at all means the lookup cannot succeed anywhere"
    assert any(root.name == "ms-playwright" for root in roots), [str(root) for root in roots]
