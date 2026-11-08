"""The choices ``agent.explain`` takes, read from the one table, ``personalclaw/explanations.json``.

Read here, in config, so the config's own reading of the field and its editable allowlist name the
choices without reaching up into ``personalclaw.explanations``, which builds what a turn carries
from these same rows and re-exports them.
"""

from __future__ import annotations

import json
from pathlib import Path

_TABLE = Path(__file__).resolve().parents[1] / "explanations.json"

#: The choice that sends nothing: the agent explains things its usual way.
DEFAULT = "default"


def _a_row(row: object) -> bool:
    return isinstance(row, dict) and all(
        isinstance(row.get(k), str) for k in ("id", "label", "hint", "sent")
    )


def _shipped(rows: object) -> tuple[dict[str, str], ...]:
    if (
        isinstance(rows, list)
        and rows
        and all(_a_row(r) for r in rows)
        and rows[0]["id"] == DEFAULT
        and not rows[0]["sent"]
    ):
        return tuple(rows)
    raise RuntimeError(
        f"{_TABLE} is not a table of explanations, 'default' first and sending nothing"
    )


ROWS: tuple[dict[str, str], ...] = _shipped(json.loads(_TABLE.read_text(encoding="utf-8")))
EXPLAIN_IDS: tuple[str, ...] = tuple(r["id"] for r in ROWS)


def normalize_choice(value: object) -> str:
    """*value* as a choice's id, or ``""`` when it names none."""
    if not isinstance(value, str):
        return ""
    word = value.strip().lower()
    return word if word in EXPLAIN_IDS else ""
