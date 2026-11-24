"""A command shape, such as ``pytest **`` or ``git commit **``, read the way a shell reads it.

Allowing a command shape is safe only if the shape matches exactly what it says. So a pattern is
one shell command, read by the one reader of shell text (:mod:`personalclaw.shell_syntax`): the
program's word, then fixed words, ``*`` (exactly one word) and, only last, ``**`` (zero or more
further words). It never matches by prefix, across ``;``, ``&&``, ``||``, a pipe or a new line, or
through a word the shell would expand differently (a parameter, a glob, a home folder's ``~``).

A wildcard never stands where a word chooses what code runs:

* after a shell, an interpreter, a launcher or a package runner (:data:`WRAPPERS`) before a fixed
  word has named the program it runs, with each program known by its name wherever it lives and
  whatever its version (``/bin/bash``, ``python3.12``);
* after a word that runs everything after it (``eval``, ``ssh``, ``watch``);
* right after an option that runs a command (:data:`EXEC_OPTIONS`: ``find -exec``,
  ``git rebase -x``).

And a wildcard never covers such an option in a command: ``find **`` does not match
``find . -exec curl …``.

* :func:`parse` reads a pattern, or refuses it with one message from a closed table
  (:data:`REFUSALS`).
* :func:`matches` says whether a command is one the pattern names: one linear pass over both word
  lists; no regular expression reads more than one word.
* :func:`suggest` offers the pattern a card shows for a command. For a program in
  :data:`TAIL_PROGRAMS` that is its name, up to two words that look like subcommands, then ``**``
  when more follow; any other command is offered as itself. ``""`` when no pattern names it.

This is the one matcher a grant of a command shape uses (#4067, #4063). Pure: it reads nothing but
its arguments.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from personalclaw.shell_syntax import Simple, Word, duplicates_a_descriptor, lex, simple_commands

#: One word, and the rest.
ONE = "*"
REST = "**"

MAX_CHARS = 200
MAX_WORDS = 12

#: Each rule a pattern can break, and what the owner is told.
EMPTY = "empty"
NOT_ONE_COMMAND = "not_one_command"
REDIRECTS = "redirects"
UNREADABLE = "unreadable"
WILDCARD_PROGRAM = "wildcard_program"
REST_NOT_LAST = "rest_not_last"
MIXED_WORD = "mixed_word"
TOO_LONG = "too_long"
WRAPPER_WILDCARD = "wrapper_wildcard"

REFUSALS: dict[str, str] = {
    EMPTY: "A pattern can't be empty.",
    NOT_ONE_COMMAND: "A pattern is one command: no `;`, `&&`, `||`, `|` or new line.",
    REDIRECTS: "A pattern can't redirect.",
    UNREADABLE: "PersonalClaw can't read that pattern the way a shell would.",
    WILDCARD_PROGRAM: "The program's name can't be a wildcard.",
    REST_NOT_LAST: "`**` can only be the last word.",
    MIXED_WORD: "A word is `*`, `**`, or plain text.",
    TOO_LONG: f"A pattern is at most {MAX_CHARS} characters and {MAX_WORDS} words.",
    WRAPPER_WILDCARD: "A wildcard can't stand where the program chooses what code runs.",
}

#: Shells, and the builtins that run their next word as a script or as code. After one, as after
#: an interpreter, the next word names the code: a script (whose arguments are its own), or one of
#: :data:`CODE_FLAGS` and then the code itself, fixed. An option of unknown arity first leaves the
#: code unknown, so every later wildcard is refused.
SHELLS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "mksh",
        "ash",
        "fish",
        "csh",
        "tcsh",
        "source",
        ".",
        "trap",
    }
)
#: Interpreters, by family: ``python3.12``, ``pypy3`` and ``php8.3`` are read as theirs.
INTERPRETERS = frozenset(
    {
        "python",
        "pypy",
        "node",
        "nodejs",
        "deno",
        "bun",
        "ruby",
        "perl",
        "php",
        "php-cgi",
        "lua",
        "luajit",
        "awk",
        "gawk",
        "mawk",
        "nawk",
        "sed",
        "gsed",
        "rscript",
        "r",
        "julia",
        "tclsh",
        "wish",
        "expect",
        "pwsh",
        "powershell",
        "osascript",
        "groovy",
    }
)
CODE_FLAGS = frozenset({"-c", "-e", "-m", "-"})
#: Launchers: the next word is the program they run. An option or a value before it leaves the
#: program unknown, so every later wildcard is refused (narrow by design: ``sudo -u bob **`` and
#: ``nice -n 5 **`` are refused, ``sudo apt install **`` is not).
LAUNCHERS = frozenset(
    {
        "env",
        "sudo",
        "doas",
        "pkexec",
        "runuser",
        "nohup",
        "exec",
        "command",
        "builtin",
        "time",
        "coproc",
        "!",
        "nice",
        "ionice",
        "xargs",
        "setsid",
        "stdbuf",
        "unshare",
        "nsenter",
        "caffeinate",
        "arch",
        "strace",
        "ltrace",
        "valgrind",
        "systemd-run",
        "sandbox-exec",
        "firejail",
        "bwrap",
        "busybox",
        "toybox",
        "torsocks",
        "proxychains",
        "proxychains4",
        "xvfb-run",
        "dbus-run-session",
    }
)
#: Launchers that take one value before the program: a duration, a folder, a lock, a CPU mask.
LAUNCHERS_AFTER_A_VALUE = frozenset({"timeout", "chroot", "taskset", "flock", "chrt"})
#: Programs that may run any word after them as code, or join them into a shell line: no wildcard
#: stands anywhere after one.
RUNS_ITS_WORDS = frozenset(
    {
        "eval",
        "watch",
        "su",
        "sg",
        "script",
        "parallel",
        "ssh",
        "gdb",
        "lldb",
        "tmux",
        "screen",
        "nix-shell",
    }
)
#: Package runners, as the words that name them (the first by its family, ``go1.21`` is ``go``):
#: the next word names what runs.
RUNNERS = frozenset(
    {
        ("npx",),
        ("bunx",),
        ("uvx",),
        ("pnpx",),
        ("pnpm", "dlx"),
        ("pnpm", "exec"),
        ("npm", "exec"),
        ("npm", "x"),
        ("yarn", "dlx"),
        ("yarn", "exec"),
        ("pipx", "run"),
        ("uv", "run"),
        ("uv", "tool", "run"),
        ("poetry", "run"),
        ("pipenv", "run"),
        ("pdm", "run"),
        ("hatch", "run"),
        ("rye", "run"),
        ("conda", "run"),
        ("mamba", "run"),
        ("micromamba", "run"),
        ("bundle", "exec"),
        ("mise", "exec"),
        ("mise", "x"),
        ("asdf", "exec"),
        ("nix", "run"),
        ("nix", "shell"),
        ("go", "run"),
        ("deno", "run"),
        ("deno", "x"),
        ("deno", "eval"),
        ("bun", "run"),
        ("bun", "x"),
        # A starter's package runs: `npm init vite` is `npm exec create-vite`.
        ("npm", "init"),
        ("npm", "create"),
        ("pnpm", "create"),
        ("yarn", "create"),
        ("bun", "create"),
    }
)
#: Other subcommands that start a program, and how: ``docker run IMAGE COMMAND`` takes the image
#: first; ``kubectl exec`` may run any word after it.
SUBCOMMAND_WRAPPERS: dict[tuple[str, ...], str] = {
    **{
        (*tool, verb): "launcher after a value"  # an image, a container or a service first
        for tool in (
            ("docker",),
            ("podman",),
            ("docker", "container"),
            ("podman", "container"),
            ("docker", "compose"),
            ("podman", "compose"),
            ("docker-compose",),
            ("podman-compose",),
        )
        for verb in ("run", "exec")
    },
    ("kubectl", "run"): "runs its words",
    ("kubectl", "exec"): "runs its words",
    ("kubectl", "debug"): "runs its words",
    ("nix", "develop"): "runs its words",
}
#: Every wrapper of more than one word, and its kind; the longest first, so ``uv tool run`` is
#: read before anything shorter.
_SUBCOMMANDS_LONGEST_FIRST = sorted(
    [*((r, "package runner") for r in RUNNERS), *SUBCOMMAND_WRAPPERS.items()],
    key=lambda pair: len(pair[0]),
    reverse=True,
)
#: The programs with a subcommand that starts another program, and those subcommands' words. No
#: wildcard stands where the subcommand goes (``npm **``), and none covers one (``npm --x y **``
#: does not match ``npm --x y exec …``).
SUBCOMMAND_PROGRAMS: dict[str, frozenset[str]] = {
    first: frozenset(t[-1] for t, _ in _SUBCOMMANDS_LONGEST_FIRST if len(t) > 1 and t[0] == first)
    for first in sorted({t[0] for t, _ in _SUBCOMMANDS_LONGEST_FIRST if len(t) > 1})
}
#: The kinds a single word is (a shell, an interpreter, a launcher…).
_ONE_WORD_KINDS = ("shell", "interpreter", "launcher", "launcher after a value", "runs its words")
#: The table, as one name for the owner's and a reviewer's reading.
WRAPPERS = {
    "shell": SHELLS,
    "interpreter": INTERPRETERS,
    "launcher": LAUNCHERS,
    "launcher after a value": LAUNCHERS_AFTER_A_VALUE,
    "runs its words": RUNS_ITS_WORDS,
    "package runner": frozenset(" ".join(r) for r in RUNNERS),
    "subcommand": frozenset(" ".join(t) for t in SUBCOMMAND_WRAPPERS),
}

_SED = frozenset({"-e", "--expression", "-f", "--file"})
_AWK = frozenset({"-f", "--file", "-e", "--source", "-i", "--include", "-l", "--load"})
_VIM = frozenset({"-c", "--cmd", "-S", "-u", "+"})
_SSH_LIKE = frozenset({"-S", "-o", "-F"})
#: For each program (by name), the options that make it run a command or code the pattern does
#: not name: under ``""`` wherever they stand, or under the subcommand they belong to. A long
#: option counts abbreviated or with ``=value``; a two-character one alone, glued to its value or
#: in a cluster; ``=`` stands for a variable assignment (``make CC=…``) and ``+`` for a
#: ``+command``; any other word counts as itself (``git bisect run``).
EXEC_OPTIONS: dict[str, dict[str, frozenset[str]]] = {
    "find": {"": frozenset({"-exec", "-execdir", "-ok", "-okdir"})},
    "git": {
        "": frozenset(
            {"-c", "--config-env", "--exec-path", "--exec", "--upload-pack", "--receive-pack"}
        ),
        "rebase": frozenset({"-x"}),
        "bisect": frozenset({"run"}),
        "submodule": frozenset({"foreach"}),
        "difftool": frozenset({"-x", "--extcmd"}),
        "grep": frozenset({"-O", "--open-files-in-pager"}),
        "clone": frozenset({"-u", "--template"}),
        "init": frozenset({"--template"}),
        "filter-branch": frozenset(
            {
                "--tree-filter",
                "--index-filter",
                "--msg-filter",
                "--commit-filter",
                "--env-filter",
                "--parent-filter",
                "--tag-name-filter",
            }
        ),
    },
    "make": {
        "": frozenset({"--eval", "-E", "-f", "--file", "--makefile", "-C", "--directory", "="})
    },
    "tar": {
        "": frozenset(
            {
                "--to-command",
                "-I",
                "--use-compress-program",
                "--checkpoint-action",
                "--info-script",
                "--new-volume-script",
                "-F",
                "--rsh-command",
                "--rmt-command",
            }
        )
    },
    "rsync": {"": frozenset({"-e", "--rsh", "--rsync-path"})},
    "scp": {"": _SSH_LIKE},
    "sftp": {"": _SSH_LIKE | {"-D"}},
    "zip": {"": frozenset({"-TT", "--unzip-command"})},
    "rg": {"": frozenset({"--pre"})},
    "go": {"": frozenset({"-exec", "-toolexec", "--exec", "--toolexec"})},
    "cargo": {"": frozenset({"--config"})},
    "npm": {"": frozenset({"--script-shell", "--node-options"})},
    "nix": {"": frozenset({"-c", "--command"})},
    "emacs": {"": frozenset({"--eval", "-l", "--load", "-f", "--funcall", "--script", "-x"})},
    **{name: {"": _SED} for name in ("sed", "gsed")},
    **{name: {"": _AWK} for name in ("awk", "gawk", "mawk", "nawk")},
    **{name: {"": _VIM} for name in ("vim", "vi", "nvim", "view", "ex", "gvim", "mvim", "rvim")},
}
#: Programs whose first argument is a cluster of options even without a dash (``tar xIf …``).
_OLD_STYLE_CLUSTERS = frozenset({"tar"})

#: The programs a card offers ``**`` after: their arguments name no other program to run, or the
#: ways they can are in :data:`EXEC_OPTIONS`, which a match refuses under a wildcard. Any other
#: command is offered as itself, so one Allow on a card never grants more than the owner read.
TAIL_PROGRAMS = frozenset(
    {
        "pytest",
        "git",
        "npm",
        "pnpm",
        "yarn",
        "cargo",
        "go",
        "make",
        "docker",
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "rg",
        "find",
        "diff",
        "du",
        "df",
        "tree",
        "stat",
        "file",
        "mypy",
        "ruff",
        "black",
        "isort",
        "flake8",
        "tsc",
    }
)
#: Their subcommands that install or run code the shape would not name: offered as themselves.
NO_TAIL = frozenset(
    {
        ("git", "config"),
        ("npm", "install"),
        ("npm", "i"),
        ("npm", "add"),
        ("pnpm", "add"),
        ("pnpm", "install"),
        ("pnpm", "i"),
        ("yarn", "add"),
        ("cargo", "install"),
        ("docker", "run"),
        ("docker", "exec"),
    }
)

#: The shell's reserved words that open syntax rather than name a program (``time``, ``coproc``
#: and ``!`` run the command after them, so they are launchers).
RESERVED = frozenset(
    "if then else elif fi case esac for while until do done in function select [[ ]] { }".split()
)

#: A word that looks like a subcommand, which :func:`suggest` keeps fixed.
_SUBCOMMAND = re.compile(r"^[a-z][a-z0-9-]*$")
#: A program's name with a version after it: ``python3.12``, ``pypy3``, ``zsh-5.9``.
_VERSIONED = re.compile(r"([a-z][a-z-]*?)-?\d[\w.-]*")
#: A variable assignment, as ``make`` reads one among its arguments.
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*[:+?!]?=")


@dataclass(frozen=True)
class Pattern:
    """A parsed pattern: its words (fixed text, or :data:`ONE`), and whether :data:`REST` ends it.
    A fixed word is its text as the program receives it."""

    words: tuple[str, ...]
    tail: bool = False
    #: Which of ``words`` are wildcards (a fixed word may itself be the text ``*``, quoted).
    wild: tuple[bool, ...] = ()


@dataclass(frozen=True)
class Refusal:
    """Why a pattern is refused: one of :data:`REFUSALS`' rules, and its message."""

    rule: str
    message: str


def _refuse(rule: str) -> Refusal:
    return Refusal(rule, REFUSALS[rule])


def program_name(word: str) -> str:
    """How *word* names a program wherever it lives: its last path part, lower-cased, without
    ``.exe``; for a package spec, the package (``eslint@9``, ``@scope/tool@1``, ``nixpkgs#bash``).
    """
    name = re.split(r"[/\\#:]", word)[-1].lower().lstrip("@").split("@", 1)[0]
    return name[:-4] if name.endswith(".exe") else name


def _named(name: str, table: frozenset[str]) -> bool:
    """Whether *name* is in *table*, itself or as its family (``python3.12`` is ``python``)."""
    return name in table or _family(name) in table


def _family(name: str) -> str:
    """*name* without a version after it (``go1.21`` is ``go``)."""
    versioned = _VERSIONED.fullmatch(name)
    return versioned.group(1) if versioned else name


def _exec_table(name: str) -> dict[str, frozenset[str]]:
    """*name*'s options that run a command (:data:`EXEC_OPTIONS`), ``{}`` for none."""
    return EXEC_OPTIONS.get(name) or EXEC_OPTIONS.get(_family(name), {})


def _subcommands_of(name: str) -> frozenset[str]:
    """The words of *name*'s subcommands that start a program (:data:`SUBCOMMAND_PROGRAMS`)."""
    return SUBCOMMAND_PROGRAMS.get(name) or SUBCOMMAND_PROGRAMS.get(_family(name), frozenset())


def wrapper_kind(word: str) -> str:
    """The kind of wrapper *word* names as a program (a key of :data:`WRAPPERS`), ``""`` for none.
    A program whose subcommand starts another is named by its first word: ``uv`` is a package
    runner (``uv run``), ``docker`` a subcommand (``docker run``)."""
    name = program_name(word)
    for kind in _ONE_WORD_KINDS:
        if _named(name, WRAPPERS[kind]):
            return kind
    if any(r[0] in (name, _family(name)) for r in RUNNERS):
        return "package runner"
    return "subcommand" if _subcommands_of(name) else ""


def _one_simple(text: str) -> tuple[Simple | None, bool]:
    """The one simple command *text* is, and whether it could be read at all."""
    tokens = lex(text)
    if tokens is None:
        return None, False
    commands = simple_commands(tokens)
    if commands is None or len(commands) != 1 or commands[0].then:
        return None, True
    return commands[0], True


def _unreadable(words: list[Word]) -> bool:
    """Whether a word's text is not what the program receives (an expansion, a home folder's
    ``~``), or the command opens syntax rather than naming a program."""
    return words[0].text in RESERVED or any(w.opaque or w.leading or w.tilde for w in words)


def parse(pattern: str) -> Pattern | Refusal:
    """*pattern* as a :class:`Pattern`, or the :class:`Refusal` of the first rule it breaks."""
    text = pattern.strip()
    if not text:
        return _refuse(EMPTY)
    if len(text) > MAX_CHARS:
        return _refuse(TOO_LONG)
    command, readable = _one_simple(text)
    if not readable:
        return _refuse(UNREADABLE)
    if command is None:
        return _refuse(NOT_ONE_COMMAND)
    if command.redirects:
        return _refuse(REDIRECTS)
    words = command.words
    if not words:
        return _refuse(EMPTY)
    if len(words) > MAX_WORDS:
        return _refuse(TOO_LONG)
    if _unreadable(words):
        return _refuse(UNREADABLE)
    kinds: list[str] = []
    for word in words:
        if word.glob_at < 0:
            kinds.append("fixed")
        elif word.text in (ONE, REST):
            kinds.append(word.text)
        else:
            return _refuse(MIXED_WORD)
    if kinds[0] != "fixed":
        return _refuse(WILDCARD_PROGRAM)
    if REST in kinds[:-1]:
        return _refuse(REST_NOT_LAST)
    fixed_texts = [w.text if k == "fixed" else None for w, k in zip(words, kinds, strict=True)]
    if _wildcard_where_the_program_is_chosen(fixed_texts):
        return _refuse(WRAPPER_WILDCARD)
    tail = kinds[-1] == REST
    body = words[:-1] if tail else words
    return Pattern(
        words=tuple(ONE if k == ONE else w.text for w, k in zip(body, kinds, strict=False)),
        tail=tail,
        wild=tuple(k == ONE for k in kinds[: len(body)]),
    )


def _wrapper_at(words: list[str | None], i: int) -> tuple[str, int]:
    """The kind of wrapper the fixed word at *i* is (``""`` for none), and the index of the word
    after it: past every word of a package runner (``uv tool run``)."""
    first = program_name(words[i] or "")
    for spelling, kind in _SUBCOMMANDS_LONGEST_FIRST:
        spelled = words[i : i + len(spelling)]
        if (
            len(spelled) == len(spelling)
            and spelling[0] in (first, _family(first))
            and all(w == r for w, r in zip(spelled[1:], spelling[1:], strict=True))
        ):
            return kind, i + len(spelling)
    for kind in _ONE_WORD_KINDS:
        if _named(first, WRAPPERS[kind]):
            return kind, i + 1
    return "", i + 1


def _wildcard_at_a_subcommand(words: list[str | None], i: int) -> bool:
    """Whether, for a program at *i* with a subcommand that starts another program, a wildcard
    stands where its subcommand goes: before any fixed word but its options."""
    if not _subcommands_of(program_name(words[i] or "")):
        return False
    for word in words[i + 1 :]:
        if word is None:
            return True
        if not word.startswith("-"):
            return False
    return False


def _wildcard_where_the_program_is_chosen(words: list[str | None]) -> bool:
    """Whether a wildcard (``None``) stands where a word chooses the code that runs."""
    i = 0
    while i < len(words):
        if words[i] is None:
            return True  # the program itself, or the one a wrapper runs
        kind, step = _wrapper_at(words, i)
        if not kind:
            return _wildcard_at_a_subcommand(words, i) or _wildcard_after_an_exec_option(words, i)
        if kind == "runs its words":
            return any(w is None for w in words[step:])
        if step >= len(words):
            return False
        following = words[step]
        if following is None:
            return True
        if kind in ("shell", "interpreter"):
            if following in CODE_FLAGS:
                # The code itself comes next: fixed, or refused.
                return step + 1 < len(words) and words[step + 1] is None
            if following.startswith("-"):
                return any(w is None for w in words[step:])  # an option of unknown arity
            return _wildcard_after_an_exec_option(words, i)  # a script names the code
        if kind == "launcher after a value":
            if following.startswith("-"):
                return any(w is None for w in words[step:])
            step += 1  # the value: a duration, a folder, a lock, a mask
            if step >= len(words):
                return False
            following = words[step]
            if following is None:
                return True
        if following.startswith("-") or "=" in following or following.isdigit():
            return any(w is None for w in words[step:])  # its program is not known
        i = step
    return False


def _takes_the_next_word(word: str, options: frozenset[str]) -> bool:
    """Whether *word* is one of *options* (:data:`EXEC_OPTIONS`) whose value is the next word."""
    for option in options:
        if option in ("=", "+"):
            continue
        if word == option:
            return True
        if option.startswith("--") and len(word) > 3 and option.startswith(word):
            return True  # an abbreviation
        if len(option) == 2 and option[0] == "-" and word[:1] == "-" and word[1:2] != "-":
            if word.endswith(option[1]):
                return True  # the last of a cluster
    return False


def _is_exec_option(word: str, options: frozenset[str]) -> bool:
    """Whether *word* is one of *options* (:data:`EXEC_OPTIONS`), in any spelling it runs as."""
    for option in options:
        if option == "=":
            hit = _ASSIGNMENT.match(word) is not None
        elif option == "+":
            hit = word.startswith("+")
        elif option.startswith("--"):
            name = word.split("=", 1)[0]
            hit = name.startswith("--") and len(name) > 3 and option.startswith(name)
        elif len(option) == 2 and option[0] == "-":
            hit = word[:1] == "-" and word[1:2] != "-" and option[1] in word[1:]
        elif option.startswith("-"):
            hit = word == option or word.startswith(option + "=")
        else:
            hit = word == option
        if hit:
            return True
    return False


class _ExecOptions:
    """The options that run a command, in force as a command's words are read: a program's own
    from where it is named, a subcommand's from where that is."""

    def __init__(self) -> None:
        self.tables: list[dict[str, frozenset[str]]] = []
        self.options: frozenset[str] = frozenset()
        self.cluster_next = False

    def read(self, word: str) -> None:
        name = program_name(word)
        table = _exec_table(name)
        self.cluster_next = name in _OLD_STYLE_CLUSTERS
        self.options = self.options | _subcommands_of(name)
        if table:
            self.tables.append(table)
            self.options = self.options | table.get("", frozenset())
        for named in self.tables:
            if word and word in named:
                self.options = self.options | named[word]

    def runs(self, word: str) -> bool:
        if not self.options:
            return False
        if self.cluster_next and word[:1] != "-" and _is_exec_option("-" + word, self.options):
            return True
        return _is_exec_option(word, self.options)


def _wildcard_after_an_exec_option(words: list[str | None], start: int) -> bool:
    """Whether a wildcard after *start* stands where an option that runs a command takes its
    value, or in the command that value names. The options of every program named before it are
    in force too: ``nix shell nixpkgs#hello -c`` is nix's ``-c``."""
    in_force = _ExecOptions()
    for word in words[:start]:
        in_force.read(word or "")
    for j in range(start, len(words)):
        word = words[j]
        if word is None:
            continue
        if j > start and _takes_the_next_word(word, in_force.options) and j + 1 < len(words):
            value = words[j + 1 :]
            if value[0] is None or _wildcard_where_the_program_is_chosen(value):
                return True
        in_force.read(word)
    return False


def _covers_an_exec_option(texts: list[str], covered: set[int]) -> bool:
    """Whether a word a wildcard covers is an option that runs a command, of a program or a
    subcommand named before it."""
    in_force = _ExecOptions()
    for k, text in enumerate(texts):
        if k in covered and in_force.runs(text):
            return True
        in_force.read(text)
    return False


def _readable_words(command: str) -> list[Word] | None:
    """*command*'s words, when it is one simple command a pattern could name: read by the shell's
    reader, no redirect but a descriptor copied onto another (``2>&1``), and no word the shell
    expands (a parameter, a glob, a home folder's ``~``)."""
    simple, _ = _one_simple(command.strip())
    if simple is None or not simple.words:
        return None
    if not all(duplicates_a_descriptor(r, t) for r, t in simple.redirects):
        return None
    if _unreadable(simple.words) or any(w.glob_at >= 0 for w in simple.words):
        return None
    return simple.words


def matches(pattern: Pattern | str, command: str) -> bool:
    """Whether *command* is one *pattern* names: each fixed word equal as the program receives it,
    each ``*`` one word, ``**`` the rest, nothing left over, and no word a wildcard covers an
    option that runs a command (:data:`EXEC_OPTIONS`). A pattern given as text is parsed first;
    one :func:`parse` refuses matches nothing."""
    parsed = parse(pattern) if isinstance(pattern, str) else pattern
    if isinstance(parsed, Refusal):
        return False
    words = _readable_words(command)
    if words is None:
        return False
    wild = parsed.wild or tuple(w == ONE for w in parsed.words)
    if len(words) < len(parsed.words) or (len(words) > len(parsed.words) and not parsed.tail):
        return False
    for want, is_wild, got in zip(parsed.words, wild, words, strict=False):
        if not is_wild and want != got.text:
            return False
    covered = {k for k, w in enumerate(wild) if w} | set(range(len(parsed.words), len(words)))
    return not _covers_an_exec_option([w.text for w in words], covered)


def _shape(texts: list[str]) -> str:
    """The shape :func:`suggest` would offer for these words, before it is checked."""
    exact = " ".join(shlex.quote(t) for t in texts)
    program = texts[0]
    if "/" in program or program_name(program) not in TAIL_PROGRAMS:
        return exact
    fixed = [program]
    for text in texts[1:3]:
        if not _SUBCOMMAND.match(text):
            break
        fixed.append(text)
    name = program_name(program)
    if len(texts) == len(fixed) or any((name, t) in NO_TAIL for t in fixed[1:]):
        return exact
    return " ".join(shlex.quote(t) for t in fixed) + f" {REST}"


def suggest(command: str) -> str:
    """The pattern a card offers for *command*: for a program in :data:`TAIL_PROGRAMS` found on the
    path, its name, up to two following words that look like subcommands, then ``**`` when more
    words follow; any other command as itself, and so is one whose shape would not match it.
    ``""`` when the command is not one a pattern could name."""
    words = _readable_words(command)
    if words is None:
        return ""
    texts = [w.text for w in words]
    exact = " ".join(shlex.quote(t) for t in texts)
    for shape in dict.fromkeys((_shape(texts), exact)):
        parsed = parse(shape)
        if isinstance(parsed, Pattern) and matches(parsed, command):
            return shape
    return ""
