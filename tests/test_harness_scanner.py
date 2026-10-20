"""Tests for the harness static boundary scanner + diff-aware selection.

These prove each check actually FIRES on a synthetic violation (a check that never fires is
worthless) and that touched-area → profile forcing works. That the scanner produces ZERO
error-level findings on the real repo, or it's noise, is CI's: ``python -m harness scan`` over
the whole tree, in the static job's harness step (and in breadth's rails-macos leg).
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


# ── Every scanner check a rule names exists ─────────────────────────────────────


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


# ── The whole-tree scan (`scan`, no `--diff`): what CI's static job runs ───────────

#: The scanner's own module: a whole-tree listing that does not hold it in the checkout is not
#: this tree's.
_OWN = "harness/scanner.py"


def _tree_with_the_scanner(root: Path) -> None:
    """``a.txt``, ``b.txt`` and ``c.txt`` over three commits, then the scanner's own module, as
    this repository tracks it."""
    _three_commits(root)
    (root / "harness").mkdir()
    _commit(root, {_OWN: "x = 1\n"}, "four")


def test_a_whole_tree_scan_reads_every_tracked_file_and_no_changed_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every file git tracks, whatever the last commit touched, judged in full."""
    _tree_with_the_scanner(tmp_path)
    (tmp_path / "untracked.txt").write_text("u\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    scanned: list[tuple[list[str], object]] = []

    def fake_scan(files, root, changed_lines=None):
        scanned.append((sorted(str(f.relative_to(root)) for f in files), changed_lines))
        return []

    monkeypatch.setattr(cli.scanner, "scan", fake_scan)

    assert cli.main(["scan"]) == 0
    assert scanned == [(["a.txt", "b.txt", "c.txt", _OWN], None)]


def test_a_whole_tree_scan_fails_on_an_error_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree_with_the_scanner(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    finding = scanner.Finding("a-check", scanner.ERROR, tmp_path / "a.txt", 1, "what", "why", "fix")
    monkeypatch.setattr(cli.scanner, "scan", lambda files, root, changed_lines=None: [finding])

    assert cli.main(["scan"]) == 1


def _git_fails_partway(monkeypatch: pytest.MonkeyPatch) -> None:
    """``git ls-files`` lists some files, then exits 128, as on an index it cannot read."""
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if list(argv[:2]) == ["git", "ls-files"]:
            return subprocess.CompletedProcess(
                argv, 128, stdout=f"{_OWN}\na.txt\n", stderr="fatal: index file corrupt\n"
            )
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", run)


#: ``(what is planted, what the refusal says)``.
_NOT_THIS_TREE = (
    ("not a repository", "git ls-files exits 128"),
    ("a repository that tracks nothing", "git lists no tracked file"),
    ("a listing git fails partway through", "git ls-files exits 128"),
    ("a listing without the scanner's own module", f"git lists no {_OWN}"),
    ("the scanner's module listed but not in the checkout", "the checkout holds no such file"),
)


@pytest.mark.parametrize("entry", ["harness scan", "harness run's scan profile"])
@pytest.mark.parametrize("planted, says", _NOT_THIS_TREE, ids=[p for p, _ in _NOT_THIS_TREE])
def test_a_whole_tree_scan_of_a_listing_that_is_not_this_trees_fails(
    planted: str,
    says: str,
    entry: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scan of nothing is no scan, and a scan of part of the tree is not the tree's: where git
    fails or lists no file, or its listing does not hold the scanner's own module in the
    checkout, the scan fails rather than printing clean, from either entry that scans the
    whole tree."""
    if planted == "not a repository":
        (tmp_path / "harness").mkdir()
        (tmp_path / _OWN).write_text("x = 1\n", encoding="utf-8")
    elif planted == "a repository that tracks nothing":
        _git_init(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    elif planted == "a listing git fails partway through":
        _tree_with_the_scanner(tmp_path)
        _git_fails_partway(monkeypatch)
    elif planted == "a listing without the scanner's own module":
        _three_commits(tmp_path)
    else:
        _tree_with_the_scanner(tmp_path)
        (tmp_path / _OWN).unlink()
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    scanned: list[object] = []
    monkeypatch.setattr(
        cli.scanner, "scan", lambda files, root, changed_lines=None: scanned.append(files) or []
    )

    if entry == "harness scan":
        assert cli.main(["scan"]) == 2
    else:
        assert cli._run_scan_if_selected(["scan"]) == 2
    assert says in capsys.readouterr().err
    assert scanned == [], "a scan of a listing that is not the tree's must not run, let alone pass"


@pytest.mark.parametrize("flag", ["--d", "--di", "--dif", "--bas=main"])
def test_the_scan_takes_its_flags_spelled_whole(flag: str) -> None:
    """``--dif`` is no ``--diff``: read as a prefix, a step spelled short would scan a diff
    while it reads as the whole tree's scan."""
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["scan", flag])
    assert exc.value.code == 2
