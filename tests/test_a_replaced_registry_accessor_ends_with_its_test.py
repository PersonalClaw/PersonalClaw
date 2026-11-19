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
child was doing. So the child stops itself at :data:`LIMIT`, under the suite's 30: faulthandler's
watchdog dumps every thread's stack into a file and ends it, and the failure carries what it
printed and that dump, wherever it was stuck (starting up, collecting, running a test, tearing
down).

The watchdog dumps once, at the limit, and never before. It reads the child's main thread from
its own, so a dump taken while the child runs Python can crash it: dumped every few seconds from
its start, a child sometimes died of SIGSEGV while it was still importing, and the test said only
DID NOT RAISE (#6268). A child also runs faulthandler for fatal signals, so a crash prints its own
stack, and one that ends early fails the test with how it ended and what it printed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import nested_pytest
import pytest

_TESTS_DIR = Path(__file__).resolve().parent

#: When the child stops itself, with one dump of every thread's stack.
LIMIT = 25
#: How long past its limit the parent waits for a child whose watchdog did not stop it. The limit
#: and this stay under the suite's 30 s timeout, so this test's failure is the child's.
BACKSTOP = 3

#: The child: report a crash's own stack (faulthandler for fatal signals), arm the one dump at its
#: limit, then run pytest. ``sys.argv`` is its limit in seconds, its dump file and pytest's
#: arguments. faulthandler's watchdog writes without the GIL, so a child stuck in C still says
#: where, and ``exit=True`` ends the child once the dump is written.
_CHILD = (
    "import faulthandler, sys; "
    "faulthandler.enable(); "
    "faulthandler.dump_traceback_later("
    "float(sys.argv[1]), exit=True, file=open(sys.argv[2], 'w')); "
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
    folder: Path, *files: str, limit: float = LIMIT
) -> subprocess.CompletedProcess[str]:
    """This suite's own conftest, as a plugin, over *files* in *folder*, in this order and on one
    process. A child still running at *limit* stops itself, and fails the test with what it
    printed and its stacks there."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    dump = folder / "stacks.txt"
    command = [sys.executable, "-c", _CHILD, str(limit), str(dump)]
    # A basetemp of its own: in pytest's shared one, this session's end would delete every finished
    # session three or more behind it (pytest keeps 3), the run's own earlier session included,
    # one unlink at a time through the real-home guard (breadth run 40199713134, seed 2).
    command += ["-o", "addopts=", "-p", "no:cacheprovider", "-p", "conftest", "-q", "--color=no"]
    # faulthandler has one watchdog timer, and pytest re-arms it for each test when this is set
    # (the repository's pyproject sets 120): the child's one dump at its limit must stay its own.
    command += ["-o", "faulthandler_timeout=0"]
    command.append(nested_pytest.basetemp(folder))
    try:
        ended = subprocess.run(
            [*command, *files],
            cwd=folder,
            env=env,
            capture_output=True,
            text=True,
            timeout=limit + BACKSTOP,
        )
    except subprocess.TimeoutExpired as stopped:
        said = [
            part.decode(errors="replace") if isinstance(part, bytes) else part or ""
            for part in (stopped.stdout, stopped.stderr)
        ]
        pytest.fail(
            f"the nested pytest was still running {limit + BACKSTOP} s in, past its own {limit} s "
            f"limit; what it printed:\n--- stdout\n{said[0][-3000:]}\n--- stderr\n"
            f"{said[1][-3000:]}\n{_stacks(dump)}",
            pytrace=False,
        )
    if dump.is_file() and dump.stat().st_size:
        # Its watchdog dumped, so the child was still running at its limit.
        pytest.fail(
            f"the nested pytest was still running after {limit} s, and ended {_how(ended)}; what "
            f"it printed:\n--- stdout\n{ended.stdout[-3000:]}\n--- stderr\n"
            f"{_stderr(ended.stderr)}\n{_stacks(dump)}",
            pytrace=False,
        )
    return ended


def _stacks(dump: Path) -> str:
    """The stacks the child's watchdog dumped at its limit, under their heading."""
    stacks = dump.read_text(errors="replace") if dump.exists() else ""
    return f"--- its stacks at the limit\n{stacks[-8000:] or '(none written)'}"


def _stderr(err: str) -> str:
    """The child's stderr for a failure: from its last crash report on, the crash site first, as
    faulthandler writes it; else its tail."""
    at = err.rfind("Fatal Python error")
    return err[at : at + 8000] if at >= 0 else err[-3000:]


def _how(ended: subprocess.CompletedProcess[str]) -> str:
    """How a child ended: its exit code, and the signal that killed it, if one did."""
    if ended.returncode >= 0:
        return f"with exit code {ended.returncode}"
    try:
        name = signal.Signals(-ended.returncode).name
    except ValueError:
        name = f"signal {-ended.returncode}"
    return f"killed by {name} (exit code {ended.returncode})"


def test_the_test_after_a_replaced_accessor_reads_the_real_registry(tmp_path: Path) -> None:
    (tmp_path / "test_a_replaces.py").write_text(textwrap.dedent(_REPLACES))
    (tmp_path / "test_b_reads.py").write_text(textwrap.dedent(_READS))
    proc = _pytest_session(tmp_path, "test_a_replaces.py", "test_b_reads.py")
    assert "2 passed" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-2000:]


def _stopped_at_the_limit(
    folder: Path, body: str, limit: float = 20, plugin: str | None = None
) -> str:
    """What the stopped child's failure said, for a test file that should still be running at
    *limit* (20 s). A child that ends before the limit fails the calling test with how it ended
    and what it printed, a crash's own stack among it, so a run where it ended early names why,
    not just that nothing was raised (#6268: a Breadth leg's child ended inside the limit, and the
    test said only DID NOT RAISE). *plugin*, if given, is a module the child loads with ``-p``
    as it starts, before any of pytest's own set-up."""
    (folder / "test_stuck.py").write_text(textwrap.dedent(body))
    first: tuple[str, ...] = ()
    if plugin is not None:
        (folder / "starts_the_child.py").write_text(textwrap.dedent(plugin))
        first = ("-p", "starts_the_child")
    try:
        ended = _pytest_session(folder, *first, "test_stuck.py", limit=limit)
    except pytest.fail.Exception as stopped:
        return str(stopped)
    pytest.fail(
        f"the nested pytest ended before its {limit} s limit, {_how(ended)}; what it printed:\n"
        f"--- stdout\n{ended.stdout[-3000:]}\n--- stderr\n{_stderr(ended.stderr)}",
        pytrace=False,
    )


#: The plants: a child that is still running at the limit, in a test and in a teardown, by the
#: frame its stacks must name and what it printed before it stuck (the teardown's passed test).
_STUCK = {
    "in-a-test": (
        """
        import time


        def test_sleeps_past_the_limit():
            time.sleep(120)
        """,
        "test_sleeps_past_the_limit",
        "",
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
        "--- stdout\n.",
    ),
}


@pytest.mark.parametrize("where", sorted(_STUCK))
def test_a_child_still_running_at_the_limit_fails_with_its_stacks(
    tmp_path: Path, where: str
) -> None:
    """A child stuck past the limit fails this test with its stacks, wherever it is stuck.

    Its one dump is at the limit, so it finds the child stuck however long its start-up took, and
    20 s with the backstop stays under the suite's 30 s."""
    body, frame, printed = _STUCK[where]
    said = _stopped_at_the_limit(tmp_path, body)
    assert "still running after 20 s" in said
    assert "most recent call first" in said, "the child's stacks are in the failure"
    assert frame in said, said[-3000:]
    assert printed in said, f"what the child printed before it stuck: {said[:1500]}"


_CRASHES = "import os\nimport signal\n\nos.kill(os.getpid(), signal.SIGSEGV)\n"


@pytest.mark.parametrize(
    ("body", "plugin", "ended", "printed"),
    [
        pytest.param(
            "raise RuntimeError('the child says why')\n",
            None,
            "with exit code",
            # The error's own line: the child's source, which pytest prints too, holds the words.
            ("RuntimeError: the child says why",),
            id="at-once",
        ),
        pytest.param(
            _CRASHES,
            None,
            "killed by SIGSEGV (exit code -11)",
            ("Fatal Python error: Segmentation fault", 'test_stuck.py", line 4'),
            id="a-crash",
        ),
        # Where #6268's child died: still starting, before pytest's own faulthandler is on.
        pytest.param(
            "def test_never_reached():\n    pass\n",
            _CRASHES,
            "killed by SIGSEGV (exit code -11)",
            ("Fatal Python error: Segmentation fault", 'starts_the_child.py", line 4'),
            id="a-crash-while-it-starts",
        ),
    ],
)
def test_a_child_that_ends_before_the_limit_fails_naming_how_and_its_output(
    tmp_path: Path, body: str, plugin: str | None, ended: str, printed: tuple[str, ...]
) -> None:
    """The control: a child that ends before the limit, as one that could not start does or one
    that crashed, fails the stuck test with how it ended and what it printed (a crash's own stack,
    from faulthandler), never a bare DID NOT RAISE. The limit is 25 s here (28 with the backstop,
    under the suite's 30) for the start-up a loaded runner gives the child; a child still running
    at it fails this control by name."""
    try:
        reached = _stopped_at_the_limit(tmp_path, body, limit=25, plugin=plugin)
    except pytest.fail.Exception as failed:
        said = str(failed)
    else:
        pytest.fail(
            "the child was still running at the 25 s limit, so the control read no early end; "
            f"the limit's failure said:\n{reached[-3000:]}",
            pytrace=False,
        )
    assert f"ended before its 25 s limit, {ended}" in said, said
    for line in printed:
        assert line in said, said[-3000:]
