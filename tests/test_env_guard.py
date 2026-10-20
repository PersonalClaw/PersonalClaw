"""The environment rail must be able to FAIL, and must look only once ``monkeypatch`` is undone.

Each test drives ``env_guard`` on probe keys it registered first (``probes``), so whatever it does
to the environment is given back after it. The rail's own failure, a test that leaks, cannot be
shown from inside the suite without leaking: the last two tests hold the order that keeps it from
failing a test that did not.
"""

import os

import env_guard
import pytest

_KEY = "PERSONALCLAW_ENV_GUARD_PROBE"
_OUTSIDE = "ENV_GUARD_PROBE_OUTSIDE_THE_PRODUCT"


@pytest.fixture
def probes(unset_env):
    unset_env(_KEY, _OUTSIDE)


def test_a_test_that_changed_nothing_has_nothing_to_give_back(probes) -> None:
    assert env_guard.give_back(env_guard.snapshot()) == []


def test_a_variable_the_test_set_is_named_and_taken_back(probes) -> None:
    before = env_guard.snapshot()
    os.environ[_KEY] = "left behind"
    assert env_guard.give_back(before) == [_KEY]
    assert _KEY not in os.environ


def test_a_variable_the_test_changed_or_removed_is_put_back(probes, monkeypatch) -> None:
    monkeypatch.setenv(_KEY, "as found")
    before = env_guard.snapshot()

    os.environ[_KEY] = "changed"
    assert env_guard.give_back(before) == [_KEY]
    assert os.environ[_KEY] == "as found"

    del os.environ[_KEY]
    assert env_guard.give_back(before) == [_KEY]
    assert os.environ[_KEY] == "as found"


def test_a_variable_outside_the_product_is_left_alone(probes) -> None:
    """An import can set one once, on purpose; taking it back would take it from later tests."""
    before = env_guard.snapshot()
    os.environ[_OUTSIDE] = "set by an import"
    assert env_guard.give_back(before) == []
    assert os.environ[_OUTSIDE] == "set by an import"


def test_a_changed_path_is_named_and_put_back(monkeypatch) -> None:
    """``PATH`` is watched too: no code PersonalClaw runs may change it, since every child it
    starts resolves its programs on it. Registered first, so it is given back however this ends."""
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    before = env_guard.snapshot()

    os.environ["PATH"] = "/somewhere/else" + os.pathsep + before["PATH"]
    assert env_guard.give_back(before) == ["PATH"]
    assert os.environ["PATH"] == before["PATH"]


def test_the_failure_names_the_test_the_variables_and_the_fix() -> None:
    message = env_guard.leak_message(
        "tests/test_x.py::test_y", ["PERSONALCLAW_PORT", "PERSONALCLAW_PROJECT_DIR"]
    )
    assert message.startswith(
        "tests/test_x.py::test_y changed PERSONALCLAW_PORT, PERSONALCLAW_PROJECT_DIR"
    )
    assert "unset_env(<key>)" in message


def test_unset_gives_back_a_key_the_code_under_test_then_set(probes) -> None:
    with pytest.MonkeyPatch.context() as patch:
        env_guard.unset(patch, _KEY)
        assert _KEY not in os.environ
        os.environ[_KEY] = "written by the code under test"
    assert _KEY not in os.environ


def test_unset_gives_back_the_value_a_key_had(probes, monkeypatch) -> None:
    monkeypatch.setenv(_KEY, "the value it had")
    with pytest.MonkeyPatch.context() as patch:
        env_guard.unset(patch, _KEY)
        assert _KEY not in os.environ
        os.environ[_KEY] = "written by the code under test"
    assert os.environ[_KEY] == "the value it had"


def test_delenv_alone_registers_nothing_for_a_key_that_is_not_set(probes) -> None:
    """Why ``unset`` exists: every leak the rail was added for had this shape."""
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(_KEY, raising=False)
        os.environ[_KEY] = "written by the code under test"
    assert os.environ[_KEY] == "written by the code under test"


@pytest.fixture
def takes_monkeypatch_first(monkeypatch) -> None:
    """A fixture that requests ``monkeypatch``, named by a ``usefixtures`` mark: set up ahead of
    every autouse fixture, the rail among them."""


@pytest.mark.usefixtures("takes_monkeypatch_first")
def test_a_change_registered_ahead_of_the_rail_is_no_leak(monkeypatch) -> None:
    """Passing is the assertion. Were ``monkeypatch`` set up before the rail, it would be undone
    after the rail compared, and this test, and every autouse fixture's own
    ``PERSONALCLAW_*`` setting, would fail it at teardown."""
    monkeypatch.setenv(_KEY, "registered")


def test_a_change_registered_by_the_test_is_no_leak(monkeypatch) -> None:
    """Passing is the assertion, as above, for the ordinary order."""
    monkeypatch.setenv(_KEY, "registered")
