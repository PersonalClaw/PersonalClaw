"""``python -m tools.ci quarantine add`` (0.4.0 A5, R3): one entry the quarantine's rules take.

Each case is a planted repository: a list, and the test modules its ids name. The helper writes
an entry the rules (``tests/quarantine.py``) accept, refuses what they would not before it writes,
and puts the list back exactly as it was when the rules refuse what it wrote.
"""

from __future__ import annotations

import datetime
import tomllib
from pathlib import Path

import pytest

from tools.ci import quarantine

HEADER = '# The quarantine (planted).\n#\n# [[entry]]\n# nodeid = "tests/test_example.py::x"\n'
TIMING = "tests/test_timing.py::test_it_answers_in_time"


def _root(tmp_path: Path, list_text: str = HEADER) -> Path:
    tests = tmp_path / "tests"
    (tests / "rails").mkdir(parents=True)
    (tests / "quarantine.toml").write_text(list_text)
    (tests / "test_timing.py").write_text(
        "import pytest\n\n\ndef test_it_answers_in_time():\n    pass\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_each(n):\n    pass\n\n\n"
        + "".join(f"def test_{i}():\n    pass\n\n\n" for i in range(12))
    )
    (tests / "test_guarded.py").write_text(
        "import pytest\n\npytestmark = pytest.mark.security\n\n\ndef test_it():\n    pass\n"
    )
    (tests / "rails" / "test_tree.py").write_text("def test_it():\n    pass\n")
    return tmp_path


def _add(root: Path, nodeid: str = TIMING, **over: object) -> str:
    fields: dict[str, object] = {
        "nodeid": nodeid,
        "issue": 5900,
        "assignee": "someone",
        "reason": "holds on a loaded runner one run in five",
    }
    fields.update(over)
    return quarantine.add(root, **fields)  # type: ignore[arg-type]


def _entries(root: Path) -> list[dict]:
    return tomllib.loads((root / quarantine.LIST).read_text()).get("entry", [])


# ── an entry the rules take ───────────────────────────────────────────────────────────────────


def test_it_appends_one_entry_the_rules_take_and_keeps_the_lines_above(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _add(root)
    text = (root / quarantine.LIST).read_text()
    assert text.startswith(HEADER)
    (entry,) = _entries(root)
    assert entry == {
        "nodeid": TIMING,
        "issue": 5900,
        "assignee": "someone",
        "expires": quarantine.today() + datetime.timedelta(days=14),
        "reason": "holds on a loaded runner one run in five",
    }
    assert quarantine._judged(root) == []


def test_a_reason_with_quotes_and_a_backslash_is_written_as_given(tmp_path: Path) -> None:
    root = _root(tmp_path)
    reason = 'waits on "the clock" \\ a slow runner'
    _add(root, reason=reason)
    assert _entries(root)[0]["reason"] == reason


def test_a_shorter_first_expiry_is_kept(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _add(root, days=7)
    assert _entries(root)[0]["expires"] == quarantine.today() + datetime.timedelta(days=7)


def test_a_case_of_a_parametrized_test_is_taken(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _add(root, "tests/test_timing.py::test_each[1]")
    assert [e["nodeid"] for e in _entries(root)] == ["tests/test_timing.py::test_each[1]"]


# ── what it refuses before writing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nodeid", "over", "says"),
    [
        pytest.param("tests/rails/test_tree.py::test_it", {}, "is a rail", id="a-rail"),
        pytest.param("src/app.py::test_it", {}, "is not a test id", id="not-under-tests"),
        pytest.param("tests/test_timing.py", {}, "is not a test id", id="a-file-alone"),
        pytest.param(TIMING, {"issue": 0}, "is no issue number", id="no-issue"),
        pytest.param(TIMING, {"assignee": "some one"}, "is no GitHub login", id="no-login"),
        pytest.param(TIMING, {"reason": ""}, "must be one line", id="no-reason"),
        pytest.param(TIMING, {"reason": "one\ntwo"}, "must be one line", id="two-lines"),
        pytest.param(TIMING, {"days": 15}, "1 to 14 days out, not 15", id="past-14-days"),
        pytest.param(TIMING, {"days": 31}, "1 to 14 days out, not 31", id="past-the-file-cap"),
        pytest.param(TIMING, {"days": 0}, "1 to 14 days out, not 0", id="expired-at-once"),
    ],
)
def test_it_refuses_before_writing(tmp_path: Path, nodeid: str, over: dict, says: str) -> None:
    root = _root(tmp_path)
    with pytest.raises(quarantine.Refused, match=says):
        _add(root, nodeid, **over)
    assert (root / quarantine.LIST).read_text() == HEADER


def test_a_test_listed_already_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _add(root)
    before = (root / quarantine.LIST).read_text()
    with pytest.raises(quarantine.Refused, match="is listed already"):
        _add(root, issue=5901)
    assert (root / quarantine.LIST).read_text() == before


def test_an_eleventh_entry_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    for i in range(10):
        _add(root, f"tests/test_timing.py::test_{i}")
    before = (root / quarantine.LIST).read_text()
    with pytest.raises(quarantine.Refused, match="holds 10 entries: at most 10"):
        _add(root, "tests/test_timing.py::test_10")
    assert (root / quarantine.LIST).read_text() == before


# ── what the rules refuse after writing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nodeid", "says"),
    [
        pytest.param("tests/test_guarded.py::test_it", "is security-marked", id="security"),
        pytest.param("tests/test_timing.py::test_gone", "no such test", id="no-such-test"),
        pytest.param("tests/test_missing.py::test_it", "does not exist", id="no-such-file"),
        pytest.param(
            "tests/test_timing.py::test_each", "without its case", id="a-parametrized-test-whole"
        ),
    ],
)
def test_a_list_the_rules_refuse_is_put_back_as_it_was(
    tmp_path: Path, nodeid: str, says: str
) -> None:
    root = _root(tmp_path)
    with pytest.raises(quarantine.Refused, match=says):
        _add(root, nodeid)
    assert (root / quarantine.LIST).read_text() == HEADER


def test_a_list_without_a_final_newline_gains_one_before_the_entry(tmp_path: Path) -> None:
    root = _root(tmp_path, HEADER.rstrip("\n"))
    _add(root)
    assert [e["nodeid"] for e in _entries(root)] == [TIMING]


# ── the command ───────────────────────────────────────────────────────────────────────────────


def test_the_command_adds_the_entry_and_prints_it(tmp_path: Path, capsys) -> None:
    root = _root(tmp_path)
    argv = ["add", "--nodeid", TIMING, "--issue", "5900", "--assignee", "someone",
            "--reason", "holds on a loaded runner", "--root", str(root)]  # fmt: skip
    assert quarantine.main(argv) == 0
    assert f'nodeid = "{TIMING}"' in capsys.readouterr().out
    assert len(_entries(root)) == 1


def test_the_command_refuses_and_leaves_the_list_unchanged(tmp_path: Path, capsys) -> None:
    root = _root(tmp_path)
    argv = ["add", "--nodeid", "tests/rails/test_tree.py::test_it", "--issue", "5900",
            "--assignee", "someone", "--reason", "r", "--root", str(root)]  # fmt: skip
    assert quarantine.main(argv) == 1
    err = capsys.readouterr().err
    assert "refused, tests/quarantine.toml is unchanged" in err and "is a rail" in err
    assert (root / quarantine.LIST).read_text() == HEADER


def test_the_helpers_numbers_are_the_rules_own() -> None:
    import quarantine as rules  # tests/quarantine.py, on the suite's path

    assert (quarantine.LIST, quarantine.CAP) == (rules.LIST, rules.CAP)
    assert quarantine.FIRST_DAYS <= rules.MAX_DAYS
