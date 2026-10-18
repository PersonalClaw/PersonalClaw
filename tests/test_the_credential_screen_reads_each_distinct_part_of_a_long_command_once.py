"""The path reading behind the shell's screens reads a repeated part of a command again only when
its first reading listed a folder.

`command_paths.named_paths` splits a command at its chain separators and reads every part for the
paths it names. A line that repeats a pipeline repeats its parts, and each repeat was read again:
the same split into words, the same paths built and globbed. On a 256 KB line repeating
`grep a b | sed s/a/b/ | node x.js ; ` that was 21,844 parts read for five distinct ones, and most
of the credential screen's time.

What a part names depends on its text, the folders the command can be in (read from every part,
repeats included, before any part is read), the bare names the caller protects, and the call's
folder-listing budget for globs that is left. The first three are fixed for the call. The budget
only goes down, and a glob found with none left is checked as written. So a part whose first
reading listed no folder (it has no glob, or the budget was already spent) names the same paths
when it repeats, and is skipped. A part whose reading listed a folder is read again, as it always
was, so the budget is spent as before; that can happen at most as many times as the budget holds.
The callers stop at the first refusal, and the first time each path is named keeps its place. The
set lives only for the call, so a path that changes between two checks is read again by the next
one.

This file proves the reading is the old one. A copy of the loop as it was (`_named_paths_before`)
gives the same paths, in the same first-seen order, over the long-command proof's corpora with
every line also repeated, with the listing budget lifted and with the real one; past the real
budget, after repeats that spend it, too, whatever the credential folder holds; the four screens
that read through it give the same answers. The time is held by `[mixed-pipeline]` in the
repeated-read-command file, which this change is expected to make pass on a slow runner; here a
count of the parts read holds it without a clock.
"""

from __future__ import annotations

import os
import random
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_the_credential_screen_checks_a_long_command_in_bounded_time import (
    _grid,
    _mixed,
    _test_commands,
)

from personalclaw import command_paths as cp
from personalclaw import file_scope, memory, owner_only, security

pytestmark = pytest.mark.security


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A scratch user home with credential folders, a PersonalClaw home with its stores, memory
    and owner-only folders, and the screen's regex built for them."""
    user = tmp_path / "user"
    for folder in (".ssh", ".aws", ".gnupg", ".kube", ".docker"):
        (user / folder).mkdir(parents=True)
    (user / ".ssh" / "id_ed25519").write_text("not a key\n", encoding="utf-8")
    (user / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    (user / "src" / "app").mkdir(parents=True)
    (user / "w" / "w").mkdir(parents=True)
    (user / "notes.txt").write_text("notes\n", encoding="utf-8")
    data = tmp_path / "data"
    for folder in ("workspace/knowledge", "workspace/memory", "hooks", "agents"):
        (data / folder).mkdir(parents=True)
    (data / ".env").write_text("X=1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(data))
    monkeypatch.setattr(security, "_SENSITIVE_RE", None)
    return user


def _named_paths_before(text, *, cwd=None, home=None, names=()):
    """`command_paths.named_paths` as it was: every part read, however often it repeats."""
    root = Path(home) if home is not None else cp._home()
    user_home = os.path.expanduser("~")
    spelled = cp._spell_out(text, personalclaw_home=str(root), user_home=user_home)
    if cwd is None:
        from personalclaw.config.loader import memory_root

        start = memory_root(root)
    else:
        start = Path(cwd)
    segments = cp._CHAIN.split(spelled)
    bases = cp._working_dirs(segments, start, user_home)
    bare = frozenset(name.casefold() for name in names)
    budget = [cp._MAX_LISTINGS]
    for segment in segments:
        for word in cp._words(segment):
            if not ("/" in word or word.startswith((".", "~")) or word.casefold() in bare):
                continue
            path = Path(os.path.expanduser(word))
            for candidate in [path] if path.is_absolute() else [base / path for base in bases]:
                for named in cp._globbed(candidate, budget):
                    yield word, named


@contextmanager
def _before(monkeypatch):
    """The screens reading through the loop as it was. `security` holds its own name for it; the
    others import it from `command_paths` when they run."""
    with monkeypatch.context() as patch:
        patch.setattr(cp, "named_paths", _named_paths_before)
        patch.setattr(security, "named_paths", _named_paths_before)
        yield


def _first_seen(pairs) -> list[tuple[str, Path]]:
    return list(dict.fromkeys(pairs))


#: What the credential folder `~/.ssh` holds: a key, only a hidden entry, or nothing. A glob into
#: it, `~/.ssh/*`, shows the folder as written; expanded, it shows what the shell's `*` reaches
#: there, and that is none of a hidden entry.
SSH_HOLDS = {"a-key": ["id_ed25519"], "only-hidden": [".keep"], "nothing": []}


def _ssh_holds(user: Path, entries: list[str]) -> None:
    for entry in (user / ".ssh").iterdir():
        entry.unlink()
    for name in entries:
        (user / ".ssh" / name).write_text("x\n", encoding="utf-8")


#: Repeats of a glob that spend the 64-listing budget before what follows them.
SPEND = "ls ~/s*; " * 70


def _past_the_budget(user: Path) -> list[str]:
    """Globs after repeats that spent the budget: new ones, and one first read before the repeats
    and read again after them."""
    data = user.parent / "data"
    tails = ["ls ~/.ss*", "ls ~/.ssh/*", "ls ~/.ss*/*", f"cat {data}/.e*", f"cp x {data}/hoo*/a"]
    return [SPEND + tail for tail in tails] + [
        "ls ~/.ssh/*; " + SPEND + "ls ~/.ssh/*",
        "ls ~/.ss*; " + SPEND + "ls ~/.ss*",
    ]


# ── the count: each distinct part read once ─────────────────────────────────────────────────────

PIPELINE = "grep a b | sed s/a/b/ | node x.js ; "


@pytest.fixture
def parts_read(monkeypatch) -> list[str]:
    """Every part `_words` is asked to read."""
    calls: list[str] = []
    real = cp._words

    def counted(text: str) -> list[str]:
        calls.append(text)
        return real(text)

    monkeypatch.setattr(cp, "_words", counted)
    return calls


def test_a_line_that_repeats_a_pipeline_reads_each_distinct_part_once(home, parts_read):
    """🔴 Red before: 1,000 repeats of a three-command pipeline were 3,001 parts read. Now each of
    the line's distinct parts is read, exactly as written, once."""
    line = PIPELINE * 1000
    list(cp.named_paths(line, cwd=str(home)))
    distinct = set(cp._CHAIN.split(line))
    assert len(distinct) == 5, distinct
    assert sorted(parts_read) == sorted(distinct)


def test_the_credential_screen_reads_each_distinct_part_once(home, parts_read):
    """The same through the screen itself: the line is allowed, and read once per distinct part."""
    line = PIPELINE * 1000
    assert security.is_sensitive_bash_command(line, cwd=str(home)) is None
    assert sorted(parts_read) == sorted(set(cp._CHAIN.split(line)))


def test_a_repeated_glob_is_read_again_only_while_the_budget_lasts(home, parts_read):
    """🔴 Red before: 1,000 repeats of a glob were 1,001 parts read. A repeat of a part whose reading
    listed a folder is read again, so the budget is spent as it was; each such reading spends one
    listing at least, so past the parts themselves at most the budget's 64 are read again."""
    line = "ls ~/s*; " * 1000
    list(cp.named_paths(line, cwd=str(home)))
    distinct = set(cp._CHAIN.split(line))
    assert len(distinct) == 3, distinct
    assert set(parts_read) == distinct
    assert len(parts_read) <= len(distinct) + cp._MAX_LISTINGS, len(parts_read)


# ── the differential: the same paths, in the same first-seen order ──────────────────────────────


def _shapes(user: Path, data: Path) -> list[str]:
    """Lines whose repeats the reasoning depends on, beside the corpora's."""
    return [
        # A repeated part before and after a `cd`, relative and to the home.
        "cat ./notes.txt; cd src; cat ./notes.txt; cd ~; cat ./notes.txt",
        "ls ../.ssh; cd app; ls ../.ssh; cd ~; ls ../.ssh",
        # A repeated `cd`: each one moves the shell again, so the folders come from every part.
        "cd w; cd w; ls ../../.ssh",
        "cd w; cd w; cat ./x; cat ./x",
        # Repeated brace lists and globs.
        "ls ~/.{ssh,aws}; ls ~/.{ssh,aws}; ls ~/.{ssh,aws}",
        "ls ~/s*; ls ~/s*; ls ~/.s*",
        "cat ~/src/{a,b}/*; cat ~/src/{a,b}/*",
        # The credential read only in the last repeat.
        "cat ./notes.txt; " * 50 + "ls ~/.ssh",
        "cat ./notes.txt | " * 50 + f"cat {data}/.env",
        # Parts that differ only in their whitespace, or only in case: each is its own part.
        "cat ./a\\; cat ./a\\ ",
        "cat ./notes.txt;cat ./notes.txt ; cat  ./notes.txt",
        "cat ./Notes.txt; cat ./notes.txt; cat ./NOTES.TXT",
        "ls ./SRC; ls ./src",
        # Parts that differ only in their quotes or a backslash, with no space after `;`, so their
        # texts with the quotes taken out are the same: each is its own part.
        f"cd {data};cat '.env x';cat .env x",
        f'cd {data};cat ".env x";cat .env x',
        f"cd {data};cat .env\\ x;cat .env x",
        f"cd {data};cat 'config.json x';cat config.json x",
        # Bare names a screen protects, repeated.
        f"cd {data}; cat config.json; cat config.json; cat .env",
        f"cd {data}/workspace/knowledge; cat notes; cat notes; cat knowledge.db",
        # A repeat inside `sh -c '…'`.
        "sh -c 'cat ./notes.txt; cat ./notes.txt; ls ~/.aws'",
        "sh -c 'cd w; cd w; ls ../../.ssh'",
        # A `cd` whose repeats are the same part exactly, so the folders must be read from every
        # part, repeats included, before any part is skipped.
        " cd w ; cd w ; ls ../../.ssh",
        " cd w ; cd w ; cat ../../notes.txt ; cat ../../notes.txt",
        # What the other screens refuse: the stores, the owner-only places and long-term memory,
        # each after an allowed part that repeats.
        *(
            f"{lead}{target}"
            for lead in ("", "cat ./notes.txt; ", "cat ./notes.txt | cat ./notes.txt | ")
            for target in (
                f"sqlite3 {data}/workspace/knowledge/knowledge.db .dump",
                f"cat {data}/workspace/knowledge/files/a.md",
                f"cd {data}/workspace/knowledge; grep -a x knowledge.db",
                f"cp x {data}/workspace/lexicon/lexicon.db",
                f"echo x > {data}/hooks/a.sh",
                f"cp x {data}/agents/x.md",
                f"cd {data}; echo x > config.json",
                f"rm {data}/grants/x",
                f"cat {data}/workspace/memory/MEMORY.md",
                f"echo x >> {data}/workspace/memory/notes.md",
                f"ls {data}/workspace/memory/",
            )
        ),
    ]


def _corpus(user: Path, size: int, seed: int) -> list[str]:
    """The long-command proof's corpora and these shapes, each line also as two and three repeats
    written so each repeat is the same part (` x ;` twice splits into ` x `, ` x ` and ``)."""
    data = user.parent / "data"
    rng = random.Random(seed)
    tests = rng.sample(_test_commands(user, data), size)
    grid = rng.sample(_grid(user), size)
    shapes = _shapes(user, data)
    lines = tests + grid + shapes + _mixed(tests + grid + shapes, size, seed=seed + 1)
    return [form for line in lines for form in (line, f" {line} ;" * 2, f" {line} |" * 3)]


def test_the_shapes_repeat_their_parts(home):
    """The corpus exercises what changed: most lines repeat a part."""
    lines = _corpus(home, 200, seed=20261018)
    repeats = sum(
        1 for line in lines if len(set(cp._CHAIN.split(line))) < len(cp._CHAIN.split(line))
    )
    assert repeats > len(lines) // 2, (repeats, len(lines))


@pytest.mark.parametrize("budget", ["real", "lifted"], ids=["real-budget", "budget-lifted"])
@pytest.mark.parametrize("cwd", ["home", None], ids=["from-the-home", "from-the-workspace"])
def test_the_reading_names_the_same_paths_in_the_same_order(home, monkeypatch, cwd, budget):
    """The new reading names every path the old one did, first seen in the same order, the first
    one included: with the real listing budget, and with it lifted so that every glob is expanded.
    """
    if budget == "lifted":
        monkeypatch.setattr(cp, "_MAX_LISTINGS", 10**9)
    where = str(home) if cwd else None
    wrong = []
    fewer = 0
    for line in _corpus(home, 300, seed=20261019):
        for names in ((), security.SensitivePaths(home_credential_dirs=False).names):
            before = list(_named_paths_before(line, cwd=where, names=names))
            now = list(cp.named_paths(line, cwd=where, names=names))
            if _first_seen(before) != _first_seen(now):
                wrong.append((line, names and "names"))
            fewer += len(now) < len(before)
    assert not wrong, wrong[:5]
    assert fewer > 1000, fewer


@pytest.mark.parametrize("holds", sorted(SSH_HOLDS))
def test_past_the_listing_budget_the_reading_is_the_old_one(home, holds):
    """🔴 Red on a reading that skips every repeat: its repeats of a glob spent no listing, so a
    later glob was expanded where the old reading, its budget spent, named it as written. Here
    every repeat of a glob spends the budget as it did, and the paths are the old ones exactly,
    first seen in the same order, whatever the credential folder holds."""
    _ssh_holds(home, SSH_HOLDS[holds])
    names = security.SensitivePaths(home_credential_dirs=False).names
    wrong = []
    for line in _past_the_budget(home):
        for cwd in (str(home), None):
            before = _first_seen(_named_paths_before(line, cwd=cwd, names=names))
            now = _first_seen(cp.named_paths(line, cwd=cwd, names=names))
            if before != now:
                wrong.append((line[-30:], cwd and "home", sorted(set(before) ^ set(now))[:3]))
    assert not wrong, wrong[:5]
    # The lines are past the budget: the old reading names a glob after the repeats as written.
    assert ("~/.ss*", home / ".ss*") in _named_paths_before(SPEND + "ls ~/.ss*", cwd=str(home))


@pytest.mark.parametrize("holds", sorted(SSH_HOLDS))
def test_with_the_real_budget_every_screen_gives_the_answer_it_gave(home, monkeypatch, holds):
    """With the real listing budget, over the corpus and lines that spend it on repeats first, from
    the home and from the workspace: each screen's answer is its old one, refusals and allows."""
    _ssh_holds(home, SSH_HOLDS[holds])
    lines = _corpus(home, 30, seed=20261020) + _past_the_budget(home)
    wrong = []
    for line in lines:
        for cwd in (str(home), None):
            with _before(monkeypatch):
                before = _answers(line, cwd)
            now = _answers(line, cwd)
            wrong += [
                (line[-30:], SCREENS[i], b, n)
                for i, (b, n) in enumerate(zip(before, now))
                if b != n
            ]
    assert not wrong, wrong[:5]


# ── the verdicts: the screens that read through it ─────────────────────────────────────────────


def _answers(line: str, cwd: str | None) -> tuple:
    """What each screen that reads a command through `named_paths` says of *line*."""
    data = os.environ["PERSONALCLAW_HOME"]
    first_memory = next(
        (pair for pair in cp.named_paths(line, cwd=cwd) if memory.in_memory_folders(str(pair[1]))),
        None,
    )
    return (
        security.is_sensitive_bash_command(line, cwd=cwd),
        file_scope.store_named_in(line, cwd=cwd),
        owner_only.named_in(line, cwd=cwd, home=data),
        first_memory,
    )


SCREENS = ["credential", "store", "owner-only", "memory"]


def test_every_screen_gives_the_same_answer(home, monkeypatch):
    """The credential screen, the store screen, the owner-only screen and the memory screen's first
    path, old reading against new, over the corpus from the home and from the workspace. Each screen
    both refuses and allows in it."""
    monkeypatch.setattr(cp, "_MAX_LISTINGS", 10**9)
    wrong = []
    refused = [0] * len(SCREENS)
    allowed = [0] * len(SCREENS)
    for line in _corpus(home, 60, seed=20261021):
        for cwd in (str(home), None):
            with _before(monkeypatch):
                before = _answers(line, cwd)
            now = _answers(line, cwd)
            if before != now:
                wrong.append((line, cwd, before, now))
            for i, answer in enumerate(before):
                refused[i] += bool(answer)
                allowed[i] += not answer
    assert not wrong, wrong[:5]
    assert min(refused) > 50 and min(allowed) > 50, dict(zip(SCREENS, zip(refused, allowed)))


@pytest.mark.parametrize(
    "line",
    [SPEND + "ls ~/.ssh/*", "ls ~/.ssh/*; " + SPEND + "ls ~/.ssh/*"],
    ids=["a-new-glob", "a-glob-read-before-the-repeats"],
)
def test_a_glob_into_a_credential_folder_past_the_listing_budget_is_refused_as_before(
    home, monkeypatch, line
):
    """🔴 Red on a reading that skips every repeat. `~/.ssh` holds only a hidden entry, which the
    shell's `*` does not reach, so the glob's expansion shows nothing and the glob as written
    shows the folder. Past the budget the old reading checked the glob as written and refused;
    so does this one."""
    _ssh_holds(home, SSH_HOLDS["only-hidden"])
    refused = security._SHELL_REFUSAL[security.CREDENTIAL]
    with _before(monkeypatch):
        assert security.is_sensitive_bash_command(line, cwd=str(home)) == refused
    assert security.is_sensitive_bash_command(line, cwd=str(home)) == refused


def test_a_credential_read_after_many_repeats_is_refused(home):
    refused = security._SHELL_REFUSAL[security.CREDENTIAL]
    assert security.is_sensitive_bash_command(PIPELINE * 2000 + "ls ~/.ssh") == refused
    assert (
        security.is_sensitive_bash_command(
            "cd w; " * 2 + PIPELINE * 2000 + "ls ../../.ssh", cwd=str(home)
        )
        == refused
    )


# ── nothing outlives a call ─────────────────────────────────────────────────────────────────────


def test_what_one_call_read_is_not_kept_for_the_next(home):
    """The same line read twice names the same paths both times."""
    line = "cat ./notes.txt; ls ~/.ssh; cat ./notes.txt"
    first = list(cp.named_paths(line, cwd=str(home)))
    assert first and list(cp.named_paths(line, cwd=str(home))) == first


@pytest.mark.parametrize(
    "line", ["ls ./keys; ls ./keys", "ls ./k*; ls ./k*"], ids=["a-path", "a-glob"]
)
def test_a_folder_that_becomes_a_link_is_read_again_by_the_next_check(home, line):
    """Allowed while `keys` is nothing; refused once it is a link to the credential folder. A glob
    is expanded against the filesystem as it is at each check: nothing matched `k*` at the first,
    so what a part named in one call is no answer for the next."""
    assert security.is_sensitive_bash_command(line, cwd=str(home)) is None
    (home / "keys").symlink_to(home / ".ssh")
    assert (
        security.is_sensitive_bash_command(line, cwd=str(home))
        == security._SHELL_REFUSAL[security.CREDENTIAL]
    )
