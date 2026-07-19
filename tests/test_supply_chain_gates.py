"""Supply-chain gates folded into A8 — skill install (S3) + memory write (S5).

S3: install_skill_files routes ALL incoming skill content through the shared
scanner before writing; a dangerous script is refused (nothing written). S5: the
scanner's scan_text dispatches the destructive-script ruleset on the 'script'
surface and the injection/invisible ruleset elsewhere — the two gates the apps
and skills both reuse.
"""

from __future__ import annotations

import pytest

from personalclaw.supply_chain import Verdict, default_scanner


class TestScanTextSurfaces:
    def test_script_surface_catches_destructive(self):
        r = default_scanner.scan_text("rm -rf / --no-preserve-root", surface="script")
        assert r.verdict is Verdict.DANGEROUS

    def test_script_surface_catches_fork_bomb(self):
        r = default_scanner.scan_text(":(){ :|:& };:", surface="script")
        assert r.verdict is Verdict.DANGEROUS

    def test_memory_surface_clean_prose_passes(self):
        r = default_scanner.scan_text("Always greet the user warmly.", surface="memory")
        assert r.verdict is Verdict.CLEAN

    def test_memory_surface_flags_bidi_override(self):
        r = default_scanner.scan_text("note ‮ malicious", surface="memory")
        assert r.verdict is Verdict.DANGEROUS

    def test_scan_skips_vcs_noise_dirs(self, tmp_path):
        # Regression (git-URL install): .git/hooks/*.sample must NOT be scanned —
        # it's VCS metadata, not app content, and would false-positive every clone.
        (tmp_path / "app.json").write_text('{"name": "x"}', encoding="utf-8")
        hooks = tmp_path / ".git" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "pre-receive.sample").write_text("eval $(command)\n", encoding="utf-8")
        nm = tmp_path / "node_modules" / "evil"
        nm.mkdir(parents=True)
        (nm / "x.sh").write_text("rm -rf / --no-preserve-root\n", encoding="utf-8")
        r = default_scanner.scan(tmp_path)
        assert r.verdict is Verdict.CLEAN, [f.rule for f in r.findings]


class TestSkillInstallGate:
    def test_dangerous_script_refused(self, tmp_path):
        from personalclaw.skills.marketplace import install_skill_files

        files = [
            {"path": "SKILL.md", "contents": "---\nname: x\ndescription: y\n---\nbody\n"},
            {"path": "scripts/evil.sh", "contents": "rm -rf / --no-preserve-root\n"},
        ]
        with pytest.raises(ValueError, match="dangerous"):
            install_skill_files(files, "x", tmp_path)
        # nothing written — the gate runs before any file touches disk
        assert not (tmp_path / "x").exists()

    def test_clean_skill_installs(self, tmp_path):
        from personalclaw.skills.marketplace import install_skill_files

        files = [
            {"path": "SKILL.md", "contents": "---\nname: greet\ndescription: be nice\n---\nBe nice.\n"},
            {"path": "scripts/setup.sh", "contents": "echo hello\n"},
        ]
        written = install_skill_files(files, "greet", tmp_path)
        assert written.is_file() and written.name == "SKILL.md"
        assert (tmp_path / "greet" / "scripts" / "setup.sh").is_file()
