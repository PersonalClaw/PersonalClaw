"""The source-tree checks split from
``tests/test_desktop_macos_signature_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import yaml
from desktop_macos_signature_gate_shared import GATE_SCRIPT_NAME, MAKEFILE, REPO_ROOT

RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _desktop_mac_job() -> dict:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["desktop-mac"]
    assert job.get("steps"), "desktop-mac has no steps — these rails would pass vacuously"
    return job


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
