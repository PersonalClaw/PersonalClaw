"""A merge restore, and an import, take a folder store in by the rule a sync takes one in.

🔴 A merge restore of a snapshot, or a merge import of an export archive, copied a folder store's
files in whole wherever this home lacked them. So another machine's workflow arrived with the
``approval_mode: auto`` and ``capability: mutating`` its steps were allowed there — its steps
acting without asking and writing here, on a yes nobody here gave — and its agent CLI runtime
config (``agents/personalclaw.json``), which lists the tools that machine's agent runs without
asking and the servers it starts, arrived in a home that had none yet.

Both now bring a folder in the way a sync brings another machine's (``reconcile.bring_in_folder``):
each file this home lacks arrives, a workflow without its steps' loosening keys; the files it has
stay exactly as they are; and what stays on each machine never comes in.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tests.test_what_another_machine_allowed_waits_for_you import _definition

_DEF = Path("workflows") / "defs" / "nightly" / "workflow.json"
_MINE = Path("workflows") / "defs" / "mine" / "workflow.json"
_AGENT_CONFIG = {
    "mcpServers": {"shell": {"command": "/nonexistent/pc-fixture-mcp"}},
    "allowedTools": ["*"],
}


def _as(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _stages(home: Path) -> dict[str, dict]:
    document = json.loads((home / _DEF).read_text())
    return {child["id"]: child["config"] for child in document["root"]["children"]}


def _another_machine(home: Path) -> None:
    """A home whose owner allowed a workflow's steps, with its agent CLI's own runtime config."""
    _write(
        home / _DEF, _definition(plan={"approval_mode": "auto"}, write={"capability": "mutating"})
    )
    _write(home / "agents" / "personalclaw.json", _AGENT_CONFIG)
    _write(home / "agents" / "helper.json", {"name": "helper", "description": "helps"})
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "standup.yaml").write_text("name: standup\ncontent: What moved?\n")


def _this_machine(home: Path) -> dict[Path, bytes]:
    """This home's own files, which a merge must leave exactly as they are."""
    _write(home / _MINE, _definition(plan={"approval_mode": "auto"}, write={}))
    _write(home / "agents" / "mine.json", {"name": "mine"})
    return {p: p.read_bytes() for p in (home / _MINE, home / "agents" / "mine.json")}


def _assert_taken_in_by_the_rule(home: Path, own: dict[Path, bytes]) -> None:
    stages = _stages(home)
    assert "approval_mode" not in stages["plan"], "a step arrived acting without asking"
    assert "capability" not in stages["write"], "a step arrived with write access"
    assert stages["plan"]["prompt"] == "plan it", "the rest of the workflow came in"
    assert not (home / "agents" / "personalclaw.json").exists(), "its agent CLI config came in"
    assert json.loads((home / "agents" / "helper.json").read_text())["description"] == "helps"
    assert (home / "prompts" / "standup.yaml").read_text().startswith("name: standup")
    for path, raw in own.items():
        assert path.read_bytes() == raw, f"{path} changed"


def test_a_merge_restore_takes_another_machines_workflow_in_asking(tmp_path, monkeypatch):
    from personalclaw.snapshot import restore_main, snapshot_main

    a = _as(monkeypatch, tmp_path / "A")
    _another_machine(a)
    assert snapshot_main([str(tmp_path / "snaps")]) in (0, None)
    (tarball,) = sorted((tmp_path / "snaps").glob("personalclaw-snapshot-*.tar.gz"))

    b = _as(monkeypatch, tmp_path / "B")
    own = _this_machine(b)
    assert restore_main([str(tarball), "--mode", "merge"]) == 0
    _assert_taken_in_by_the_rule(b, own)


def test_an_import_takes_another_machines_workflow_in_asking(tmp_path, monkeypatch):
    from personalclaw.portability import apply_import_zip

    source = tmp_path / "their-home"
    _another_machine(source)
    archive = tmp_path / "export.zip"
    root = "personalclaw-export-20260901T000000Z"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            zf.writestr(f"{root}/{path.relative_to(source).as_posix()}", path.read_bytes())

    b = _as(monkeypatch, tmp_path / "B")
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: b)
    own = _this_machine(b)
    apply_import_zip(archive, mode="merge")
    _assert_taken_in_by_the_rule(b, own)


@pytest.mark.parametrize("store", ["workflows", "agents", "prompts"])
def test_the_merge_plan_says_how_a_folder_comes_in(tmp_path, store):
    from personalclaw.snapshot import merge_plan

    _another_machine(tmp_path / "A")
    rows = {r["path"]: r for r in merge_plan(tmp_path / "A", tmp_path / "B", None)}
    assert rows[store]["detail"] == "one file at a time, by the rule a sync brings them in"
