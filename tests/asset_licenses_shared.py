"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_asset_licenses.py``

Its checks:
``tests/rails/test_asset_licenses_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


_CHECK = re.compile(r"^(?:uv run )?python3? scripts/check_asset_licenses\.py( --built-web)?$")


def _asset_checks(workflow_text: str) -> list[tuple[bool, bool]]:
    """``(built_web, after_a_web_build)`` for every EXECUTED asset-licence check."""
    workflow = yaml.safe_load(workflow_text)
    checks = []
    for job in (workflow.get("jobs") or {}).values():
        built_at = None
        for index, step in enumerate(job.get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for raw in run.splitlines():
                line = raw.strip()
                if line == "npm run build" and built_at is None:
                    built_at = index
                match = _CHECK.match(line)
                if match:
                    checks.append((bool(match.group(1)), built_at is not None and built_at < index))
    return checks
