"""Provider-backed skill file browser (`/api/skills/{name}/files`).

The security set is the load-bearing part: reads go through the Skills entity
provider's single-root `fetch()` (the containment boundary), with
`is_sensitive_path` + size/entry caps as defense-in-depth. These tests pin:

  - tree view returns {path,size} per file, contents omitted;
  - single-file view returns content;
  - unsafe skill key / path → 400;
  - unknown skill → 404; unknown file → 404;
  - a file symlinked to a credential location → omitted from the tree / 403 on
    direct read (`is_sensitive_path`);
  - over-cap file → 413; the entry cap is honored;
  - `loaded_by_agents` reflects AgentProfile.skills + resources skill:// globs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import skills as skills_h


def _make_skill(root: Path, name: str, files: dict[str, str]) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = skill_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return skill_dir


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    """A single skill discovery root, wired into _all_skill_paths."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("personalclaw.agent._all_skill_paths", lambda: [str(root)])
    return root


def _files(name: str, path: str | None = None) -> tuple[int, dict]:
    query = f"?path={path}" if path is not None else ""
    req = make_mocked_request("GET", f"/api/skills/{name}/files{query}", match_info={"name": name})
    resp = asyncio.run(skills_h.api_skill_files(req))
    return resp.status, json.loads(resp.body.decode())


def test_tree_lists_paths_and_sizes(skill_root):
    _make_skill(skill_root, "greet", {"SKILL.md": "---\nname: greet\n---\nhi", "ref.md": "xyz"})
    status, body = _files("greet")
    assert status == 200
    paths = {f["path"]: f["size"] for f in body["files"]}
    assert paths["SKILL.md"] == len("---\nname: greet\n---\nhi".encode())
    assert paths["ref.md"] == 3
    assert all("content" not in f for f in body["files"])  # tree omits contents


def test_single_file_returns_content(skill_root):
    _make_skill(skill_root, "greet", {"SKILL.md": "body", "ref.md": "deep content"})
    status, body = _files("greet", "ref.md")
    assert status == 200
    assert body == {"name": "greet", "path": "ref.md", "content": "deep content"}


def test_unsafe_skill_name_rejected(skill_root):
    status, body = _files("../etc")
    assert status == 400


def test_unsafe_path_rejected(skill_root):
    _make_skill(skill_root, "greet", {"SKILL.md": "x"})
    status, body = _files("greet", "../../secret")
    assert status == 400


def test_unknown_skill_404(skill_root):
    status, body = _files("nonexistent")
    assert status == 404


def test_unknown_file_404(skill_root):
    _make_skill(skill_root, "greet", {"SKILL.md": "x"})
    status, body = _files("greet", "missing.md")
    assert status == 404


def test_oversize_file_413(skill_root, monkeypatch):
    monkeypatch.setattr(skills_h, "SKILL_FILE_MAX_BYTES", 8)
    _make_skill(skill_root, "greet", {"SKILL.md": "x", "big.md": "0123456789"})
    status, body = _files("greet", "big.md")
    assert status == 413


def test_entry_cap_honored(skill_root, monkeypatch):
    monkeypatch.setattr(skills_h, "SKILL_FILES_MAX", 2)
    files = {"SKILL.md": "x", "a.md": "a", "b.md": "b", "c.md": "c"}
    _make_skill(skill_root, "greet", files)
    status, body = _files("greet")
    assert status == 200
    assert len(body["files"]) == 2


def test_sensitive_file_omitted_from_tree(skill_root, monkeypatch):
    _make_skill(skill_root, "greet", {"SKILL.md": "x", "creds.txt": "secret"})
    # Mark the resolved creds.txt path as sensitive.
    monkeypatch.setattr(
        "personalclaw.security.is_sensitive_path",
        lambda p: p.endswith("creds.txt"),
    )
    status, body = _files("greet")
    assert status == 200
    assert {f["path"] for f in body["files"]} == {"SKILL.md"}


def test_sensitive_file_403_on_direct_read(skill_root, monkeypatch):
    _make_skill(skill_root, "greet", {"SKILL.md": "x", "creds.txt": "secret"})
    monkeypatch.setattr(
        "personalclaw.security.is_sensitive_path",
        lambda p: p.endswith("creds.txt"),
    )
    status, body = _files("greet", "creds.txt")
    assert status == 403


class TestLoadedByAgents:
    def test_from_agent_profile_skills(self, monkeypatch):
        class _Profile:
            skills = ["greet", "search"]

        class _Cfg:
            agents = {"helper": _Profile()}

        monkeypatch.setattr("personalclaw.config.AppConfig.load", staticmethod(lambda: _Cfg()))
        # No AGENTS_DIR resources path for this test.
        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", Path("/nonexistent-agents-dir"))
        out = skills_h._loaded_by_agents(["greet", "search", "other"])
        assert out["greet"] == ["helper"]
        assert out["search"] == ["helper"]
        assert out["other"] == []

    def test_from_resources_skill_glob(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "acp.json").write_text(
            json.dumps({"name": "acp-agent", "resources": ["skill://gr*"]}),
            encoding="utf-8",
        )

        class _Cfg:
            agents = {}

        monkeypatch.setattr("personalclaw.config.AppConfig.load", staticmethod(lambda: _Cfg()))
        monkeypatch.setattr("personalclaw.agent.AGENTS_DIR", agents_dir)
        out = skills_h._loaded_by_agents(["greet", "search"])
        assert out["greet"] == ["acp-agent"]
        assert out["search"] == []


# ── S6 integrity surface (POST /api/skills/{name}/verify + list annotation) ──────


def _verify(name: str) -> tuple[int, dict]:
    req = make_mocked_request("POST", f"/api/skills/{name}/verify", match_info={"name": name})
    resp = asyncio.run(skills_h.api_skill_verify(req))
    return resp.status, json.loads(resp.body.decode())


def test_verify_unlocked_skill_is_unverified(skill_root):
    """A hand-placed skill with no .pclaw-lock.json → unverified (not a failure)."""
    _make_skill(skill_root, "greet", {"SKILL.md": "---\nname: greet\n---\nhi"})
    status, body = _verify("greet")
    assert status == 200
    assert body["integrity"] == "unverified" and body["unlocked"] is True


def test_verify_detects_tamper(skill_root):
    """A locked skill whose file changed on disk → tampered, naming the mutated file."""
    import hashlib
    import json as _json

    skill = _make_skill(skill_root, "locked", {"SKILL.md": "---\nname: locked\n---\nbody"})
    baseline = hashlib.sha256("---\nname: locked\n---\nbody".encode()).hexdigest()
    (skill / ".pclaw-lock.json").write_text(
        _json.dumps({"id": "locked", "sha256": {"SKILL.md": baseline}}), encoding="utf-8"
    )
    # intact first
    status, body = _verify("locked")
    assert body["integrity"] == "intact"
    # tamper → detected
    (skill / "SKILL.md").write_text("---\nname: locked\n---\nEVIL", encoding="utf-8")
    status, body = _verify("locked")
    assert status == 200
    assert body["integrity"] == "tampered"
    assert body["mutated"] == ["SKILL.md"]


def test_verify_unsafe_name_rejected(skill_root):
    status, _ = _verify("../etc")
    assert status == 400


def test_verify_unknown_skill_404(skill_root):
    status, _ = _verify("nope")
    assert status == 404


def test_list_annotates_integrity(skill_root):
    """GET /api/skills carries an integrity field per skill."""
    _make_skill(skill_root, "greet", {"SKILL.md": "---\nname: greet\n---\nhi"})
    req = make_mocked_request("GET", "/api/skills")
    resp = asyncio.run(skills_h.api_skills_list(req))
    skills = json.loads(resp.body.decode())
    greet = next(s for s in skills if s["name"] == "greet")
    assert greet["integrity"] == "unverified"  # no lock → unverified
