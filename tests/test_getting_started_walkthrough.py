"""Rails for the two commands the getting-started guide hands a newcomer first.

PUBL-7 walked the published guide on a clean machine — a fresh anonymous clone of
`github.com/PersonalClaw/PersonalClaw`, a brand-new venv, `personalclaw` 0.1.3 from PyPI —
and both of the guide's own commands crashed with raw Python tracebacks in the state a
newcomer is actually in:

  * `personalclaw setup` (guide § "1. Install") died with `EOFError: EOF when reading a line`
    the moment stdin was not a terminal — a pipe, a redirect, a Dockerfile `RUN`, CI.
  * `personalclaw chat -m "hello"` (guide § "4. First chat") dumped ~30 asyncio stack frames
    to bury an already well-composed `WHAT/WHY/FIX` message, because a fresh install has no
    chat model bound yet — which is precisely the state that step runs in.

These tests drive the REAL entry point in a subprocess with an isolated `PERSONALCLAW_HOME`,
so they reproduce the newcomer's state rather than a reimplementation of it, and can never
touch the developer's real home. They are fully offline: provider resolution fails before
any network call, and the URL rail below asserts *shape*, never reachability.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `cli.py` has no ``__main__`` guard (it is reached through the ``personalclaw`` console
#: script), so call `main` directly to exercise the real parser.
_RUN_CLI = "from personalclaw.cli import main; main()"


def _run_cli(
    args: list[str], home: Path, stdin_devnull: bool = True
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERSONALCLAW_HOME": str(home)}
    # A stray real-home token/browser-open would make these tests environment-dependent.
    env.pop("PERSONALCLAW_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, "-c", _RUN_CLI, *args],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL if stdin_devnull else None,
        timeout=180,
    )


# ── Guide step 1: `personalclaw setup` ────────────────────────────────────────


def test_setup_survives_a_non_interactive_stdin(tmp_path) -> None:
    """`setup` takes its printed defaults instead of dying on EOFError.

    Seeds `<home>/workspace_dir` so the wizard's workspace default is a tmp path: the
    platform default is `~/workplace/personalclaw-workspace`, and a test must never create
    a directory in the real home.
    """
    ws = tmp_path / "ws"
    (tmp_path / "workspace_dir").write_text(str(ws) + "\n", encoding="utf-8")

    proc = _run_cli(["setup"], tmp_path)

    assert "Traceback" not in proc.stderr, proc.stderr
    assert "EOFError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr}"
    # It reached the end of the wizard, not just the banner.
    assert "Agent installed" in proc.stdout, proc.stdout
    # And it said out loud that it was defaulting rather than prompting.
    assert "non-interactive stdin" in proc.stdout, proc.stdout


def test_ask_returns_the_typed_answer_on_a_terminal(monkeypatch, capsys) -> None:
    """The interactive path is unchanged — the guard must not swallow a real answer."""
    from personalclaw import cli_setup

    class _Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(cli_setup.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda _prompt: "  America/Denver  ")

    assert cli_setup._ask("  Timezone: ") == "America/Denver"
    assert "non-interactive" not in capsys.readouterr().out


def test_ask_survives_eof_on_a_terminal_that_closes(monkeypatch, capsys) -> None:
    """A terminal that reports `isatty` and then hits EOF still must not raise.

    The `isatty` guard alone does not cover this: with the guard in place a pipe never
    reaches `input()`, so the `except EOFError` branch is unreachable from the
    non-interactive test above and was passing under mutation. This pins the other
    door — a real terminal whose stdin closes mid-wizard (ssh drop, closed pty) —
    which is the only way that branch is entered.
    """
    from personalclaw import cli_setup

    class _Tty:
        def isatty(self) -> bool:
            return True

    def _eof(_prompt: str) -> str:
        raise EOFError("stdin closed")

    monkeypatch.setattr(cli_setup.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", _eof)

    assert cli_setup._ask("  Timezone: ") == ""
    # It took the interactive door (no non-interactive notice), then absorbed the EOF.
    assert "non-interactive" not in capsys.readouterr().out


def test_ask_defaults_and_says_so_without_a_terminal(monkeypatch, capsys) -> None:
    from personalclaw import cli_setup

    class _NotATty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(cli_setup.sys, "stdin", _NotATty())

    def _boom(_prompt: str) -> str:  # pragma: no cover - must never be reached
        raise AssertionError("_ask consulted stdin despite it not being a terminal")

    monkeypatch.setattr("builtins.input", _boom)

    assert cli_setup._ask("  Workspace path [/tmp/x]: ") == ""
    assert "non-interactive stdin" in capsys.readouterr().out


def test_no_wizard_prompt_bypasses_the_guard() -> None:
    """Every `setup` prompt goes through `_ask`; only `_ask` itself may call `input`.

    A new bare `input()` would reintroduce the EOFError crash at a new step.
    """
    src = (REPO_ROOT / "src" / "personalclaw" / "cli_setup.py").read_text(encoding="utf-8")
    calls = re.findall(r"\binput\(", src)
    assert len(calls) == 1, f"expected only _ask's own input() call, found {len(calls)}"
    assert "return input(prompt).strip()" in src


# ── Guide step 4: `personalclaw chat -m "hello"` ──────────────────────────────


def test_chat_with_no_provider_prints_the_fix_not_a_traceback(tmp_path) -> None:
    """The guide's first-chat command in the state the guide leaves you in."""
    proc = _run_cli(["chat", "-m", "hello"], tmp_path)

    assert proc.returncode == 1, f"rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "asyncio" not in proc.stderr, proc.stderr
    # The resolver's composed guidance survives intact.
    assert "no model provider resolves for use case 'chat'" in proc.stderr, proc.stderr
    assert "FIX:" in proc.stderr, proc.stderr


# ── Remote friction: GitHub owner casing ─────────────────────────────────────

#: The canonical owner, measured: `https://github.com/PersonalClaw/PersonalClaw` answers 200
#: with no redirect, and an anonymous `git clone` of it succeeds with no credentials.
_CANONICAL_OWNER = "PersonalClaw"

#: Literal `github.com/<owner>/<repo>` URLs only. `api.github.com/repos/...` has `repos` in the
#: owner slot and is deliberately out of scope, as are f-string URLs built from a constant.
_GH_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

#: Shipped code and the docs a newcomer reads. Roadmap plans are excluded on purpose: they
#: quote the *old* `keyurgolani/*` repos as historical record, and rewriting a record to
#: satisfy a lint rule is a false positive.
_SCAN_ROOTS = (
    "src",
    "deploy",
    "docs/guides",
    "docs/reference",
    "README.md",
    "CONTRIBUTING.md",
)

_TEXT_SUFFIXES = {".py", ".md", ".sh", ".yaml", ".yml", ".toml", ".ts", ".tsx", ".json"}


def _scan_github_urls() -> list[tuple[Path, str, str]]:
    hits: list[tuple[Path, str, str]] = []
    for root in _SCAN_ROOTS:
        target = REPO_ROOT / root
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for path in files:
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for owner, repo in _GH_URL.findall(text):
                hits.append((path, owner, repo))
    return hits


def test_project_github_urls_use_the_canonical_owner_casing() -> None:
    """`github.com/personalclaw/personalclaw` in shipped code is a consistency defect.

    Two of them shipped: the knowledge crawler's outbound `User-Agent` (the project's public
    identity to every site it fetches) and the systemd unit's `Documentation=` field, which
    `personalclaw service install` writes onto the user's machine — a command the guide's
    "Where to go next" recommends. GitHub resolves the lowercase form (200, no redirect), so
    this is legibility, not a broken link; it is still the project's own name spelled wrong
    in state we hand to third parties.
    """
    hits = _scan_github_urls()

    # Vacuity floor: a rail that matched nothing looks clean forever.
    assert len(hits) >= 10, f"URL scan found only {len(hits)} github.com URLs — rail is inert"
    assert len({p for p, _, _ in hits}) >= 5, "URL scan reached fewer than 5 files"

    ours = [(p, owner, repo) for p, owner, repo in hits if owner.lower() == "personalclaw"]
    assert len(ours) >= 8, f"only {len(ours)} own-org URLs matched — the owner filter is inert"

    wrong = [
        f"{p.relative_to(REPO_ROOT)}: github.com/{owner}/{repo}"
        for p, owner, repo in ours
        if owner != _CANONICAL_OWNER
    ]
    assert not wrong, "non-canonical GitHub owner casing:\n  " + "\n  ".join(wrong)


def test_the_guide_does_not_promise_setup_collects_a_provider_credential() -> None:
    """Guide § 1 claimed `setup` gathers "name + first provider credential". It gathers
    neither: a fresh install has no provider app to hold a credential, which § 3 then
    correctly explains. The walkthrough followed § 1, expected a configured provider, and
    had none — so the two sections contradicted each other at the newcomer's expense.
    """
    guide = (REPO_ROOT / "docs" / "guides" / "getting-started.md").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "deploy" / "website" / "install.sh").read_text(encoding="utf-8")

    assert "name + first provider credential" not in guide
    assert "name + first model provider" not in installer
    # And the correction is present, not merely the claim removed.
    assert "does **not** ask for a model provider credential" in guide
