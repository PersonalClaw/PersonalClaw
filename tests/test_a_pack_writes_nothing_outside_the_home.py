"""Importing a pack writes nothing outside the home: a pack whose name or component id would climb
out of its folder is refused, before any of it is parsed or written.

🔴 A component's id builds its path in the home (``import_.component_path``: ``prompts/<id>.yaml``,
``agents/<id>/agent.json``, …) and the pack's name its staging folder (``packs/staged/<name>/``).
The build checks the ids it writes (``build.safe_component_id``); the import checked neither, and
inspection called such a pack clean. A prompt id of ``../../../escaped`` wrote ``escaped.yaml``
outside the home, and a pack named ``../../../staged-escape`` staged its triggers out of it.

The import now refuses such a pack whole, as it refuses a member name that climbs out
(``_extract_quarantine``), and every path the pack layout builds is checked again, the symlinks on
the way followed (``_inside``).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from personalclaw.packs import import_ as pack_import
from personalclaw.packs.build import build_pack
from personalclaw.packs.import_ import PackImportRefused, import_pack, inspect_pack

TRIGGER = {"name": "nightly", "kind": "clock", "enabled": True, "action": {"ref": "prompt:intro"}}


@pytest.fixture
def pack(tmp_path, monkeypatch) -> Path:
    """A real pack, built from an author home that holds one prompt."""
    author = tmp_path / "author"
    (author / "prompts").mkdir(parents=True)
    (author / "prompts" / "intro.yaml").write_text("name: intro\nkind: user\ncontent: |\n  Hi.\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(author))
    out = tmp_path / "p.pclaw"
    build_pack(["prompt:intro"], name="probe", version="1.0.0", out_path=out)
    return out


@pytest.fixture
def importer(tmp_path, monkeypatch) -> Path:
    """The importing home, as a user's is: it has prompts, and has imported a pack before."""
    home = tmp_path / "homes" / "importer"
    (home / "prompts").mkdir(parents=True)
    (home / "packs" / "staged").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _rewrite(path: Path, change) -> None:
    """Rewrite *path*'s manifest and members through ``change``, and re-derive its content hash
    the way the build does — an honest pack but for what ``change`` did."""
    with zipfile.ZipFile(path) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(members["pack.json"])
    change(manifest, members)
    shas = [
        hashlib.sha256(members[c["path"]]).hexdigest()
        for c in manifest["components"]
        if c["path"] in members
    ]
    manifest["provenance"]["content_hash"] = hashlib.sha256(
        "".join(sorted(shas)).encode()
    ).hexdigest()
    members["pack.json"] = json.dumps(manifest, indent=2).encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, raw in members.items():
            zf.writestr(name, raw)


def _with_a_trigger(manifest: dict, members: dict[str, bytes]) -> None:
    raw = json.dumps(TRIGGER).encode()
    members["triggers/nightly.json"] = raw
    manifest["components"].append(
        {
            "kind": "trigger",
            "id": "nightly",
            "path": "triggers/nightly.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "depends_on": [],
        }
    )


def _import(pack: Path) -> PackImportRefused | None:
    try:
        import_pack(pack, consent=True)
    except PackImportRefused as exc:
        return exc
    return None


def _outside(tmp_path: Path, home: Path, name: str) -> list[Path]:
    return [p for p in tmp_path.rglob(f"{name}*") if home not in p.parents]


def test_the_control_the_pack_imports(pack, importer):
    """The pack each test below changes one thing of imports as it is, trigger and all."""
    _rewrite(pack, _with_a_trigger)
    assert _import(pack) is None
    assert (importer / "prompts" / "intro.yaml").is_file()
    assert (importer / "packs" / "staged" / "probe" / "triggers" / "nightly.json").is_file()


def test_a_component_id_that_climbs_out_writes_nothing_outside_the_home(tmp_path, pack, importer):
    def climb(manifest: dict, _members: dict) -> None:
        (prompt,) = [c for c in manifest["components"] if c["kind"] == "prompt"]
        prompt["id"] = "../../../escaped"

    _rewrite(pack, climb)
    refused = _import(pack)
    assert _outside(tmp_path, importer, "escaped") == [], "the pack wrote outside the home"
    assert refused is not None and refused.reason == "integrity"
    assert "names a path outside its store" in str(refused)
    with pytest.raises(PackImportRefused):
        inspect_pack(pack)  # nor is it shown clean: the inspection refuses it too


def test_a_pack_name_that_climbs_out_stages_nothing_outside_the_home(tmp_path, pack, importer):
    def named(manifest: dict, members: dict) -> None:
        _with_a_trigger(manifest, members)
        manifest["name"] = "../../../staged-escape"

    _rewrite(pack, named)
    refused = _import(pack)
    assert _outside(tmp_path, importer, "staged-escape") == [], "staged outside the home"
    assert refused is not None and refused.reason == "integrity"
    assert "not one plain name" in str(refused)


def test_the_build_and_the_import_share_one_id_rule(tmp_path):
    """A nested skill id is a pack id (``utils/tiny-url``); one that climbs out is none."""
    from personalclaw.packs.build import safe_component_id

    home = tmp_path / "home"
    assert safe_component_id("utils/tiny-url")
    assert pack_import.component_path("skill", "utils/tiny-url", home, "") == (
        home / "skills" / "utils" / "tiny-url"
    )
    assert not safe_component_id("")
    for bad in ("../x", "/abs", "a/../../b", "a\\b"):
        assert not safe_component_id(bad), bad
        with pytest.raises(PackImportRefused):
            pack_import.component_path("prompt", bad, home, "")


def test_the_layout_refuses_a_path_that_leaves_through_a_symlink(tmp_path):
    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (home / "prompts" / "shared").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(PackImportRefused, match="outside prompts/"):
        pack_import.component_path("prompt", "shared/card", home, "")


def test_uninstall_keeps_a_component_whose_folder_leads_out(tmp_path):
    """Uninstall removes a component only where the pack layout puts it. An agent whose folder was
    made a symlink out of the home: its ``agent.json`` there was removed through the link."""
    from personalclaw.packs import uninstall

    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "agent.json").write_text("{}", encoding="utf-8")
    (home / "agents" / "cfo").symlink_to(elsewhere, target_is_directory=True)
    lock = {"path": "agents/cfo/agent.json"}
    assert uninstall._removable_path("agent:cfo", lock, home, "probe") is None
