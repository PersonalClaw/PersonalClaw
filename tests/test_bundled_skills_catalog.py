"""S2/E15: bundled skills catalog refresh (widgets + artifacts).

Validates the two net-new ports land through the existing Skills entity with no
code change: discovered by the native marketplace, synced by the loader,
trigger-matched by the frontmatter contract. Plus the D2 gate — the `artifacts`
skill may only reference `artifact_*` tools that actually exist in mcp_core, so
the skill can never drift from (or ship ahead of) the live tool set.
"""

from __future__ import annotations

import re

from personalclaw.skills.marketplace import _parse_description
from personalclaw.skills.native import NativeSkillsMarketplace, _bundled_root


class TestVisualOutputSkill:
    """The merged visual-output skill (absorbs the old widgets + illustrations)."""

    def test_discovered_by_native_marketplace(self):
        detail = NativeSkillsMarketplace().fetch("visual-output")
        paths = {f["path"] for f in detail.files}
        assert "SKILL.md" in paths

    def test_frontmatter_single_line_description(self):
        md = (_bundled_root() / "visual-output" / "SKILL.md").read_text(encoding="utf-8")
        desc = _parse_description(_bundled_root() / "visual-output" / "SKILL.md")
        assert desc and "\n" not in desc  # single-line → both parsers agree
        assert "triggers:" in md and "<widget" in md

    def test_triggered_on_widget_request(self, tmp_path, monkeypatch):
        from personalclaw.skills.loader import SkillsLoader

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loader = SkillsLoader(skills_path=tmp_path / "skills")
        names = {s["key"] for s in loader.list_skills()}
        assert "visual-output" in names
        hit = loader.get_triggered_skills("can you render a widget chart")
        assert "visual-output" in hit
        miss = loader.get_triggered_skills("what is the weather today")
        assert "visual-output" not in miss

    def test_no_internal_nouns(self):
        # The denylist is base64-encoded so the published repo itself never
        # grep-hits the guarded nouns; decode at runtime to keep the guard live.
        import base64

        banned_b64 = (
            "bWVzaGNsYXc=",
            "bXdpbml0",
            "bWlkd2F5",
            "a2lybw==",
            "Y29kZS5hbWF6b24uY29t",
            "YXJjYw==",
            "dGFza2Vp",
            "cGhvbmV0b29s",
        )
        md = (_bundled_root() / "visual-output" / "SKILL.md").read_text(encoding="utf-8").lower()
        for encoded in banned_b64:
            banned = base64.b64decode(encoded).decode("utf-8")
            assert banned not in md, f"visual-output skill leaked internal noun: {banned!r}"


class TestArtifactsSkill:
    def test_discovered_by_native_marketplace(self):
        detail = NativeSkillsMarketplace().fetch("artifacts")
        paths = {f["path"] for f in detail.files}
        assert "SKILL.md" in paths

    def test_triggered_on_save_widget(self, tmp_path, monkeypatch):
        from personalclaw.skills.loader import SkillsLoader

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loader = SkillsLoader(skills_path=tmp_path / "skills")
        assert "artifacts" in {s["key"] for s in loader.list_skills()}
        assert "artifacts" in loader.get_triggered_skills("save this widget to the library")

    def test_tool_existence_cross_check(self):
        """D2: every artifact_* tool the skill names must exist on the personalclaw-core
        MCP server surface (`@personalclaw-core` is what the skill references).

        Guards against the skill shipping ahead of (or drifting from) the live tool set
        — a skill that tells the agent to call a nonexistent tool. The artifact tools
        live in the mcp_artifacts category module now, aggregated into the core MCP
        server surface, so cross-check against that aggregate.
        """
        from personalclaw.mcp_core import _aggregated_list_tools

        live = {t["name"] for t in _aggregated_list_tools()}
        md = (_bundled_root() / "artifacts" / "SKILL.md").read_text(encoding="utf-8")
        referenced = set(re.findall(r"\bartifact_[a-z]+\b", md))
        assert referenced, "skill names no artifact_* tools — wrong file?"
        missing = referenced - live
        assert not missing, f"artifacts skill references nonexistent tools: {missing}"

    def test_personalclaw_namespace(self):
        import base64

        md = (_bundled_root() / "artifacts" / "SKILL.md").read_text(encoding="utf-8")
        assert "@personalclaw-core" in md
        # base64("@meshclaw-core") — the pre-rename namespace must not resurface
        # (encoded so the published repo doesn't grep-hit the old name).
        assert base64.b64decode("QG1lc2hjbGF3LWNvcmU=").decode("utf-8") not in md
