"""Tests for the harness static boundary scanner + diff-aware selection.

The load-bearing test is `test_scanner_clean_on_current_tree`: the scanner must produce
ZERO error-level findings on the real repo, or it's noise. The rest prove each check
actually FIRES on a synthetic violation (a check that never fires is worthless) and that
touched-area → profile forcing works.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from harness import cli, scanner
from harness.diff import (
    BaseNotFound,
    _parse_added_lines,
    compute_diff,
    has_fix_shaped_commit,
    touches_specs,
)
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


# ── Undecodable git output (#2960) ───────────────────────────────────────────────


def _git_init(root: Path) -> None:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_compute_diff_survives_a_text_diffed_binary_file(tmp_path: Path) -> None:
    """A mostly-ASCII binary fixture must not take the harness down.

    git only calls a file binary if it finds a NUL in the first 8000 bytes, so a PDF whose
    header line is followed by high non-UTF-8 bytes is diffed as TEXT. Decoding that
    strictly raised UnicodeDecodeError out of `subprocess.run`, so the whole harness run
    died with a nameless exit 1 (#2960) instead of scanning the change.
    """
    _git_init(tmp_path)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True, capture_output=True)

    # No NUL anywhere, so git text-diffs it; \x93\x8c\x8b\x9e is not valid UTF-8.
    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.3\n%\x93\x8c\x8b\x9e\n" + b"A" * 64 + b"\n")
    (tmp_path / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "fix: add"], cwd=tmp_path, check=True, capture_output=True
    )

    probe = subprocess.run(
        ["git", "diff", "--unified=0", "HEAD~1"], cwd=tmp_path, capture_output=True, check=False
    )
    assert b"%PDF-1.3" in probe.stdout, "git treated the fixture as binary — probe no longer bites"

    diff = compute_diff(tmp_path, base_ref="HEAD~1")

    assert "scan.pdf" in diff.files
    assert "code.py" in diff.files
    # The point of surviving: the sibling text file's line data is still there to scan.
    assert diff.changed_lines.get("code.py") == {1}


def _run_call_args(body: str) -> list[tuple[int, str]]:
    """Every ``subprocess.run(...)`` argument list in ``body`` as (1-based line, args).

    Paren-balanced rather than a regex: a single-line call and a call whose args contain
    ``)`` are both real shapes here, and a regex that missed either would let the rail below
    pass while the defect it guards sat in the file.
    """
    out: list[tuple[int, str]] = []
    for match in re.finditer(r"subprocess\.run\(", body):
        i = match.end()
        depth = 1
        while i < len(body) and depth:
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        out.append((body[: match.start()].count("\n") + 1, body[match.end() : i - 1]))
    return out


def test_every_harness_subprocess_declares_a_decode_error_policy() -> None:
    """`text=True` without `errors=` is the #2960 defect — one rail, so a new call site
    can't reintroduce it silently."""
    root = _repo_root()
    calls = 0
    offenders: list[str] = []
    for path in sorted((root / "harness").rglob("*.py")):
        body = path.read_text(encoding="utf-8", errors="replace")
        for line, args in _run_call_args(body):
            if "text=True" not in args:
                continue
            calls += 1
            if "errors=" not in args:
                offenders.append(f"{path.relative_to(root)}:{line}")
    assert calls >= 3, f"rail found only {calls} text=True subprocess calls — it stopped matching"
    assert not offenders, "text=True with no errors= policy (see #2960):\n" + "\n".join(offenders)


# ── A named base (`scan --diff --base <ref>`) ────────────────────────────────────


def _commit(root: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout.strip()


def _three_commits(root: Path) -> tuple[str, str]:
    """``a.txt``, then ``b.txt``, then ``c.txt`` and an edit of ``a.txt``: (first, second)."""
    _git_init(root)
    first = _commit(root, {"a.txt": "a\n"}, "one")
    second = _commit(root, {"b.txt": "b\n"}, "two")
    _commit(root, {"c.txt": "c\n", "a.txt": "a\nmore\n"}, "three")
    return first, second


def test_a_named_base_diffs_exactly_what_changed_since_it(tmp_path: Path) -> None:
    """On a push to ``main`` the base is the commit the push replaced, not the merge-base with
    ``origin/main``, which there is the pushed commit itself."""
    first, second = _three_commits(tmp_path)

    since_second = compute_diff(tmp_path, base_ref=second, fallback=False)
    since_first = compute_diff(tmp_path, base_ref=first, fallback=False)

    assert since_second.base == second
    assert sorted(since_second.files) == ["a.txt", "c.txt"]
    assert sorted(since_first.files) == ["a.txt", "b.txt", "c.txt"]
    assert since_second.changed_lines == {"a.txt": {2}, "c.txt": {1}}


def test_a_named_base_that_does_not_resolve_is_refused_not_replaced(tmp_path: Path) -> None:
    """The default falls back to ``main``, which on ``main`` is HEAD: an empty diff that passes.
    A named base is never answered by another one."""
    _three_commits(tmp_path)
    missing = "0" * 40

    with pytest.raises(BaseNotFound):
        compute_diff(tmp_path, base_ref=missing, fallback=False)
    # The local default keeps its fallback, and on `main` that is HEAD: nothing to scan.
    assert compute_diff(tmp_path, base_ref=missing).files == []


def test_scan_takes_its_base_and_fails_on_one_it_cannot_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first, _second = _three_commits(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    scanned: list[list[str]] = []

    def fake_scan(files, root, changed_lines=None):
        scanned.append(sorted(str(f.relative_to(root)) for f in files))
        return []

    monkeypatch.setattr(cli.scanner, "scan", fake_scan)

    assert cli.main(["scan", "--diff", "--base", first]) == 0
    assert scanned == [["a.txt", "b.txt", "c.txt"]]

    assert cli.main(["scan", "--diff", "--base", "0" * 40]) == 2
    assert "does not name a commit" in capsys.readouterr().err
    assert cli.main(["scan", "--base", first]) == 2, "--base without --diff names nothing"
    assert len(scanned) == 1, "a refused base must not fall through to a scan"
