"""Collection policy for the workflow test family's timeout ceiling."""

from pathlib import Path

import conftest
import pytest


class _Item:
    def __init__(self, path: str, timeout_marker: pytest.Mark | None = None) -> None:
        self.path = Path(path)
        self.timeout_marker = timeout_marker
        self.added: list[pytest.Mark] = []

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        if name == "timeout":
            return self.timeout_marker
        return None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.added.append(marker.mark)


def test_workflows_family_receives_the_measured_timeout_only() -> None:
    workflow_item = _Item("tests/test_workflows_engine.py")
    unrelated_item = _Item("tests/test_gateway.py")

    conftest.pytest_collection_modifyitems([workflow_item, unrelated_item])

    assert [mark.args for mark in workflow_item.added] == [(46,)]
    assert unrelated_item.added == []


def test_an_explicit_workflows_timeout_is_not_overwritten() -> None:
    explicit = pytest.mark.timeout(17).mark
    workflow_item = _Item("tests/test_workflows_engine.py", timeout_marker=explicit)

    conftest.pytest_collection_modifyitems([workflow_item])

    assert workflow_item.timeout_marker is explicit
    assert workflow_item.added == []
