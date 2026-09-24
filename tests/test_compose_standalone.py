"""DIST-16 — `deploy/compose/compose.yaml` is true to its own header.

**The defect, measured on ``origin/main`` @ ``bbdfecfb0``.** The file's header said

    Pulls pre-built images from the registry. Use it to run a release without
    checking out the source tree, or to validate a published image.

and then declared, on two of its three services (``compose.yaml:22`` and ``:71``)::

    env_file:
      - ../../.env

Per the Compose spec a relative ``env_file`` path resolves from the **compose file's
parent directory** — not the invoking shell's cwd — and ``required`` defaults to **true**.
So the file could not be used the way its own header advertised: copied on its own into a
directory, ``../../.env`` pointed at a path outside that directory and Compose **failed
hard** (``env file … not found``) before pulling anything.

The shipped guide described that failure BACKWARDS. ``docs/guides/platforms.md:190-193``
told the reader "that relative path breaks and your keys silently do not load", which is
the one thing Compose does not do here — a missing required ``env_file`` is a startup
error, not a silent skip. A user debugging "why are my keys empty" was sent looking for a
loading problem that does not exist.

**What this rail asserts.** Two layers, because the layer that can prove the most is not
available everywhere:

1. **Structural, always runs** — every ``env_file`` entry in every shipped compose file is
   long syntax with ``required: false``, and at least one entry per service resolves
   *inside* the compose file's own directory. That is exactly the property that makes a
   standalone copy work, so it is the property the rail pins.
   ``test_the_structural_rail_reds_on_the_old_shape`` feeds it the pre-DIST-16 shape and
   watches it fail, so the rail is not vacuous.
2. **Behavioural, when a container runtime is installed** — copy ``compose.yaml`` ALONE
   into a scratch directory (no ``.env``, no ``deploy/`` nesting) and run
   ``compose config``. Skipped without ``docker``/``finch``, which is why layer 1 exists.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = REPO_ROOT / "deploy" / "compose"
COMPOSE_FILE = COMPOSE_DIR / "compose.yaml"

#: Every shipped compose file. An overlay that reintroduced a required env_file would break
#: the standalone story just as thoroughly as the base file, so the rail sweeps all of them.
COMPOSE_FILES = sorted(COMPOSE_DIR.glob("compose*.yaml"))


def _services(path: Path) -> dict:
    return (yaml.safe_load(path.read_text()) or {}).get("services") or {}


def _entries(env_file) -> list:
    """Normalize ``env_file`` (absent | str | list of str/dict) to a list of entries."""
    if env_file is None:
        return []
    if isinstance(env_file, (str, dict)):
        return [env_file]
    return list(env_file)


def _offending_entries(services: dict) -> list[str]:
    """Every ``env_file`` entry that would make a standalone copy fail.

    An entry offends when it is short syntax (``required`` then defaults to true) or when
    it sets ``required: true`` explicitly. Shared by the real assertion and by its own
    negative case, so the two cannot drift.
    """
    bad: list[str] = []
    for name, service in services.items():
        for entry in _entries((service or {}).get("env_file")):
            if isinstance(entry, str):
                bad.append(f"{name}: {entry!r} is short syntax, so `required` defaults to true")
            elif entry.get("required", True):
                bad.append(f"{name}: {entry.get('path')!r} is required")
    return bad


def test_every_shipped_compose_env_file_is_optional():
    for path in COMPOSE_FILES:
        assert not _offending_entries(_services(path)), (
            f"{path.relative_to(REPO_ROOT)} declares a required env_file — a copy of this "
            "file on its own would fail with `env file … not found` before pulling an image"
        )


def test_every_service_with_an_env_file_can_find_one_beside_the_compose_file():
    """A standalone copy must have a `.env` location it can actually use.

    All-optional alone is not enough: if the only declared location were `../../.env`, a
    downloaded `compose.yaml` would start but could never be given provider keys.
    """
    services = _services(COMPOSE_FILE)
    for name, service in services.items():
        entries = _entries((service or {}).get("env_file"))
        if not entries:
            continue
        paths = [e["path"] if isinstance(e, dict) else e for e in entries]
        assert any(not p.startswith("..") for p in paths), (
            f"{name}: every env_file location ({paths}) is outside this file's own "
            "directory, so a standalone copy has nowhere to put a .env"
        )


def test_the_repo_root_location_still_works_from_a_checkout():
    """The from-a-checkout layout is not broken by the standalone fix.

    Users who already have a repo-root `.env` must keep having their keys loaded, and the
    repo-root entry must come LAST so it wins when both files exist (Compose applies
    `env_file` entries in order).
    """
    for name, service in _services(COMPOSE_FILE).items():
        entries = _entries((service or {}).get("env_file"))
        if not entries:
            continue
        paths = [e["path"] if isinstance(e, dict) else e for e in entries]
        assert "../../.env" in paths, f"{name}: the repo-root .env location was dropped"
        assert (
            paths[-1] == "../../.env"
        ), f"{name}: the repo-root .env must be last so a checkout's .env overrides"


def test_the_structural_rail_reds_on_the_old_shape():
    """The pre-DIST-16 shape, fed to the same predicate the assertion above uses."""
    old = yaml.safe_load("""
        services:
          personalclaw-gateway:
            env_file:
              - ../../.env
          personalclaw-slack:
            env_file:
              - path: ../../.env
                required: true
    """)["services"]
    offenders = _offending_entries(old)
    assert len(offenders) == 2
    assert "short syntax" in offenders[0]
    assert "is required" in offenders[1]


def test_the_header_does_not_claim_something_the_file_cannot_do():
    """The header advertises a source-tree-free run, so nothing may need the tree.

    `build:` is the one key that would make the claim false — it needs a build context,
    i.e. a checkout. It belongs in `compose.build.yaml`, which advertises exactly that.
    """
    text = COMPOSE_FILE.read_text()
    assert "run a release without\n# checking out the source tree" in text
    for name, service in _services(COMPOSE_FILE).items():
        assert "build" not in (
            service or {}
        ), f"{name}: a `build:` key contradicts the header's source-tree-free claim"
        assert str((service or {}).get("image", "")).startswith(
            "ghcr.io/"
        ), f"{name}: the header promises published images"
    # And no volume mounts a host path from the tree (a named volume has no '/' or '.').
    for name, service in _services(COMPOSE_FILE).items():
        for mount in (service or {}).get("volumes") or []:
            source = mount.split(":")[0] if isinstance(mount, str) else ""
            assert not source.startswith(
                (".", "/")
            ), f"{name}: bind mount {mount!r} needs the source tree"


def test_the_guide_states_the_real_failure_not_a_silent_one():
    """`docs/guides/platforms.md` had the direction backwards; it must not regress."""
    raw = (REPO_ROOT / "docs" / "guides" / "platforms.md").read_text()
    # Strip the blockquote markers and collapse the wrapping, so the assertion pins the
    # SENTENCE rather than the column at which a paragraph happened to be re-flowed.
    guide = " ".join(" ".join(line.lstrip().removeprefix(">").split()) for line in raw.splitlines())
    assert (
        "keys silently do not load" not in guide
    ), "a missing required env_file is a HARD Compose failure, not a silent skip"
    assert "`required` defaults to **true**" in guide
    assert "failed to start at all" in guide


# ── Behavioural layer: the real `compose config` from a scratch directory ─────────


#: A compose file that is trivially valid. Used as a CONTROL: if `compose config` cannot
#: resolve even this, the runtime is unusable on this host (finch is on PATH here but its
#: VM is not initialized, and `finch vm init` is not something a test may do) — so the
#: behavioural layer skips instead of reporting the environment as a product defect.
_CONTROL_COMPOSE = 'services:\n  probe:\n    image: "busybox:latest"\n'


def _usable_runtime(scratch: Path) -> str | None:
    """A container runtime whose ``compose config`` actually runs in *scratch*."""
    for candidate in ("docker", "finch"):
        if not shutil.which(candidate):
            continue
        control = scratch / "control.yaml"
        control.write_text(_CONTROL_COMPOSE)
        try:
            probe = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [candidate, "compose", "-f", "control.yaml", "config"],
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            control.unlink()
            continue
        control.unlink()
        if probe.returncode == 0:
            return candidate
    return None


@pytest.mark.timeout(240)
def test_compose_config_succeeds_from_a_scratch_directory(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    runtime = _usable_runtime(scratch)
    if runtime is None:
        pytest.skip("no usable docker/finch compose — the structural rail above covers this")
    shutil.copy(COMPOSE_FILE, scratch / "compose.yaml")
    assert not (scratch / ".env").exists()

    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [runtime, "compose", "-f", "compose.yaml", "config"],
        cwd=scratch,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "compose.yaml must resolve with nothing but itself present; "
        f"{runtime} said: {proc.stderr[-600:]}"
    )
    assert "ghcr.io/personalclaw/personalclaw-gateway" in proc.stdout
