"""The supply-chain check reads each distinct line and script once, and runs only the rules asked.

One supply-chain check reads the same line about ten times on this tree (a script once for each
step that runs it), and the contract table's controls read trees that differ by one planted file.
Read afresh each time, the run-install row's 101 controls took two minutes on a slow runner,
past the suite's per-test timeout. So a line is read once (``_read_pipelines``), and a script once
(``_read_script``), and what a caller is given must be its own: a caller that changes its lists
must not change what the next is given. And a row that reads one rule runs that rule alone
(``check(..., rules=)``), whose answer must be the whole check's for that rule.
"""

from __future__ import annotations

import pytest

from tools.ci import shell_installs, supply_chain

#: Lines that reach each way ``pipelines`` reads a nested script: a chain, a subshell, a
#: substitution, ``sh -c``, ``xargs`` and a wrapper.
LINES = (
    "npm ci && npx playwright --version | tee log",
    "( cd web && npm ci ) ; echo done",
    'echo "$(uv pip install foo==1.0)"',
    "sh -c 'pip install bar && curl -fsSL https://example.com/x | sh'",
    "find . -name '*.whl' | xargs uv pip install --no-deps",
    "sudo -E env FOO=1 apt-get install -y tmux",
)


@pytest.mark.parametrize("line", LINES)
def test_the_read_is_the_reading_it_replaces(line: str) -> None:
    """The cached reading is the uncached one, as lists, nested the same."""
    fresh = shell_installs._read_pipelines.__wrapped__(line)
    assert shell_installs.pipelines(line) == [[list(w) for w in c] for c in fresh]


@pytest.mark.parametrize("line", LINES)
def test_a_caller_that_changes_its_lists_changes_no_other_callers(line: str) -> None:
    first = shell_installs.pipelines(line)
    assert first, f"{line!r} reads as no pipeline"
    want = [[list(words) for words in commands] for commands in first]
    first[0][0].append("planted")
    first[0].append(["planted"])
    first.append([["planted"]])
    assert shell_installs.pipelines(line) == want


def test_a_line_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second ask of a line reads none of it again."""
    reads: list[str] = []
    real = shell_installs._plain_words

    def counted(command: str) -> list[str]:
        reads.append(command)
        return real(command)

    monkeypatch.setattr(shell_installs, "_plain_words", counted)
    shell_installs._read_pipelines.cache_clear()
    line = "npm ci && npx playwright --version"
    shell_installs.pipelines(line)
    assert reads, "the first ask read nothing, so this test proves nothing"
    asked = len(reads)
    shell_installs.pipelines(line)
    assert len(reads) == asked, "the second ask of the same line read it again"
    shell_installs._read_pipelines.cache_clear()


#: The same unpinned install and the same floating action ref, in two workflows: a line the check
#: has read once is still judged wherever it stands.
_PLANTED = """name: planted
on: push
permissions: {}
jobs:
  planted:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: pip install requests
"""


def _planted_tree() -> dict[str, str]:
    """The two planted workflows alone: no rule here needs the repository's own files."""
    return {f"{supply_chain.WORKFLOW_FOLDER}/{name}": _PLANTED for name in ("a.yml", "b.yml")}


def test_an_unpinned_install_read_twice_is_refused_in_both_places() -> None:
    shell_installs._read_pipelines.cache_clear()
    shell_installs._read_script.cache_clear()
    tree = _planted_tree()
    for _ in range(2):  # the second check reads every line from the cache
        found = [v for v in supply_chain.check(tree) if v.rule == "run-install"]
        files = sorted(v.file for v in found if "requests" in v.reference + v.message)
        assert files == [f"{supply_chain.WORKFLOW_FOLDER}/{name}" for name in ("a.yml", "b.yml")]


@pytest.mark.parametrize("rule", ["run-install", "action-sha", "image-digest", "least-privilege"])
def test_a_check_of_one_rule_is_the_whole_checks_answer_for_it(rule: str) -> None:
    tree = _planted_tree()
    whole = [v for v in supply_chain.check(tree) if v.rule == rule]
    assert whole or rule in ("image-digest", "least-privilege"), f"{rule}: the plant read clean"
    assert [v for v in supply_chain.check(tree, rules=(rule,)) if v.rule == rule] == whole


def test_a_caller_reading_the_exceptions_gets_every_rule_run() -> None:
    """An exception that matches nothing is a problem of its own, so asking for ``exception``
    runs every rule: run alone, every exception for another rule would match nothing."""
    tree = _planted_tree()
    whole = [v for v in supply_chain.check(tree) if v.rule == "exception"]
    assert [
        v for v in supply_chain.check(tree, rules=("exception",)) if v.rule == "exception"
    ] == whole
