"""The run controller's source, for the rails that read it as text.

The controller is ``workflows/controller.py`` plus its responsibility modules: every workflows
module whose functions take the controller as ``ctl: RunController``. A rail that reads only
``controller.py`` silently stops seeing whatever moves into one of them, so every rail that asks
"does the controller publish/spell/journal X" reads this set instead. Derived rather than listed,
so a responsibility split out later is covered without anyone remembering to add it here.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "workflows"

#: The derivation's vacuity floor. The split that introduced this set left thirteen responsibility
#: modules plus ``liveness``; a derivation that finds far fewer has broken, and every rail built on
#: it would read a clean result off a handful of files.
MIN_CONTROLLER_MODULES = 12


def controller_modules() -> list[Path]:
    """``controller.py`` first, then each responsibility module, sorted."""
    modules = [WORKFLOWS / "controller.py"]
    for path in sorted(WORKFLOWS.glob("*.py")):
        if path.name == "controller.py":
            continue
        if "ctl: RunController" in path.read_text(encoding="utf-8"):
            modules.append(path)
    assert len(modules) >= MIN_CONTROLLER_MODULES, (
        f"found only {len(modules)} controller modules under {WORKFLOWS} — the derivation is "
        "broken, so a rail reading them would pass on nothing"
    )
    return modules


def controller_source() -> str:
    """Every controller module's text, concatenated in ``controller_modules()`` order."""
    return "\n".join(path.read_text(encoding="utf-8") for path in controller_modules())
