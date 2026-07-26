"""Tests for the harness static boundary scanner + diff-aware selection (Session 2).

The load-bearing test is `test_scanner_clean_on_current_tree`: the scanner must produce
ZERO error-level findings on the real repo, or it's noise. The rest prove each check
actually FIRES on a synthetic violation (a check that never fires is worthless) and that
touched-area → profile forcing works.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from harness import scanner
from harness.diff import _parse_added_lines, has_fix_shaped_commit, touches_specs
from harness.selection import forced_profiles


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
    ).stdout
    return [root / ln.strip() for ln in out.splitlines() if ln.strip()]


# ── The calibration guard ───────────────────────────────────────────────────────


def test_scanner_clean_on_current_tree() -> None:
    """No ERROR-level scanner finding on the real repo. WARNINGs are allowed (advisory)."""
    root = _repo_root()
    findings = scanner.scan(_tracked_files(root), root)
    errors = [f for f in findings if f.level == scanner.ERROR]
    assert not errors, "scanner has false-positive ERRORs on a clean tree:\n" + "\n".join(
        f.format(root) for f in errors
    )


def test_known_checks_matches_seed_rule_scanner_refs() -> None:
    """Every scanner check-id a shipped rule spec references actually exists."""
    from harness.specs import load_specs

    referenced = {str(s.meta["scanner"]) for s in load_specs() if s.meta.get("scanner")}
    assert referenced, "seed rules should reference scanner checks"
    assert (
        referenced <= scanner.known_checks()
    ), f"rule specs reference unknown scanner checks: {referenced - scanner.known_checks()}"


# ── Each check FIRES on a synthetic violation ────────────────────────────────────


def test_config_four_points_fires_on_missing_load_mapping(tmp_path: Path) -> None:
    root = tmp_path
    loader = root / "src" / "personalclaw" / "config" / "loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        textwrap.dedent("""
            from dataclasses import dataclass, field
            def _meta(label, help, **k): return {"label": label}
            @dataclass
            class WidgetConfig:
                mapped_field: bool = field(default=True, metadata=_meta("A", "a"))
                forgotten_field: bool = field(default=False, metadata=_meta("B", "b"))
            @dataclass
            class AppConfig:
                @classmethod
                def load(cls, data):
                    w = data.get("widget", {})
                    return cls(widget=WidgetConfig(mapped_field=bool(w.get("mapped_field", True))))
            """),
        encoding="utf-8",
    )
    findings = [f for f in scanner.scan([loader], root) if f.check == "config-four-points"]
    names = {f.what for f in findings}
    assert any("forgotten_field" in n for n in names)
    assert not any("mapped_field" in n for n in names)  # properly wired → no finding


def test_hook_provider_parity_fires_on_unlisted_provider(tmp_path: Path) -> None:
    root = tmp_path
    val = root / "src" / "personalclaw" / "validation.py"
    val.parent.mkdir(parents=True)
    val.write_text('ALLOWED_HOOK_PROVIDERS = frozenset({"bash", "webhook"})\n', encoding="utf-8")
    ap = root / "src" / "personalclaw" / "action_providers"
    ap.mkdir(parents=True)
    (ap / "ghost_provider.py").write_text(
        textwrap.dedent("""
            class GhostActionProvider:
                @property
                def name(self) -> str:
                    return "ghost"
            """),
        encoding="utf-8",
    )
    findings = [
        f
        for f in scanner.scan([ap / "ghost_provider.py"], root)
        if f.check == "hook-provider-parity"
    ]
    assert any("ghost" in f.what for f in findings)


def test_sse_event_registered_fires_on_unregistered_event(tmp_path: Path) -> None:
    root = tmp_path
    fe = root / "web" / "src" / "pages" / "loops" / "useRunStream.ts"
    fe.parent.mkdir(parents=True)
    fe.write_text("export const RUN_LIFECYCLE = ['known_event'] as const\n", encoding="utf-8")
    py = root / "src" / "personalclaw" / "loop" / "kinds" / "x.py"
    py.parent.mkdir(parents=True)
    py.write_text(
        'def go(ctx, cid):\n    ctx.publish(cid, "unregistered_event", {})\n', encoding="utf-8"
    )
    findings = [f for f in scanner.scan([py], root) if f.check == "sse-event-registered"]
    assert any("unregistered_event" in f.what for f in findings)


def test_sse_event_registered_ignores_registered_and_nonloop(tmp_path: Path) -> None:
    root = tmp_path
    fe = root / "web" / "src" / "pages" / "loops" / "useRunStream.ts"
    fe.parent.mkdir(parents=True)
    fe.write_text("export const RUN_LIFECYCLE = ['known'] as const\n", encoding="utf-8")
    py = root / "src" / "personalclaw" / "loop" / "kinds" / "x.py"
    py.parent.mkdir(parents=True)
    # registered loop event → ok; a non-loop registry publish → ignored entirely.
    py.write_text(
        "def go(ctx, other):\n"
        '    ctx.publish(1, "known", {})\n'
        '    other.publish(1, "some_other_registry_event", {})\n',
        encoding="utf-8",
    )
    findings = [f for f in scanner.scan([py], root) if f.check == "sse-event-registered"]
    assert findings == []


def test_app_sdk_boundary_fires_on_deep_import(tmp_path: Path) -> None:
    root = tmp_path
    appf = root / "apps" / "demo" / "provider.py"
    appf.parent.mkdir(parents=True)
    appf.write_text("from personalclaw.loop.worktree import thing\n", encoding="utf-8")
    findings = [f for f in scanner.scan([appf], root) if f.check == "app-sdk-boundary"]
    assert any("personalclaw.loop.worktree" in f.what for f in findings)


def test_app_sdk_boundary_allows_sdk_import(tmp_path: Path) -> None:
    root = tmp_path
    appf = root / "apps" / "demo" / "provider.py"
    appf.parent.mkdir(parents=True)
    appf.write_text("from personalclaw.sdk import net\n", encoding="utf-8")
    findings = [f for f in scanner.scan([appf], root) if f.check == "app-sdk-boundary"]
    assert findings == []


# ── Diff-aware selection ─────────────────────────────────────────────────────────


def test_chat_touch_forces_replay_and_web() -> None:
    forced = {f.profile for f in forced_profiles(["web/src/pages/chat/coalesceReducers.ts"])}
    assert "replay" in forced
    assert "web" in forced


def test_config_loader_touch_forces_scan() -> None:
    forced = {f.profile for f in forced_profiles(["src/personalclaw/config/loader.py"])}
    assert "scan" in forced


def test_unrelated_touch_forces_nothing_sensitive() -> None:
    forced = {f.profile for f in forced_profiles(["README.md"])}
    assert "replay" not in forced and "scan" not in forced and "web" not in forced


# ── Same-PR rule helpers ─────────────────────────────────────────────────────────


def test_fix_shaped_detection() -> None:
    assert has_fix_shaped_commit(["fix(loop): stop double-count"])
    assert has_fix_shaped_commit(["bugfix: nasty regression"])
    assert not has_fix_shaped_commit(["feat(x): add thing", "docs: tidy"])


def test_touches_specs_detection() -> None:
    assert touches_specs(["harness/specs/rules/new.md", "src/x.py"])
    assert not touches_specs(["src/x.py", "web/y.ts"])


# ── Diff line parsing ────────────────────────────────────────────────────────────


def test_parse_added_lines_reads_hunks() -> None:
    patch = textwrap.dedent("""\
        diff --git a/x.py b/x.py
        --- a/x.py
        +++ b/x.py
        @@ -1,0 +2,2 @@
        +added line one
        +added line two
        """)
    parsed = _parse_added_lines(patch)
    assert parsed == {"x.py": {2, 3}}
