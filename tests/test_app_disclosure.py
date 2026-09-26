"""`apps/disclosure` — the one projection of what installing an app grants and runs.

The Store card, the install review and the install gate all read it, so what it omits no
consent surface can show. These pin that it states everything the install then DOES —
including the things the old consent screen never mentioned (the job that switches on, the
server process, the install hook, the MCP servers) — and that the digest consent is bound
to moves with every byte.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.apps.catalog import CatalogEntry
from personalclaw.apps.disclosure import bundle_digest, changed, describe
from personalclaw.apps.manifest import AppManifest


def _manifest(**over) -> AppManifest:
    data = {
        "name": "growth-like",
        "version": "1.0.0",
        "displayName": "Growth-like",
        "description": "fixture",
        "permissions": {"api": ["/api/tasks"], "agent": True, "cron": True, "network": False},
        "crons": [
            {"name": "daily", "cron_expr": "3 18 * * *", "message": "capture today"},
            {"name": "hourly", "every": 3600, "agent": "researcher", "message": "advance"},
        ],
        "backend": {"entryPoint": "backend/server.py", "type": "python"},
        "setup": {"onInstall": "bash setup.sh", "onUpdate": "bash migrate.sh"},
        "mcpServers": {
            "local": {"command": "python", "args": ["backend/mcp.py"]},
            "remote": {"url": "https://mcp.example.com/sse"},
        },
        "ui": {"pages": [{"route": "/apps/growth-like", "label": "Growth"}]},
        "dependencies": {"pythonDependencies": ["somepkg>=1.0"]},
    }
    data.update(over)
    return AppManifest.from_dict(data)


def test_it_states_what_the_install_grants_and_runs():
    d = describe(_manifest())
    assert d["permissions"]["api"] == ["/api/tasks"] and d["permissions"]["agent"] is True
    assert [c["name"] for c in d["crons"]] == ["daily", "hourly"]
    assert all(c["scheduled"] for c in d["crons"]), "the cron permission switches both on"
    assert d["crons"][0]["cadence"], "a clock-time job is described in words"
    assert d["crons"][1]["agent"] == "researcher"
    assert d["pythonDependencies"] == [{"spec": "somepkg>=1.0", "coreOwned": False}]
    assert d["hasUI"] is True
    assert d["hasBackend"] is True
    assert d["onInstall"] == "bash setup.sh" and d["onUpdate"] == "bash migrate.sh"
    assert d["mcpServers"] == [
        {"name": "local", "launches": "python backend/mcp.py"},
        {"name": "remote", "launches": "https://mcp.example.com/sse"},
    ]


def test_a_job_without_the_cron_permission_is_declared_but_not_scheduled():
    d = describe(_manifest(permissions={"network": False}))
    assert [c["scheduled"] for c in d["crons"]] == [False, False]


def test_every_key_is_a_catalog_entry_field_so_the_card_and_the_dialog_share_one_shape():
    entry = CatalogEntry(name="x", displayName="X", consentKnown=True, **describe(_manifest()))
    assert entry.to_dict()["onInstall"] == "bash setup.sh"


def test_the_digest_moves_with_every_byte_path_and_file(tmp_path: Path):
    root = tmp_path / "b"
    (root / "pkg").mkdir(parents=True)
    (root / "app.json").write_text("{}", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    first = bundle_digest(root)
    assert first == bundle_digest(root), "the same bytes must give the same digest"

    (root / "pkg" / "a.py").write_text("x = 2\n", encoding="utf-8")
    edited = bundle_digest(root)
    assert edited != first

    (root / "pkg" / "a.py").rename(root / "pkg" / "b.py")
    assert bundle_digest(root) != edited, "a rename is a different bundle"

    (root / "pkg" / "empty.txt").write_text("", encoding="utf-8")
    assert bundle_digest(root) != edited


def test_an_update_changes_what_the_app_gets_only_when_the_projection_does():
    base = describe(_manifest())
    assert changed(base, describe(_manifest(version="2.0.0"))) is False
    assert changed(base, describe(_manifest(permissions={"network": True}))) is True
    assert changed(None, base) is True, "an unreadable installed manifest is not 'no change'"
