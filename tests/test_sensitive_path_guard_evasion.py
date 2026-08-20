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
import tempfile

import pytest

from personalclaw.security import (
    _SENSITIVE_HOME_DIRS,
    is_sensitive_bash_command,
    is_sensitive_path,
    is_system_path,
    strip_shell_quotes,
)

_HOME = str(pathlib.Path.home())


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
