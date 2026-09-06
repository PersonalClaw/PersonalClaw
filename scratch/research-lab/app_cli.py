"""CLI seams for research-lab: a setup step and a doctor probe.

``personalclaw setup`` calls :func:`setup` after the core steps; ``personalclaw doctor``
calls :func:`doctor` and renders the lines it returns as this app's section.

There is no credential to collect — this app never talks to a network. Setup's whole job
is to state the budget the campaigns it opens will inherit; doctor's is to say whether
the unattended loop has anything to work on.

This module reads the campaign directory itself instead of importing ``provider``: core
loads a CLI hook by file path, not as a package, so a sibling import is not guaranteed to
resolve when ``personalclaw doctor`` runs before anything has loaded the provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personalclaw.sdk.cli import DoctorLine, SetupContext
from personalclaw.sdk.util import app_data_dir

APP_NAME = "research-lab"
DEFAULT_CYCLE_BUDGET = 5
DEFAULT_BREADTH = 3
STATUS_OPEN = "open"


def setup(ctx: SetupContext) -> None:
    """Run this app's interactive setup step."""
    saved = ctx.settings.load(ctx.app_name) or {}
    budget = saved.get("default_cycle_budget", DEFAULT_CYCLE_BUDGET)
    breadth = saved.get("cycle_breadth", DEFAULT_BREADTH)
    ctx.print(
        f"Research Lab: no credentials needed. New campaigns get {budget} unattended "
        f"cycle(s), {breadth} sub-question(s) per cycle."
    )
    ctx.print("The hourly 'advance-campaigns' cron runs the cycles; change either in Settings.")


def doctor() -> list[DoctorLine]:
    """Report this app's health to ``personalclaw doctor``."""
    try:
        root = app_data_dir(APP_NAME) / "campaigns"
        files = sorted(root.glob("*/campaign.json")) if root.is_dir() else []
    except OSError as exc:
        return [DoctorLine(label="Campaign store", status="fail", detail=str(exc))]

    campaigns, unreadable = _read(files)
    lines = [
        DoctorLine(
            label="Campaign store",
            status="ok",
            detail=f"{root} — {len(campaigns)} campaign(s)",
        )
    ]
    if unreadable:
        lines.append(
            DoctorLine(
                label="Unreadable campaigns",
                status="warn",
                detail=(
                    f"{unreadable} campaign file(s) will not parse; the tools skip them, so "
                    f"doctor is where they are visible"
                ),
            )
        )
    open_ones = [c for c in campaigns if c.get("status") == STATUS_OPEN]
    if not campaigns:
        detail, status = "none yet — ask for one with research_open", "info"
    elif not open_ones:
        detail, status = "none open; the hourly cron has nothing to advance", "info"
    else:
        detail = ", ".join(
            f"{c.get('id')} (cycle {len(c.get('cycles') or [])}/{c.get('cycle_budget')})"
            for c in open_ones
        )
        status = "ok"
    lines.append(DoctorLine(label="Open campaigns", status=status, detail=detail))
    return lines


def _read(files: list[Path]) -> tuple[list[dict[str, Any]], int]:
    campaigns: list[dict[str, Any]] = []
    unreadable = 0
    for path in files:
        try:
            campaigns.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            unreadable += 1
    return campaigns, unreadable
