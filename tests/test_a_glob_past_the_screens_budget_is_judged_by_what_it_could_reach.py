"""A glob the shell's screen did not expand is judged by every protected place it could reach.

The credential screen (``security.is_sensitive_bash_command``) expands each glob a command names
against the filesystem, so it judges what the shell will read. Expanding costs folder listings, so
one command gets at most ``command_paths._MAX_LISTINGS`` of them, and a glob that matches more than
``_MAX_MATCHES`` paths is not expanded either. A glob left unexpanded past either bound used to be
checked as written, and as written a glob names no protected file. Now it is refused when it could
name one: a protected place it could reach (a credential folder, PersonalClaw's own secrets,
another tool's sign-in), or a protected name its last part could match. A glob that could reach
none of them still passes, and so does one that matched nothing within the bounds.

Driven through the screen itself, over a real home, a real PersonalClaw home and a workspace on
disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw import command_paths
from personalclaw.security import is_sensitive_bash_command

pytestmark = pytest.mark.security


@pytest.fixture
def places(tmp_path, monkeypatch) -> dict[str, Path]:
    root = Path(os.path.realpath(tmp_path))
    home = root / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("an ssh key\n")
    monkeypatch.setenv("HOME", str(home))
    pchome = root / "pchome"
    pchome.mkdir()
    (pchome / ".env").write_text("A_KEY=a stored secret\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pchome))
    ws = root / "ws"
    (ws / "many").mkdir(parents=True)
    (ws / "many" / "a.txt").write_text("ordinary\n")
    unrelated = root / "unrelated"
    unrelated.mkdir()
    (unrelated / "report-1.txt").write_text("ordinary\n")
    return {"home": home, "pchome": pchome, "ws": ws, "unrelated": unrelated}


def _spent() -> str:
    """Harmless globs enough to spend the screen's whole listing budget, a listing or more each
    (a relative word is read against every folder the command can be in). Each could name only
    an ordinary text file, so one left unexpanded reaches nothing protected."""
    return " ".join(f"many/x{n}-*.txt" for n in range(4 * command_paths._MAX_LISTINGS))


def _reaching(places: dict[str, Path]) -> dict[str, str]:
    """Globs that reach a protected place only once expanded."""
    return {
        "a key in the SSH folder": f"{places['home']}/.ss*/id_ed25519",
        "the home's own secret file": f"{places['pchome']}/.en*",
    }


@pytest.mark.parametrize("which", ["a key in the SSH folder", "the home's own secret file"])
def test_past_the_listing_budget_a_glob_that_reaches_a_secret_is_refused(places, which) -> None:
    glob = _reaching(places)[which]
    alone = is_sensitive_bash_command(f"cat {glob}", cwd=places["ws"])
    assert alone, "within the budget the glob is expanded, and refused"
    after = is_sensitive_bash_command(f"ls {_spent()}; cat {glob}", cwd=places["ws"])
    assert after, f"past the budget, {glob} was let through"


def test_past_the_listing_budget_a_glob_that_reaches_nothing_protected_passes(places) -> None:
    glob = f"{places['unrelated']}/report-*.txt"
    assert is_sensitive_bash_command(f"cat {glob}", cwd=places["ws"]) is None
    assert is_sensitive_bash_command(f"ls {_spent()}; cat {glob}", cwd=places["ws"]) is None


def test_a_glob_with_more_matches_than_the_screen_expands_is_judged_the_same(places) -> None:
    """Past ``_MAX_MATCHES`` a glob is not expanded either: one that reaches the SSH folder through
    that many neighbours is refused."""
    home, unrelated = places["home"], places["unrelated"]
    for n in range(command_paths._MAX_MATCHES + 1):
        (home / f".s{n:05d}").mkdir()
        (unrelated / f"report-{n:05d}.txt").write_text("ordinary\n")
    assert is_sensitive_bash_command(f"cat {home}/.s*/id_ed25519", cwd=places["ws"])
    assert is_sensitive_bash_command(f"cat {unrelated}/report-*.txt", cwd=places["ws"]) is None


def test_a_glob_that_matched_nothing_within_the_budget_still_passes(places) -> None:
    """A glob the screen could expand and that matched nothing names nothing, and runs as before."""
    assert is_sensitive_bash_command("ls many/*.nothing-here", cwd=places["ws"]) is None
