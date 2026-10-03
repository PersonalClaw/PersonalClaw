"""Every image stage that builds the web copies every file outside ``web/`` the build reads.

The SPA reaches outside ``web/`` on purpose: data both the dashboard and the backend use lives
in one JSON file under ``src/personalclaw/`` that the web build imports by a relative path. An
image stage that builds the web starts from an empty tree, copies ``web/`` and names every other
file it needs, and ``tsc`` resolves every import of every file under ``web/``. So a cross-tree
import that a stage does not name breaks that image's build with TS2307. Nothing else sees it:
every pull-request check builds the web from a full checkout, and only the image workflows
(``release.yml``, ``docker-single-container.yml``) build these stages. It happened when the
hands-free voice phrases moved into ``src/personalclaw/voice/phrases.json``: both images stopped
building, and both pull-request gates stayed green.

This rail compares the two sides, with no Docker daemon:

* **What the build reads.** Every module specifier in a source file under ``web/`` (static and
  dynamic imports, re-exports, ``require``, CSS ``@import``), resolved as the bundler does,
  followed through any file outside ``web/`` that is itself a module. A read under
  ``node_modules/`` is what the stage's ``npm ci`` installs, so it is not a copy.
* **What a stage provides.** Each COPY before the RUN that builds the web, mapped to where it
  lands in the image, minus what ``.dockerignore`` keeps out of the build context.

Each read must land where its import resolves (beside the copied ``web/``, before the build),
and each file a stage copies for the build must be one the build reads. A COPY no import needs is
dead weight, and holding the stages to it is what keeps this rail honest: a scan that stopped
finding reads would leave every such COPY unread, and fail.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import posixpath
import re
import shlex
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DOCKERFILES = sorted((_REPO / "deploy" / "docker").glob("Dockerfile*"))

_MODULE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_STYLE_SUFFIXES = (".css",)
#: What a specifier with no extension may name, in the order the bundler tries them.
_RESOLVE_SUFFIXES = (*_MODULE_SUFFIXES, ".d.ts", ".json", ".css")
#: A specifier spelled with a script extension names its TypeScript source.
_TS_SOURCE_FOR = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}

#: One JavaScript or TypeScript comment, regular expression literal, or string literal with the
#: keyword that makes it a module specifier. Each is matched whole, so an import written inside a
#: comment or quoted inside a template literal is never read as code, and a quote or a backtick
#: inside a regular expression never opens a string. A `/` opens a regular expression only where
#: an expression can start: after an operator, an opening bracket, a separator or `return`.
_JS_SPECIFIER = re.compile(
    r"""
    (?P<comment>//[^\n]*|/\*.*?\*/)
    |(?:(?<=[(,=:\[!&|?{};>])|(?<=\breturn))\s*/(?![*/])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/
    |(?:(?<![\w$.])(?P<lead>from|import|require)\s*(?P<paren>\(\s*)?)?
     (?:'(?P<single>(?:[^'\\\n]|\\.)*)'|"(?P<double>(?:[^"\\\n]|\\.)*)"|`(?P<template>(?:[^`\\]|\\.)*)`)
    """,
    re.VERBOSE | re.DOTALL,
)
#: ``/// <reference path="…" />``, which is a comment to the scan above.
_TS_REFERENCE = re.compile(
    r"""^[ \t]*///[ \t]*<reference[ \t]+path[ \t]*=[ \t]*(['"])(.+?)\1""", re.M
)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_SPECIFIER = re.compile(
    r"""@(?:import|source|plugin|config|reference)\s+(?:url\(\s*)?"""
    r"""(?:(['"])(?P<quoted>[^'"]+)\1|(?P<bare>[^'"\s);]+))"""
)

#: A RUN that builds the web: the web workspace's `build`, or vite itself.
_WEB_BUILD = re.compile(r"\bnpm\b[^\n]*\brun\s+build\b|\bvite\s+build\b")
_NPM_INSTALL = re.compile(r"\bnpm\s+(?:ci|install)\b")


# ── What the web build reads ────────────────────────────────────────────────────────────────


def _js_specifiers(text: str) -> Iterator[str]:
    for match in _TS_REFERENCE.finditer(text):
        yield match.group(2)
    for match in _JS_SPECIFIER.finditer(text):
        lead, called = match.group("lead"), bool(match.group("paren"))
        if lead is None or (lead == "from" and called) or (lead == "require" and not called):
            continue  # a comment, a string no keyword brings in, `from(…)`, a bare `require`
        template = match.group("template")
        if template is not None and "${" in template:
            continue  # a computed specifier names no one file
        yield next(
            g for g in (match.group("single"), match.group("double"), template) if g is not None
        )


def _css_specifiers(text: str) -> Iterator[str]:
    for match in _CSS_SPECIFIER.finditer(_CSS_COMMENT.sub("", text)):
        yield match.group("quoted") or match.group("bare")


def _resolve(root: Path, importer: str, specifier: str) -> str | None:
    """The repository file a relative *specifier* in *importer* names, or None: a package or a
    builtin, a path outside the repository, or one that names no file (which fails every build,
    not just an image's, so the web job's own typecheck reports it)."""
    path = specifier.split("?", 1)[0].split("#", 1)[0]
    if not (path in (".", "..") or path.startswith(("./", "../"))):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), path))
    if target == ".." or target.startswith("../"):
        return None
    stem, suffix = posixpath.splitext(target)
    candidates = [
        target,
        *(stem + ts for ts in _TS_SOURCE_FOR.get(suffix, ())),
        *(target + s for s in _RESOLVE_SUFFIXES),
        *(f"{target}/index{s}" for s in _RESOLVE_SUFFIXES),
    ]
    return next((c for c in candidates if (root / c).is_file()), None)


def _reads_outside_web(root: Path, sources: Iterable[str]) -> dict[str, set[str]]:
    """Each repository file outside ``web/`` the build reads, with the files that import it.

    *sources* are the files under ``web/``. A file outside ``web/`` that is itself a module or a
    stylesheet is scanned in turn, since what it imports must be in the image too."""
    reads: dict[str, set[str]] = {}
    pending = list(sources)
    scanned: set[str] = set()
    while pending:
        importer = pending.pop()
        if importer in scanned or not importer.endswith(_MODULE_SUFFIXES + _STYLE_SUFFIXES):
            continue
        scanned.add(importer)
        text = (root / importer).read_text(encoding="utf-8", errors="replace")
        found = (
            _css_specifiers(text) if importer.endswith(_STYLE_SUFFIXES) else _js_specifiers(text)
        )
        for specifier in found:
            target = _resolve(root, importer, specifier)
            if target is None or target.startswith("web/") or "node_modules" in target.split("/"):
                continue
            reads.setdefault(target, set()).add(importer)
            pending.append(target)
    return reads


def _web_sources() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "web"],
        cwd=_REPO,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [path for path in listed.split("\0") if path]


# ── What an image stage provides ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class _Copy:
    sources: tuple[str, ...]  # build-context paths; "." is the whole context
    dest: PurePosixPath  # absolute, in the image
    into_directory: bool

    def lands(self, root: Path, path: str) -> PurePosixPath | None:
        """Where this COPY puts the context file *path*, or None when it does not copy it."""
        for source in self.sources:
            if source == ".":
                return self.dest / path
            if any(ch in source for ch in "*?["):
                pattern, parts = source.split("/"), path.split("/")
                if len(pattern) == len(parts) and all(map(fnmatch.fnmatchcase, parts, pattern)):
                    return self.dest / parts[-1]
            elif path == source:
                return self.dest / posixpath.basename(path) if self.into_directory else self.dest
            elif path.startswith(source + "/") and (root / source).is_dir():
                return self.dest / path[len(source) + 1 :]
        return None


@dataclasses.dataclass
class _Stage:
    dockerfile: str
    name: str
    workdir: PurePosixPath
    steps: list[tuple[str, object]]  # ("copy", _Copy) | ("run", command)


def _instructions(text: str) -> Iterator[tuple[str, str]]:
    """``(INSTRUCTION, arguments)`` in order: comments dropped, continued lines joined."""
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        word, _, rest = (pending + line).partition(" ")
        pending = ""
        yield word.upper(), rest.strip()


def _parse_copy(arguments: str, workdir: PurePosixPath) -> _Copy | None:
    words = arguments.split()
    flags = []
    while words and words[0].startswith("--"):
        flags.append(words.pop(0))
    if any(flag.startswith("--from") for flag in flags):
        return None  # from another stage or image, not from the build context
    rest = " ".join(words)
    items = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
    *sources, dest = items
    normalized = tuple(posixpath.normpath(s.lstrip("/")) for s in sources)
    return _Copy(normalized, workdir / dest, dest.endswith("/") or len(sources) > 1)


def _stages(dockerfile: Path) -> list[_Stage]:
    stages: dict[str, _Stage] = {}
    current: _Stage | None = None
    for word, arguments in _instructions(dockerfile.read_text(encoding="utf-8")):
        if word == "FROM":
            parts = [p for p in arguments.split() if not p.startswith("--")]
            name = parts[2] if len(parts) > 2 and parts[1].upper() == "AS" else str(len(stages))
            parent = stages.get(parts[0])  # FROM an earlier stage keeps what that stage has
            current = _Stage(
                dockerfile=dockerfile.relative_to(dockerfile.parents[2]).as_posix(),
                name=name,
                workdir=parent.workdir if parent else PurePosixPath("/"),
                steps=list(parent.steps) if parent else [],
            )
            stages[name] = current
        elif current is None:
            continue  # an ARG before the first FROM
        elif word == "WORKDIR":
            current.workdir = current.workdir / arguments
        elif word in ("COPY", "ADD"):
            copy = _parse_copy(arguments, current.workdir)
            if copy is not None:
                current.steps.append(("copy", copy))
        elif word == "RUN":
            current.steps.append(("run", arguments))
    return list(stages.values())


def _ignore_patterns(root: Path) -> list[tuple[bool, re.Pattern[str]]]:
    """``.dockerignore`` as ``(re-included, pattern)`` pairs, in file order: the last match
    wins."""
    ignore = root / ".dockerignore"
    patterns = []
    for raw in ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        glob = posixpath.normpath(line.lstrip("!").strip().lstrip("/"))
        regex = (
            re.escape(glob)
            .replace(r"\*\*/", "(?:.*/)?")
            .replace(r"\*\*", ".*")
            .replace(r"\*", "[^/]*")
            .replace(r"\?", "[^/]")
        )
        patterns.append((negated, re.compile(regex)))
    return patterns


def _ignored(path: str, patterns: list[tuple[bool, re.Pattern[str]]]) -> bool:
    """Whether the build context leaves *path* out: a pattern matches it or a folder above it."""
    parts = path.split("/")
    prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    excluded = False
    for negated, pattern in patterns:
        if any(pattern.fullmatch(prefix) for prefix in prefixes):
            excluded = not negated
    return excluded


def _stage_problems(
    stage: _Stage,
    reads: dict[str, set[str]],
    root: Path,
    ignore: list[tuple[bool, re.Pattern[str]]],
) -> list[str]:
    """What *stage* gets wrong about the files outside ``web/`` its web build reads."""
    runs = [i for i, (kind, step) in enumerate(stage.steps) if kind == "run"]
    build_at = next((i for i in runs if _WEB_BUILD.search(str(stage.steps[i][1]))), None)
    if build_at is None:
        return []
    install_at = next(
        (i for i in runs if i < build_at and _NPM_INSTALL.search(str(stage.steps[i][1]))), 0
    )
    copies = [step for kind, step in stage.steps[:build_at] if kind == "copy"]
    where = f"{stage.dockerfile} (stage {stage.name})"

    def landings(path: str) -> set[PurePosixPath]:
        if _ignored(path, ignore):
            return set()
        return {at for copy in copies if isinstance(copy, _Copy) and (at := copy.lands(root, path))}

    problems = []
    for path, importers in sorted(reads.items()):
        for importer in sorted(importers):
            homes = landings(importer)
            if not homes:
                problems.append(
                    f"{where}: {importer} imports {path}, and the stage has no {importer}"
                )
                break
            # Where the import resolves in the image: relative to wherever the importer landed.
            relative = posixpath.relpath(path, posixpath.dirname(importer))
            wanted = {
                PurePosixPath(posixpath.normpath(f"{home.parent}/{relative}")) for home in homes
            }
            if not wanted & landings(path):
                excluded = (
                    " (.dockerignore keeps it out of the build context)"
                    if _ignored(path, ignore)
                    else ""
                )
                problems.append(
                    f"{where}: the web build reads {path} ({importer} imports it), but no COPY "
                    f"before the build puts it at {min(wanted)}{excluded}. Add "
                    f"`COPY {path} {posixpath.dirname(path)}/` before the RUN that builds the web."
                )
                break
    for kind, copy in stage.steps[install_at:build_at]:
        if kind != "copy" or not isinstance(copy, _Copy):
            continue
        for source in copy.sources:
            if source == "web" or source.startswith("web/"):
                continue
            if not any(
                _Copy((source,), copy.dest, copy.into_directory).lands(root, p) for p in reads
            ):
                problems.append(
                    f"{where}: COPY {source} brings in a file outside web/ that the web build does "
                    "not read. Remove it."
                )
    return problems


# ── The rail ────────────────────────────────────────────────────────────────────────────────


def _web_build_stages() -> list[_Stage]:
    return [
        stage
        for dockerfile in _DOCKERFILES
        for stage in _stages(dockerfile)
        if any(kind == "run" and _WEB_BUILD.search(str(step)) for kind, step in stage.steps)
    ]


def test_every_dockerfile_that_builds_the_web_is_read_by_this_rail() -> None:
    """The floor: a Dockerfile that builds the web and parses to no web-building stage would
    leave the rail below checking nothing about it."""
    built = {stage.dockerfile for stage in _web_build_stages()}
    building = {
        dockerfile.relative_to(_REPO).as_posix()
        for dockerfile in _DOCKERFILES
        if any(
            _WEB_BUILD.search(line) and not line.lstrip().startswith("#")
            for line in dockerfile.read_text(encoding="utf-8").splitlines()
        )
    }
    assert building, "no Dockerfile under deploy/docker builds the web: retarget this rail"
    assert built == building, f"stages that build the web were not found in {building - built}"


def test_every_image_stage_that_builds_the_web_copies_every_file_the_build_reads() -> None:
    reads = _reads_outside_web(_REPO, _web_sources())
    ignore = _ignore_patterns(_REPO)
    problems = [
        problem
        for stage in _web_build_stages()
        for problem in _stage_problems(stage, reads, _REPO, ignore)
    ]
    assert not problems, "\n".join(problems)


# ── The rail's own controls: each half, driven on a tree made here ──────────────────────────


def _tree(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


_IMPORTER = """\
import {
  one,
  two,
} from '../../shared/multi.json'
export * from "../../shared/reexport.ts"
export type { Shape } from '../../shared/types'
import '../../shared/side.css'
const lazy = () => import('../../shared/lazy.ts')
const legacy = require('../../shared/legacy.js')
import raw from '../../shared/notes.txt?raw'
/// <reference path="../../shared/ambient.d.ts" />
// import commented from '../../shared/commented.json'
/* import blocked from '../../shared/blocked.json' */
const sample = `
import quoted from '../../shared/quoted.json'
`
const plain = '../../shared/plain.json'
const computed = import(`../../shared/${name}.json`)
const items = Array.from('../../shared/plain.json')
const endpoint = /["'`]\\/api\\//
const half = total / 2 / 3
const afterThem = () => import('../../shared/after.json')
import react from 'react'
import local from './local'
import installed from '../../node_modules/pkg/index.js'
"""


def test_the_scan_reads_every_import_form_and_nothing_else(tmp_path: Path) -> None:
    """Every way a module names another is read, through a cross-tree module too; a comment, a
    string, a computed specifier, a package and ``node_modules`` are not reads, and a quote or a
    backtick inside a regular expression does not hide the import after it."""
    shared = [
        "multi.json", "reexport.ts", "types.ts", "side.css", "legacy.js", "notes.txt",
        "ambient.d.ts", "commented.json", "blocked.json", "quoted.json", "plain.json", "after.json",
    ]  # fmt: skip
    _tree(
        tmp_path,
        {
            "web/src/a.ts": _IMPORTER,
            "web/src/local.ts": "",
            "web/src/style.css": (
                "@import '../../shared/sheet.css';\n/* @import '../../x.css'; */\n"
            ),
            "shared/lazy.ts": "import data from '../more/deeper.json'\n",
            "shared/sheet.css": "",
            "more/deeper.json": "{}",
            "x.css": "",
            "node_modules/pkg/index.js": "",
            **{f"shared/{name}": "" for name in shared},
        },
    )
    reads = _reads_outside_web(tmp_path, ["web/src/a.ts", "web/src/local.ts", "web/src/style.css"])
    assert sorted(reads) == [
        "more/deeper.json",
        "shared/after.json",
        "shared/ambient.d.ts",
        "shared/lazy.ts",
        "shared/legacy.js",
        "shared/multi.json",
        "shared/notes.txt",
        "shared/reexport.ts",
        "shared/sheet.css",
        "shared/side.css",
        "shared/types.ts",
    ]
    assert reads["more/deeper.json"] == {"shared/lazy.ts"}


_STAGE = """\
FROM node:20-alpine AS web
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --workspace=web
{copies}
COPY web/ web/
RUN npm run build --workspace=web
{after}
"""


@pytest.mark.parametrize(
    "copies, after, ignore, expected",
    [
        ("COPY shared/data.json shared/", "", "", []),
        ("COPY shared/ shared/", "", "", []),
        ('COPY ["shared/data.json", "shared/"]', "", "", []),
        ("COPY shared/*.json shared/", "", "", []),
        ("", "", "", ["no COPY before the build puts it at /app/shared/data.json"]),
        ("COPY shared/data.json ./", "", "", ["no COPY before the build puts it at /app/shared/"]),
        ("", "COPY shared/data.json shared/", "", ["no COPY before the build puts it at"]),
        ("COPY shared/data.json shared/", "", "shared/*.json", [".dockerignore keeps it out"]),
        ("COPY --from=deps /app/shared/data.json shared/", "", "", ["no COPY before the build"]),
        (
            "COPY shared/data.json shared/\nCOPY shared/unused.json shared/",
            "",
            "",
            ["COPY shared/unused.json brings in a file outside web/ that the web build does not"],
        ),
    ],
    ids=[
        "file", "folder", "json-form", "wildcard", "missing", "wrong-place", "after-the-build",
        "dockerignored", "from-another-stage", "stale",
    ],
)  # fmt: skip
def test_a_stage_is_held_to_what_the_build_reads(
    tmp_path: Path, copies: str, after: str, ignore: str, expected: list[str]
) -> None:
    """The comparison, on a stage made here: a read must land where its import resolves, before
    the build and inside the build context, and a copy outside ``web/`` must be read."""
    _tree(
        tmp_path,
        {
            "web/src/a.ts": "import data from '../../shared/data.json'\n",
            "shared/data.json": "{}",
            "shared/unused.json": "{}",
            ".dockerignore": ignore,
            "deploy/docker/Dockerfile.web": _STAGE.format(copies=copies, after=after),
        },
    )
    (stage,) = _stages(tmp_path / "deploy" / "docker" / "Dockerfile.web")
    reads = _reads_outside_web(tmp_path, ["web/src/a.ts"])
    problems = _stage_problems(stage, reads, tmp_path, _ignore_patterns(tmp_path))
    assert len(problems) == len(expected), problems
    for problem, phrase in zip(problems, expected):
        assert phrase in problem, problem
