"""An archive handed to the dashboard brings no integration client in.

A registered integration client is a credential: its token's hash, in ``inbound_clients.json``,
lets whoever holds the token reach what the client is bound to. A sync never brings another
machine's in, but an import (``POST /api/durability/import``, merge) copied the archive's registry
into any home without one — the usual case — so any live session could plant a working credential
by uploading an archive built around a token of its choosing, and the client then appeared in
Settings → Devices as if the owner had registered it. A merge restore did the same from an old
archive. Both now leave the registry, and the token lifetimes beside it, to this machine; a replace
restore, run with the gateway stopped, still brings the whole home back.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from personalclaw.durability import inventory as inv
from personalclaw.inbound import clients as clients_mod
from personalclaw.portability import apply_import_zip, create_export_zip


def _home(path: Path):
    """Point every resolver at *path*, as the gateway serving that home does."""
    return patch.dict(os.environ, {"PERSONALCLAW_HOME": str(path)})


def _export_with_a_client(source: Path) -> Path:
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    with _home(source), patch("personalclaw.portability.config_dir", return_value=source):
        clients_mod.reset_for_tests()
        clients_mod.create_client("Build bot", surfaces=["mcp"], actor="test")
        assert (source / "inbound_clients.json").is_file()
        zip_bytes, _manifest = create_export_zip()
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    handle.write(zip_bytes)
    handle.close()
    return Path(handle.name)


def test_the_export_still_carries_the_registry_for_a_replace_restore(tmp_path):
    archive = _export_with_a_client(tmp_path / "source")
    try:
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            names = {Path(n).name for n in zf.namelist()}
        assert "inbound_clients.json" in names
    finally:
        archive.unlink()


def test_an_import_into_a_home_without_clients_registers_none(tmp_path):
    archive = _export_with_a_client(tmp_path / "source")
    target = tmp_path / "target"
    target.mkdir()
    try:
        with _home(target), patch("personalclaw.portability.config_dir", return_value=target):
            clients_mod.reset_for_tests()
            apply_import_zip(archive, mode="merge")
            assert clients_mod.load_clients() == {}, "the archive's client was registered here"
        assert not (target / "inbound_clients.json").exists()
        assert not (target / "inbound_tokens.json").exists()
    finally:
        archive.unlink()
        clients_mod.reset_for_tests()


def test_neither_a_merge_restore_nor_an_import_takes_an_archives_clients():
    for path in ("inbound_clients.json", "inbound_tokens.json"):
        (entry,) = [e for e in inv.INVENTORY if e.path == path]
        assert entry.merged_in is False, f"{path} would arrive from an archive"
