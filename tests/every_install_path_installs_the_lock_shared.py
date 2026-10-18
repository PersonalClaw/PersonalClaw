"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_install_path_installs_the_lock.py``

Its checks:
``tests/rails/test_every_install_path_installs_the_lock_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

_ROOT = Path(__file__).resolve().parents[1]


#: Installer spellings: the token that runs pip (a venv's own ``bin/pip`` included) and uv.
_PIP = re.compile(r"(?:^|/)pip3?$")


_UV = re.compile(r"(?:^|/)uv$|^\$\(UV\)$")


#: Options that take a value, so the value is not read as something to install.
_VALUED = set(
    "-c --constraint -i --index-url --extra-index-url -f --find-links -t --target --prefix --root "
    "--python -p --python-version --platform --only-binary --no-binary --root-user-action "
    "--progress-bar --cache-dir --with --from --index --default-index -o --output-file --extra "
    "--group --format --no-emit-package --prune --package".split()
)


#: Commands whose argv is text for a reader, not an install.
_TEXT = {"echo", "printf", "say", "print", "log"}


@dataclass(frozen=True)
class Install:
    path: str
    command: str
    tokens: tuple[str, ...]


def _declared() -> set[str]:
    """Every distribution pyproject declares, in its core list or any extra."""
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    specs = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        specs += extra
    names = {canonicalize_name(re.match(r"[A-Za-z0-9][\w.-]*", s).group(0)) for s in specs}
    return names - {"personalclaw"}


def _logical_lines(text: str) -> list[str]:
    """The file's lines with backslash continuations joined and full-line comments dropped."""
    out: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):  # a comment line, inside a continued RUN too, as Docker reads it
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        out.append(pending + line)
        pending = ""
    if pending:
        out.append(pending)
    return out


def _expand(token: str, variables: dict[str, str]) -> str:
    return re.sub(r"\$\{?([A-Za-z_]\w*)\}?", lambda m: variables.get(m.group(1), m.group(0)), token)


def _commands(path: str, text: str) -> list[Install]:
    """Every install command in *text*: pip, ``uv pip``, ``uv tool``, ``uv sync``, ``uv export``."""
    variables = dict(re.findall(r'^([A-Z_][A-Z0-9_]*)="([^"$]*)"$', text, re.M))
    found: list[Install] = []
    for line in _logical_lines(text):
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = line.split()
        command: list[str] = []
        for token in [*tokens, ";"]:
            if token and set(token) <= set(";&|()"):
                if command:
                    found.extend(_install(path, command, variables))
                command = []
            else:
                command.append(token)
    return found


def _install(path: str, argv: list[str], variables: dict[str, str]) -> list[Install]:
    words = list(argv)
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):  # leading `NAME=value` assignments
        words.pop(0)
    if words[:1] == ["RUN"]:
        words = words[1:]
    if not words or words[0] in _TEXT:
        return []
    for i, word in enumerate(words):
        rest = words[i + 1 :]
        pip = _PIP.search(word) and rest[:1] == ["install"]
        uv = _UV.search(word) and (
            rest[:1] in (["sync"], ["export"])
            or rest[:2] in (["pip", "install"], ["tool", "install"])
        )
        if pip or uv:
            tokens = tuple(_expand(w, variables) for w in words[i:])
            return [Install(path, " ".join(words[i:]), tokens)]
    return []


def _targets(tokens: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """What an install names: its requirement arguments, and the files ``-r`` reads."""
    start = tokens.index("install") + 1
    targets, files = [], []
    it = iter(tokens[start:])
    for token in it:
        if token in ("-r", "--requirement"):
            files.append(next(it, ""))
        elif token in ("-e", "--editable"):
            targets.append(next(it, ""))
        elif token in _VALUED:
            next(it, None)
        elif not token.startswith("-"):
            targets.append(token)
    return targets, files


def _is_the_project(target: str) -> bool:
    if target in (".", "./") or target.startswith((".[", "./[")):
        return True
    name = re.match(r"[A-Za-z0-9][\w.-]*", target)
    return bool(name) and canonicalize_name(name.group(0)) == "personalclaw"


def _from_ranges(install: Install, declared: set[str]) -> str | None:
    """Why *install* resolves pyproject's ranges instead of the lock; ``None`` when it does not."""
    tokens = install.tokens
    verb = tokens[1] if len(tokens) > 1 else ""
    if _UV.search(tokens[0]) and verb in ("sync", "export"):
        if "--locked" in tokens or "--frozen" in tokens:
            return None
        return f"`uv {verb}` without --locked re-resolves the ranges"
    targets, files = _targets(tokens)
    no_deps = "--no-deps" in tokens
    if files and not ("--require-hashes" in tokens and no_deps):
        return "a requirements list installed without --require-hashes --no-deps is not the lock"
    for target in targets:
        if _is_the_project(target) and not no_deps:
            return f"installs {target} with its dependencies, resolved from pyproject's ranges"
        name = re.match(r"[A-Za-z0-9][\w.-]*", target)
        if name and canonicalize_name(name.group(0)) in declared and not _is_the_project(target):
            return f"installs {name.group(0)}, which pyproject declares, from a range"
    return None
