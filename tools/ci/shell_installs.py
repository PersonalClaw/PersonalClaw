"""The installs a ``run:`` script or a Dockerfile ``RUN`` performs, read closely enough to judge.

tools/ci/supply_chain.py's run-install rule asks this module which commands of a script install a
tool or run a download, and whether each names an exact version. The shell is read, never run, so
the reader leans to failing: a command it cannot place is a problem, not a pass. It reads:

- continuations, and a line that ends in ``|``, ``&&`` or ``||``, joined as the shell joins them;
- a ``#`` comment, outside quotes and at the start of a word, cut off;
- every command of a line: after ``;``, ``&&``, ``||``, ``|`` and ``&``, inside ``( )``,
  ``{ }``, ``$( )`` and backticks, and after ``if``, ``then``, ``do`` and the other keywords;
- the wrappers in front of a command (``sudo`` and ``env`` with their options, ``timeout``,
  ``xargs``, ``nice``, ``nohup``, ``time``, ``command``, ``exec``, ``stdbuf``), and the script
  of ``sh -c "<script>"``;
- a heredoc a shell reads, whose body is a script, and one anything else reads, whose body is
  data (kept for the image rule);
- a Dockerfile ``RUN``'s flags, its exec form and its heredocs.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field

_SHELLS = re.compile(r"(?:ba|z|da|k)?sh")
_INTERPRETERS = re.compile(r"(?:ba|z|da|k)?sh|python\d*(?:\.\d+)?|perl|ruby|node")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


@dataclass
class Script:
    """A script read into lines: (line number, logical line) for each shell line, heredoc
    bodies a shell reads included, and (command line, body lines) for each data heredoc."""

    lines: list[tuple[int, str]] = field(default_factory=list)
    data: list[tuple[str, list[str]]] = field(default_factory=list)


def _outside_quotes(line: str) -> list[bool]:
    """For each character of *line*, whether it sits outside quotes."""
    out: list[bool] = []
    quote = ""
    escaped = False
    for char in line:
        out.append(not quote)
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote and char == quote:
            quote = ""
        elif not quote and char in "'\"":
            quote = char
    return out


def strip_comment(line: str) -> str:
    """*line* without its shell comment: a ``#`` outside quotes that starts a word."""
    free = _outside_quotes(line)
    for index, char in enumerate(line):
        if char == "#" and free[index] and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _heredoc_tags(line: str) -> list[tuple[int, str]]:
    """Each heredoc a line opens, outside quotes: (position, terminator)."""
    free = _outside_quotes(line)
    found: list[tuple[int, str]] = []
    for match in _HEREDOC.finditer(line):
        start = match.start()
        if line.startswith("<<<", start) or line[start - 1 : start] == "<":
            continue
        opened = line.rfind("$(", 0, start)
        inside = opened >= 0 and all(line[opened:start].count(q) % 2 == 0 for q in "'\"")
        if free[start] or inside:
            found.append((start, match.group(2)))
    return found


def _reads_a_script(prefix: str) -> bool:
    """Whether the command a heredoc feeds (the text before ``<<``) runs its body as a script:
    a shell with no script file, or nothing (a Dockerfile ``RUN <<EOF``)."""
    prefix = re.sub(r"^\s*RUN\b(?:\s+--\S+)*", "", prefix, flags=re.I)
    command = re.split(r"&&|\|\||;|\||\(", prefix)[-1]
    words = unwrap(_plain_words(command))
    if not words:
        return True
    return bool(_SHELLS.fullmatch(words[0].rsplit("/", 1)[-1])) and not any(
        not w.startswith("-") for w in words[1:]
    )


def read_script(text: str, offset: int = 0) -> Script:
    """*text* as shell lines. Line numbers are *text*'s, plus *offset*."""
    script = Script()
    raw = text.splitlines()
    pending, start, index = "", 0, 0
    while index < len(raw):
        number = index + 1 + offset
        line = strip_comment(raw[index]).strip()
        index += 1
        if not pending and not line:
            continue
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        if re.search(r"(?:\|\||&&|\|)$", line):
            pending += line + " "
            continue
        full, pending = pending + line, ""
        script.lines.append((start, full))
        for position, tag in _heredoc_tags(full):
            body_start = index
            body: list[str] = []
            while index < len(raw) and raw[index].strip() != tag:
                body.append(raw[index])
                index += 1
            index += 1
            if _reads_a_script(full[:position]):
                inner = read_script("\n".join(body), offset + body_start)
                script.lines += inner.lines
                script.data += inner.data
            else:
                script.data.append((full, body))
    if pending:
        script.lines.append((start, pending))
    return script


# ── commands ──────────────────────────────────────────────────────────────────────────────────

#: Words that start a command without being one.
_KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "do",
    "done",
    "while",
    "until",
    "for",
    "case",
    "esac",
    "in",
    "!",
    "{",
    "}",
    "time",
}
#: Wrappers that run the command after them, with the options whose next word is a value.
_WRAPPERS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "--user", "--group"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "exec": {"-a"},
    "command": set(),
    "builtin": set(),
    "nice": {"-n", "--adjustment"},
    "nohup": set(),
    "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-L", "-n", "-P", "-d", "-a", "-E", "-s", "--max-args", "--max-procs"},
}


def _plain_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def unwrap(words: list[str]) -> list[str]:
    """*words* with assignments, keywords and wrappers (and their options) taken off the front,
    so the first word is the program that runs."""
    while words:
        head = words[0]
        if head in _KEYWORDS or re.fullmatch(r"[A-Za-z_]\w*=.*", head):
            words = words[1:]
            continue
        name = head.rsplit("/", 1)[-1]
        if name not in _WRAPPERS:
            return words
        valued = _WRAPPERS[name]
        words = words[1:]
        while words and (words[0].startswith("-") or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
            flag = words[0].split("=", 1)[0]
            words = words[1 + (flag in valued and "=" not in words[0]) :]
        if name == "timeout" and words:
            words = words[1:]  # the duration
    return words


def _substitutions(line: str) -> list[str]:
    """The scripts inside ``$( )``, backticks and ``<( )`` of *line*."""
    found = re.findall(r"`([^`]*)`", line)
    for match in re.finditer(r"[$<]\(", line):
        depth, index = 1, match.end()
        while index < len(line) and depth:
            depth += {"(": 1, ")": -1}.get(line[index], 0)
            index += 1
        found.append(line[match.end() : index - 1])
    return found


def _separators(line: str) -> str:
    """*line* with each command separator outside quotes made a control character: ``\0``
    between commands (``;``, ``&&``, ``||``, ``&``, a newline, and the parens and braces of a
    subshell or group) and ``\1`` between the commands of a pipe. ``${NAME}`` keeps its braces,
    and a redirection's ``&`` (``2>&1``, ``&>``) is not a separator."""
    free = _outside_quotes(line)
    expansion = [False] * len(line)
    for match in re.finditer(r"\$\{[^}]*\}", line):
        for i in range(match.start(), match.end()):
            expansion[i] = True
    out: list[str] = []
    i = 0
    while i < len(line):
        char, pair = line[i], line[i : i + 2]
        if not free[i] or expansion[i]:
            out.append(char)
        elif pair in ("&&", "||"):
            out.append("\0")
            i += 1
        elif char == "|":
            out.append("\1")
        elif char == "&" and line[i - 1 : i] != ">" and line[i + 1 : i + 2] != ">":
            out.append("\0")
        elif char in ";\n(){}":
            out.append("\0")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def pipelines(line: str) -> list[list[list[str]]]:
    """Every pipeline of *line*, as its commands' words, each command unwrapped. A subshell,
    a group, a substitution and a ``sh -c`` script are read as pipelines of their own. An
    ``xargs`` command gets the word ``<stdin>``, since what it runs takes words it cannot see."""
    out: list[list[list[str]]] = []
    for chain in _separators(line).split("\0"):
        commands: list[list[str]] = []
        for part in chain.split("\1"):
            raw = _plain_words(part)
            words = unwrap(raw)
            if words and any(w.rsplit("/", 1)[-1] == "xargs" for w in raw[: len(raw) - len(words)]):
                words = [*words, "<stdin>"]
            if words:
                commands.append(words)
        if commands:
            out.append(commands)
    for inner in _substitutions(line):
        out += pipelines(inner)
    for commands in list(out):
        for words in commands:
            if _SHELLS.fullmatch(words[0].rsplit("/", 1)[-1]) and "-c" in words[1:-1]:
                out += pipelines(words[words.index("-c") + 1])
    return out


# ── judging ───────────────────────────────────────────────────────────────────────────────────

#: Options whose next word is their value, so the value is not read as something to install.
VALUED = set(
    "-c --constraint -i --index-url --extra-index-url -f --find-links -t --target --prefix "
    "--root --python -p --cache-dir -o --output-file --from --workspace -w "
    "--directory --project --spec".split()
)
_VERSION = r"v?\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[\w.]+)?"
PIN = re.compile(rf"[A-Za-z0-9][\w.\-]*(?:\[[\w,.\-]+\])?=={_VERSION}")
NPM_PIN = re.compile(r"(?:@[^/@\s]+/)?[^@\s]+@\d+\.\d+\.\d+")
#: Installers of other ecosystems: the rule does not read their pins, so each is refused.
_OTHER_INSTALLERS = {
    ("go", "install"),
    ("cargo", "install"),
    ("gem", "install"),
    ("pnpm", "add"),
    ("pnpm", "install"),
    ("pnpm", "dlx"),
    ("yarn", "add"),
    ("yarn", "dlx"),
    ("bun", "add"),
    ("bun", "install"),
    ("bunx", ""),
    ("corepack", "enable"),
    ("dotnet", "tool"),
}


def own_project(target: str) -> bool:
    """This repository's own code: a path in the checkout, a built wheel, or the published
    package itself. A URL, a VCS reference or a variable is never this project."""
    if "://" in target or re.match(r"(?:git|hg|svn|bzr)\+", target, flags=re.I):
        return False
    if target.startswith("$"):
        return False
    name = re.split(r"[=<>!~\[@ ]", target, maxsplit=1)[0].lower()
    return target.startswith((".", "/")) or "/" in target or name == "personalclaw"


def operands(words: list[str]) -> tuple[list[str], set[str]]:
    targets: list[str] = []
    flags: set[str] = set()
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            flag = word.split("=", 1)[0]
            flags.add(flag)
            skip = flag in VALUED and "=" not in word
        else:
            targets.append(word)
    return targets, flags


def lock_binaries(lock_text: str) -> set[str]:
    """The packages the root lock installs for a workspace, and the binaries they provide."""
    try:
        data = json.loads(lock_text or "{}")
    except ValueError:
        return set()
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        return set()
    found: set[str] = set()
    for key, entry in packages.items():
        if "node_modules/" in key or not isinstance(entry, dict):
            continue
        names = {**(entry.get("dependencies") or {}), **(entry.get("devDependencies") or {})}
        for name in names:
            locked = packages.get(f"node_modules/{name}")
            if not isinstance(locked, dict):
                continue
            found.add(name)
            bins = locked.get("bin")
            found.update(
                bins if isinstance(bins, dict) else [name.rsplit("/", 1)[-1]] if bins else []
            )
    return found


def _resolve(word: str, values: dict[str, str]) -> str:
    """*word* with each ``$NAME`` or ``${NAME}`` replaced by a literal value it is given, and
    left in place when none is."""
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: values.get(m.group(1), m.group(0)), word)


def _is_pin(target: str, values: dict[str, str]) -> bool:
    return bool(PIN.fullmatch(_resolve(target, values)))


def command_problem(
    words: list[str], binaries: set[str], in_workflow: bool, values: dict[str, str]
) -> str | None:
    """Why one command installs a tool with no exact version, or None. *binaries* is what the
    lock has installed for this job by now; *values* are the literal variables in scope."""
    if not words or words[0] in ("echo", "printf"):
        return None
    head = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if re.fullmatch(r"python\d*(?:\.\d+)?", head) and rest[:2] == ["-m", "pip"]:
        head, rest = "pip", rest[2:]
    if re.fullmatch(r"pip\d*(?:\.\d+)?", head):
        head = "pip"
    if head == "uv" and rest[:1] == ["run"]:
        withs = [rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "--with"]
        withs += [w.split("=", 1)[1] for w in rest if w.startswith("--with=")]
        loose = [w for w in withs if not _is_pin(w, values)]
        if loose:
            return f"uv run --with {', '.join(loose)} names no exact version"
        if "--with-requirements" in " ".join(rest):
            return "uv run --with-requirements reads no hashes"
        tail = rest[1:]
        while tail and tail[0].startswith("-"):
            tail = tail[1 + (tail[0] in VALUED or tail[0] == "--with") :]
        return command_problem(tail, binaries, in_workflow, values) if tail else None
    if head == "uv" and rest[:2] == ["pip", "sync"]:
        return None if "--require-hashes" in rest else "uv pip sync without --require-hashes"
    if head == "uv" and rest[:1] in (["pip"], ["tool"]) and rest[1:2] == ["install"]:
        head, rest = "pip", rest[1:]
    elif (
        head == "uvx"
        or (head == "uv" and rest[:2] == ["tool", "run"])
        or (head == "pipx" and rest[:1] == ["run"])
    ):
        args = rest[2:] if head == "uv" else rest[1:] if head == "pipx" else rest
        targets, _ = operands(args)
        source = next(
            (args[i + 1] for i, w in enumerate(args[:-1]) if w in ("--from", "--spec")), ""
        )
        tool = targets[0] if targets else ""
        exact = (
            _is_pin(source, values)
            or _is_pin(tool, values)
            or re.fullmatch(r"\S+@\d+\.\d+\.\d+", tool)
        )
        return None if exact else f"{head} run of {tool} names no exact version"
    if head in ("pip", "pipx") and rest[:1] == ["install"]:
        targets, flags = operands(rest[1:])
        if flags & {"-r", "--requirement"}:
            return (
                None if "--require-hashes" in flags else "pip install -r without --require-hashes"
            )
        loose = [t for t in targets if not _is_pin(t, values) and not own_project(t)]
        return f"install of {', '.join(loose)} names no exact version" if loose else None
    if head == "npm" and rest[:1] in (["install"], ["i"], ["add"], ["update"]):
        targets, _ = operands(rest[1:])
        if not targets:
            return "npm install rewrites the lock; use npm ci" if in_workflow else None
        loose = [t for t in targets if not NPM_PIN.fullmatch(_resolve(t, values))]
        return f"npm install of {', '.join(loose)} names no exact version" if loose else None
    if head == "npx" or (head == "npm" and rest[:1] in (["exec"], ["x"])):
        args = rest[1:] if head == "npm" else rest
        flag = next((i for i, w in enumerate(args) if w in ("--package", "-p")), None)
        name = args[flag + 1] if flag is not None and flag + 1 < len(args) else ""
        name = name or next((w for w in args if not w.startswith("-")), "")
        if name and not re.search(r"@\d+\.\d+\.\d+$", name) and name not in binaries:
            return (
                f"npx {name} runs a package that is neither pinned nor installed by an `npm ci`"
                " earlier in this job"
            )
    for program, verb in _OTHER_INSTALLERS:
        if head == program and (not verb or rest[:1] == [verb]):
            return f"{program} {verb} installs through a tool this rule does not read".replace(
                "  ", " "
            )
    return None


def download_problems(line: str, own: str | None) -> list[str]:
    """A download a shell or an interpreter runs: piped into it (through ``tee`` or ``cat``
    too), given as ``<(curl ...)``, or as the ``-c`` script or ``eval`` of ``$(curl ...)``.
    *own* is the one URL a step may pipe into ``sh``, or None."""
    found: list[str] = []
    for commands in pipelines(line):
        for at, words in enumerate(commands):
            if words[0].rsplit("/", 1)[-1] not in ("curl", "wget"):
                continue
            urls = [w for w in words if "://" in w]
            for later in commands[at + 1 :]:
                name = later[0].rsplit("/", 1)[-1]
                if name in ("tee", "cat"):
                    continue
                if _INTERPRETERS.fullmatch(name) and not (
                    own is not None and urls == [own] and _SHELLS.fullmatch(name)
                ):
                    found.append(f"runs a download with {name}: {' '.join(urls) or 'a URL'}")
                break
    if re.search(
        r"\b(?:(?:ba|z|da|k)?sh|python\d*|perl|ruby|node)\s+<\(\s*(?:curl|wget)\b"
        r"|\b(?:(?:ba|z|da|k)?sh|python\d*|perl|ruby|node)\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b"
        r"|\beval\s+[\"']?\$\(\s*(?:curl|wget)\b",
        line,
    ):
        found.append("runs a download through a substitution")
    return found


#: The shells, for a caller that unwraps an exec form.
SHELLS = _SHELLS
