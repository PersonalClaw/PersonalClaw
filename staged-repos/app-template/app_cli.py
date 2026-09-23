"""CLI seams for app-template (plan 32): a setup step and a doctor probe.

``personalclaw setup`` calls :func:`setup` after the core steps; ``personalclaw doctor``
calls :func:`doctor` and renders the lines it returns as this app's section.
"""

from __future__ import annotations

from personalclaw.sdk.cli import DoctorLine, SetupContext


def setup(ctx: SetupContext) -> None:
    """Run this app's interactive setup step. Collect what the provider needs here."""
    ctx.print("App Template: nothing to configure yet.")


def doctor() -> list[DoctorLine]:
    """Report this app's health to ``personalclaw doctor``."""
    return [
        DoctorLine(
            label="App Template",
            status="ok",
            detail="provider stub installed — no checks declared yet",
        )
    ]
