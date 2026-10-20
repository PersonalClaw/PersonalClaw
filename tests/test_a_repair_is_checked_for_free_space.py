"""A Repair is checked for free space like any other download.

A model whose weights came down incomplete is on disk (`downloaded: true`) with
`integrity: "truncated"`, and its Repair — the Models page chip, the Providers row — is
`POST /api/models/downloads` again. `ModelDownloadRegistry.start` fetches it again because it is
not intact; the route's free-space check skipped it because it is on disk. So a Repair started
with no check at all: never refused for space it did not have, and never carrying the warning a
disk that cannot be measured puts on the job, which is the warning its progress row draws.
"""

from __future__ import annotations

import json
import shutil

import pytest
import test_model_fit_payload as _payload

from personalclaw.dashboard import model_downloads as M
from personalclaw.dashboard.handlers import model_downloads as H
from personalclaw.local_models.provider import LocalModel

# The #3706 download harness: no bytes move, the catalog is wired per test.
_download_env = _payload._download_env
_req = _payload._req
_free_after_the_real_probe = _payload._free_after_the_real_probe

_MB = 1024 * 1024


def _truncated() -> LocalModel:
    return LocalModel(name="weights", size_mb=138, downloaded=True, integrity="truncated")


async def _repair(reg):
    resp = await H.api_model_download_start(
        _req("POST", "/api/models/downloads", reg, body={"provider": "ollama", "model": "weights"})
    )
    return resp, json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_a_repair_that_cannot_land_is_refused_up_front(
    _download_env, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(shutil, "disk_usage", _free_after_the_real_probe(50 * _MB))
    _download_env(cache_dir=str(tmp_path), models=[_truncated()])
    reg = M.ModelDownloadRegistry()

    resp, body = await _repair(reg)

    assert resp.status == 400, body
    assert body["error"]["code"] == "insufficient_disk_space"
    assert body["error"]["message"] == (
        "Not enough free disk space for this download: it needs 138.0 MiB, and 50.0 MiB is free."
    )
    assert reg.list() == []


@pytest.mark.asyncio
async def test_a_repair_on_an_unmeasurable_disk_carries_the_warning(
    _download_env, tmp_path
) -> None:
    (tmp_path / "models").symlink_to(tmp_path / "unplugged-drive")
    _download_env(cache_dir=str(tmp_path / "models" / "ollama"), models=[_truncated()])
    reg = M.ModelDownloadRegistry()

    resp, body = await _repair(reg)

    assert resp.status == 202, body
    assert body["warning"] == (
        "Free space could not be checked, so the download was not verified to fit."
    )
    assert [job.warning for job in reg.list()] == [body["warning"]]


@pytest.mark.asyncio
async def test_an_intact_model_is_still_not_checked(_download_env, monkeypatch, tmp_path) -> None:
    """The control: nothing lands for a model on disk intact, so there is nothing to refuse."""
    monkeypatch.setattr(shutil, "disk_usage", _free_after_the_real_probe(50 * _MB))
    intact = LocalModel(name="weights", size_mb=138, downloaded=True)
    _download_env(cache_dir=str(tmp_path), models=[intact])
    reg = M.ModelDownloadRegistry()

    resp, body = await _repair(reg)

    assert resp.status == 202, body
    assert body["warning"] == ""
