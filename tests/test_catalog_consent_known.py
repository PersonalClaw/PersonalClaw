"""consentKnown separates "declared none" from "not known yet" (issue 614).

33 of 36 Store apps declare no permissions block, and the consent panel hid the whole
section for them — "asked for nothing" rendered exactly like silence. But the fix cannot
simply always-render: a registry POINTER has no manifest until install, so its empty
permissions genuinely mean "unknown". Both cases ship permissions={} on the wire; this
flag is the one authority for which sentence the consent surface may say.
"""

from __future__ import annotations

import json

from personalclaw.apps import catalog as C


def _write_app(root, name: str, manifest: dict) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({"name": name, "version": "1.0.0", **manifest}))


def _scan(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "list_local_sources", lambda: [str(tmp_path)])
    monkeypatch.setattr(C, "_installed_names", lambda: set())
    monkeypatch.setattr(C, "first_party_sources", lambda: set())
    return C._scan_local_sources()


def test_scanned_manifest_without_permissions_is_known_and_empty(tmp_path, monkeypatch):
    """The issue's 33-app case: a real manifest, no permissions block → consent IS
    known, and it is 'declared none' — the UI may say so."""
    _write_app(tmp_path, "silent-app", {"description": "declares nothing"})
    entries = _scan(tmp_path, monkeypatch)
    assert len(entries) == 1
    e = entries[0]
    assert e.consentKnown is True
    assert e.permissions == {}
    assert e.to_dict()["consentKnown"] is True


def test_scanned_manifest_with_permissions_is_known_and_carries_them(tmp_path, monkeypatch):
    _write_app(tmp_path, "loud-app", {"permissions": {"storage": True}})
    (e,) = _scan(tmp_path, monkeypatch)
    assert e.consentKnown is True
    assert e.permissions.get("storage") is True


def test_registry_pointer_is_not_known(tmp_path):
    """A pointer has no manifest yet — its empty permissions mean 'unknown', and the
    consent surface must not present that as 'declared none'."""
    p = C.RegistryPointer(name="remote-thing", repo="https://example.invalid/repo.git")
    e = C._pointer_to_entry("https://example.invalid/source.git", p, is_git=True)
    assert e.consentKnown is False
    assert e.permissions == {}
