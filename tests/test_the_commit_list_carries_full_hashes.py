"""The Changes panel's commit list carries each commit's full hash.

Measured before this was written: ``GET /api/file-git-log`` listed commits by ``%h``, so a
repository whose ``core.abbrev`` is below 7 listed hashes of 4 to 6 characters. The cockpit's
address and ``personalclaw://`` links take 7 to 40, so such a commit opened but was not written to
the address, and a reload did not reopen it; a copied short hash also goes ambiguous as the
repository grows. Now the list carries ``%H``, and the panel shows it short.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock
from urllib.parse import quote

from aiohttp.test_utils import make_mocked_request
from test_files_git_status import git_repo  # noqa: F401 (fixture)

from personalclaw.dashboard.handlers import files as F


def test_a_repository_that_abbreviates_to_four_lists_full_hashes(
    git_repo, monkeypatch  # noqa: F811 - the imported fixture
):
    """🔴 Red before: the hash was as short as the repository abbreviates it."""
    import subprocess

    subprocess.run(["git", "config", "core.abbrev", "4"], cwd=git_repo, check=True)
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())

    req = make_mocked_request("GET", f"/api/file-git-log?path={quote(str(git_repo))}&limit=10")
    body = json.loads(asyncio.run(F.api_file_git_log(req)).body.decode())

    full = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert [c["hash"] for c in body["commits"]] == [full]
    assert len(full) == 40
