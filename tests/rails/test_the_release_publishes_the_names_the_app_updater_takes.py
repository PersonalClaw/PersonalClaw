"""The release stages its desktop copies under exactly the names the desktop app's own update takes.

The app's update looks for one asset by name in the release it picked: on macOS
``PersonalClaw-<version>-<arch>.dmg``, on Linux ``PersonalClaw-<version>.AppImage``, where
``<version>`` is the release's tag without its ``v`` (``desktop/appUpdate.js``, ``assetName``). A
copy staged under another spelling of the same version (``0.3.8rc1`` for ``v0.3.8-rc.1``) is a
release no installed copy can update to: each one answers that the release has no copy for this
computer. So the release workflow's staging step runs here, against artifacts named both ways, and
must stage the tag's spelling and refuse the other.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS_YML = REPO / ".github" / "workflows" / "release-artifacts.yml"
APP_UPDATE_JS = REPO / "desktop" / "appUpdate.js"
STEP = "Stage the release's files"
#: A release candidate's tag, as `scripts/release_tags.py` classifies it into `build`'s version.
TAG_VERSION = "0.3.8-rc.1"


def _staging_step() -> tuple[str, str]:
    """The staging step's `VERSION` and its script, read from the workflow as it is written."""
    lines = ARTIFACTS_YML.read_text(encoding="utf-8").splitlines()
    named = [i for i, line in enumerate(lines) if line.strip() == f"- name: {STEP}"]
    assert len(named) == 1, f"expected one step named {STEP!r}"
    step_indent = len(lines[named[0]]) - len(lines[named[0]].lstrip())
    body: list[str] = []
    for line in lines[named[0] + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= step_indent:
            break
        body.append(line)
    version = next(
        (line.split(":", 1)[1].strip() for line in body if line.strip().startswith("VERSION:")), ""
    )
    runs = [i for i, line in enumerate(body) if line.strip() == "run: |"]
    assert len(runs) == 1, f"the step {STEP!r} has no script of its own"
    return version, textwrap.dedent("\n".join(body[runs[0] + 1 :]))


def _artifacts(root: Path, desktop_version: str) -> None:
    """What the jobs before `manifest` upload, the desktop copies built as *desktop_version*."""
    files = [
        "dist/personalclaw-0.3.8rc1-py3-none-any.whl",
        "dist/personalclaw-0.3.8rc1.tar.gz",
        "dist-client/personalclaw_client-0.3.8rc1-py3-none-any.whl",
        "dist-client/personalclaw_client-0.3.8rc1.tar.gz",
        f"desktop-linux/PersonalClaw-{desktop_version}.AppImage",
        f"desktop-linux/personalclaw_{desktop_version}_amd64.deb",
        f"desktop-mac-arm64/PersonalClaw-{desktop_version}-arm64.dmg",
        f"desktop-mac-x64/PersonalClaw-{desktop_version}-x64.dmg",
        "wheel-sbom/personalclaw.spdx.json",
        "image-sbom-gateway/gateway.spdx.json",
        "image-sbom-web/web.spdx.json",
        "image-index-gateway/gateway.index.json",
        "image-index-web/web.index.json",
        "build-record-desktop-linux/desktop-linux.json",
    ]
    for rel in files:
        path = root / "artifacts" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def _stage(tmp_path: Path, desktop_version: str) -> subprocess.CompletedProcess[str]:
    version_expr, script = _staging_step()
    assert (
        version_expr == "${{ needs.build.outputs.version }}"
    ), "the staging step reads the version `build` classified from the tag"
    runner_temp = tmp_path / "runner"
    work = tmp_path / "work"
    work.mkdir()
    _artifacts(runner_temp, desktop_version)
    bash = shutil.which("bash")
    assert bash, "no bash to run the step with"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RUNNER_TEMP": str(runner_temp),
        "VERSION": TAG_VERSION,
    }
    return subprocess.run(
        [bash, "-c", script], cwd=work, env=env, capture_output=True, text=True, timeout=60
    )


def test_the_names_are_the_ones_the_app_update_asks_for() -> None:
    source = APP_UPDATE_JS.read_text(encoding="utf-8")
    assert re.search(
        r"return `\$\{APP_NAME\}-\$\{version\}-\$\{arch\}\.dmg`;", source
    ), "the app's update asks for another dmg name"
    assert re.search(
        r"return `\$\{APP_NAME\}-\$\{version\}\.AppImage`;", source
    ), "the app's update asks for another AppImage name"
    assert re.search(
        r'String\(chosen\.release\.tag_name\)\.replace\(/\^v/, ""\)', source
    ), "the app's update spells the version another way"


def test_the_tags_spelling_is_staged(tmp_path: Path) -> None:
    done = _stage(tmp_path, TAG_VERSION)
    assert done.returncode == 0, done.stdout + done.stderr
    staged = {p.name for p in (tmp_path / "work" / "staged").iterdir()}
    for name in (
        f"PersonalClaw-{TAG_VERSION}-arm64.dmg",
        f"PersonalClaw-{TAG_VERSION}-x64.dmg",
        f"PersonalClaw-{TAG_VERSION}.AppImage",
    ):
        assert name in staged


@pytest.mark.parametrize("spelling", ["0.3.8rc1", "0.3.8-rc1"])
def test_another_spelling_of_the_version_is_refused(tmp_path: Path, spelling: str) -> None:
    done = _stage(tmp_path, spelling)
    assert (
        done.returncode != 0
    ), f"copies built as {spelling} were staged for the tag v{TAG_VERSION}"
    assert "the name the desktop app's update takes" in done.stdout + done.stderr
