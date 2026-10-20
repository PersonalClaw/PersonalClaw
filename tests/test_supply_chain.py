"""Rails for lock refresh, the release's SBOMs and its provenance attestations."""

from __future__ import annotations

from supply_chain_shared import _LOGIN_SHELL_RE, README, REPO_ROOT, _logical_lines

MAKEFILE = REPO_ROOT / "Makefile"


def _make_recipe(target: str) -> list[str]:
    recipe: list[str] = []
    in_target = False
    for raw in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t"):
            if in_target:
                recipe.append(raw.lstrip("\t"))
            continue
        if raw.startswith("#") or not raw.strip():
            continue
        head, separator, _tail = raw.partition(":")
        if separator and not head.startswith("."):
            in_target = head.strip() == target
    return recipe


def test_make_lock_runs_the_real_uv_lock_command_and_is_listed_in_help() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "UV      ?= uv" in makefile
    assert _make_recipe("lock") == ["$(UV) lock"]
    assert "## lock: refresh uv.lock" in makefile


# ---------------------------------------------------------------------------
# The wheel's SBOM lists what the wheel installs, and the Release attaches it
# ---------------------------------------------------------------------------
#
# v0.2.0's `build` handed syft `dist/`. syft catalogs INSTALLED packages, and `dist/` holds the
# wheel and the sdist as two files, so its SBOM recorded one package (the directory itself) and
# no job attached even that to the Release, while the README promised an SBOM for the wheel.
# The step is dry-run here: its own `run:` script, with `uv` and syft replaced by stand-ins that
# do to a real venv what each does, so what is checked is what the release runs.


def test_the_readme_shows_no_badge_that_nothing_publishes() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "coverage-badge.json" not in readme, "that badge read a branch nothing writes any more"


# ---------------------------------------------------------------------------
# A container command may never be run through a LOGIN shell (#3562)
# ---------------------------------------------------------------------------
#
# 🔴 THE MEASURED DEFECT. `release.yml`'s release-blocking per-arch smoke ran
# `sh -lc "$SMOKE"` from 2026-08-10 (#1004) onwards, and the last Release before the fix
# was v0.1.3 on 2026-07-31 — so the step had NEVER executed and v0.2.0 would have been its
# first run. `-l` makes the shell read `/etc/profile`, and `/etc/profile` does not EXTEND
# `PATH`, it OVERWRITES it, discarding the image's own `ENV PATH`. Measured in containers
# against the published 0.1.3 gateway image:
#
#     sh -lc 'personalclaw --version'  ->  127  "sh: 1: personalclaw: not found"
#     sh -c  'personalclaw --version'  ->  0    "personalclaw 0.1.3"
#
# `images` pushes `:X.Y.Z`, `:X.Y` and `:latest` BEFORE it smokes what it pushed, so this
# would have moved three GHCR tags and then failed the job — published tags with no
# GitHub Release to explain them.
#
# WHY THE RULE IS ABOUT THE LOGIN SHELL AND NOT ABOUT THE USER. The issue attributed the
# `web` leg's survival to `/etc/profile` taking its root branch. Measured, that is only
# half true and the other half is worse: Debian's profile does branch on `id -u` (the
# gateway drops to uid 10001), but web's **Alpine** profile overwrites `PATH` in ONE
# unconditional line with no user branch at all — it survives purely because that line
# happens to contain `/usr/sbin`, where `nginx` lives, and it survives as a non-root user
# too. Both bases clobber `PATH`; only one clobbers it into something that still works. So
# the rule cannot be "do not do this as a non-root user" — it is "do not do this at all",
# and the `web` leg is one binary relocation away from the same 127.


def test_the_login_shell_detector_fires_and_ignores_prose() -> None:
    """Three controls on the detector itself, because a regex that cannot match reads clean.

    The middle one is the trap this rail was written to survive: the commit that fixes a
    login-shell bug has to WRITE `sh -lc` down in order to explain it, so a detector that
    counts comments reds its own fix and the next author reaches for rewording instead.
    """
    invocation = 'docker run --rm --entrypoint \'\' "$IMAGE" \\\n  sh -lc "$SMOKE"'
    assert [
        line for line in _logical_lines(invocation) if _LOGIN_SHELL_RE.search(line)
    ], "the detector does not fire on the exact pre-fix invocation, so its zero is vacuous"

    documented = 'docker run --rm "$IMAGE" sh -c "$SMOKE"\n# it used to say sh -lc, which broke'
    assert not [
        line for line in _logical_lines(documented) if _LOGIN_SHELL_RE.search(line)
    ], "a whole-line comment explaining the fix must not read as the defect"

    for variant in ("sh -l -c cmd", "bash -lc cmd", "sh -cl cmd", "/bin/bash --login -c cmd"):
        assert _LOGIN_SHELL_RE.search(f'docker run "$IMAGE" {variant}'), variant
    for benign in ("sh -c cmd", "git stash -l", "npm publish -l", "sh -eu -c cmd"):
        assert not _LOGIN_SHELL_RE.search(f'docker run "$IMAGE" {benign}'), benign
