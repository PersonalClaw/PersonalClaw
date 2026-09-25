"""Rails for the macOS desktop signing guards — the three that must not be silently removed.

The macOS dmg shipped wrong twice in two days, and the second defect was caused by the fix for
the first:

1. electron-builder AUTO-DISCOVERS a signing identity from the build machine's login keychain,
   so a local build produced ``PersonalClaw.app`` signed ``Authority=MeetNote Developer`` — an
   unrelated third party's identity on what would have been a public release artifact — while
   the Makefile comment claimed the build was unsigned.
2. Fixing (1) with ``"identity": null`` disabled signing, which does NOT leave the bundle
   unsigned: it leaves Electron's stock LINKER seal, which declares that sealed resources must
   be present while sealing none of ``Frameworks``, the four helper apps, ``app.asar`` or the
   PyInstaller backend. macOS refuses a damaged signature more firmly than an absent one, so
   that build failed to install harder than the foreign-signed one did.

Three guards now hold the artifact in its intended state — ad-hoc signed, valid seal, no named
authority — and **each one is invisible in the artifact if it goes missing**, which is precisely
how both defects survived:

* ``CSC_IDENTITY_AUTO_DISCOVERY=false`` in the ``desktop-dist`` recipe,
* ``"identity": null`` + ``"afterPack"`` in ``desktop/package.json``,
* ``scripts/verify_macos_app_signature.sh`` invoked by ``desktop-dist`` AND by ``release.yml``'s
  ``desktop-mac`` job.

These rails assert the guards are WIRED. They deliberately do not re-derive what the gate
checks: that belongs in the script, and it is proven against three real artifacts (a
foreign-signed bundle, a damaged-seal bundle and a known-good one). What a suite can cheaply
catch is somebody deleting a line — the failure mode both defects actually had.

Parsed from source rather than executed: codesign and electron-builder are not test
dependencies, and this runs on Linux CI too. Same precedent as ``test_desktop_seam.py`` and
``test_desktop_install_kind.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
DESKTOP_PACKAGE_JSON = REPO_ROOT / "desktop" / "package.json"
GATE = REPO_ROOT / "scripts" / "verify_macos_app_signature.sh"
AFTER_PACK = REPO_ROOT / "desktop" / "afterPack.js"

#: The gate's filename, referenced by both callers. Kept as one constant so a rename has to
#: update this file too rather than quietly unwiring one of the two call sites.
GATE_SCRIPT_NAME = "scripts/verify_macos_app_signature.sh"


def _make_recipe(target: str) -> list[str]:
    """The recipe lines of one Makefile target (tab-indented lines under its rule)."""
    recipe: list[str] = []
    in_target = False
    for raw in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t"):
            if in_target:
                recipe.append(raw.lstrip("\t"))
            continue
        if raw.startswith("#") or not raw.strip():
            continue
        in_target = raw.split(":", 1)[0].strip() == target
    assert recipe, f"Makefile target {target!r} has no recipe — these rails would pass vacuously"
    return recipe


def _desktop_build_config() -> dict:
    config = json.loads(DESKTOP_PACKAGE_JSON.read_text(encoding="utf-8"))["build"]
    assert config, "desktop/package.json has no build block — these rails would pass vacuously"
    return config


def _desktop_mac_job() -> dict:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["desktop-mac"]
    assert job.get("steps"), "desktop-mac has no steps — these rails would pass vacuously"
    return job


# ── the gate exists and is runnable ────────────────────────────────────────────────────────


def test_the_signature_gate_exists_and_is_executable() -> None:
    """A gate invoked as a bare path must carry its execute bit or the build dies at the call."""
    assert GATE.is_file(), f"{GATE_SCRIPT_NAME} is missing — both call sites below would fail"
    assert os.access(GATE, os.X_OK), (
        f"{GATE_SCRIPT_NAME} is not executable; the Makefile and release.yml both invoke it "
        "as a bare path, so a lost execute bit breaks the build rather than the check"
    )


def test_the_gate_reads_exit_status_and_never_swallows_it() -> None:
    """``|| true`` anywhere in the gate would make every check vacuous.

    The whole gate turns on codesign's EXIT STATUS: an unsigned app exits non-zero, and
    ``codesign --verify --deep --strict`` exiting non-zero is the one signal that catches the
    damaged-seal state. A swallowed status silently inverts the gate into a no-op.
    """
    text = GATE.read_text(encoding="utf-8")
    assert "codesign --verify --deep --strict" in text or "--verify" in text, (
        "the gate must use `codesign --verify --deep --strict` as its primitive — an "
        "authority-absence check passes the damaged bundle, which carries no Authority= line"
    )
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "|| true" in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        "the gate swallows a command's exit status with `|| true`, which is exactly what makes "
        f"a codesign check vacuous: {offenders}"
    )


# ── guard 1: auto-discovery is off in the build recipe ──────────────────────────────────────


def test_desktop_dist_disables_signing_identity_auto_discovery() -> None:
    """Without this, electron-builder signs with whatever identity the keychain happens to hold."""
    recipe = "\n".join(_make_recipe("desktop-dist"))
    assert "CSC_IDENTITY_AUTO_DISCOVERY=false" in recipe, (
        "desktop-dist must set CSC_IDENTITY_AUTO_DISCOVERY=false; without it electron-builder "
        "auto-discovers a Developer identity from the build machine's login keychain, which is "
        "how a release artifact came to be signed by an unrelated third party"
    )


# ── guard 2: the manifest says the same thing, and names the re-signing hook ────────────────


def test_desktop_manifest_declares_no_signing_identity() -> None:
    """Belt and braces with the environment variable, which is lost on any other invocation."""
    mac = _desktop_build_config()["mac"]
    assert "identity" in mac, (
        'desktop/package.json build.mac must declare "identity"; the environment variable in '
        "the Makefile is lost the moment the build is invoked another way (npm directly, CI, a "
        "future script)"
    )
    assert mac["identity"] is None, (
        f'build.mac.identity must be null, got {mac["identity"]!r} — a non-null value asks '
        "electron-builder to sign with a real identity, which the owner ruling of 2026-09-22 "
        "rules out"
    )


def test_desktop_manifest_wires_the_adhoc_resigning_hook() -> None:
    """``identity: null`` alone leaves Electron's stock linker seal, which seals none of our files.

    The hook must run at pack time, not after ``npm run dist``: a post-dmg step would sign an
    app the dmg already contains.
    """
    config = _desktop_build_config()
    assert "afterPack" in config, (
        'desktop/package.json build must declare "afterPack"; without it the bundle keeps '
        "Electron's linker signature, which declares sealed resources while sealing none of "
        "Frameworks, the helper apps, app.asar or the PyInstaller backend — an uninstallable dmg"
    )
    assert (
        AFTER_PACK.is_file()
    ), f"build.afterPack points at {config['afterPack']!r} but {AFTER_PACK} is missing"
    hook = AFTER_PACK.read_text(encoding="utf-8")
    assert "--sign" in hook and '"-"' in hook.replace("'", '"'), (
        "afterPack must ad-hoc sign (`--sign -`), which needs no identity, keychain or Apple "
        "account and so does not reverse the two guards above"
    )
    assert "--verify" in hook, (
        "afterPack must verify its own work before the dmg is assembled; signing without "
        "verifying is how the damaged seal reached a user"
    )


# ── guard 3: both call sites invoke the gate ────────────────────────────────────────────────


def test_desktop_dist_verifies_the_built_app() -> None:
    recipe = "\n".join(_make_recipe("desktop-dist"))
    assert GATE_SCRIPT_NAME in recipe, (
        f"desktop-dist must invoke {GATE_SCRIPT_NAME} after the build, so a developer on a Mac "
        "that holds a Developer identity cannot produce a mis-signed app silently"
    )


def test_the_release_mac_job_verifies_the_app_inside_the_dmg() -> None:
    """CI never reproduced the foreign-identity bug (no keychain) but WOULD reproduce the seal bug.

    The stale linker seal comes from Electron itself, not from the keychain, so an
    identity-less runner produces it too. Checking the copy inside the mounted dmg is what
    holds local and CI artifacts to one standard.
    """
    steps = _desktop_mac_job()["steps"]
    runs = "\n".join(step.get("run", "") for step in steps)
    assert GATE_SCRIPT_NAME in runs, (
        f"release.yml's desktop-mac job must invoke {GATE_SCRIPT_NAME} on the app it mounts "
        "from the dmg; otherwise a stale-linker-seal regression ships an uninstallable dmg"
    )


@pytest.mark.parametrize("forbidden", ["assert_macos_app_unsigned"])
def test_no_call_site_still_names_the_retired_gate(forbidden: str) -> None:
    """The gate was renamed when it stopped being only an "is it unsigned" check.

    A call site left on the old name is a build that dies at the invocation — or worse, a
    reader who believes the old, authority-only contract still applies.
    """
    for path in (MAKEFILE, RELEASE_WORKFLOW):
        assert forbidden not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(REPO_ROOT)} still references the retired {forbidden!r}; the gate "
            f"is now {GATE_SCRIPT_NAME}"
        )
