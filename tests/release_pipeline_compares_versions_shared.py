"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_release_pipeline_compares_versions.py``

Its checks:
``tests/rails/test_release_pipeline_compares_versions_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


SCRIPT = REPO / "scripts" / "release_version.py"


def _classified_version(ref: str) -> str:
    """The version the `build` job hands on for *ref*: `release_tags.py --github-output`."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "release_tags.py"),
            "--ref",
            ref,
            "--github-output",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    versions = [
        line.split("=", 1)[1] for line in proc.stdout.splitlines() if line.startswith("version=")
    ]
    assert len(versions) == 1, proc.stdout
    return versions[0]


@pytest.fixture
def release_version():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_release_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pointer(shown: int, total: int, url: str) -> str:
    return (
        f"These notes show {shown:,} of the release's {total:,} entries. "
        f"All of them are in [CHANGELOG.md]({url})."
    )
