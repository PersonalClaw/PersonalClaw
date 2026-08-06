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
        # The denylist is stored as irreversible SHA-256 digests so the published
        # repo never contains the guarded nouns in any recoverable form (a base64
        # denylist still ships them). The guard stays live: hash each token in the
        # skill text and compare against the digest set.
        import hashlib
        import re

        banned_digests = {
            "9cfc2063b8f2b755719b465df28f87226d29d38e4e4485801b3d7a4b49ec53ad",
            "9565bef533fe7668fdfea4dea2d77de9b5b000d76634267faf5b2d4b2538777b",
            "2a0abe451aede7f7139e3b7be00c2adff97b5ef4a50c6b5f4a2165125b55cc15",
            "6e885b857804f868c79c20c78f03696636427565a4fbac95d7352d8530bfadf1",
            "acc0e211a3e3d504b51e3d0e0dd24d597472cfbb2f5e9897483154589826f4d4",
            "da72f57a8db8bba716a5e6bc030530b2160ff92b1c51f10b4b407aea1ef71e58",
            "87ec940abd81dcaa2ef5deb4b3bf9e354f161dc5eb51ba0e26f88ea797080b8c",
            "87cb60d3f9cbfa1e55661503e2ca017f5a11c2aa3d78e44982e370866aa8f71b",
        }
        md = (_bundled_root() / "visual-output" / "SKILL.md").read_text(encoding="utf-8").lower()
        tokens = set(re.findall(r"[a-z0-9][a-z0-9.]*", md))
        leaked = {t for t in tokens if hashlib.sha256(t.encode()).hexdigest() in banned_digests}
        assert not leaked, f"visual-output skill leaked internal noun(s): {leaked}"


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
        md = (_bundled_root() / "artifacts" / "SKILL.md").read_text(encoding="utf-8")
        assert "@personalclaw-core" in md
        # The skill must reference only the canonical core MCP namespace — no
        # pre-rename or vendor namespace may resurface. Assert every "@…-core"
        # token the skill names is exactly "@personalclaw-core".
        core_namespaces = set(re.findall(r"@[a-z][a-z0-9-]*-core\b", md))
        assert core_namespaces == {"@personalclaw-core"}, (
            f"skill references unexpected core namespace(s): "
            f"{core_namespaces - {'@personalclaw-core'}}"
        )
