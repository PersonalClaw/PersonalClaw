"""The brownfield context pass (UP-R17) — what a plan should assume about the code it targets.

A generated spec for an EXISTING project fails a particular way: it scaffolds generic stages that
name the wrong language, the wrong test runner, the wrong directory layout — because the planner was
told the goal and nothing about the ground it stands on. This pass reads that ground once, cheaply,
and hands the planner a `CODEBASE_CONTEXT` block so its stages inherit the project's real
conventions instead of inventing plausible ones.

**Pure and deterministic.** No model call: the synthesis is a mechanical composition of a
depth-filtered file tree + the README head + the project-metadata markers (package.json /
pyproject.toml / Makefile / …). A model summary reads nicely and cannot be tested, and the value
here is being TRUE about the directory, which a fixed reading of it delivers and a paraphrase risks
drifting from.

**Cached per `(project_id, tree-hash)` with a 7-day TTL.** The walk plus the README read is the
cost, and it recurs on every plan scoped to the same project. The tree-hash is a digest of the
filtered listing so ADDING or REMOVING a file re-synthesizes — a changed codebase must not plan
against a stale reading — while the TTL bounds how long an unchanged tree trusts an old summary.

Every path in and out is best-effort: this feeds a planning prompt, and a project directory that
cannot be read must cost the context block, never the plan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Two levels of tree, no deeper. A planner needs the SHAPE of a project — its top-level packages
#: and their immediate contents — not a full recursive listing that spends the prompt on leaf files
#: it will never name. The plan's number.
MAX_TREE_DEPTH = 2

#: README/docs head is capped so a project with a book-length README cannot crowd out the rest of
#: the planning prompt. The plan's number.
MAX_README_CHARS = 8_000

#: Entries below this many characters of listing are still worth the walk; above it the tree is
#: truncated with a stated count, never silently — a planner told about 30 of 300 files would
#: otherwise conclude the other 270 do not exist.
MAX_TREE_ENTRIES = 120

#: Cache lifetime for one project's synthesis. Seven days: a tree that has not changed in a week is
#: unlikely to have shifted its conventions, and re-reading it every plan is the cost this avoids.
DEFAULT_TTL_SECS = 7 * 24 * 3600

#: Directories never worth walking — build output, dependency trees, VCS internals. A planner given
#: `node_modules` learns nothing and pays for thousands of entries.
_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "target",
        ".next",
        ".cache",
        "coverage",
        ".tox",
        "site-packages",
    }
)

#: Filenames whose head we read for the "what this project is" sentence, most-informative first.
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

#: `(marker file, what its presence tells the planner)`. Presence only — a planner does not need the
#: file's contents to know "this is a Python project with a Makefile", and reading each would be I/O
#: for nothing.
_METADATA_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python (pyproject.toml)"),
    ("setup.py", "Python (setup.py)"),
    ("requirements.txt", "Python (requirements.txt)"),
    ("package.json", "Node/JS (package.json)"),
    ("tsconfig.json", "TypeScript (tsconfig.json)"),
    ("Cargo.toml", "Rust (Cargo.toml)"),
    ("go.mod", "Go (go.mod)"),
    ("pom.xml", "Java/Maven (pom.xml)"),
    ("build.gradle", "Java/Gradle (build.gradle)"),
    ("Gemfile", "Ruby (Gemfile)"),
    ("Makefile", "Makefile present"),
    ("Dockerfile", "Dockerfile present"),
    ("docker-compose.yml", "docker-compose present"),
)


@dataclass
class CodebaseContext:
    """The deterministic reading of one project directory. A projection, so the pass stays pure and
    testable without a real filesystem in the unit tests."""

    tree: list[str] = field(default_factory=list)
    tree_truncated: int = 0
    readme_head: str = ""
    metadata: list[str] = field(default_factory=list)

    def render(self) -> str:
        """The `CODEBASE_CONTEXT` block for the planning prompt.

        Ordered stack-first: what the project IS (metadata) before what it CONTAINS (tree) before
        what it SAYS (README), because a planner reading top-down should fix the language and layout
        before it reads prose that might wander.
        """
        parts: list[str] = []
        if self.metadata:
            parts.append("Project type: " + ", ".join(self.metadata))
        if self.tree:
            listing = "\n".join(f"- {p}" for p in self.tree)
            if self.tree_truncated:
                listing += f"\n- (+{self.tree_truncated} more entries not listed)"
            parts.append("Layout (depth-limited):\n" + listing)
        if self.readme_head:
            parts.append("README (head):\n" + self.readme_head)
        return "\n\n".join(parts)


def tree_hash(entries: list[str], truncated: int) -> str:
    """A cheap digest of the filtered tree, so a changed codebase invalidates the cache.

    Over the tree listing alone — computable from the walk, which the caller must do anyway — so a
    cache hit can skip the README read and the render, which are the parts worth skipping. Root
    metadata markers (pyproject.toml, Makefile, …) appear in the depth-2 listing, so ADDING one
    changes the tree and re-synthesizes; a README content rewrite that leaves the tree unchanged is
    bounded by the 7-day TTL instead, which is the trade the plan's "tree-hash" key makes.
    """
    h = hashlib.sha256()
    h.update("\n".join(entries).encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update(str(truncated).encode("ascii"))
    return h.hexdigest()[:16]


def depth_filtered_tree(root: Path, *, max_depth: int = MAX_TREE_DEPTH) -> tuple[list[str], int]:
    """The project's directory shape to `max_depth`, common ignores dropped.

    Returns `(entries, truncated_count)`. Directories are marked with a trailing `/` so a planner
    can tell a package from a file. Sorted for a stable hash — an unsorted walk would re-synthesize
    on filesystem ordering that says nothing about the project.
    """
    entries: list[str] = []
    try:
        _walk(root, root, 0, max_depth, entries)
    except OSError:
        logger.debug("brownfield tree walk failed under %s", root, exc_info=True)
        return [], 0
    entries.sort()
    if len(entries) > MAX_TREE_ENTRIES:
        return entries[:MAX_TREE_ENTRIES], len(entries) - MAX_TREE_ENTRIES
    return entries, 0


def _walk(base: Path, current: Path, depth: int, max_depth: int, out: list[str]) -> None:
    if depth >= max_depth:
        return
    for child in sorted(current.iterdir(), key=lambda p: p.name):
        name = child.name
        if name.startswith(".") and name not in (".github",):
            # Dotfiles are config noise for a shape reading; `.github` is the one worth naming
            # because it tells a planner about CI conventions.
            continue
        is_dir = child.is_dir()
        if is_dir and name in _IGNORE_DIRS:
            continue
        rel = child.relative_to(base).as_posix()
        out.append(rel + "/" if is_dir else rel)
        if is_dir:
            _walk(base, child, depth + 1, max_depth, out)


def _readme_head(root: Path, *, cap: int = MAX_README_CHARS) -> str:
    for name in _README_NAMES:
        path = root / name
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                return text[:cap]
        except OSError:
            logger.debug("brownfield README read failed for %s", path, exc_info=True)
    return ""


def _project_metadata(root: Path) -> list[str]:
    out: list[str] = []
    for filename, label in _METADATA_MARKERS:
        try:
            if (root / filename).is_file():
                out.append(label)
        except OSError:
            continue
    return out


def build_codebase_context(root: Path) -> CodebaseContext | None:
    """Read one project directory into a `CodebaseContext`, or None when it is not a directory.

    None rather than an empty context: "this path is not a project" and "this project is empty" are
    different answers, and only the second should reach the planner as a real (if sparse) reading.
    """
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    tree, truncated = depth_filtered_tree(root)
    return CodebaseContext(
        tree=tree,
        tree_truncated=truncated,
        readme_head=_readme_head(root),
        metadata=_project_metadata(root),
    )


@dataclass
class BrownfieldCache:
    """A tiny JSON-backed cache keyed on `(project_id, tree-hash)`, TTL-expired on read.

    Path-backed rather than in-memory so a synthesis survives a gateway restart within its TTL — the
    common case is planning repeatedly against one project across sessions. Best-effort on every
    operation: a corrupt or unwritable cache costs a recompute, never the plan.
    """

    path: Path
    ttl_secs: int = DEFAULT_TTL_SECS

    def get(self, project_id: str, tree_hash: str, *, now: float | None = None) -> str | None:
        """The cached render for this project+tree, or None on miss/expiry/tree-change.

        The stored `tree_hash` must match: a hit whose tree-hash differs is a changed codebase and
        is a miss by design — serving it would plan against a directory that no longer exists.
        """
        entry = self._read().get(project_id)
        if not isinstance(entry, dict):
            return None
        if entry.get("tree_hash") != tree_hash:
            return None
        stored_at = entry.get("stored_at")
        clock = time.time() if now is None else now
        if not isinstance(stored_at, (int, float)) or clock - stored_at > self.ttl_secs:
            return None
        rendered = entry.get("rendered")
        return rendered if isinstance(rendered, str) else None

    def put(
        self, project_id: str, tree_hash: str, rendered: str, *, now: float | None = None
    ) -> None:
        data = self._read()
        data[project_id] = {
            "tree_hash": tree_hash,
            "rendered": rendered,
            "stored_at": time.time() if now is None else now,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            logger.debug("brownfield cache write failed at %s", self.path, exc_info=True)

    def _read(self) -> dict:
        try:
            if self.path.is_file():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            logger.debug("brownfield cache read failed at %s", self.path, exc_info=True)
        return {}


def codebase_context(
    project_id: str,
    root: Path,
    *,
    cache: BrownfieldCache | None = None,
    now: float | None = None,
) -> str:
    """The `CODEBASE_CONTEXT` string for a project-scoped plan, cached per `(project_id, treehash)`.

    Builds the deterministic reading, computes its tree-hash, and returns the cached render when the
    tree is unchanged and the entry is within TTL — otherwise re-reads and re-stores. Returns ""
    when the directory does not resolve, so the caller simply omits the block.

    The tree walk always runs (the hash needs it); the cache saves the README read + render — the
    part worth skipping on the common unchanged-tree path.
    """
    try:
        if not root.is_dir():
            return ""
    except OSError:
        return ""
    tree, truncated = depth_filtered_tree(root)
    digest = tree_hash(tree, truncated)
    if cache is not None:
        hit = cache.get(project_id, digest, now=now)
        if hit is not None:
            return hit
    # Miss (or no cache): now pay for the README read and the render.
    ctx = CodebaseContext(
        tree=tree,
        tree_truncated=truncated,
        readme_head=_readme_head(root),
        metadata=_project_metadata(root),
    )
    rendered = ctx.render()
    if cache is not None and project_id:
        cache.put(project_id, digest, rendered, now=now)
    return rendered
