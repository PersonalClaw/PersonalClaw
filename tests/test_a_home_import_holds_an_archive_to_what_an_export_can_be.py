"""Importing a home export holds the archive to what an export can be, before anything is read.

Settings › Import / Export, and ``personalclaw restore <export.zip>``, bring another home's export
into this one: validation first (``portability.validate_import_zip``), then the merge
(``apply_import_zip``). Both trusted the archive's sizes. Validation read the manifest whole and
every declared member whole to hash it, Import unpacked every member, and the upload was spooled at
any size. A ZIP's members are compressed, so a small file can unpack to far more than the disk or
memory holds, and an archive can hold any number of members.

Now an import refuses, with a sentence that names the limit and before anything is written:
- an archive with more members than an export holds (``MAX_IMPORT_MEMBERS``);
- a manifest past its ceiling (``MAX_IMPORT_MANIFEST_BYTES``);
- an archive whose members unpack to more than its temporary folder's disk has free, keeping a
  margin;
- a member larger than the v3 manifest declares, without reading it.
Members are hashed and unpacked as streams, never read whole, and the upload stops while it
streams once it passes the disk's free space. A real export validates and imports as before.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import portability, snapshot

ROOT = "personalclaw-export-20260101T000000Z"
_MB = 1024 * 1024


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    """An isolated home holding only its settings, and no gateway running."""
    h = tmp_path / "home"
    h.mkdir()
    (h / "config.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: h)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)
    return h


@pytest.fixture
def read_bytes(monkeypatch) -> list[int]:
    """How many bytes anything reads out of an archive's members."""
    counted: list[int] = []
    real = zipfile.ZipExtFile.read

    def read(self, n=-1):
        data = real(self, n)
        counted.append(len(data))
        return data

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    return counted


def _v2(tmp: Path, members: int = 1) -> Path:
    """A v2 archive (no checksums) holding *members* small files and its manifest."""
    path = tmp / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(members):
            zf.writestr(f"{ROOT}/workspace/notes/{i:05d}.md", f"note {i}\n")
        zf.writestr(f"{ROOT}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
    return path


def _v3_with_a_member_past_its_declared_size(tmp: Path) -> Path:
    """A v3 archive whose one member unpacks to 32 MB, compressed to a few KB, while its manifest
    says it holds 1 KB. Its checksum is the member's own, so only its size gives it away."""
    payload = b"\0" * (32 * _MB)
    manifest = {
        "version": portability.MANIFEST_VERSION,
        "members": [
            {
                "path": "workspace/notes/big.md",
                "bytes": 1024,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        "contents": {},
    }
    path = tmp / "in.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ROOT}/workspace/notes/big.md", payload)
        zf.writestr(f"{ROOT}/MANIFEST.json", json.dumps(manifest))
    assert path.stat().st_size < _MB
    return path


def _staged() -> list[Path]:
    return sorted(Path(tempfile.gettempdir()).glob(f"*/{ROOT}"))


# ── a member past its declared size ─────────────────────────────────────────


def test_a_member_larger_than_its_manifest_says_is_refused_unread(tmp_path, read_bytes) -> None:
    """🔴 Red before: the member was read whole (32 MB) to hash it, and the archive passed."""
    ok, error, _ = portability.validate_import_zip(
        _v3_with_a_member_past_its_declared_size(tmp_path)
    )

    assert not ok
    assert error == "Archive member is larger than its manifest says: workspace/notes/big.md"
    assert sum(read_bytes) < _MB, "the member was read past its declared size"


def test_a_real_v3_export_still_validates_by_streaming(tmp_path, home, read_bytes) -> None:
    """Every member of a real export is hashed, read in chunks, never whole."""
    (home / "workspace" / "notes").mkdir(parents=True)
    (home / "workspace" / "notes" / "garden.md").write_text("beans\n" * 200_000, "utf-8")
    data, _ = portability.create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(data)
    read_bytes.clear()  # what the export read making its checksums

    ok, error, manifest = portability.validate_import_zip(archive)

    assert (ok, error, manifest["verified"]) == (True, "", True)
    assert max(read_bytes) <= _MB


# ── more members than an export holds ───────────────────────────────────────


def test_more_members_than_an_export_holds_are_refused_before_any_is_read(
    tmp_path, home, monkeypatch, read_bytes
) -> None:
    """🔴 Red before: any number of members validated and unpacked."""
    monkeypatch.setattr(portability, "MAX_IMPORT_MEMBERS", 40, raising=False)
    archive = _v2(tmp_path, members=40)  # 41 with the manifest

    ok, error, _ = portability.validate_import_zip(archive)

    assert not ok
    assert error == "The archive holds 41 members; a home export holds at most 40."
    assert read_bytes == [], "a member was read before the count was checked"
    with pytest.raises(ValueError, match="holds 41 members"):
        portability.apply_import_zip(archive, "merge")
    assert not (home / "workspace").exists() and _staged() == []


def test_an_export_within_the_count_imports_as_before(tmp_path, home) -> None:
    archive = _v2(tmp_path, members=3)

    assert portability.validate_import_zip(archive)[0]
    portability.apply_import_zip(archive, "merge")

    assert (home / "workspace" / "notes" / "00002.md").read_text("utf-8") == "note 2\n"


# ── a manifest past its ceiling ─────────────────────────────────────────────


def test_a_manifest_past_its_ceiling_is_refused_unread(tmp_path, monkeypatch, read_bytes) -> None:
    """🔴 Red before: the manifest was read whole, at any size."""
    monkeypatch.setattr(portability, "MAX_IMPORT_MANIFEST_BYTES", 16, raising=False)

    ok, error, _ = portability.validate_import_zip(_v2(tmp_path))

    assert not ok
    assert error.startswith("The archive's MANIFEST.json is ")
    assert error.endswith("larger than any export's (16 bytes).")
    assert read_bytes == []


# ── more than the disk has free ─────────────────────────────────────────────


def test_an_archive_that_unpacks_past_the_free_disk_is_refused(
    tmp_path, home, monkeypatch, read_bytes
) -> None:
    """🔴 Red before: an archive was unpacked whatever it would take of the disk."""
    archive = _v3_with_a_member_past_its_declared_size(tmp_path)
    monkeypatch.setattr(
        portability, "_free_bytes", lambda _path: portability_margin() + 8 * _MB, raising=False
    )

    ok, error, _ = portability.validate_import_zip(archive)

    assert not ok
    assert error.startswith("The archive unpacks to 32.0 MB, more than the space free on the disk")
    assert read_bytes == []
    with pytest.raises(ValueError, match="unpacks to 32.0 MB"):
        portability.apply_import_zip(archive, "merge")
    assert not (home / "workspace").exists() and _staged() == []


def portability_margin() -> int:
    return getattr(portability, "IMPORT_DISK_MARGIN_BYTES", 0)


# ── the upload, and the terminal's door ─────────────────────────────────────


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app.router.add_post("/api/durability/import", mod.api_durability_import)
    return app


@pytest.mark.asyncio
async def test_an_upload_past_the_free_disk_stops_while_it_streams(tmp_path, monkeypatch) -> None:
    """🔴 Red before: the upload was spooled whole, at any size, and then validated."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    monkeypatch.setattr(
        portability, "_free_bytes", lambda _path: portability_margin() + _MB, raising=False
    )
    big = tmp_path / "big.zip"
    big.write_bytes(b"PK" + b"\0" * (3 * _MB))

    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/durability/import", data={"file": big.read_bytes()})
        body = await resp.json()

    assert resp.status == 413, body
    assert body["error"]["code"] == "request_too_large"
    assert "larger than the space free on the disk" in body["error"]["message"]
    assert list((tmp_path / "spool").iterdir()) == [], "the spool was left behind"


def test_the_terminals_restore_refuses_what_the_dashboard_refuses(
    tmp_path, home, monkeypatch, capsys
) -> None:
    """🔴 Red before: `personalclaw restore` merged an archive of any member count."""
    monkeypatch.setattr(portability, "MAX_IMPORT_MEMBERS", 40, raising=False)
    archive = _v2(tmp_path, members=40)

    assert snapshot.restore_main([str(archive), "--mode", "merge"]) == 1

    assert (
        "The archive holds 41 members; a home export holds at most 40." in capsys.readouterr().err
    )
    assert not (home / "workspace").exists()
