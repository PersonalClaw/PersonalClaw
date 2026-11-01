"""``client``, ``package``, ``audit``, ``web-unit`` and ``web-build``, and ``desktop-unit`` and
``mobile-unit`` once the web tests split, declare the paths they read as their inputs (#3945).

Each took the whole tree, so every change planned it. Each now lists what it reads, and this rail
holds the list to a census of those reads, so a docs-only change plans none of them while a
change to what one reads still plans it:

- **client**: the folder its steps name (``packages/personalclaw-client-py``). No file in it may
  climb out of it (a ``..`` or a ``parents[k]`` above the folder) or name a repository path
  outside it.
- **package**: what setuptools reads (its config files, the README and the licence files, the
  folders ``packages.find`` searches, what ``MANIFEST.in`` grafts) and every path its step scripts
  name, followed into each script they name in turn.
- **audit**: the graphs ``tools/ci/audit.py`` audits, read from the module itself: ``uv.lock``,
  each npm lock ``npm_graphs`` finds with the ``package.json`` beside it, the root's workspaces'
  manifests, and its waivers.
- **web-unit** and **web-build** (and **desktop-unit** and **mobile-unit**): the npm workspaces
  their ``npm run`` steps run in (read from the root's scripts), every path the workspaces' code
  names outside them, followed into each script it names, and what their step scripts read.

The web, desktop and mobile tests are one job, ``web-unit``, or three, each planned by what its
own workspace reads (#5810). The split lands in two merges (#4584 step 4), this rail first, so it
reads the shape the catalogue holds: its jobs, and the entries the planted drops take out.

Each job also reads what its toolchain reads at the root: uv its settings (``pyproject.toml``,
``.python-version``) and Node its version (``.nvmrc``, the setup action's ``node-version-file``).

A path a census finds that the tree does not track is read through what it is built from: a
folder of one of the root's npm workspaces (``web/dist``) through the workspace and the root's npm
manifests, any other through the tracked folder above it.

A Python file names a path with a string literal whose first part is a tracked top-level entry, or
with a literal run joined onto a base (``root / "web" / "dist"``, ``os.path.join(base, "web")``),
read from its first part that is one. A bare top-level name (``"docs"``) is a read of that folder
only when it is joined or assigned to a name, so a dictionary key like ``"scripts"`` is not one.

A workspace's code (its scripts and its TypeScript and npm configs; prose and fixtures name paths
they do not read) names a path, outside its comment lines, with a relative literal read from its
folder, a literal path whose first part is a tracked top-level entry, a path call's literal run
read from the base it joins onto (the file's folder, the workspace a step runs it in, a name the
file binds to one of those; on a base the census cannot read, a run that spells a path below a
top-level entry), or a top-level name it assigns to a constant. A template literal names the
folder its text before ``${}`` is in. A JavaScript script it names is followed, since a test that
names one imports or evaluates it; a Python file only when the code runs it (it is inside a
child's argv), since a Python file a web test names is a twin it points at or a source it reads.

The planted controls drop an entry from each list and plant a read the list does not reach.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import posixpath
import re
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from job_reads import (
    forget,
    literal,
    python_names,
    python_reads,
    top_entries,
    tracked_dirs,
    tracked_paths,
)
from rails_repo import REPO

from tools.ci import audit, catalogue
from tools.ci import plan as planner

_CAT = catalogue.read(REPO)
#: Whether the catalogue holds the web tests split in three (test_ci_plan_tree holds it to both of
#: the split's jobs or neither).
_SPLIT = {"desktop-unit", "mobile-unit"} <= {j.id for j in _CAT.jobs}
_JOBS = (
    "client",
    "package",
    "audit",
    "web-unit",
    *(("desktop-unit", "mobile-unit") if _SPLIT else ()),
    "web-build",
)

#: What each toolchain word reads at the root: uv its settings, for every command it runs there,
#: and the setup action Node's version (``node-version-file``).
_TOOLCHAIN_READS = {
    "uv": ("pyproject.toml", "uv.toml", ".python-version"),
    "uv-sync": ("pyproject.toml", "uv.toml", ".python-version"),
    "node": (".nvmrc",),
    "npm-ci": ("package.json", "package-lock.json"),
}

#: setuptools' build config, read when present at the root.
_BACKEND_CONFIG = ("pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in")

#: The licence files setuptools ships when ``license-files`` is not set.
_DEFAULT_LICENSE_FILES = ("LICEN[CS]E*", "COPYING*", "NOTICE*", "AUTHORS*")


# ── each job's census ────────────────────────────────────────────────────────────────────────


def _toolchain_reads(root: Path, job: catalogue.Job) -> dict[str, str]:
    tracked = tracked_paths(root)
    return {
        name: f"its toolchain's {word} reads it"
        for word in job.toolchain
        for name in _TOOLCHAIN_READS.get(word, ())
        if name in tracked
    }


def _step_paths(job: catalogue.Job, root: Path) -> set[str]:
    """Each argument of the job's steps that is a tracked path, extras (``[dev]``) cut off."""
    tracked = tracked_paths(root)
    known = tracked | tracked_dirs(root)
    words = (w.split("[")[0].rstrip("/") for step in job.command for w in step.argv)
    return {w for w in words if w in known}


def client_reads(root: Path, job: catalogue.Job) -> dict[str, str]:
    found = {p: "a step names it" for p in _step_paths(job, root)}
    folders = [p for p in found if p in tracked_dirs(root)]
    tops = top_entries(tracked_paths(root))
    for folder in folders:
        for rel in sorted(p for p in tracked_paths(root) if p.startswith(folder + "/")):
            if not rel.endswith(".py"):
                continue
            depth = rel.count("/") - folder.count("/") - 1
            for climb in _climbs(root, rel, depth):
                found[climb] = f"{rel} climbs out of {folder}"
            for path in python_names(root, rel, tops):
                if not path.startswith(folder + "/"):
                    found[path] = f"named by {rel}"
    return found | _toolchain_reads(root, job)


def _climbs(root: Path, rel: str, depth: int) -> list[str]:
    """Each way ``rel`` reaches above its folder: a ``..`` literal, or ``parents[k]`` with ``k``
    past its depth in the folder (``parents[depth]`` is the folder itself). Reported as the
    repository root, which no input reaches but the whole tree."""
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        text = literal(node)
        if text is not None and (text == ".." or text.startswith("../")):
            out.append(".")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            and node.slice.value > depth
        ):
            out.append(".")
    return out


def _manifest_in(root: Path) -> set[str]:
    """The paths ``MANIFEST.in`` adds: what ``include``, ``graft`` and ``recursive-include``
    name (an exclude or prune adds nothing)."""
    path = root / "MANIFEST.in"
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split("#")[0].split()
        if not words:
            continue
        if words[0] in ("include", "graft"):
            out.update(w.rstrip("/") for w in words[1:])
        elif words[0] == "recursive-include" and len(words) > 1:
            out.add(words[1].rstrip("/"))
    return out


def package_reads(root: Path, job: catalogue.Job) -> dict[str, str]:
    tracked = tracked_paths(root)
    found = {n: "setuptools reads it" for n in _BACKEND_CONFIG if n in tracked}
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    meta = project.get("project", {})
    readme = meta.get("readme")
    if isinstance(readme, dict):
        readme = readme.get("file")
    if isinstance(readme, str):
        found[readme] = "the project's readme"
    globs = meta.get("license-files") or _DEFAULT_LICENSE_FILES
    for name in sorted(t for t in tracked if "/" not in t):
        if any(fnmatch.fnmatchcase(name, g) for g in globs):
            found[name] = "a licence file setuptools ships"
    find = project.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    for where in find.get("where", ["."]):
        found[where.rstrip("/")] = "packages.find searches it"
    for path in _manifest_in(root):
        found[path] = "MANIFEST.in adds it"
    scripts = sorted(p for p in _step_paths(job, root) if p.endswith(".py"))
    for path, why in python_reads(root, ["setup.py", *scripts]).items():
        found.setdefault(path, why)
    return found | _toolchain_reads(root, job)


def audit_reads(root: Path, job: catalogue.Job) -> dict[str, str]:
    found = {"uv.lock": "uv export --locked reads it"}
    found[audit.BASELINE.resolve().relative_to(REPO).as_posix()] = "the waivers"
    found[Path(audit.__file__).resolve().relative_to(REPO).as_posix()] = "the audit's own code"
    for _graph, lock in audit.npm_graphs(root):
        found[lock] = "npm audit reads it"
        found[str(Path(lock).with_name("package.json"))] = "npm audit reads it beside its lock"
    for workspace in _workspaces(root):
        found[f"{workspace}/package.json"] = "a workspace of the root's lock"
    return found | _toolchain_reads(root, job)


def _workspaces(root: Path) -> list[str]:
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    return [w.rstrip("/") for w in manifest.get("workspaces", [])]


# ── what a workspace's code names ────────────────────────────────────────────────────────────

#: The files of a workspace its steps compile, run or load: code, and the TypeScript and npm
#: configs. Prose, fixtures and snapshots name paths they do not read.
_JS_CODE = re.compile(r"(\.(ts|tsx|js|mjs|cjs|jsx)|/tsconfig[^/]*\.json|/package\.json)$")
_JS_LITERAL = re.compile(r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`""")
_JS_PATH_CALL = re.compile(
    r"\b(?:join|resolve|URL|readdirSync|readFileSync|existsSync|statSync|readdir|readFile"
    r"|globSync|glob|opendirSync)\s*\("
)
_JS_ASSIGNED = re.compile(
    r"""\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*(?::[^=\n]+)?=\s*(['"`])([^'"`\n$]+)\1"""
)
_STEP_WORKSPACE = re.compile(r"--workspace[= ]([\w./-]+)")
#: A line that is a comment through and through (``//``, ``/*`` or a block comment's ``*``): a
#: path it names is prose, a twin a comment points at, never a read.
_JS_COMMENT_LINE = re.compile(r"^[ \t]*(?://|/\*|\*).*$", re.MULTILINE)
_JS_SCRIPT = re.compile(r"\.(ts|tsx|js|mjs|cjs|jsx)$")
#: Where code runs or loads another file: a child it starts, a module it imports or requires.
_JS_RUN_CALL = re.compile(
    r"\b(?:spawnSync|spawn|execFileSync|execFile|execSync|exec|fork|require|import)\s*\("
)
_JS_IMPORT = re.compile(r"""\b(?:import|export)\b[^;'"`]*?\bfrom\s*(?=['"])|\bimport\s*(?=['"])""")


def _js_text(match: re.Match[str]) -> str:
    return next(g for g in match.groups() if g is not None)


def _js_path(text: str) -> tuple[str, bool]:
    """The path a literal spells, and whether it stops at a folder: a template's text before
    its first ``${}`` names the folder it is in; a query, a fragment or a glob ends a path."""
    folder = "${" in text
    if folder:
        text = text.split("${", 1)[0].rsplit("/", 1)[0] + "/"
    cut = re.split(r"[?#*]", text, maxsplit=1)
    return cut[0].rstrip("/"), folder or len(cut) > 1


def _js_args(source: str, start: int) -> list[str]:
    """A call's arguments from just past its ``(``, split at its own commas."""
    depth, args, current, i = 0, [], "", start
    while i < len(source) and i - start < 800:
        char = source[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return [*args, current.strip()]
            depth -= 1
        elif char == "," and depth == 0:
            args.append(current.strip())
            current, i = "", i + 1
            continue
        elif char in "'\"`":
            literal = _JS_LITERAL.match(source, i)
            if literal:
                current, i = current + literal.group(0), literal.end()
                continue
        current, i = current + char, i + 1
    return args


def _normal(path: str) -> str | None:
    path = posixpath.normpath(path)
    return None if path in (".", "") or path.startswith("..") else path


_JS_HERE = ("__dirname", "import.meta.dirname", "dirname(fileURLToPath(import.meta.url))")
_JS_NAMED = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*")
_JS_JOIN = re.compile(r"(?:path\.)?(?:join|resolve)\s*\(")


def _js_folder(expr: str, here: str, own: str, known: Mapping[str, str]) -> str | None:
    """The repository folder ``expr`` names ("" for the root), when the census can read it: the
    file's folder, the workspace a step runs it in (``process.cwd()``), a name bound to one, or a
    join of literals onto one of those. None when it cannot."""
    expr = expr.strip().removeprefix("path.")
    if any(expr.startswith(h) for h in _JS_HERE):
        return here
    if expr.startswith("process.cwd()"):
        return own
    if expr in known:
        return known[expr]
    join = _JS_JOIN.match(expr)
    if not join:
        return None
    args = _js_args(expr, join.end())
    if not args:
        return None
    base = _js_folder(args[0], here, own, known)
    parts = []
    for arg in args[1:]:
        literal = _JS_LITERAL.fullmatch(arg)
        if not literal:
            return None
        parts.append(_js_text(literal))
    if base is None:
        return None
    joined = posixpath.normpath(posixpath.join(base or ".", *parts))
    return None if joined.startswith("..") else ("" if joined == "." else joined)


def _js_bases(source: str, here: str, own: str) -> dict[str, str]:
    """Each name the file binds to a folder the census can read, in the order it binds them. A
    name bound twice to different folders is no base."""
    known: dict[str, str] = {}
    unsure: set[str] = set()
    for match in _JS_NAMED.finditer(source):
        name = match.group(1)
        folder = _js_folder(source[match.end() : match.end() + 300], here, own, known)
        if folder is None:
            continue
        if name in known and known[name] != folder:
            unsure.add(name)
        known[name] = folder
    return {k: v for k, v in known.items() if k not in unsure}


def js_names(root: Path, rel: str, tops: frozenset[str]) -> dict[str, bool]:
    """Each repository path outside its own workspace that the code file ``rel`` names outside
    its comment lines, and whether ``rel`` runs or loads it (it is inside a child's argv, an
    import or a require): a
    relative literal (``'../src/x.json'``) read from its folder; a literal path whose first part
    is a tracked top-level entry; a path call's literal run (``join(__dirname, '..', 'docs',
    'x.md')``, ``join(process.cwd(), '..', 'CHANGELOG.md')``, ``join(REPO, 'docs', name)``),
    from its folder, from the workspace a step runs it in, or from its first part that is a
    top-level entry; and a top-level name assigned to a constant (``const D = 'docs'``)."""
    source = _JS_COMMENT_LINE.sub(
        lambda m: " " * len(m.group(0)),
        (root / rel).read_text(encoding="utf-8", errors="replace"),
    )
    here, own = posixpath.dirname(rel), rel.split("/")[0]
    runs: list[tuple[int, int]] = []
    for match in _JS_RUN_CALL.finditer(source):
        runs.append((match.end(), match.end() + len(",".join(_js_args(source, match.end())))))
    for match in _JS_IMPORT.finditer(source):
        literal = _JS_LITERAL.match(source, match.end())
        if literal:
            runs.append((literal.start(), literal.end()))
    bases = _js_bases(source, here, own)
    found: dict[str, bool] = {}

    def note(path: str | None, at: int) -> None:
        if path and path.split("/")[0] in tops and path.split("/")[0] != own:
            found[path] = found.get(path, False) or any(a <= at < b for a, b in runs)

    for match in _JS_LITERAL.finditer(source):
        text = _js_text(match)
        path, _ = _js_path(text)
        if text.startswith(("./", "../")):
            note(_normal(posixpath.join(here, path)), match.start())
        elif "/" in path and " " not in path and "://" not in text:
            note(_normal(path), match.start())
    for match in _JS_ASSIGNED.finditer(source):
        if match.group(2) in tops:
            note(match.group(2), match.start())
    for match in _JS_PATH_CALL.finditer(source):
        args = _js_args(source, match.end())
        if not args:
            continue
        base = None if _JS_LITERAL.fullmatch(args[0]) else _js_folder(args[0], here, own, bases)
        if not _JS_LITERAL.fullmatch(args[0]):
            args = args[1:]  # the base; one the census cannot read leaves the run to name its top
        parts: list[str] = []
        for arg in args:
            literal = _JS_LITERAL.fullmatch(arg)
            if not literal:
                break
            path, stops = _js_path(_js_text(literal))
            parts.append(path)
            if stops:
                break
        if not parts:
            continue
        if base is None:
            # A base the census cannot read is most often a folder the test made: a lone
            # top-level name joined onto it (join(root, 'scripts')) names that folder's copy.
            spelled = "/".join(parts)
            if spelled.split("/")[0] not in tops or "/" not in spelled:
                continue
            base = ""
        note(_normal(posixpath.join(base or ".", *parts)), match.start())
    return found


def _step_workspaces(root: Path, job: catalogue.Job) -> list[str]:
    """The workspaces the job's ``npm run`` steps run in, read from the root's scripts."""
    scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    out: list[str] = []
    for step in job.command:
        argv = step.argv
        if argv[:2] == ("npm", "run") and len(argv) > 2:
            for workspace in _STEP_WORKSPACE.findall(scripts.get(argv[2], "")):
                if workspace not in out:
                    out.append(workspace)
    return out


def web_reads(root: Path, job: catalogue.Job) -> dict[str, str]:
    """The workspaces the job's steps run in, every path their code names, followed into each
    script it names, and what its step scripts and toolchain read. A JavaScript script is followed
    whenever it is named, since a test that names one imports or evaluates it; a Python file only
    when the code runs it (it is inside a child's argv), since a Python file a web test names is
    a twin whose text it reads or a source it holds itself to."""
    tracked = tracked_paths(root)
    tops = top_entries(tracked)
    workspaces = _step_workspaces(root, job)
    found = {w: "a step runs in it" for w in workspaces}
    if workspaces:
        # npm resolves --workspace from every workspace's manifest, read on each npm run.
        for manifest in (f"{w}/package.json" for w in _workspaces(root)):
            if manifest in tracked:
                found.setdefault(manifest, "npm reads every workspace's manifest")
    todo = sorted(p for p in tracked if p.split("/")[0] in workspaces and _JS_CODE.search("/" + p))
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if rel.endswith(".py"):
            names = {path: False for path in python_names(root, rel, tops)}
        else:
            names = js_names(root, rel, tops)
        for path, run in sorted(names.items()):
            if path.split("/")[0] in workspaces:
                continue
            if path in tracked or path in tracked_dirs(root):
                found.setdefault(path, f"{'run' if run else 'named'} by {rel}")
            runs_python = path.endswith(".py") and (run or rel.endswith(".py"))
            if path in tracked and (_JS_SCRIPT.search(path) or runs_python):
                todo.append(path)
    scripts = sorted(p for p in _step_paths(job, root) if p.endswith(".py"))
    for path, why in python_reads(root, scripts).items():
        found.setdefault(path, why)
    return found | _toolchain_reads(root, job)


_CENSUS = {
    "client": client_reads,
    "package": package_reads,
    "audit": audit_reads,
    "web-unit": web_reads,
    "desktop-unit": web_reads,
    "mobile-unit": web_reads,
    "web-build": web_reads,
}


# ── what the inputs must reach ───────────────────────────────────────────────────────────────


def _through(root: Path, path: str) -> list[str]:
    """What an untracked path is read through: a workspace folder with the root's npm
    manifests, else the tracked folder above it. The root itself is reached by no input."""
    tracked = tracked_paths(root)
    if path == "." or path in tracked or path in tracked_dirs(root):
        return [path]
    top = path.split("/")[0]
    if top in _workspaces(root):
        return [top, "package.json", "package-lock.json"]
    parts = path.split("/")
    while parts:
        parts.pop()
        if "/".join(parts) in tracked_dirs(root):
            return ["/".join(parts)]
    return []


def _reached(inputs: tuple[str, ...], path: str, root: Path) -> bool:
    if path == ".":
        return any(planner.whole_tree(s) for s in inputs)
    if path in tracked_dirs(root):
        return planner.reaches(inputs, path + "/x")
    return planner.reaches(inputs, path)


def uncovered(inputs: tuple[str, ...], reads: Mapping[str, str], root: Path = REPO) -> list[str]:
    """Each path of ``reads`` that ``inputs`` do not reach, with why it is read. A folder
    counts as reached only when an arbitrary file under it is."""
    out = []
    for path, why in sorted(reads.items()):
        for need in _through(root, path):
            if not _reached(inputs, need, root):
                out.append(f"{need} ({why})" if need == path else f"{need} ({path}: {why})")
    return out


@pytest.mark.parametrize("job_id", _JOBS)
def test_each_jobs_inputs_reach_every_path_it_reads(job_id: str) -> None:
    job = _CAT.job(job_id)
    missing = uncovered(job.inputs, _CENSUS[job_id](REPO, job))
    assert not missing, (
        f"{job_id}'s inputs (tools/ci/catalogue.toml) miss paths it reads; name each:\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.parametrize("job_id", _JOBS)
def test_each_jobs_inputs_are_not_the_whole_tree(job_id: str) -> None:
    """A whole-tree input names no path, so every change would plan the job again."""
    assert not any(planner.whole_tree(s) for s in _CAT.job(job_id).inputs), job_id


def test_the_census_is_not_vacuous() -> None:
    package = package_reads(REPO, _CAT.job("package"))
    for path in ("src", "web/dist", "README.md", "LICENSE", "scripts/backend_bundle_manifest.py"):
        assert path in package, path
    reads = audit_reads(REPO, _CAT.job("audit"))
    assert "package-lock.json" in reads and "deploy/docker/agent-clis/package-lock.json" in reads
    assert "packages/personalclaw-client-py" in client_reads(REPO, _CAT.job("client"))


def test_the_wheel_check_loads_the_manifest_script_by_its_path() -> None:
    """No import names backend_bundle_manifest.py: verify_wheel.py loads it by path, so the
    planner's step reading (a script and the siblings it imports) misses it and the census, which
    reads the joined path, is what lists it."""
    tree = ast.parse((REPO / "scripts" / "verify_wheel.py").read_text(encoding="utf-8"))
    loads = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "spec_from_file_location"
    ]
    assert loads, "verify_wheel.py loads no script by path any more: drop the entry"
    named = python_names(REPO, "scripts/verify_wheel.py", top_entries(tracked_paths(REPO)))
    assert "scripts/backend_bundle_manifest.py" in named, sorted(named)


def test_a_docs_only_change_plans_none_of_them() -> None:
    """A docs page no job reads plans none of them; the docs the web tests read still plan
    the web jobs."""
    areas = catalogue.read_areas(REPO, _CAT)
    for tier in ("change", "merge"):
        p = planner.plan(_CAT, tier, ["docs/maintainers/ci.md"], overrides=areas, root=REPO)
        assert not p.full, p.full_reasons
        assert not set(_JOBS) & set(p.jobs), (
            tier,
            p.jobs,
        )


# ── the planted controls ─────────────────────────────────────────────────────────────────────


#: The drops for the web tests as one job: web-unit reads the mobile tests and the desktop's home.
_ONE_JOB_DROPS = [
    ("web-unit", "mobile/", "mobile"),
    ("web-unit", "scripts/lib/named_home.mjs", "scripts/lib/named_home.mjs"),
]
#: The drops for the web tests split in three: each job reads its own workspace and what it names.
_SPLIT_DROPS = [
    ("web-unit", "mobile/package.json", "mobile/package.json"),
    ("desktop-unit", "desktop/", "desktop"),
    ("desktop-unit", "tools/ci/", "tools/ci"),
    ("desktop-unit", "web/src/", "web/src"),
    ("desktop-unit", "scripts/lib/named_home.mjs", "scripts/lib/named_home.mjs"),
    ("desktop-unit", "docs/brand/marks.json", "docs/brand/marks.json"),
    ("desktop-unit", "web/package.json", "web/package.json"),
    ("mobile-unit", "mobile/", "mobile"),
    ("mobile-unit", "desktop/package.json", "desktop/package.json"),
    ("mobile-unit", ".nvmrc", ".nvmrc"),
]


def _without(job_id: str, dropped: str) -> tuple[str, ...]:
    inputs = _CAT.job(job_id).inputs
    assert dropped in inputs, (job_id, dropped)
    return tuple(s for s in inputs if s != dropped)


@pytest.mark.parametrize(
    "job_id, dropped, named",
    [
        ("client", "packages/personalclaw-client-py/", "packages/personalclaw-client-py"),
        ("client", ".python-version", ".python-version"),
        ("package", "src/", "src"),
        ("package", "web/", "web"),
        ("package", "package-lock.json", "package-lock.json"),
        ("package", ":(glob)LICENSE*", "LICENSE"),
        ("package", "MANIFEST.in", "MANIFEST.in"),
        ("package", "scripts/backend_bundle_manifest.py", "scripts/backend_bundle_manifest.py"),
        ("audit", "uv.lock", "uv.lock"),
        ("audit", ":(glob)**/package-lock.json", "deploy/docker/agent-clis/package-lock.json"),
        ("audit", ":(glob)**/package.json", "web/package.json"),
        ("audit", "tools/ci/audit-baseline.toml", "tools/ci/audit-baseline.toml"),
        ("audit", ".nvmrc", ".nvmrc"),
        ("web-unit", "web/", "web"),
        ("web-unit", "src/", "src"),
        ("web-unit", "docs/feature-map.md", "docs/feature-map.md"),
        ("web-unit", "docs/design/buddy/poses/", "docs/design/buddy/poses"),
        ("web-unit", "scripts/spa_dist_freshness.py", "scripts/spa_dist_freshness.py"),
        ("web-unit", ".nvmrc", ".nvmrc"),
        *(_SPLIT_DROPS if _SPLIT else _ONE_JOB_DROPS),
        ("web-build", "desktop/", "desktop"),
        ("web-build", "scripts/check_asset_licenses.py", "scripts/check_asset_licenses.py"),
        ("web-build", "tests/fixtures/", "tests/fixtures"),
        ("web-build", "mobile/package.json", "mobile/package.json"),
    ],
)
def test_an_input_list_missing_an_entry_the_job_reads_fails(
    job_id: str, dropped: str, named: str
) -> None:
    job = _CAT.job(job_id)
    missing = uncovered(_without(job_id, dropped), _CENSUS[job_id](REPO, job))
    assert any(m == named or m.startswith(named + " ") for m in missing), missing


def _git_tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    forget()
    return tmp_path


_CLIENT_ARGV = ("uv", "pip", "install", "packages/c[dev]")


def _client_job() -> catalogue.Job:
    return catalogue.Job(
        id="client",
        tiers=("change",),
        inputs=("packages/c/",),
        external_inputs=(),
        toolchain=("uv",),
        cache=(),
        security="ordinary",
        timeout_min=5,
        command=(catalogue.Step(_CLIENT_ARGV),),
    )


@pytest.mark.parametrize(
    "test_text, missing",
    [
        ("X = 1\n", []),
        ("DOC = 'docs/guide.md'\n", ["docs/guide.md (named by packages/c/tests/test_c.py)"]),
        (
            "from pathlib import Path\nROOT = Path(__file__).parents[3]\n",
            [". (packages/c/tests/test_c.py climbs out of packages/c)"],
        ),
        (
            "SIBLING = '../other/x.py'\n",
            [". (packages/c/tests/test_c.py climbs out of packages/c)"],
        ),
    ],
)
def test_a_client_file_that_reads_outside_its_folder_fails(
    tmp_path: Path, test_text: str, missing: list[str]
) -> None:
    root = _git_tree(
        tmp_path,
        {
            "packages/c/pyproject.toml": "[project]\nname = 'c'\n",
            "packages/c/tests/test_c.py": test_text,
            "docs/guide.md": "# guide\n",
        },
    )
    try:
        job = _client_job()
        assert uncovered(job.inputs, client_reads(root, job), root) == missing
    finally:
        forget()


def test_a_new_npm_lock_the_audit_audits_is_a_read(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {
            "package.json": '{"workspaces": ["web"]}\n',
            "package-lock.json": "{}\n",
            "web/package.json": "{}\n",
            "deploy/docker/tools/package.json": "{}\n",
            "deploy/docker/tools/package-lock.json": "{}\n",
            "uv.lock": "",
            "pyproject.toml": "",
        },
    )
    try:
        reads = audit_reads(root, _CAT.job("audit"))
        assert "deploy/docker/tools/package-lock.json" in reads, reads
        assert "deploy/docker/tools/package.json" in reads, reads
        assert "web/package.json" in reads, reads
    finally:
        forget()


def test_a_script_a_step_script_names_is_followed(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {
            "scripts/step.py": "import subprocess\nsubprocess.run(['python3', 'scripts/b.py'])\n",
            "scripts/b.py": "from pathlib import Path\nX = Path('.') / 'docs' / 'b.md'\n",
            "docs/b.md": "",
        },
    )
    try:
        reads = python_reads(root, ["scripts/step.py"])
        assert reads["scripts/b.py"] == "named by scripts/step.py"
        assert reads["docs/b.md"] == "named by scripts/b.py"
        assert "docs" not in reads, "a joined run is the path it joins, not its first folder"
    finally:
        forget()


def test_a_bare_top_level_name_is_a_read_only_when_joined_or_assigned(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {
            "scripts/a.py": "D = 'docs'\nKEYS = {'scripts': 1}\nV = cfg.get('docs')\n",
            "docs/x.md": "",
        },
    )
    try:
        assert python_names(root, "scripts/a.py", frozenset({"docs", "scripts"})) == {"docs"}
    finally:
        forget()


def test_an_untracked_build_output_is_read_through_its_sources(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {
            "package.json": '{"workspaces": ["web"]}\n',
            "web/src/a.ts": "",
            "src/pkg/__init__.py": "",
        },
    )
    try:
        assert _through(root, "web/dist") == ["web", "package.json", "package-lock.json"]
        assert _through(root, "src/pkg/static/dist") == ["src/pkg"]
        assert uncovered(("src/",), {"web/dist": "MANIFEST.in adds it"}, root) == [
            "web (web/dist: MANIFEST.in adds it)",
            "package.json (web/dist: MANIFEST.in adds it)",
            "package-lock.json (web/dist: MANIFEST.in adds it)",
        ]
    finally:
        forget()


_WEB_PACKAGE = '{"workspaces": ["web"], "scripts": {"test:web": "npm run test --workspace=web"}}\n'


def _web_job() -> catalogue.Job:
    return catalogue.Job(
        id="web-unit",
        tiers=("change",),
        inputs=("web/",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        command=(catalogue.Step(("npm", "run", "test:web")),),
    )


@pytest.mark.parametrize(
    "test_text, missing",
    [
        ("export const X = 1\n", []),
        (
            "import { join } from 'node:path'\n"
            "const D = join(process.cwd(), '..', 'docs', 'a.md')\n",
            ["docs/a.md (named by web/src/a.test.ts)"],
        ),
        ("// the twin: docs/a.md and tests/test_twin.py\nexport const X = 1\n", []),
        (
            "const REPO = resolve(__dirname, '..', '..')\n"
            "const C = readFileSync(join(REPO, 'docs', 'c', 'c.md'))\n",
            ["docs/c/c.md (named by web/src/a.test.ts)"],
        ),
        (
            "const UP = join(__dirname, '..', '..')\n"
            "const LOG = readFileSync(join(UP, 'CHANGELOG.md'))\n",
            ["CHANGELOG.md (named by web/src/a.test.ts)"],
        ),
        ("const copy = (root) => join(root, 'scripts')\n", []),
        (
            "const page = (name) => join(process.cwd(), `../docs/c/${name}.md`)\n",
            ["docs/c (named by web/src/a.test.ts)"],
        ),
        (
            "import { x } from '../../scripts/lib/x.mjs'\n",
            [
                "docs/d.md (named by scripts/lib/x.mjs)",
                "scripts/lib/x.mjs (run by web/src/a.test.ts)",
            ],
        ),
        (
            "spawnSync('python3', ['scripts/y.py', 'check'])\n",
            ["docs/y.md (named by scripts/y.py)", "scripts/y.py (run by web/src/a.test.ts)"],
        ),
        (
            "const PEER = 'tests/test_twin.py'\n",
            ["tests/test_twin.py (named by web/src/a.test.ts)"],
        ),
    ],
)
def test_a_web_file_that_reads_outside_its_workspace_is_a_read(
    tmp_path: Path, test_text: str, missing: list[str]
) -> None:
    """Each read form the census follows, and the two it leaves alone: a comment line, and a lone
    folder name joined onto a base it cannot read (a folder the test made). A Python twin a web
    file names is read, not followed: its own reads (docs/twin.md) are not the web job's."""
    root = _git_tree(
        tmp_path,
        {
            "package.json": _WEB_PACKAGE,
            "web/package.json": "{}\n",
            "web/src/a.test.ts": test_text,
            "scripts/lib/x.mjs": "const D = join(__dirname, '..', '..', 'docs', 'd.md')\n",
            "scripts/y.py": "from pathlib import Path\nY = Path('.') / 'docs' / 'y.md'\n",
            "tests/test_twin.py": "T = 'docs/twin.md'\n",
            "docs/a.md": "",
            "docs/c/c.md": "",
            "docs/d.md": "",
            "docs/y.md": "",
            "docs/twin.md": "",
            "CHANGELOG.md": "",
        },
    )
    try:
        job = _web_job()
        assert uncovered(job.inputs, web_reads(root, job), root) == missing
    finally:
        forget()


def test_the_web_steps_name_the_workspaces_they_run_in(tmp_path: Path) -> None:
    root = _git_tree(tmp_path, {"package.json": _WEB_PACKAGE, "web/src/a.ts": ""})
    try:
        job = _web_job()
        assert _step_workspaces(root, job) == ["web"]
        assert uncovered(("docs/",), web_reads(root, job), root) == ["web (a step runs in it)"]
    finally:
        forget()
