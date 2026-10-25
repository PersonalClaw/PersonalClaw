"""A test that replaces the provider registry's accessor leaves every module the real one.

🔴 The defect. Many tests give themselves a private provider registry by replacing the accessor,
``monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)``.
Undoing that puts the registry module's name back, but a module IMPORTED while it was replaced
bound the replacement by name (``from personalclaw.llm.registry import get_default_registry``)
and kept it. ``test_a_named_model_is_said_not_swapped.py`` was the first test on its worker to
import ``personalclaw.sdk.model`` and ``personalclaw.sdk.provider_helpers`` that way, so every
provider app loaded later on that worker registered its type into that test's registry, which
nothing read again. ``test_best_of_n_provider_outage.py`` then failed all fifteen of its tests
with "type 'ollama', which no loaded app provides", but only in an order where it came after. Two
image tests did the same, and ``test_inert_surface_baseline.py`` then reported
``sdk.model.ProviderRegistry`` inert, because the accessor whose signature returns it was their
lambda.

``tests/conftest.py``'s ``_restore_provider_registry`` now starts every test by putting the real
accessor back in every module of ours that binds it. This file runs that order in a session of
its own, serially: one test replaces the accessor while the SDK is first imported, and the next
must find the real accessor in the SDK and its app's type in the real registry.

That session takes about a second on a runner (1.07 s in breadth run 40199713134, seed 81), but
once it ran past the suite's 30 s timeout (seed 82), and the outer timeout threw away whatever the
child was doing. So the child dumps every thread's stack into a file every :data:`DUMP_EVERY`
seconds from its start, whatever it is doing then (starting up, collecting, running a test,
tearing down), and is stopped at :data:`LIMIT`, under the suite's 30, so the failure carries what
the child printed and its last dumps.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

#: How often the child dumps every thread's stack, from its start.
DUMP_EVERY = 5
#: When the child is stopped: under the suite's 30 s timeout, so this test's failure is the child's.
LIMIT = 25

#: The child: arm the dump from the start, then run pytest. ``sys.argv`` is its seconds, its file
#: and pytest's arguments. faulthandler's watchdog writes without the GIL, so a child stuck in C
#: still says where.
_CHILD = (
    "import faulthandler, sys; "
    "faulthandler.dump_traceback_later("
    "float(sys.argv[1]), repeat=True, file=open(sys.argv[2], 'w')); "
    "import pytest; sys.exit(pytest.main(sys.argv[3:]))"
)


_REPLACES = """
import importlib
import sys

from personalclaw.llm import registry


def test_replaces_the_accessor_while_the_sdk_is_imported(monkeypatch):
    private = registry.ProviderRegistry()
    monkeypatch.setattr(registry, "get_default_registry", lambda: private)
    sys.modules.pop("personalclaw.sdk.model", None)
    model = importlib.import_module("personalclaw.sdk.model")
    assert model.get_default_registry() is private
"""

_READS = """
from personalclaw.llm import registry
from personalclaw.llm.capabilities import Capability, ProviderCapability


def test_the_next_test_registers_into_the_real_registry():
    from personalclaw.sdk import model

    assert model.get_default_registry is registry.get_default_registry
    model.get_default_registry().register_type(
        ProviderCapability(
            type="accessor-probe",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=1024,
        ),
        lambda **_kw: None,
    )
    assert "accessor-probe" in registry.get_default_registry()._factories
"""


def _pytest_session(
    folder: Path, *files: str, dump_every: float = DUMP_EVERY, limit: float = LIMIT
) -> subprocess.CompletedProcess[str]:
    """This suite's own conftest, as a plugin, over *files* in *folder*, in this order and on one
    process. A child still running at *limit* fails the test with what it printed and its stacks."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    dump = folder / "stacks.txt"
    command = [sys.executable, "-c", _CHILD, str(dump_every), str(dump)]
    # A basetemp of its own: in pytest's shared one, this session's end would delete every finished
    # session three or more behind it (pytest keeps 3), the run's own earlier session included,
    # one unlink at a time through the real-home guard (breadth run 40199713134, seed 2).
    command += ["-o", "addopts=", "-p", "no:cacheprovider", "-p", "conftest", "-q", "--color=no"]
    command += ["--basetemp", str(folder / "basetemp")]
    try:
        return subprocess.run(
            [*command, *files], cwd=folder, env=env, capture_output=True, text=True, timeout=limit
        )
    except subprocess.TimeoutExpired as stopped:
        said = [
            part.decode(errors="replace") if isinstance(part, bytes) else part or ""
            for part in (stopped.stdout, stopped.stderr)
        ]
        stacks = dump.read_text(errors="replace") if dump.exists() else ""
        pytest.fail(
            f"the nested pytest was still running after {limit} s; what it printed:\n"
            f"--- stdout\n{said[0][-3000:]}\n--- stderr\n{said[1][-3000:]}\n"
            f"--- its stacks, every {dump_every} s, the last ones\n"
            f"{stacks[-8000:] or '(none written)'}",
            pytrace=False,
        )


def test_the_test_after_a_replaced_accessor_reads_the_real_registry(tmp_path: Path) -> None:
    (tmp_path / "test_a_replaces.py").write_text(textwrap.dedent(_REPLACES))
    (tmp_path / "test_b_reads.py").write_text(textwrap.dedent(_READS))
    proc = _pytest_session(tmp_path, "test_a_replaces.py", "test_b_reads.py")
    assert "2 passed" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-2000:]


#: The plants: a child that is still running at the limit, in a test and in a teardown.
_STUCK = {
    "in-a-test": (
        """
        import time


        def test_sleeps_past_the_limit():
            time.sleep(120)
        """,
        "test_sleeps_past_the_limit",
    ),
    "in-a-teardown": (
        """
        import time

        import pytest


        @pytest.fixture(scope="module")
        def ends_slowly():
            yield
            time.sleep(120)


        def test_passes_and_its_module_ends_slowly(ends_slowly):
            pass
        """,
        "ends_slowly",
    ),
}


@pytest.mark.parametrize("where", sorted(_STUCK))
def test_a_child_still_running_at_the_limit_fails_with_its_stacks(
    tmp_path: Path, where: str
) -> None:
    """A child stuck past the limit fails this test with its stacks, wherever it is stuck.

    It dumps every 2 s, so the dumps after a slow start-up still find it stuck, and 20 s stays
    under the suite's 30 s."""
    body, frame = _STUCK[where]
    (tmp_path / "test_stuck.py").write_text(textwrap.dedent(body))
    with pytest.raises(pytest.fail.Exception) as failed:
        _pytest_session(tmp_path, "test_stuck.py", dump_every=2, limit=20)
    said = str(failed.value)
    assert "still running after 20 s" in said
    assert "most recent call first" in said, "the child's stacks are in the failure"
    assert frame in said, said[-3000:]
