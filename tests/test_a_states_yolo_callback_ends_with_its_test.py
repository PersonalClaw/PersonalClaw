"""The YOLO callback a test's dashboard state registers ends with that test.

🔴 The defect. Every ``DashboardState`` registers a callback on the process-wide YOLO state
(``trust_mode.register_on_disable``), which runs it whenever YOLO switches off. A gateway makes one
state for its whole life, so in the product the callback is the gateway's own. In the suite each
test makes its own, and they stayed registered, and alive, for the rest of their worker: every
later switch-off ran every earlier test's callback over the sessions that test had left in its
state. ``test_ws_app_event_gate.py`` leaves a mock session, whose mock key the callback's session
key rule strips forever, so ``test_yolo_ttl.py`` grew its worker's memory until the kernel killed
it (Linux CI) or ran out of time (macOS), and only in an order where it came after.

``tests/conftest.py``'s ``_reset_trust_mode`` now puts the callbacks back as each test found them.
This file runs that order in a session of its own, serially: one test leaves a state holding a
stand-in session, and the next must find none of its callbacks, and switch YOLO on and off.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

_LEAVES = """
from unittest.mock import MagicMock

from personalclaw.dashboard.state import DashboardState


def test_a_state_is_left_holding_a_stand_in_session():
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    state._sessions = {"chat-1": MagicMock()}
    state.left_by_the_test_before = True
"""

_SWITCHES = """
from personalclaw import trust_mode


def test_the_next_tests_switch_off_runs_none_of_its_callbacks():
    left = [
        callback
        for callback in trust_mode._TRUST._on_disable
        if getattr(getattr(callback, "__self__", None), "left_by_the_test_before", False)
    ]
    assert left == [], "the state the test before made is still registered"
    trust_mode.enable_yolo(ttl_secs=60)
    trust_mode.disable_yolo()
    assert trust_mode.is_yolo_active() is False
"""


def test_the_test_after_a_state_was_made_runs_none_of_its_yolo_callbacks(tmp_path: Path) -> None:
    (tmp_path / "test_a_leaves.py").write_text(textwrap.dedent(_LEAVES))
    (tmp_path / "test_b_switches.py").write_text(textwrap.dedent(_SWITCHES))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    # This suite's own conftest, as a plugin, over the two files in this order and on one process.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "test_a_leaves.py",
            "test_b_switches.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "2 passed" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-2000:]
