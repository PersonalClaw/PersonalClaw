"""No workflow runs on ``pull_request_target`` but the ones listed (#3908).

A ``pull_request_target`` run is main's copy of the workflow, with a token that can write, started
by a pull request's events, beside a pull request's content. GitHub's default policy stops such runs
in public repositories from 2026-12-02, so each workflow on it moves off.

The tree's list is ``tools/ci/workflows.py``'s ``PULL_REQUEST_TARGET``. The workflows that run on
the event must be exactly the ones it lists: a new one fails, and so does a listed one that no
longer runs on it, so a stale entry never lets one back. The list may name no workflow that
:data:`ALLOWED` does not. That list only shrinks: a move drops its workflow from both in the
change that moves it, and main's copy of this rail, which judges that change, still allows it.

Main's contract runs this rail over a head with the head's ``tools/ci`` on the path, so it runs no
code of the head's: it reads each workflow's ``on:`` itself, and the list as the literal tuple the
file assigns.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

_WORKFLOWS = REPO / ".github" / "workflows"
_LIST = REPO / "tools" / "ci" / "workflows.py"
_NAME = "PULL_REQUEST_TARGET"

#: The most the tree may run on ``pull_request_target``, each with why. Only ever shrinks, and holds
#: nothing since the dependency review moved off it.
ALLOWED: dict[str, str] = {}


def events(text: str) -> set[str]:
    """The events a workflow's *text* runs on: its ``on:`` as a string, a list or a mapping. YAML
    1.1 reads a bare ``on`` key as True."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return set()
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, (list, dict)):
        return {str(event) for event in on}
    return set()


def on_pull_request_target(texts: dict[str, str]) -> list[str]:
    """The workflows, by file name, of *texts* (each file's text by its name) that run on
    ``pull_request_target``."""
    return [name for name, text in sorted(texts.items()) if "pull_request_target" in events(text)]


def listed(source: str) -> tuple[str, ...] | str:
    """The tuple of file names *source* assigns to :data:`_NAME` once, as a literal, or why it
    does not."""
    found = []
    for node in ast.parse(source).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == _NAME and node.value is not None:
            found.append(node.value)
    if len(found) != 1:
        return f"{_LIST.name} assigns {_NAME} {len(found)} times, not once"
    try:
        value = ast.literal_eval(found[0])
    except (ValueError, TypeError, SyntaxError):
        return f"{_LIST.name}'s {_NAME} is not a literal"
    if not isinstance(value, tuple) or not all(isinstance(n, str) for n in value):
        return f"{_LIST.name}'s {_NAME} is not a tuple of file names"
    return value


def problems(
    texts: dict[str, str], names: tuple[str, ...] | str, allowed: dict[str, str]
) -> list[str]:
    """Each way *texts* and the list *names* break the rule, one line each."""
    if isinstance(names, str):
        return [names]
    runs = set(on_pull_request_target(texts))
    found = [
        f"{n} runs on pull_request_target and is not listed" for n in sorted(runs - set(names))
    ]
    found += [
        f"{n} is listed but {'is gone' if n not in texts else 'no longer runs on it'}"
        for n in sorted(set(names) - runs)
    ]
    found += [
        f"{n} is listed and this rail does not allow it" for n in sorted(set(names) - set(allowed))
    ]
    if len(set(names)) != len(names):
        found.append(f"the list names a workflow twice: {names}")
    return found


def _texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(_WORKFLOWS.glob("*.y*ml"))
        if path.suffix in (".yml", ".yaml")
    }


def _source() -> str:
    return Path(_LIST).read_text(encoding="utf-8")


def _names() -> tuple[str, ...]:
    names = listed(_source())
    assert isinstance(names, tuple), names
    return names


def test_the_tree_runs_on_pull_request_target_exactly_what_it_lists() -> None:
    assert problems(_texts(), listed(_source()), ALLOWED) == []


@pytest.mark.parametrize(
    "on",
    [
        "on:\n  pull_request_target:\n    types: [opened]\n",
        "on: [push, pull_request_target]\n",
        "on: pull_request_target\n",
        "'on':\n  pull_request_target: {}\n",
    ],
    ids=["mapping", "list", "string", "quoted-key"],
)
def test_a_new_workflow_on_pull_request_target_fails(on: str) -> None:
    planted = {**_texts(), "planted.yml": f"name: Planted\n{on}jobs: {{}}\n"}
    assert problems(planted, _names(), ALLOWED) == [
        "planted.yml runs on pull_request_target and is not listed"
    ]


def test_a_listed_workflow_that_moved_off_it_fails() -> None:
    # A planted entry, so the row holds when the tree's own list is empty.
    moved = "name: Planted\non:\n  pull_request:\n    types: [opened]\njobs: {}\n"
    texts = {**_texts(), "planted.yml": moved}
    names = (*_names(), "planted.yml")
    assert problems(texts, names, {**ALLOWED, "planted.yml": "a plant"}) == [
        "planted.yml is listed but no longer runs on it"
    ]


def test_a_listed_workflow_that_is_gone_fails() -> None:
    names = (*_names(), "gone.yml")
    assert problems(_texts(), names, {**ALLOWED, "gone.yml": "a plant"}) == [
        "gone.yml is listed but is gone"
    ]


def test_a_list_that_names_more_than_this_rail_allows_fails() -> None:
    planted = {**_texts(), "planted.yml": "name: Planted\non: pull_request_target\njobs: {}\n"}
    assert problems(planted, (*_names(), "planted.yml"), ALLOWED) == [
        "planted.yml is listed and this rail does not allow it"
    ]


_ASSIGNED = re.compile(rf"^{_NAME}: tuple\[str, \.\.\.\] = \((?P<names>[^\n]*)\)$", re.M)


@pytest.mark.parametrize(
    ("spelled", "says"),
    [
        (f"{_NAME}: tuple[str, ...] = tuple([\\g<names>])", "is not a literal"),
        (f"_{_NAME}: tuple[str, ...] = (\\g<names>)", "0 times, not once"),
        (f"{_NAME}: tuple[str, ...] = [\\g<names>]", "is not a tuple of file names"),
        (f"{_NAME}: tuple[str, ...] = (\\g<names>)\n{_NAME} = ()", "2 times, not once"),
    ],
    ids=["computed", "renamed", "a-list", "assigned-twice"],
)
def test_a_list_that_is_not_one_literal_tuple_fails(spelled: str, says: str) -> None:
    source = _source()
    assert len(_ASSIGNED.findall(source)) == 1, f"{_LIST} assigns {_NAME} other than on one line"
    found = problems(_texts(), listed(_ASSIGNED.sub(spelled, source)), ALLOWED)
    assert len(found) == 1 and says in found[0], found


def test_the_rail_runs_no_code_of_the_head_s() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert "tools" not in imported, imported


def test_a_workflow_on_pull_request_alone_is_not_one() -> None:
    planted = {
        "planted.yml": "name: Planted\non:\n  pull_request:\n    types: [opened]\njobs: {}\n"
    }
    assert on_pull_request_target(planted) == []
