"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_action_pins.py``

Its checks:
``tests/rails/test_ci_action_pins_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


SHA = "a" * 40


OTHER = "b" * 40


class FakeAPI:
    """Answers GETs from a table and counts every call by path."""

    def __init__(self, answers: dict[str, tuple[int, object]]) -> None:
        self.answers = answers
        self.calls: dict[str, int] = {}

    def __call__(self, path: str) -> tuple[int, object]:
        self.calls[path] = self.calls.get(path, 0) + 1
        return self.answers.get(path, (404, None))


def _ref(repo: str, tag: str) -> str:
    return f"/repos/{repo}/git/ref/tags/{tag}"


def _commit(sha: str) -> tuple[int, object]:
    return 200, {"object": {"type": "commit", "sha": sha}}


def _workflow(*lines: str) -> list[tuple[str, str]]:
    return [("ci.yml", "jobs:\n  it:\n    steps:\n" + "".join(f"      - {x}\n" for x in lines))]
