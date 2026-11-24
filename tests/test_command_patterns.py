"""A command pattern is read the way a shell reads it, and matches only what it says (#4062).

Property tests over commands built from plain words: a pattern never matches by prefix, never
across an operator, a substitution or a redirect, never through a word the shell would expand,
and the shape ``suggest`` offers always matches the command it was offered for. Then each refusal
rule, each place a wildcard may not stand (a wrapper known by its name wherever it lives and
whatever its version, a builtin that runs code, an option that runs a command), each option a
wildcard may not cover, and the design's examples.
"""

from __future__ import annotations

import shlex

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from personalclaw import command_patterns as P

#: The shell's reserved words, which begin what the reader declines (a case, a loop).
RESERVED = frozenset(
    "if then else elif fi case esac for while until do done in function select time coproc".split()
)
WORD = st.from_regex(r"[a-z][a-z0-9._/-]{0,6}", fullmatch=True).filter(lambda w: w not in RESERVED)
#: A first word that is no wrapper and has no option that runs a command, so a wildcard after it
#: is a pattern's own business.
PROGRAM = WORD.filter(lambda w: not P.wrapper_kind(w) and w not in P.EXEC_OPTIONS)
COMMAND = st.tuples(PROGRAM, st.lists(WORD, min_size=0, max_size=6)).map(lambda t: [t[0], *t[1]])
JOINS = [";", "&&", "||", "|", "&", "\n"]
INSIDE = [
    "$(id)",
    "`id`",
    "$HOME",
    "${X}",
    "> out.txt",
    ">> log",
    "< in.txt",
    "x*",
    "f?",
    "[ab]",
    "~",
    "~root",
    "~/x",
]

#: Each property's own settings, so no profile of the suite's changes.
SETTINGS = settings(deadline=None, max_examples=200)


def _pattern(text: str) -> P.Pattern:
    parsed = P.parse(text)
    assert isinstance(parsed, P.Pattern), parsed
    return parsed


# ── properties ───────────────────────────────────────────────────────────────────────────────


@SETTINGS
@given(COMMAND, st.integers(min_value=1, max_value=6))
def test_a_pattern_without_rest_never_matches_a_command_it_is_a_strict_prefix_of(words, cut):
    cut = min(cut, len(words))
    if cut == len(words):
        words = [*words, "more"]
    assert not P.matches(_pattern(" ".join(words[:cut])), " ".join(words))


@SETTINGS
@given(COMMAND, st.sampled_from(JOINS), COMMAND)
def test_nothing_matches_across_an_operator(first, join, second):
    rest = _pattern(f"{first[0]} **")
    assert P.matches(rest, " ".join(first))
    assert not P.matches(rest, f"{' '.join(first)} {join} {' '.join(second)}")


@SETTINGS
@given(COMMAND, st.sampled_from(INSIDE))
def test_nothing_matches_through_a_substitution_a_redirect_or_an_expanded_word(words, inside):
    assert not P.matches(_pattern(f"{words[0]} **"), f"{' '.join(words)} {inside}")


@SETTINGS
@given(COMMAND)
def test_the_suggestion_matches_the_command_it_was_offered_for(words):
    command = " ".join(words)
    shape = P.suggest(command)
    assert shape, f"no shape offered for {command!r}"
    assert P.matches(P.parse(shape), command)


def test_a_long_command_is_read_in_one_pass() -> None:
    """Linear by construction (one pass over both word lists, no regular expression): a command
    of two thousand words matches a short pattern, and one word over a fixed pattern does not."""
    words = ["pytest", *(f"t{i}.py" for i in range(2000))]
    assert P.matches(_pattern("pytest **"), " ".join(words))
    assert not P.matches(_pattern(" ".join(words[:12])), " ".join(words[:13]))


# ── the refusals ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern,rule",
    [
        ("", P.EMPTY),
        ("   ", P.EMPTY),
        ("git status; rm -rf /", P.NOT_ONE_COMMAND),
        ("make && make install", P.NOT_ONE_COMMAND),
        ("make || true", P.NOT_ONE_COMMAND),
        ("ls | wc -l", P.NOT_ONE_COMMAND),
        ("make\nmake install", P.NOT_ONE_COMMAND),
        ("make > out.txt", P.REDIRECTS),
        ("make 2>&1", P.REDIRECTS),
        ("echo $(id)", P.UNREADABLE),
        ("(make)", P.UNREADABLE),
        ("echo $HOME", P.UNREADABLE),
        ("cat $TMPDIR/x", P.UNREADABLE),
        ("* status", P.WILDCARD_PROGRAM),
        ("** status", P.WILDCARD_PROGRAM),
        ("git ** status", P.REST_NOT_LAST),
        ("git stat*", P.MIXED_WORD),
        ("git ?", P.MIXED_WORD),
        ("x" * (P.MAX_CHARS + 1), P.TOO_LONG),
        (" ".join(["make"] * (P.MAX_WORDS + 1)), P.TOO_LONG),
        ("python **", P.WRAPPER_WILDCARD),
        ("ls ~", P.UNREADABLE),
        ("if **", P.UNREADABLE),
    ],
)
def test_each_refusal_says_its_rule(pattern, rule) -> None:
    refusal = P.parse(pattern)
    assert isinstance(refusal, P.Refusal), refusal
    assert (refusal.rule, refusal.message) == (rule, P.REFUSALS[rule])


def test_every_rule_has_its_case() -> None:
    """Vacuity: the table above covers every rule there is."""
    assert set(P.REFUSALS) == {
        P.EMPTY,
        P.NOT_ONE_COMMAND,
        P.REDIRECTS,
        P.UNREADABLE,
        P.WILDCARD_PROGRAM,
        P.REST_NOT_LAST,
        P.MIXED_WORD,
        P.TOO_LONG,
        P.WRAPPER_WILDCARD,
    }


# ── where a wildcard may not stand ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        # right after a shell, and after its -c
        "bash **",
        "sh *",
        "zsh -c **",
        "bash -c *",
        # after a launcher, and after one whose option leaves its program unknown
        "env **",
        "sudo **",
        "sudo -u bob **",
        "doas *",
        "nohup **",
        "timeout 10 **",
        "xargs **",
        "exec **",
        "command **",
        "nice -n 5 **",
        "env FOO=1 **",
        # after a package runner
        "npx **",
        "pnpm dlx **",
        "bunx *",
        "uvx **",
        "pipx run **",
        "npm exec **",
        # after an interpreter, and its -c, -e, -m or -
        "python **",
        "python3 -m **",
        "node -e **",
        "ruby *",
        "perl -e *",
        "php **",
        "deno **",
        "bun **",
        "python -X dev **",
        # a wrapper launched by another
        "sudo python **",
        "env bash **",
        # a program named by its path, its case or its version
        "/bin/bash **",
        "./bash **",
        "BASH **",
        "/usr/bin/env **",
        "/usr/bin/env bash **",
        "python3.12 **",
        "pypy3 *",
        "ruby3.2 **",
        "perl5.36 **",
        "php8.3 **",
        "zsh-5.9 **",
        "go1.21 run **",
        # builtins and keywords that run code
        "eval **",
        "eval 'echo hi' **",
        "source **",
        ". **",
        "trap *",
        "builtin **",
        "builtin eval **",
        "time **",
        "time bash **",
        "coproc **",
        "! **",
        # interpreters
        "awk **",
        "gawk **",
        "sed -e **",
        "lua **",
        "Rscript **",
        "tclsh **",
        "pwsh **",
        "osascript **",
        "julia **",
        # package runners, and a starter's package
        "uv run **",
        "uv tool run **",
        "poetry run **",
        "pipenv run **",
        "conda run **",
        "bundle exec **",
        "mise exec **",
        "mise exec node@20 **",
        "nix run **",
        "nix run nixpkgs#bash **",
        "yarn dlx **",
        "npm x **",
        "npm init **",
        "go run **",
        "deno eval **",
        # launchers, and those that take a value first
        "watch **",
        "flock /tmp/lock **",
        "setsid **",
        "stdbuf -oL **",
        "ionice **",
        "chrt 5 **",
        "taskset 1 **",
        "chroot /srv **",
        "timeout 10s **",
        "unshare **",
        "nsenter **",
        "su **",
        "runuser **",
        "pkexec **",
        "script **",
        "caffeinate **",
        "arch **",
        "parallel **",
        "strace **",
        "gdb **",
        "sandbox-exec **",
        "systemd-run **",
        # whatever follows them may be code
        "ssh host **",
        "ssh host uptime **",
        "watch ls **",
        # right after an option that runs a command, or in the command it names
        "find . -exec **",
        "find . -exec bash **",
        "git rebase -x **",
        "git rebase --exe **",
        "git submodule foreach **",
        "git bisect run **",
        "make --eval **",
        "tar --to-command **",
        "rsync -e **",
        "vim -c **",
        "nix shell nixpkgs#hello -c **",
        "sed s/a/b/ -e **",
    ],
)
def test_no_wildcard_stands_where_the_program_chooses_the_code(pattern) -> None:
    refusal = P.parse(pattern)
    assert isinstance(refusal, P.Refusal) and refusal.rule == P.WRAPPER_WILDCARD, refusal


@pytest.mark.parametrize(
    "pattern",
    [
        "python -m pytest **",
        "python3 manage.py test **",
        "bash scripts/check.sh **",
        "sudo apt install **",
        "npx eslint **",
        "pnpm dlx create-vite **",
        "nice make **",
        "git commit **",
        "npm run build",
        "timeout 10s make **",
        "npm init vite **",
        "deno run main.ts **",
        "go test **",
        "find **",
        "make **",
        "git rebase -x 'make test' **",
        "python3.12 -m pytest **",
    ],
)
def test_a_wildcard_after_the_program_is_named_is_accepted(pattern) -> None:
    assert isinstance(P.parse(pattern), P.Pattern)


#: Every single word that names a wrapper.
WRAPPER_WORDS = sorted(
    set().union(*(t for k, t in P.WRAPPERS.items() if k != "package runner"))
    | {r[0] for r in P.RUNNERS}
)


@SETTINGS
@given(st.sampled_from(WRAPPER_WORDS), COMMAND)
def test_suggest_never_puts_a_wildcard_at_a_wrapper_position(wrapper, words):
    shape = P.suggest(f"{wrapper} {' '.join(words)}")
    assert shape == "" or not isinstance(P.parse(shape), P.Refusal)
    if shape:
        assert P.parse(shape).words[0] == wrapper


# ── the examples ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command,shape",
    [
        ("pytest tests/x.py -k y", "pytest **"),
        ('git commit -m "fix"', "git commit **"),
        ("npm run build", "npm run build"),
        ("docker compose up -d", "docker compose up **"),
        # a command whose program is not one a card offers `**` after is offered as itself
        ('bash -c "make"', "bash -c make"),
        ("awk '{print $1}' f", "awk '{print $1}' f"),
        ("/bin/bash scripts/x.sh", "/bin/bash scripts/x.sh"),
        ("tar -xf a.tar", "tar -xf a.tar"),
        ("npm install lodash", "npm install lodash"),
        ("docker container run -it x sh", "docker container run -it x sh"),
        ("git config alias.x '!sh'", "git config alias.x '!sh'"),
        # and so is one whose shape would put a wildcard after an option that runs a command
        ("git bisect run ./t.sh", "git bisect run ./t.sh"),
        # a shape a card offers covers no option that runs a command, so it is safe to offer
        ("find . -name '*.py'", "find **"),
        ("git rebase -i main", "git rebase **"),
    ],
)
def test_the_suggestion_examples(command, shape) -> None:
    assert P.suggest(command) == shape


def test_quoting_does_not_change_a_fixed_word() -> None:
    assert P.matches(_pattern("git commit **"), 'git "commit" -a')
    assert P.matches(_pattern("git 'commit' **"), "git commit -a")


def test_a_descriptor_copied_onto_another_still_matches() -> None:
    assert P.matches(_pattern("make **"), "make test 2>&1")
    assert not P.matches(_pattern("make **"), "make test 2>out.txt")


@pytest.mark.parametrize(
    "command", ["", "echo $(id)", "make; ls", "ls *.py", "cat $HOME/x", "ls ~", "rm -rf ~/x"]
)
def test_a_command_no_pattern_could_name_gets_no_suggestion(command) -> None:
    assert P.suggest(command) == ""


# ── what a wildcard may not cover ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern,command",
    [
        ("find **", "find . -exec curl x \\;"),
        ("find **", "find . -execdir sh -c x '{}' +"),
        ("find . *", "find . -okdir"),
        ("git rebase **", "git rebase --exec 'curl x' main"),
        ("git rebase **", "git rebase -x 'curl x' main"),
        ("git rebase **", "git rebase --exe='curl x' main"),
        ("git **", "git -c core.pager=x log"),
        ("git **", "git rebase -x x main"),
        ("git submodule **", "git submodule foreach 'curl x'"),
        ("git bisect **", "git bisect run ./x.sh"),
        ("git clone **", "git clone -u x repo"),
        ("git fetch **", "git fetch --upload-pack=x origin"),
        ("make **", "make --eval=x"),
        ("make **", "make --ev=x"),
        ("make **", "make CC=x"),
        ("make **", "make -f /tmp/x.mk"),
        ("make **", "make -C /tmp/x"),
        ("tar **", "tar --to-command=x -xf a.tar"),
        ("tar **", "tar -I x -xf a.tar"),
        ("tar **", "tar -xIf zstd a.tar"),
        ("tar **", "tar xIf zstd a.tar"),
        ("tar **", "tar --checkpoint-action=exec=x -cf a.tar ."),
        ("rsync **", "rsync -e x a b:"),
        ("rsync **", "rsync -avze x a b:"),
        ("scp **", "scp -o ProxyCommand=x a b:"),
        ("vim **", "vim -c x f"),
        ("vim **", "vim +x f"),
        ("go test **", "go test -exec /tmp/x ./..."),
        ("go build **", "go build -toolexec=/tmp/x"),
        ("cargo test **", "cargo test --config x"),
        ("npm run **", "npm run build --script-shell=/tmp/x"),
        ("rg **", "rg --pre x foo"),
        ("sed s/a/b/ **", "sed s/a/b/ -e 1ex f"),
        ("nix shell nixpkgs#hello **", "nix shell nixpkgs#hello -c x"),
        ("sudo find **", "sudo find . -exec x \\;"),
    ],
)
def test_a_wildcard_never_covers_an_option_that_runs_a_command(pattern, command) -> None:
    """The command is readable, and the pattern that names it word for word matches it; a
    wildcard over the option that runs a command does not."""
    exact = " ".join(shlex.quote(w) for w in shlex.split(command))
    assert P.matches(exact, command), "the row is vacuous: no pattern could name the command"
    assert isinstance(P.parse(pattern), P.Pattern)
    assert not P.matches(pattern, command)


@pytest.mark.parametrize(
    "pattern,command",
    [
        ("git rebase **", "git rebase -i main"),
        ("git **", "git push -u origin main"),
        ("find **", "find . -name '*.py' -type f"),
        ("make **", "make test"),
        ("tar **", "tar -xvf a.tar"),
        ("rsync **", "rsync -avz a b:"),
        ("git rebase -x 'make test' **", "git rebase -x 'make test' main"),
    ],
)
def test_a_wildcard_covers_options_that_run_nothing(pattern, command) -> None:
    assert P.matches(pattern, command)


@pytest.mark.parametrize(
    "pattern,command",
    [("rm -rf *", "rm -rf ~"), ("ls *", "ls ~root"), ("ls **", "ls ~/x"), ("echo **", "echo A=~")],
)
def test_a_wildcard_never_covers_a_home_folder(pattern, command) -> None:
    assert not P.matches(pattern, command)


@pytest.mark.parametrize(
    "pattern,command", [("ls *", "ls '~'"), ("git log *", "git log HEAD~1"), ("ls *", "ls x~")]
)
def test_a_tilde_the_shell_leaves_alone_is_a_word(pattern, command) -> None:
    assert P.matches(pattern, command)


# ── the wrapper rule, as a property ─────────────────────────────────────────────────────────────


def _spellings(words: tuple[str, ...]) -> list[tuple[str, ...]]:
    """A wrapper's words as a command line may spell them: by name, by path, upper-cased and
    versioned (for a name, not for `.` or `!`)."""
    first, rest = words[0], words[1:]
    if not first[0].isalpha():
        return [words]
    return [words, (f"/usr/bin/{first}", *rest), (first.upper(), *rest), (f"{first}3.12", *rest)]


#: One wrapper, in one of its spellings.
WRAPPER = st.one_of(
    st.sampled_from([(w,) for w in WRAPPER_WORDS if P.wrapper_kind(w) != "package runner"]),
    st.sampled_from(sorted(P.RUNNERS)),
).flatmap(lambda words: st.sampled_from(_spellings(words)))
OPTION = st.sampled_from(["-x", "--flag", "-n"])
#: Pattern components: a wrapper, an option, a plain program word, a wildcard.
PART = st.one_of(
    WRAPPER.map(lambda w: ("wrapper", w)),
    OPTION.map(lambda o: ("option", (o,))),
    PROGRAM.map(lambda p: ("program", (p,))),
    st.just(("wild", (P.ONE,))),
)


def _unsafe(parts) -> bool:
    """The rule, said once over components: a wildcard before a fixed word has named what the
    wrappers run is unsafe, and so is one anywhere after a wrapper that runs its words. A plain
    program names it, and so does the word right after a shell or an interpreter: its script."""
    for at, (kind, words) in enumerate(parts):
        if kind == "wild":
            return True
        if kind == "program":
            return False
        if kind == "wrapper":
            wrapper = P.wrapper_kind(words[0])
            if wrapper == "runs its words":
                return any(k == "wild" for k, _ in parts[at + 1 :])
            following = parts[at + 1][0] if at + 1 < len(parts) else ""
            if wrapper in ("shell", "interpreter") and following in ("wrapper", "program"):
                return False  # the script
    return False


@SETTINGS
@given(WRAPPER, st.lists(PART, min_size=0, max_size=5))
def test_no_wildcard_before_what_a_wrapper_runs_is_ever_accepted(first, rest):
    parts = [("wrapper", first), *rest]
    words = [w for _, ws in parts for w in ws]
    if len(words) > P.MAX_WORDS or not _unsafe(parts):
        return
    pattern = " ".join(words)
    assert isinstance(P.parse(pattern), P.Refusal), pattern
