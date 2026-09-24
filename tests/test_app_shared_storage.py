"""APE-10: consented cross-app READ-ONLY file sharing (the mirror of APE-9 messaging).

The file-sharing seam reuses APE-9's shape: DOUBLE-DECLARATION (the sharer opts in with
``storageShared`` and the consumer names it in ``storageRead``), DENY-BY-DEFAULT, FENCED
by the sandbox (the mount is read-only and the SDK refuses writes), and SEL-AUDITED (an
active grant emits ``capability_grant``). This suite pins each of those properties end to
end: a granted consumer reads a file the sharer wrote via ``PERSONALCLAW_APP_SHARED_DIR_
<NAME>``; an undeclared pair gets NO mount; a write to the shared dir fails; and install
consent lists the grant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, backend_runtime, manager
from personalclaw.apps.manifest import AppManifest, Permissions
from personalclaw.apps.permissions import PermissionChecker, checker_for

_SHARER_ENV = "PERSONALCLAW_APP_SHARED_DIR_NOTE_KEEPER"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Bind config_dir AND the SEL home to tmp_path so nothing touches the real home
    (a ``capability_grant`` SEL is emitted by the mount path)."""
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _install(tmp_path: Path, name: str, *, permissions: dict | None = None, backend=False) -> Path:
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    mani: dict = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if permissions is not None:
        mani["permissions"] = permissions
    if backend:
        mani["backend"] = {"entryPoint": "backend/server.py", "type": "python"}
        bd = d / "backend"
        bd.mkdir()
        (bd / "server.py").write_text("print('x')\n", encoding="utf-8")
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    res = app_manager.install(d)
    assert res.ok, res.error
    return d


# ── unit: the double-declaration gate (deny-by-default) ──


def test_permissions_roundtrip_carries_the_pair():
    p = Permissions(storageShared=True, storageRead=["note-keeper", "mail-*"])
    d = p.to_dict()
    assert d["storageShared"] is True
    assert d["storageRead"] == ["note-keeper", "mail-*"]
    r = Permissions.from_dict(d)
    assert r.storageShared is True
    assert r.storageRead == ["note-keeper", "mail-*"]
    # Empty is omitted from the consent surface (deny-by-default reads as silence there).
    assert "storageShared" not in Permissions().to_dict()
    assert "storageRead" not in Permissions().to_dict()


def test_can_read_shared_storage_deny_by_default():
    # A consumer that names nothing may read no app — even before any target exists.
    assert not PermissionChecker("reader", Permissions()).can_read_shared_storage("note-keeper")


def test_double_declaration_grants_read(tmp_path):
    _install(tmp_path, "note-keeper", permissions={"storageShared": True})
    _install(tmp_path, "reader", permissions={"storageRead": ["note-keeper"]})
    assert checker_for("reader").can_read_shared_storage("note-keeper")


def test_consumer_without_storage_read_is_denied(tmp_path):
    _install(tmp_path, "note-keeper", permissions={"storageShared": True})
    _install(tmp_path, "reader")  # names nothing
    assert not checker_for("reader").can_read_shared_storage("note-keeper")


def test_sharer_without_storage_shared_is_denied(tmp_path):
    # The consumer names the sharer, but the sharer never opted in → no one-sided grant.
    _install(tmp_path, "note-keeper")  # no storageShared
    _install(tmp_path, "reader", permissions={"storageRead": ["note-keeper"]})
    assert not checker_for("reader").can_read_shared_storage("note-keeper")


# ── the env mount: read-only, deny-by-default, SEL-audited ──


def test_env_mount_reads_sharer_file_and_audits_grant(tmp_path, monkeypatch):
    """done_when: a granted consumer reads the sharer's file via the env mount, and the
    active grant is recorded on the SEL."""
    from personalclaw.sdk.util import shared_app_data_dir
    from personalclaw.sel import sel

    _install(tmp_path, "note-keeper", permissions={"storageShared": True})
    _install(tmp_path, "reader", permissions={"storageRead": ["note-keeper"]})
    # The sharer writes into its OWN data dir.
    (manager.app_data_dir("note-keeper") / "notes.json").write_text('{"n":1}', encoding="utf-8")

    env = backend_runtime.shared_storage_env("reader")
    assert env == {_SHARER_ENV: str(manager.app_data_dir("note-keeper"))}

    # The consumer's backend sees that env var; the SDK hands it a read-only handle.
    monkeypatch.setenv(_SHARER_ENV, env[_SHARER_ENV])
    shared = shared_app_data_dir("note-keeper")
    assert shared is not None
    assert (shared / "notes.json").read_text() == '{"n":1}'

    # A capability_grant SEL row audits the active grant.
    grants = [
        e
        for e in sel().recent(20)
        if e.get("event_type") == "capability_grant" and e.get("outcome") == "granted"
    ]
    assert grants, f"no capability_grant in SEL: {sel().recent(20)}"
    assert "sharer=note-keeper" in grants[0].get("resources", "")
    assert grants[0].get("caller_identity") == "app:reader"


def test_undeclared_pair_gets_no_mount(tmp_path):
    """done_when: missing EITHER declaration → no mount at all (deny-by-default)."""
    _install(tmp_path, "note-keeper")  # sharer never opted in
    _install(tmp_path, "reader", permissions={"storageRead": ["note-keeper"]})
    assert backend_runtime.shared_storage_env("reader") == {}


def test_write_to_shared_dir_fails(tmp_path, monkeypatch):
    """done_when: read-only is the contract — a write to the shared path fails."""
    from personalclaw.sdk.util import shared_app_data_dir

    _install(tmp_path, "note-keeper", permissions={"storageShared": True})
    _install(tmp_path, "reader", permissions={"storageRead": ["note-keeper"]})
    (manager.app_data_dir("note-keeper") / "notes.json").write_text("v", encoding="utf-8")

    env = backend_runtime.shared_storage_env("reader")
    monkeypatch.setenv(_SHARER_ENV, env[_SHARER_ENV])
    shared = shared_app_data_dir("note-keeper")
    assert shared is not None
    with pytest.raises(PermissionError):
        (shared / "evil.txt").write_text("nope")
    with pytest.raises(PermissionError):
        (shared / "notes.json").open("w")
    # ...while reads still work through the same handle.
    assert (shared / "notes.json").read_text() == "v"


def test_backend_start_hands_reader_the_shared_dir_env(tmp_path, monkeypatch):
    """The full launch path (mirrors test_app_storage): the reader's backend env carries
    PERSONALCLAW_APP_SHARED_DIR_<SHARER>, pointing at the sharer's data dir."""
    captured: dict = {}

    class _FakeProc:
        def __init__(self):
            self.pid = 4444

        def poll(self):
            return None

    def _fake_popen(cmd, cwd=None, env=None, **kw):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(backend_runtime.subprocess, "Popen", _fake_popen)
    _install(tmp_path, "note-keeper", permissions={"storageShared": True})
    _install(tmp_path, "reader", backend=True, permissions={"storageRead": ["note-keeper"]})
    sup = backend_runtime.BackendSupervisor()
    manifest = AppManifest.from_json_file(manager.app_dir("reader") / "app.json")
    sup.start(manifest)
    assert captured["env"][_SHARER_ENV] == str(manager.app_dir("note-keeper") / "data")


# ── install consent lists the grant ──


def test_consent_surface_lists_the_grant():
    from personalclaw.apps.catalog import _manifest_consent

    consumer = AppManifest.from_dict(
        {
            "name": "reader",
            "version": "1.0.0",
            "displayName": "Reader",
            "description": "x",
            "permissions": {"storageRead": ["note-keeper", "mail-*"]},
        }
    )
    perms, _crons = _manifest_consent(consumer)
    assert perms["storageRead"] == ["note-keeper", "mail-*"]

    sharer = AppManifest.from_dict(
        {
            "name": "note-keeper",
            "version": "1.0.0",
            "displayName": "Note Keeper",
            "description": "x",
            "permissions": {"storageShared": True},
        }
    )
    perms2, _ = _manifest_consent(sharer)
    assert perms2["storageShared"] is True
