"""A login shell in the published image finds `personalclaw`.

The image puts its virtual environment on PATH with ``ENV PATH="/opt/venv/bin:$PATH"``, which
reaches every process the container runtime starts and no login shell: ``/etc/profile`` sets
PATH from scratch (Debian's value for a user who is not root is
``/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games``). The dashboard's terminal runs a
login shell, so in the image `personalclaw` was "not found" there. The runtime stage now installs
a script into ``/etc/profile.d``, which ``/etc/profile`` reads after its reset, and it puts the
environment back in front.

These run the script under this host's own ``/bin/sh`` against that reset PATH, which is what a
test run without a container runtime can hold. The built image's own login shell is step 7 of
``tools/docker_single_container_smoke.py``, which the single-container workflow runs against the
image it builds.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPO / "deploy" / "docker" / "Dockerfile.backend"
_SCRIPT = _REPO / "deploy" / "docker" / "personalclaw-profile.sh"
#: What Debian's /etc/profile sets PATH to for a user who is not root, before profile.d.
_RESET_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"


def _runtime_stage() -> str:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index("AS runtime") :]


def _venv_bin() -> str:
    """The directory the runtime stage's ENV PATH puts first: the image's environment."""
    match = re.search(r'ENV PATH="([^:"$]+):\$PATH"', _runtime_stage())
    assert match, 'the runtime stage no longer sets PATH as `ENV PATH="<dir>:$PATH"`'
    return match.group(1)


def _login_path(*sources: Path) -> str:
    dots = "; ".join(f'. "{s}"' for s in sources)
    done = subprocess.run(
        ["/bin/sh", "-c", f'{dots}; printf %s "$PATH"'],
        env={"PATH": _RESET_PATH},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def test_the_runtime_stage_installs_the_script_where_a_login_shell_reads_it() -> None:
    copies = re.findall(r"^COPY (\S+) (\S+)$", _runtime_stage(), re.MULTILINE)
    assert ("deploy/docker/personalclaw-profile.sh", "/etc/profile.d/personalclaw.sh") in copies


def test_a_login_shell_puts_the_images_environment_back_in_front() -> None:
    assert _venv_bin() == "/opt/venv/bin"
    assert _venv_bin() not in _RESET_PATH.split(":")  # the defect: the reset drops it
    path = _login_path(_SCRIPT).split(":")
    assert path[0] == _venv_bin()
    assert ":".join(path[1:]) == _RESET_PATH


def test_reading_it_twice_adds_the_environment_once() -> None:
    """A nested login shell reads /etc/profile again; PATH must not grow each time."""
    assert _login_path(_SCRIPT, _SCRIPT).split(":").count(_venv_bin()) == 1


def test_the_script_names_the_environment_the_dockerfile_builds() -> None:
    """One directory in two files: a venv moved in the Dockerfile must move here too."""
    assert f"*:{_venv_bin()}:*" in _SCRIPT.read_text(encoding="utf-8")
    assert f'PATH="{_venv_bin()}:${{PATH}}"' in _SCRIPT.read_text(encoding="utf-8")
