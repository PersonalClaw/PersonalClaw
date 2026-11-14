"""The credential guards fire on every spelling that reaches the same file.

Two independent evasions of `security.py`'s sensitive-path family, both found by probing
rather than by reading, and both of the batch's defining shape: the control exists, it is
wired at ~70 call sites, and it did not fire.

**1. Case.** `is_sensitive_path("~/.SSH/id_rsa")` returned False while `~/.ssh/id_rsa`
returned True — and the default macOS filesystem, like Windows, is case-INSENSITIVE.
Verified end to end rather than argued: a temp directory created as `.ssh` was read back
through `.SSH` and returned the file's contents. All FOURTEEN entries in
`_SENSITIVE_HOME_DIRS` were bypassed by shifting a key, including `~/.personalclaw/.env`
(this product's own credential store) and `~/.personalclaw/governance` (the governance
ceiling, whose whole point is that the agent cannot rewrite it). `Path.resolve()` normalises
`..`, `.`, `//`, `~` and `$HOME` — every one of those is blocked — but it does not normalise
case. `is_system_path` had the same bug in part: `/etc/passwd` was blocked (macOS resolves
`/etc` through a symlink, which normalises case as a side effect) while `/SYSTEM/x` and
`/USR/bin/x` were allowed.

**2. Reader coverage.** `is_sensitive_bash_command` enumerated fifteen read commands.
Measured against eighteen content-returning forms, **15 passed**: `grep`, `awk`, `sed`, `od`,
`hexdump`, `nl`, `cut`, `sort`, `wc`, `diff`, `tar cf - ~/.gnupg`, `rsync -a ~/.ssh/`, `jq`,
`bat`, and a python one-liner using `read_text()` instead of `open()`. Every one returns the
same bytes `cat` is blocked from. The guard was enumerating the tools someone thought of
rather than the capability. Two spelling families went with it: shell quoting
(`cat ~/'.ssh'/id_rsa`, `cat ~/.s''sh/id_rsa` — both ordinary shell, both allowed) and
built-up paths (`process.env.HOME+'/.ssh/id_rsa'`, which named no `~`, no `$HOME` and no
literal home path).

**What is deliberately NOT claimed.** String CONCATENATION cannot be closed by a regex over
a command string — `'/.s' + 'sh/id_rsa'` never contains the name of the file it opens. That
limit is asserted below as a known gap rather than papered over, because the alternative is
a control that looks complete. The primary defence for that family is the OS sandbox's
bind-mounts over `~/.aws`, `~/.gnupg` and friends, which `security.py`'s own comment already
names; this guard is defence in depth.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unicodedata

import pytest
from names_matched_byte_for_byte import matched_byte_for_byte

from personalclaw import command_paths, security
from personalclaw.security import (
    _SENSITIVE_HOME_DIRS,
    SensitivePaths,
    is_sensitive_bash_command,
    is_sensitive_path,
    is_system_path,
    strip_shell_quotes,
)

pytestmark = pytest.mark.security


_HOME = str(pathlib.Path.home())


def _without_the_home(spelling: str) -> str:
    """*spelling*'s test id, with the home it starts with written as ``{home}``. A test's id
    names what it checks, so it is the same on every machine and under every home a shard runs
    with, ``/`` among them; the test itself still checks the spelling with the real home in it."""
    home = _HOME + "/"
    return "{home}/" + spelling[len(home) :] if spelling.startswith(home) else spelling


# ── 1. Case ───────────────────────────────────────────────────────────────


def test_no_sensitive_dir_is_reachable_by_shifting_a_key():
    """Every entry, not a sample: the bypass applied to all fourteen."""
    bypassed = [
        sd
        for sd in _SENSITIVE_HOME_DIRS
        if is_sensitive_path(f"{_HOME}/{sd}/secret")
        and not is_sensitive_path(f"{_HOME}/{sd.upper()}/secret")
    ]
    assert not bypassed, (
        "these sensitive dirs are still reachable by changing case, which on a "
        f"case-insensitive filesystem is the same file: {bypassed}"
    )


@pytest.mark.parametrize(
    "spelling",
    [
        "~/.ssh/id_rsa",
        "$HOME/.ssh/id_rsa",
        f"{_HOME}/.ssh/id_rsa",
        f"{_HOME}/.SSH/id_rsa",
        f"{_HOME}/.Ssh/id_rsa",
        f"{_HOME}/.ssh/../.ssh/id_rsa",
        f"{_HOME}/./.ssh/id_rsa",
        f"{_HOME}//.ssh//id_rsa",
        f"{_HOME}/tmp/../.ssh/id_rsa",
    ],
    ids=_without_the_home,
)
def test_every_spelling_of_one_key_is_blocked(spelling):
    """The respelling table, as assertions. Only the two case rows were failing."""
    assert is_sensitive_path(spelling), f"{spelling} reaches ~/.ssh and was allowed"


def test_the_case_bypass_was_real_on_this_filesystem():
    """The premise, measured rather than assumed.

    If the filesystem were case-SENSITIVE, `~/.SSH` would be a different directory and the
    case fix would be over-blocking rather than a fix. This records which world the test is
    running in, so a green run on Linux is not mistaken for proof that the bug was
    theoretical there too.
    """
    d = pathlib.Path(tempfile.mkdtemp())
    (d / ".ssh").mkdir()
    (d / ".ssh" / "key").write_text("SECRET", encoding="utf-8")
    try:
        insensitive = (d / ".SSH" / "key").read_text(encoding="utf-8") == "SECRET"
    except FileNotFoundError:
        insensitive = False
    # Blocking is correct either way; this only reports the platform.
    assert is_sensitive_path(f"{_HOME}/.SSH/key"), "the case guard is not applied"
    if not insensitive:
        pytest.skip("case-sensitive filesystem — the bypass was macOS/Windows-only")


@pytest.mark.parametrize("path", ["/SYSTEM/x", "/USR/bin/x", "/ETC/passwd", "/etc/passwd"])
def test_system_roots_are_case_insensitive_too(path):
    """`is_system_path` shared the bug: two roots behaved differently from the rest."""
    assert is_system_path(path), f"{path} is a system root and was allowed"


def test_an_ordinary_home_path_is_still_allowed():
    """Vacuity for the case fix. Casefolding everything into "blocked" is not a fix."""
    assert not is_sensitive_path(f"{_HOME}/Documents/notes.md")
    assert not is_sensitive_path(f"{_HOME}/code/project/README.md")
    assert not is_system_path(f"{_HOME}/code/project")


# ── 2. Reader coverage ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.ssh/id_rsa",
        "grep -a . ~/.ssh/id_rsa",
        "awk '{print}' ~/.aws/credentials",
        "sed -n 1,99p ~/.netrc",
        "od -c ~/.ssh/id_rsa",
        "hexdump -C ~/.gnupg/secring.gpg",
        "nl ~/.git-credentials",
        "cut -d: -f1 ~/.netrc",
        "sort ~/.pypirc",
        "wc -c ~/.ssh/id_rsa",
        "diff ~/.aws/credentials /dev/null",
        "tar cf - ~/.gnupg",
        "rsync -a ~/.ssh/ /tmp/x",
        "jq . ~/.docker/config.json",
        "bat ~/.ssh/id_rsa",
        "tr -d '\\n' < ~/.ssh/id_rsa",
        "cat ~/.SSH/id_rsa",
        "cat ~/'.ssh'/id_rsa",
        "cat ~/.s''sh/id_rsa",
        'cat "$HOME"/.ssh/id_rsa',
        "python -c \"import pathlib;print(pathlib.Path('~/.ssh/id_rsa')"
        '.expanduser().read_text())"',
        "node -e \"console.log(require('fs').readFileSync(process.env.HOME+'/.ssh/id_rsa'))\"",
        "ruby -e \"puts File.read('~/.netrc')\"",
        "perl -ne 'print' ~/.git-credentials",
    ],
)
def test_every_content_returning_form_is_blocked(command):
    """The measured table, as assertions. 15 of these 18-plus forms used to pass."""
    assert is_sensitive_bash_command(command), f"this returns credential bytes: {command}"


@pytest.mark.parametrize(
    "command",
    [
        "grep -r TODO .",
        "cat README.md",
        "ls -la",
        "sort data.csv",
        "tar cf out.tar src/",
        "diff a.py b.py",
        "jq . package.json",
        "python -m pytest tests/",
        "python -c \"print(open('a.txt').read())\"",
        "npm run build",
        "git commit -m 'fix: the thing'",
        'echo "hello world"',
        "ls ~/Documents",
        "cat src/personalclaw/security.py",
    ],
)
def test_ordinary_work_is_not_blocked(command):
    """The vacuity half, and the one that decides whether this fix is shippable.

    Widening a BLOCKING control is the direction that breaks people's work. Every command
    here names a reader that was just added to the pattern; none names a credential path,
    and the path requirement is what keeps them apart.
    """
    assert not is_sensitive_bash_command(command), f"legitimate command blocked: {command}"


def test_quote_stripping_collapses_a_respelling_and_nothing_else():
    """The helper, directly: it must remove quotes and leave the rest of the command."""
    assert strip_shell_quotes("cat ~/'.ssh'/id_rsa") == "cat ~/.ssh/id_rsa"
    assert strip_shell_quotes("cat ~/.s''sh/id_rsa") == "cat ~/.ssh/id_rsa"
    assert strip_shell_quotes("echo hello") == "echo hello"


def test_the_concatenation_gap_is_recorded_rather_than_claimed_closed():
    """The known limit, pinned so nobody reads the fixes above as completeness.

    A path assembled from fragments never appears in the command text, so no regex over that
    text can see it. If this ever starts passing, someone has found a way — update the
    docstring rather than deleting the test, because the reason matters more than the result.
    """
    concatenated = "python -c \"print(open('/.s' + 'sh/id_rsa').read())\""
    assert not is_sensitive_bash_command(concatenated), (
        "the concatenation family is now detected — that is good news, but this test and the "
        "module docstring both claim it is NOT, so say what changed"
    )


# ── Respellings that name a sensitive path without spelling it the guard's way ──────────────
#
# Both measured ALLOWED against the shipped guard, and both are in the hazard list verbatim:
# "$HOME/.aws/...", "${HOME}/.aws/...", "cd ~ && cat .aws/credentials".


@pytest.mark.parametrize(
    "command",
    [
        "cat ${HOME}/.ssh/id_rsa",
        "cat ${HOME}/.aws/credentials",
        "grep -a . ${HOME}/.netrc",
        "cd ~ && cat .ssh/id_rsa",
        "cd $HOME && grep -a . .aws/credentials",
        "cd ${HOME} && jq . .docker/config.json",
        "cd && cat .ssh/id_rsa",
    ],
)
def test_a_respelt_home_path_is_blocked(command):
    assert is_sensitive_bash_command(command), (
        f"{command!r} reaches a credential the guard blocks when it is spelt ~/…; a brace or a "
        f"preceding cd is syntax, not a different file."
    )


@pytest.mark.parametrize(
    "command",
    [
        "cd ~ && cat notes.txt",
        "cd ~ && ls -la",
        "cd /tmp && cat .ssh/id_rsa",
        "cat .ssh/id_rsa",
        "cd ~/project && npm test",
    ],
)
def test_the_normalisation_does_not_block_ordinary_work(command):
    """The direction that matters as much as the fix.

    `cd /tmp && cat .ssh/id_rsa` stays allowed on purpose: without a home-cd the dot-relative
    path is not the credential store, and blocking it would be a guess. `cat .ssh/id_rsa` alone
    is the same case — the cwd is unknown, so the guard does not invent one.
    """
    assert not is_sensitive_bash_command(command), f"{command!r} is ordinary work"


def test_the_home_cd_rewrite_needs_a_home_cd_to_fire():
    """The vacuity floor for the rewrite: if it fired unconditionally, the test above would be
    asserting nothing and every dot-relative path anywhere would be blocked."""
    from personalclaw.security import _normalise_for_matching

    assert "~/" not in _normalise_for_matching("cat .ssh/id_rsa")
    assert "~/.ssh/" in _normalise_for_matching("cd ~ && cat .ssh/id_rsa")


# ── 3. PersonalClaw's own secrets: two lists that disagreed (#643) ─────────
#
# Same family, third shape. Not "the control did not fire on a respelling" but "the
# control did not know these files existed": `handlers/files.py` refused every one of
# them by basename, so `/api/file-read` answered 400 — while `is_sensitive_path`, which
# the terminal cwd guard and the bash read hook both consult, returned False.
#
# Measured against the shipped guard, default home: only `~/.personalclaw/.env` was
# blocked. `sel_hmac.key`, `.local_secret`, `telemetry_salt` and `credentials/` were all
# allowed, and the issue's repro read the SEL signing key through a real PTY.


@pytest.fixture
def pclaw_home(tmp_path, monkeypatch):
    """A PERSONALCLAW_HOME that is NOT under `$HOME`.

    Deliberately elsewhere: the `.personalclaw/…` entries are `$HOME`-relative, so this is
    the configuration in which they match nothing — a dev home, or any user override.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("name", ["sel_hmac.key", ".local_secret", "telemetry_salt"])
def test_our_own_secret_names_are_refused_wherever_they_sit(name, tmp_path):
    """By basename, because `PERSONALCLAW_HOME` moves and a copy can be made anywhere.

    Each of these has a specific cost: `sel_hmac.key` signs the append-only security log
    (with it, a forged row still verifies, and that log cannot be repaired afterwards);
    `.local_secret` is the loopback auth rail; `telemetry_salt` de-anonymizes telemetry.
    """
    assert is_sensitive_path(str(tmp_path / name))
    assert is_sensitive_path(f"{_HOME}/.personalclaw/{name}")
    assert is_sensitive_path(f"/tmp/a-copy-someone-made/{name}")
    # Case, for the reason the top of this file establishes.
    assert is_sensitive_path(str(tmp_path / name.upper()))


@pytest.mark.parametrize(
    "entry", [".env", "credentials", "governance", "session_key", "sessions.json"]
)
def test_the_active_pclaw_home_is_covered_even_when_it_is_not_under_home(entry, pclaw_home):
    """The sharper half of the finding. The `$HOME`-relative entries cannot follow
    `PERSONALCLAW_HOME`, so with a custom home `.env` AND `governance` were both allowed —
    meaning on every dev home and every user override the governance ceiling was
    agent-writable, which is exactly what its entry in `_SENSITIVE_HOME_DIRS` exists to
    prevent ("a bound the agent can rewrite is not a bound").
    """
    assert is_sensitive_path(str(pclaw_home / entry))
    assert is_sensitive_path(str(pclaw_home / entry / "nested-file"))


def test_the_home_itself_and_its_ordinary_contents_stay_readable(pclaw_home):
    """Scope check. The PersonalClaw home is a browsable dashboard root holding the
    knowledge DB, installed apps and logs, so refusing it wholesale would break the files
    area. Only the secret-bearing entries are refused."""
    assert not is_sensitive_path(str(pclaw_home))
    for ordinary in ("knowledge.db", "gateway.log", "config.json", "apps"):
        assert not is_sensitive_path(str(pclaw_home / ordinary)), ordinary


@pytest.mark.parametrize("ordinary", ["sessions.json", "session_key"])
def test_a_name_that_is_ours_only_by_location_does_not_block_a_users_own_file(
    ordinary, tmp_path, pclaw_home
):
    """Why the two tiers are split by name-uniqueness rather than by module.

    `sessions.json` is a plausible filename in someone's own project, so blocking it by
    NAME everywhere would make the agent's bash guard refuse an ordinary file. Ours is
    refused by PATH; theirs is not touched.
    """
    project = tmp_path.parent / "someones-project"
    project.mkdir(exist_ok=True)
    assert is_sensitive_path(str(pclaw_home / ordinary))
    assert not is_sensitive_path(str(project / ordinary))


@pytest.mark.parametrize(
    "command",
    [
        "wc -c < {home}/sel_hmac.key",  # the issue's own repro
        "cat {home}/sel_hmac.key",
        "od -c {home}/.local_secret",
        "cp {home}/telemetry_salt /tmp/exfil",
        "grep -a . {home}/sel_hmac.key",
        "cat ~/.personalclaw/sel_hmac.key",
    ],
)
def test_the_bash_hook_refuses_a_read_of_our_own_keys(command, pclaw_home):
    """The bash pattern anchors every alternative on `$HOME`, so it could not express a
    file that follows `PERSONALCLAW_HOME`. A second pattern covers these by basename.

    This is the boundary that actually holds for an AGENT. A raw PTY is a real shell with
    the owner's uid and can read any file they can — see the honesty note below.
    """
    assert is_sensitive_bash_command(command.format(home=pclaw_home))


@pytest.mark.parametrize(
    "command",
    [
        "cat /tmp/proj/sessions.json",
        "wc -c < /tmp/proj/server.key",
        "cat /tmp/proj/public.pem",
        "ls ~/.personalclaw",
        "cat ./notes.md",
    ],
)
def test_the_new_pattern_does_not_block_ordinary_work(command):
    """The cost of over-blocking is a guard people route around. A project's own
    `sessions.json`, private key and certificate are its business — `files.py` keeps the
    stricter suffix tier, scoped to the dashboard's allowlisted roots where it belongs."""
    assert is_sensitive_bash_command(command) is None


def test_the_pty_gap_is_recorded_rather_than_claimed_closed(pclaw_home):
    """🔴 What this fix does NOT do.

    An interactive terminal session is a real shell running as the owner, so it can read
    any file the owner can — no path guard in this process can prevent that, and the
    issue says so too. What is closed here is every AGENT-MEDIATED path: `/api/file-read`
    (already), the bash read hook, the action denylist, and the terminal's `cwd` guard,
    which no longer accepts a credential directory as a PTY working directory.

    Recorded as an assertion so the limit stays visible: the cwd guard is the thing that
    changed for the terminal, not the shell's own reach.
    """
    from personalclaw.security import is_sensitive_path as guard

    assert guard(str(pclaw_home / "credentials")), "a credential dir must be a refused cwd"
    assert not guard(str(pclaw_home)), "the home itself remains a legitimate cwd"


def test_the_guard_creates_nothing(tmp_path, monkeypatch):
    """🔴 A read-only predicate must not touch the filesystem.

    The first version of this fix resolved the home through
    ``config.loader.config_dir()``, which calls ``_ensure_dir`` — so with ~70 call sites,
    merely CHECKING a path created the PersonalClaw home. It surfaced as an unrelated
    file-listing test failing on an unexpected `.personalclaw` entry inside a fixture's
    fake `$HOME`: the guard had created it mid-assertion.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(fake_home / "pclaw"))

    before = sorted(p.name for p in fake_home.iterdir())
    is_sensitive_path(str(fake_home / "notes.md"))
    is_sensitive_path(str(fake_home / "pclaw" / ".env"))
    is_sensitive_bash_command(f"cat {fake_home}/pclaw/sel_hmac.key")

    assert sorted(p.name for p in fake_home.iterdir()) == before
    assert not (fake_home / "pclaw").exists(), "the guard created the home it was checking"
    # ...and it still blocks, i.e. the fix is not "stop resolving the home".
    assert is_sensitive_path(str(fake_home / "pclaw" / ".env"))


# ── 5. one walk, one resolution: the same answers ──────────────────────────


def test_a_walks_checker_answers_every_path_as_the_one_shot_guard_does(tmp_path, monkeypatch):
    """``SensitivePaths`` resolves the protected locations once for a walk over thousands of files
    (an import scan: 0.2 ms a file saved, 2.4 s of a 12,000-file history). It must refuse exactly
    what ``is_sensitive_path`` refuses — every evasion above included: a link INTO a protected
    folder, a protected folder that is itself a link (a dotfile manager's), case, a NUL, our own
    secret names, and the active PersonalClaw home's secret entries."""
    from personalclaw.security import SensitivePaths

    home = tmp_path / "home"
    dotfiles = tmp_path / "dotfiles"
    (dotfiles / "ssh").mkdir(parents=True)
    (dotfiles / "ssh" / "id_ed25519").write_text("key")
    home.mkdir()
    (home / ".ssh").symlink_to(dotfiles / "ssh")  # a protected folder that is a link
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").write_text("[default]")
    (home / "src").mkdir()
    (home / "src" / "notes.md").write_text("notes")
    (home / "src" / "sneaky").symlink_to(home / ".aws" / "credentials")  # a link into one
    pclaw = tmp_path / "pclaw"
    (pclaw / "credentials").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pclaw))

    paths = [
        str(home / ".ssh" / "id_ed25519"),
        str(dotfiles / "ssh" / "id_ed25519"),
        str(home / ".SSH" / "id_ed25519"),
        str(home / ".aws" / "credentials"),
        str(home / "src" / "sneaky"),
        str(home / "src" / "notes.md"),
        "~/.aws/config",
        "$HOME/src/notes.md",
        str(tmp_path / "a\x00b"),
        str(tmp_path / "copy" / "sel_hmac.key"),
        str(pclaw / "credentials" / "store.json"),
        str(pclaw / "sessions" / "chat.jsonl"),
        "/tmp/ordinary/file.jsonl",
    ]
    walk = SensitivePaths()
    one_shot = [is_sensitive_path(p) for p in paths]
    assert [walk(p) for p in paths] == one_shot
    # Vacuity: the set holds both answers, so agreeing is not agreeing on all-False.
    assert any(one_shot) and not all(one_shot)


# ── one Unicode form ──────────────────────────────────────────────────────────
#
# A path can hold a letter written as one code point or as a base letter and a combining mark
# (Unicode NFC and NFD), and a filesystem may keep or hand back either. The guard compares every
# path, and every protected place and name, in one form, so a protected place is refused
# whichever spelling it is asked by.

_FORMS = ["NFC", "NFD"]


def _in(form: str, text: str) -> str:
    return unicodedata.normalize(form, text)


@pytest.fixture(params=["as-is", "bytes"])
def names_matched(request, monkeypatch, tmp_path):
    """Each row on this filesystem as it is, and on one that matches names byte for byte, as
    Linux's do (:mod:`names_matched_byte_for_byte`): on a Mac, a home folder kept in one Unicode
    form and named in another is found by either spelling, and on Linux only by its own."""
    if request.param == "bytes":
        monkeypatch.setattr(security, "_LINKED_ENTRIES", {})
        with matched_byte_for_byte(monkeypatch, tmp_path):
            yield
    else:
        yield


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_a_protected_place_is_refused_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / _in(kept, "Zoë")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert is_sensitive_path(_in(asked, f"{home}/.ssh/id_ed25519"))
    assert is_sensitive_path(_in(asked, f"{home}/.aws/credentials"))
    assert not is_sensitive_path(_in(asked, f"{home}/notes.txt"))


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
@pytest.mark.parametrize("entry", [".env", "credentials", "governance"])
def test_the_own_homes_secrets_are_refused_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked, entry
):
    home = tmp_path / _in(kept, "Cåfé")
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    assert is_sensitive_path(_in(asked, f"{home}/{entry}"))
    assert is_sensitive_path(_in(asked, f"{home}/{entry}/nested-file"))
    assert not is_sensitive_path(_in(asked, f"{home}/knowledge.db"))


@pytest.mark.parametrize("asked", _FORMS)
def test_a_protected_name_is_refused_in_either_unicode_form_wherever_it_sits(tmp_path, asked):
    folder = tmp_path / "Résumé"
    for name in ("sel_hmac.key", ".local_secret", ".credentials.json"):
        assert is_sensitive_path(_in(asked, f"{folder}/{name}")), name
    assert not is_sensitive_path(_in(asked, f"{folder}/notes.txt"))


#: A letter whose two spellings fold apart when case is folded before the letter is decomposed:
#: alpha with an iota subscript, then a diaeresis. Composed, the subscript is part of the letter
#: and folds to a separate iota, which the diaeresis then follows; decomposed, the diaeresis comes
#: first.
_SUBSCRIPT = "\u1fb3\u0308"


def test_both_spellings_of_every_text_fold_to_one_form():
    """The one form is one form: each code point, and each letter with an iota subscript and more
    marks, folds to the same text whichever of its spellings it is given in."""
    folded = command_paths.folded
    apart = [
        hex(code)
        for code in range(0x110000)
        if not 0xD800 <= code <= 0xDFFF
        and folded(_in("NFC", chr(code))) != folded(_in("NFD", chr(code)))
    ]
    assert apart == []
    for text in (
        _SUBSCRIPT,
        "\u1fb3\u0301\u0308",
        "\u1f80\u0300",
        "x\u1fb3\u0308y",
        "\u1ff3\u0308",
    ):
        assert folded(_in("NFC", text)) == folded(_in("NFD", text)), ascii(text)


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_a_protected_place_is_refused_whichever_way_a_subscripted_letter_is_spelled(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / _in(kept, f"x{_SUBSCRIPT}y")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert is_sensitive_path(_in(asked, f"{home}/.ssh/id_ed25519"))
    assert not is_sensitive_path(_in(asked, f"{home}/notes.txt"))


def test_an_ascii_path_is_answered_as_it_was():
    assert is_sensitive_path(f"{_HOME}/.ssh/id_rsa")
    assert is_sensitive_path(f"{_HOME}/.SSH/id_rsa")
    assert not is_sensitive_path(f"{_HOME}/projects/notes.txt")


def _spent() -> str:
    """Enough globs to spend the screen's listing budget before the command after them."""
    return " ".join(f"many/x{n}-*.txt" for n in range(4 * command_paths._MAX_LISTINGS))


def _workspace(tmp_path) -> pathlib.Path:
    """A folder the listing globs of :func:`_spent` list."""
    ws = tmp_path / "ws"
    (ws / "many").mkdir(parents=True)
    (ws / "many" / "a.txt").write_text("ordinary\n")
    return ws


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_a_protected_bare_name_is_named_in_either_unicode_form(tmp_path, monkeypatch, kept, asked):
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf" / _in(kept, "clé")))
    walk = SensitivePaths(home_credential_dirs=False)
    assert walk.names_one(_in(asked, "clé"))
    assert not walk.names_one("notes.txt")


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_a_protected_bare_name_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    folder = tmp_path / "hf"
    folder.mkdir()
    monkeypatch.setenv("HF_TOKEN_PATH", str(folder / _in(kept, "clé")))
    assert is_sensitive_bash_command(_in(asked, f"cd {folder} && cat clé"))
    assert is_sensitive_bash_command(f"cd {folder} && cat notes.txt") is None


@pytest.mark.parametrize("command", ["cat", "grep -a .", "tar cf -"])
@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_a_home_credential_read_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked, command
):
    home = tmp_path / _in(kept, "Zoë")
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(_in(asked, f"{command} {home}/.ssh/id_ed25519"))
    assert is_sensitive_bash_command(_in(asked, f"{command} {home}/notes.txt")) is None


@pytest.mark.parametrize("spelling", ["~", "$HOME"])
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_a_home_credential_read_by_the_homes_spelling(
    tmp_path, monkeypatch, names_matched, kept, spelling
):
    home = tmp_path / _in(kept, "Zoë")
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(f"cat {spelling}/.ssh/id_ed25519")
    assert is_sensitive_bash_command(f"cat {spelling}/notes.txt") is None


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_listing_a_credential_folder_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / _in(kept, "Zoë")
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(_in(asked, f"ls {home}/.ssh"))
    assert is_sensitive_bash_command(_in(asked, f"ls {home}/notes")) is None


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_a_link_into_a_protected_folder_is_refused_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    dotfiles = tmp_path / _in(kept, "Zoë-dotfiles")
    dotfiles.mkdir()
    (dotfiles / "id_ed25519").write_text("not a key\n")
    (home / ".ssh" / "id_ed25519").symlink_to(dotfiles / "id_ed25519")
    monkeypatch.setenv("HOME", str(home))
    assert is_sensitive_path(_in(asked, str(dotfiles / "id_ed25519")))
    assert not is_sensitive_path(_in(asked, str(dotfiles / "notes.txt")))


@pytest.mark.parametrize("named", _FORMS)
def test_a_link_in_a_credential_folder_of_a_home_named_in_its_other_form_is_refused(
    tmp_path, monkeypatch, names_matched, named
):
    """`$HOME` written in one form, its folder kept in the other: a filesystem that keeps a name
    as its bytes finds the folder, and the links inside it, only by the spelling it was made with,
    so each is looked for by both."""
    home = tmp_path / _in("NFD" if named == "NFC" else "NFC", "Zoë")
    (home / ".ssh").mkdir(parents=True)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "id_ed25519").write_text("not a key\n")
    (home / ".ssh" / "id_ed25519").symlink_to(dotfiles / "id_ed25519")
    monkeypatch.setenv("HOME", _in(named, str(home)))
    assert is_sensitive_path(str(dotfiles / "id_ed25519"))
    assert not is_sensitive_path(str(dotfiles / "notes.txt"))


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_the_own_home_in_either_unicode_form(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / _in(kept, "Cåfé")
    (home / "credentials").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    glob = f"{tmp_path}/{_in(asked, 'Cåf')}*"
    assert is_sensitive_bash_command(f"cat {glob}/credentials/store.json")
    assert is_sensitive_bash_command(f"cat {glob}/notes.txt") is None


@pytest.mark.parametrize("asked", _FORMS)
@pytest.mark.parametrize("kept", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_the_own_home_past_the_listing_budget(
    tmp_path, monkeypatch, names_matched, kept, asked
):
    home = tmp_path / _in(kept, "Cåfé")
    (home / "credentials").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    ws = _workspace(tmp_path)
    glob = f"{tmp_path}/{_in(asked, 'Cåf')}*"
    assert is_sensitive_bash_command(f"ls {_spent()}; cat {glob}/credentials/store.json", cwd=ws)
    assert is_sensitive_bash_command(f"ls {_spent()}; cat {glob}/notes.txt", cwd=ws) is None


# ── a glob, as the shell expands it ───────────────────────────────────────────
#
# The shell compares a glob with a name as each is written, so `Cafe*` names a folder kept as a
# base letter and a combining mark. The screen matches such a glob as the shell does, as well as
# regardless of case and of Unicode form.


def _utf8_locale() -> str | None:
    names = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
    return next((n for n in ("C.UTF-8", "C.utf8", "en_US.UTF-8", "en_US.utf8") if n in names), None)


def _is_bash(shell: str) -> bool:
    out = subprocess.run([shell, "-c", "echo ${BASH_VERSION:-}"], capture_output=True, text=True)
    return bool(out.stdout.strip())


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/sh"])
@pytest.mark.parametrize(
    "name,glob", [("Café", "Cafe*"), ("Café", "Cafe?"), ("Zoë", "Zoe*"), ("Zoë", "Zo??")]
)
def test_the_shell_expands_a_glob_to_a_folder_kept_decomposed(tmp_path, shell, name, glob):
    """What the rows below rest on, measured on this machine's shell."""
    if not pathlib.Path(shell).exists() or not _is_bash(shell):
        pytest.skip(f"{shell} is not bash here")
    locale = _utf8_locale()
    if locale is None:
        pytest.skip("no UTF-8 locale here")
    kept = _in("NFD", name)
    (tmp_path / kept / "credentials").mkdir(parents=True)
    env = {"LC_ALL": locale, "PATH": "/bin:/usr/bin", "HOME": str(tmp_path)}
    out = subprocess.run(
        [shell, "-c", f"cd {tmp_path} && printf '%s\\n' {glob}/credentials"],
        capture_output=True, env=env, stdin=subprocess.DEVNULL,
    ).stdout.decode("utf-8").strip()  # fmt: skip
    assert out == f"{kept}/credentials", (shell, glob, ascii(out))


@pytest.mark.parametrize("glob", ["Caf*", "Cafe*", "Cafe?", "C*"])
@pytest.mark.parametrize("named", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_an_own_home_kept_decomposed(
    tmp_path, monkeypatch, names_matched, named, glob
):
    home = tmp_path / _in("NFD", "Café")
    (home / "credentials").mkdir(parents=True)
    (home / "credentials" / "store.json").write_text("{}\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", _in(named, str(home)))
    assert is_sensitive_bash_command(f"cat {tmp_path}/{glob}/credentials/store.json")
    assert is_sensitive_bash_command(f"cat {tmp_path}/{glob}/notes.txt") is None


@pytest.mark.parametrize("entry", ["governance/ceiling.yaml", "session_key", ".env"])
@pytest.mark.parametrize("named", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_an_own_homes_other_secrets_kept_decomposed(
    tmp_path, monkeypatch, names_matched, named, entry
):
    home = tmp_path / _in("NFD", "Café")
    (home / "governance").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", _in(named, str(home)))
    assert is_sensitive_bash_command(f"cat {tmp_path}/Cafe*/{entry}")
    assert is_sensitive_bash_command(f"cat {tmp_path}/Cafe*/notes.txt") is None


@pytest.mark.parametrize("glob", ["Zo*", "Zoe*", "Zo??"])
@pytest.mark.parametrize("named", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_a_home_kept_decomposed(
    tmp_path, monkeypatch, names_matched, named, glob
):
    home = tmp_path / _in("NFD", "Zoë")
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", _in(named, str(home)))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(f"cat {tmp_path}/{glob}/.ssh/id_ed25519")
    assert is_sensitive_bash_command(f"cat {tmp_path}/{glob}/notes.txt") is None


#: A home folder's name with one letter decomposed and another composed, as a filesystem that
#: matches names byte for byte keeps it as it was typed.
_MIXED = "Zoe\u0308Ren\u00e9e"


def test_the_shells_screen_refuses_a_glob_into_a_home_kept_in_mixed_form(
    tmp_path, monkeypatch, names_matched
):
    home = tmp_path / _MIXED
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", _in("NFC", str(home)))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(f"cat {tmp_path}/Zo*/.ssh/id_ed25519")
    assert is_sensitive_bash_command(f"cat {tmp_path}/Zo*/notes.txt") is None


def test_the_shells_screen_refuses_a_glob_into_a_home_named_in_another_case_and_form(
    tmp_path, monkeypatch, names_matched
):
    """`$HOME` written in other letter case as well as the other form than its folder is kept in:
    the one form folds case too, so the folder is found by its own spelling."""
    home = tmp_path / _in("NFD", "Zoë")
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", _in("NFC", str(tmp_path / "ZOË")))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(f"cat {tmp_path}/Zo*/.ssh/id_ed25519")
    assert is_sensitive_bash_command(f"cat {tmp_path}/Zo*/notes.txt") is None


def test_the_shells_screen_refuses_a_glob_into_an_ascii_home_as_it_did(
    tmp_path, monkeypatch, names_matched
):
    home = tmp_path / "zoe"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    assert is_sensitive_bash_command(f"cat {tmp_path}/zo*/.ssh/id_ed25519")
    assert is_sensitive_bash_command(f"cat {tmp_path}/zo*/notes.txt") is None


def test_a_path_found_as_written_or_missing_only_ascii_parts_costs_what_it_did(
    tmp_path, monkeypatch
):
    """A path that is ASCII, or there as written, or missing only parts that are ASCII, is looked
    up at most twice and lists no folder: the walk is only for a part that is not ASCII and is
    missing as written."""
    home = tmp_path / _in("NFD", "Zoë")
    home.mkdir()
    looked: list[str] = []
    lstat = security.os.lstat

    def counted(path, *args, **kwargs):
        looked.append(str(path))
        return lstat(path, *args, **kwargs)

    def listed(*_args, **_kwargs):
        raise AssertionError("a folder was listed")

    for path in (f"{tmp_path}/nowhere/.ssh", str(home), f"{home}/.aws/credentials"):
        looked.clear()
        with monkeypatch.context() as patched:
            patched.setattr(security.os, "lstat", counted)
            patched.setattr(security.os, "listdir", listed)
            spellings = command_paths.kept_spellings(path)
        assert spellings == [path]
        assert len(looked) <= 2, looked


@pytest.mark.parametrize("glob", ["Caf*", "Cafe*"])
@pytest.mark.parametrize("named", _FORMS)
def test_the_shells_screen_refuses_a_glob_into_an_own_home_kept_decomposed_past_the_budget(
    tmp_path, monkeypatch, names_matched, named, glob
):
    home = tmp_path / _in("NFD", "Café")
    (home / "credentials").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", _in(named, str(home)))
    ws = _workspace(tmp_path)
    cmd = f"ls {_spent()}; cat {tmp_path}/{glob}"
    assert is_sensitive_bash_command(f"{cmd}/credentials/store.json", cwd=ws)
    assert is_sensitive_bash_command(f"{cmd}/notes.txt", cwd=ws) is None


def test_the_shells_screen_matches_a_glob_to_a_name_as_the_shell_does(tmp_path, monkeypatch):
    """`?` names one letter as written, and a letter that casefolds to two is two to a folded
    match: only the shell's own comparison names this folder."""
    home = tmp_path / "Straße"
    (home / "credentials").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    assert is_sensitive_bash_command(f"cat {tmp_path}/Stra?e*/credentials/store.json")
    assert is_sensitive_bash_command(f"cat {tmp_path}/Stra?e*/notes.txt") is None
