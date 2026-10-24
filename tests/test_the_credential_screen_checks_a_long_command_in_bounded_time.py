"""The shell's credential screen checks a long command in time that grows with its length.

`security.is_sensitive_bash_command` runs one regex over the command, then checks each path the
command names. The regex's redirect alternative began with `.*`. The screen only asks whether a
search finds a match, and a search already tries every position, so that `.*` changed no verdict;
tried from every position it ran to the end of the line each time, so a line with no match took
time in the square of its length: about 40 s at 100 KB. It is now `[<>|]\\s*`. The screen also
checks each distinct word and path a command names once per call, where it checked every mention.

The first half of this file proves both are the same check. The regex's redirect alternative,
read by Python's own parser, is that `.*` in front of exactly the new one. Today's screen is kept
here as a reference (`_reference_screen`: the old regex, every mention checked), and it gives the
same answer as the screen, refusal string included, over the screen's real inputs: every command
the credential-screen tests pass in, a grid of every read command, interpreter and redirect with
every home spelling and credential folder, and a seeded mix of their pieces. The second half holds
the time.
"""

from __future__ import annotations

import ast
import random
import re
from pathlib import Path

import cpu_budget
import pytest

from personalclaw import security
from personalclaw.command_paths import named_paths, strip_shell_quotes

pytestmark = pytest.mark.security

TESTS = Path(__file__).parent
NEW_REDIRECT = r"|[<>|]\s*)"
OLD_REDIRECT = r"|.*[<>|]\s*)"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A scratch user home with credential folders, a PersonalClaw home with its store, and the
    screen's regex built for them."""
    user = tmp_path / "user"
    for folder in (".ssh", ".aws", ".gnupg", ".kube", ".docker"):
        (user / folder).mkdir(parents=True)
    (user / ".ssh" / "id_ed25519").write_text("not a key\n", encoding="utf-8")
    (user / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    (user / ".kube" / "config").write_text("kind: Config\n", encoding="utf-8")
    (user / "src" / "app").mkdir(parents=True)
    (user / "notes.txt").write_text("notes\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / ".env").write_text("X=1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(data))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    return user


def _old_regex() -> re.Pattern[str]:
    """Today's regex before the change, from the one the screen builds for this home."""
    new = security._get_sensitive_re().pattern
    assert new.count(NEW_REDIRECT) == 1, new
    return re.compile(new.replace(NEW_REDIRECT, OLD_REDIRECT), re.IGNORECASE)


def _reference_screen(command: str, old: re.Pattern[str], *, cwd=None) -> str | None:
    """`is_sensitive_bash_command` as it was: the old regex, and every mention of a path checked."""
    normalised = strip_shell_quotes(security._normalise_for_matching(command))
    if old.search(normalised):
        return security._SHELL_REFUSAL[security.CREDENTIAL]
    owned = security.SensitivePaths(home_credential_dirs=False)
    folders = security._credential_folders()
    for word, path in named_paths(command, cwd=cwd, names=owned.names):
        kind = owned.kind(str(path))
        if kind:
            return security._SHELL_REFUSAL[kind]
        if security._shows_credential_folder(word, path, folders):
            return security._SHELL_REFUSAL[security.CREDENTIAL]
    return None


# ── the regex: the same alternative behind one `.*` ─────────────────────────────────────────────


def _shape(node: object) -> object:
    data = getattr(node, "data", None)
    if data is not None:
        return [_shape(item) for item in data]
    if isinstance(node, (list, tuple)):
        return [_shape(item) for item in node]
    return node


def test_the_old_redirect_alternative_is_one_dot_star_in_front_of_the_new_one():
    """Read by the parser `re.compile` runs: `.*` (any character, zero or more, greedy, no group)
    followed by exactly the new alternative. In a search, `.*[<>|]` matches from some position
    exactly when `[<>|]` does: where `[<>|]` matches, `.*` matches empty in front of it. `.*` opens
    no group, so the groups of the whole pattern are the same."""
    from re import _constants as c
    from re import _parser

    old = _shape(_parser.parse(OLD_REDIRECT[1:-1], re.IGNORECASE))
    new = _shape(_parser.parse(NEW_REDIRECT[1:-1], re.IGNORECASE))
    assert old[0] == _shape((c.MAX_REPEAT, (0, c.MAXREPEAT, [(c.ANY, None)])))
    assert old[1:] == new


def test_the_screen_regex_differs_from_the_old_one_only_there(home):
    new = security._get_sensitive_re()
    old = _old_regex()
    assert new.pattern.replace(NEW_REDIRECT, OLD_REDIRECT) == old.pattern
    assert new.groups == old.groups and new.flags == old.flags


# ── the corpus: the screen's real inputs ────────────────────────────────────────────────────────

SCREEN_TESTS = [
    "test_security.py",
    "test_sensitive_path_guard_evasion.py",
    "test_the_shell_refuses_listing_a_credential_folder.py",
    "test_the_shell_refuses_another_tools_sign_in.py",
    "test_the_shell_refuses_the_credential_store_wherever_the_home_is.py",
    "test_bash_action_is_screened.py",
]


def _test_commands(user: Path, data: Path) -> list[str]:
    """Every string literal the credential-screen tests hold that could be a command, with the
    homes they format in written out."""
    out: list[str] = []
    for name in SCREEN_TESTS:
        tree = ast.parse((TESTS / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if 2 < len(text) < 400 and " " in text and "\n\n" not in text:
                    out.append(
                        text.replace("{home}", str(user))
                        .replace("{user}", str(user))
                        .replace("{pclaw_home}", str(data))
                        .replace("{container}", str(data))
                    )
    return out


def _verbs() -> list[str]:
    reads = re.findall(r"[a-z]+", security._READ_CMDS.split(")")[0])
    scripts = re.findall(r"[a-z]+", security._SCRIPT_OPEN.split(")")[0])
    return reads + scripts + ["python3", "nodejs", "ls", "echo", "ssh -i", "tee", "x"]


def _home_spellings(user: Path) -> list[str]:
    return [
        "~",
        str(user),
        "$HOME",
        "${HOME}",
        "$USERPROFILE",
        "os.environ['HOME']",
        "os.getenv('HOME')",
        "process.env.HOME",
        "ENV['HOME']",
        "Path.home()",
        "os.homedir()",
        "os.path.expanduser('~')",
        "/nothome",
    ]


def _grid(user: Path) -> list[str]:
    """Every verb or redirect, home spelling, joiner, credential folder and ending."""
    leads = [f"{v} " for v in _verbs()] + ["cat x > ", "cat x >", "cat < ", "echo | ", "2>"]
    folders = list(security._SENSITIVE_HOME_DIRS) + ["notes.txt", ".sshx", "src"]
    joins = ["", "+", ",", " ", "+ ", "'+'"]
    ends = ["", "/", "/x", " ", "'", '"', ")", ",", "x", "\nls"]
    out = []
    for lead in leads:
        for spelling in _home_spellings(user):
            for folder in folders:
                for join in joins:
                    for end in ends:
                        out.append(f"{lead}{spelling}{join}/{folder}{end}")
    # PersonalClaw's own store and keys, by path and by name, so that answer is well represented.
    data = user.parent / "data"
    own = [".env", "sel_hmac.key", ".local_secret", "telemetry_salt", "notes.md"]
    for lead in leads:
        for entry in own:
            for end in ends[:4]:
                out += [f"{lead}{data}/{entry}{end}", f"cd {data} && {lead}{entry}{end}"]
    return out


def _mixed(pieces: list[str], n: int, seed: int) -> list[str]:
    """Seeded joins of corpus pieces with the separators and quotes a command uses, each piece
    sometimes repeated, so a path is named more than once."""
    rng = random.Random(seed)
    seps = ["; ", " && ", " | ", "\n", " ", " || ", "'", '"']
    out = []
    for _ in range(n):
        chosen = rng.sample(pieces, rng.randint(1, 4))
        if rng.random() < 0.3:
            chosen = chosen + [chosen[0]] * rng.randint(1, 3)
        rng.shuffle(chosen)
        out.append("".join(rng.choice(seps) + c for c in chosen).strip())
    return out


@pytest.mark.timeout(80)
def test_the_regex_gives_the_old_verdict_on_every_line(home):
    """The cheap half, over the whole grid and the test commands: the same yes or no."""
    new, old = security._get_sensitive_re(), _old_regex()
    lines = _grid(home) + _test_commands(home, home.parent / "data")
    lines += _mixed(lines, 20_000, seed=20261018)
    hits = 0
    wrong = []
    for line in lines:
        text = strip_shell_quotes(security._normalise_for_matching(line))
        before = old.search(text) is not None
        if before != (new.search(text) is not None):
            wrong.append(line)
        hits += before
    assert not wrong, wrong[:10]
    assert len(lines) > 50_000 and 1_000 < hits < len(lines) - 1_000, (len(lines), hits)


@pytest.mark.timeout(60)
def test_the_screen_gives_the_old_answer_on_the_screens_real_inputs(home):
    """The whole screen, refusal string and all: every test command, a seeded sample of the grid,
    and a seeded mix that names paths more than once. Every answer the screen gives occurs."""
    old = _old_regex()
    rng = random.Random(20261019)
    tests = _test_commands(home, home.parent / "data")
    grid = _grid(home)
    lines = tests + rng.sample(grid, 3_000) + _mixed(tests + grid, 2_000, seed=20261020)
    seen: dict[str | None, int] = {}
    wrong = []
    for line in lines:
        for cwd in (None, str(home)):
            expected = _reference_screen(line, old, cwd=cwd)
            got = security.is_sensitive_bash_command(line, cwd=cwd)
            if expected != got:
                wrong.append((line, cwd, expected, got))
            seen[expected] = seen.get(expected, 0) + 1
    assert not wrong, wrong[:10]
    assert set(seen) == {
        None,
        security._SHELL_REFUSAL[security.CREDENTIAL],
        security._SHELL_REFUSAL[security.OWN_SECRET],
    }, seen
    assert min(seen.values()) > 50, seen


@pytest.mark.parametrize(
    "command",
    [
        "ls notes.txt notes.txt notes.txt ~/.ssh",
        "cat notes.txt notes.txt; ls ~/.aws/*",
        "cat ./notes.txt notes.txt ../data/.env",
        "ls ~/src ~/src ~/src ~/.s*",
    ],
)
def test_a_path_named_again_before_a_refused_one_is_still_refused(home, command):
    """Checking a word and path once per call skips only a repeat of one that was allowed: a
    refused one later in the same command is still refused, with the same sentence."""
    assert security.is_sensitive_bash_command(command, cwd=str(home)) is not None
    assert security.is_sensitive_bash_command(command, cwd=str(home)) == _reference_screen(
        command, _old_regex(), cwd=str(home)
    )


def test_what_one_call_checked_is_not_kept_for_the_next(home):
    """No state outlives a call: the same refused command is refused every time it is asked."""
    command = "ls notes.txt ~/.ssh"
    answers = {security.is_sensitive_bash_command(command, cwd=str(home)) for _ in range(3)}
    assert answers == {security._SHELL_REFUSAL[security.CREDENTIAL]}


# ── the time ─────────────────────────────────────────────────────────────────────────────────────

LONG = 256 * 1024


def _joined(command: str) -> str:
    """The command as an agent CLI's call is screened: its lines joined into one."""
    from personalclaw.acp.permission_authority import command_probe

    probe = command_probe("", command)
    assert probe.startswith("Running: ")
    return probe[len("Running: ") :]


@pytest.mark.parametrize(
    "build",
    [
        lambda: _joined(
            "cat > notes.md <<'EOF'\n" + "a line of text here\n" * (LONG // 20) + "EOF"
        ),
        lambda: "echo " + "word " * (LONG // 5),
    ],
    ids=["joined-heredoc-256-KB", "many-words-256-KB"],
)
def test_a_256_kb_command_is_checked_by_the_credential_screen_in_under_a_second(home, build):
    """🔴 Red before: the redirect alternative's leading `.*` made a line with no match take time
    in the square of its length, about 40 s at 100 KB. Measured on this tree at 256 KB: 0.34 s."""
    command = build()
    assert len(command) >= LONG
    took = cpu_budget.seconds(lambda: security.is_sensitive_bash_command(command))
    assert security.is_sensitive_bash_command(command) is None
    assert took < 1.0, f"{took:.2f}s"


def test_a_32_kb_heredoc_is_checked_by_the_credential_screen_in_under_a_second(home):
    """🔴 Red before, on the budget itself: at 32 KB the quadratic search still finishes, so it fails
    here rather than on the test's timeout. Measured CPU time for this command: 4.2 s on the code
    before the fix, 0.03 s on this tree."""
    command = _joined(
        "cat > notes.md <<'EOF'\n" + "a line of text here\n" * (32 * 1024 // 20) + "EOF"
    )
    assert len(command) >= 32000
    took = cpu_budget.seconds(lambda: security.is_sensitive_bash_command(command))
    assert security.is_sensitive_bash_command(command) is None
    assert took < 1.0, f"{took:.2f}s"


def test_a_long_command_that_reads_a_credential_is_still_refused(home):
    """The time is not bought with a verdict: a read of a credential folder after a long line, or
    after a long here-document joined into one line, is refused."""
    tail = "cat ~/.ssh/id_ed25519"
    refused = security._SHELL_REFUSAL[security.CREDENTIAL]
    assert security.is_sensitive_bash_command("echo " + "word " * 20_000 + "; " + tail) == refused
    heredoc = "cat > notes.md <<'EOF'\n" + "a line\n" * 5000 + "EOF\n" + tail
    assert security.is_sensitive_bash_command(_joined(heredoc)) == refused
    assert security.is_sensitive_bash_command("echo " + "word " * 20_000 + " > ~/.ssh/x") == refused
