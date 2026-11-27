"""The gateway orders versions as the desktop app and the web do: every row of the one table.

Three comparators decide by a version's order: the gateway's (:mod:`personalclaw.versions`, for
its updates, the app gate and the Store), the desktop app's (``desktop/appUpdate.js``, for which
release the app takes) and the web's (``web/src/lib/versions.ts``, for an app older than the
gateway it uses). Each reads ``tests/fixtures/version_order.json`` in its own test, so a
spelling one of them orders differently fails there instead of in an update.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import versions

TABLE = json.loads((Path(__file__).parent / "fixtures" / "version_order.json").read_text("utf-8"))


def _order(a: str, b: str) -> int | None:
    if versions.same_version(a, b):
        return 0
    if versions.is_newer(a, b):
        return 1
    return -1 if versions.is_newer(b, a) else None


@pytest.mark.parametrize(
    ("a", "b", "order"), TABLE["rows"], ids=[f"{a} vs {b}" for a, b, _ in TABLE["rows"]]
)
def test_each_row_orders_as_the_table_says(a: str, b: str, order: int) -> None:
    assert _order(a, b) == order
    assert _order(b, a) == -order, "the order reads the same from either side"


@pytest.mark.parametrize("text", TABLE["unreadable"])
def test_what_no_comparator_reads_is_not_a_version(text: str) -> None:
    assert versions.parse_version(text) is None
    assert _order(text, "0.3.8") is None


def test_the_table_has_rows_of_each_order() -> None:
    assert {order for _a, _b, order in TABLE["rows"]} == {-1, 0, 1}
    assert len(TABLE["rows"]) >= 15
