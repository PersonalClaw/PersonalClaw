"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_dependency_bounds.py``

Its checks:
``tests/rails/test_dependency_bounds_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


#: distribution that must carry an upper bound wherever it is declared, and why.
_MUST_BE_BOUNDED = {
    "mcp": (
        "mcp 2.0.0 renamed mcp.client.streamable_http.streamablehttp_client, which "
        "src/personalclaw/mcp_client.py imports by name"
    ),
    "openai": (
        "openai 3 declares AsyncOpenAI's http_client as an httpx2.AsyncClient, and the openai "
        "providers pass the egress-guarded httpx.AsyncClient, so whole-tree mypy fails where an "
        "unlocked install resolved 3.x"
    ),
    "python-dateutil": (
        "every botocore release requires python-dateutil<3, and the app installer refuses an app "
        "whose dependencies would move a gateway dependency, so with 3.0.0 (2026-10-21) in a fresh "
        "install no app built on botocore could be added"
    ),
}


def _declarations() -> dict[str, list[str]]:
    """Every requirement list in pyproject: the core dependencies and each extra."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    return {"dependencies": project["dependencies"], **project["optional-dependencies"]}


#: A distribution whose CEILING a behaviour needs, wherever pyproject declares the dependency
#: that needs it: that dependent, the newest release known to work, the first one that breaks it,
#: and why. The dependent does not bound it itself, so pyproject has to.
_MUST_KEEP_A_CEILING = {
    "av": (
        "faster-whisper",
        "18.1.0",
        "19.0.0",
        "PyAV 19 removed `av.open`'s `metadata_errors` keyword, which faster-whisper's "
        "decode_audio passes on every call (every release from 1.0 through 1.2.1 does), so "
        "every transcription fails with a TypeError",
    ),
}
