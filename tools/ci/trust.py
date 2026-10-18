"""The CI trust boundary: which files judge a candidate, what a receipt names, and what is on main.

A candidate's own run counts only when it ran the default branch's definitions: the files that
decide which jobs run, what each one runs and how the verdict is computed. ``compare`` says
whether a candidate changed any of them. When it did, the candidate's run is advisory and the
verdict comes from a run of the default branch's definitions on the candidate's tree.

``check_receipt`` refuses a receipt that cannot say who judged what: one that lacks a field,
names a controller revision that is not a commit on ``main``, or names a tree that is not its
head's. ``require_on_main`` refuses a commit that is not an ancestor of ``main``.

Everything here reads git objects. Nothing in a tree it judges is imported or run.
See ``docs/maintainers/ci.md`` ("Trust boundary").
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml

#: Every file under these decides which jobs run, what each one runs or how the verdict is
#: computed. The catalogue (#3909) lives under ``tools/ci/``.
DEFINITION_ROOTS: tuple[str, ...] = (".github/workflows/", ".github/actions/", "tools/ci/")

#: The CI contract table and the census it reads. Tests are otherwise code under test, but a
#: candidate that deletes a row of this table must not pass by it.
CONTRACT_TABLE: tuple[str, ...] = (
    "tests/rails/test_ci_contract.py",
    "tests/rails/test_ci_supply_chain_contract.py",
    "tests/security_census.py",
)

#: In-repo files that a command's entry point loads, keyed by that entry point. The list is
#: reviewed by hand rather than found by following imports: an entry point loads its helpers by
#: path, by ``sys.path`` edits and by ``-m``, and a reader that guessed wrong would leave a helper
#: out in silence. A value ending in ``/`` names every file under that directory. A helper counts
#: only while a command names its entry point.
REVIEWED_HELPERS: Mapping[str, tuple[str, ...]] = {
    "scripts/apps_sdk_contract.py": ("scripts/sdk_signature_snapshot.py",),
    "scripts/check_commit_provenance.py": ("MAINTAINERS",),
    "scripts/gate_report.py": (
        "scripts/generate_config_baseline.py",
        "scripts/generate_docs_lint_baseline.py",
        "scripts/generate_inert_surface_baseline.py",
        "scripts/generate_structural_baseline.py",
    ),
    "scripts/verify_wheel.py": (
        "scripts/backend_bundle_manifest.py",
        "scripts/installed_bundled_model_probe.py",
    ),
    "tools/docker_single_container_smoke.py": ("scripts/installed_bundled_model_probe.py",),
    "tools/ghcr_anonymous_pull.py": ("scripts/release_tags.py",),
    "harness/__main__.py": ("harness/",),
}

#: A path under one of these that a step's script names is an entry point wherever it appears.
_ENTRY_DIRS = ("scripts/", "tools/", "harness/", ".github/")

_INTERPRETER = re.compile(r"^(python(3(\.\d+)?)?|bash|sh|dash|zsh|node)$")
_SHELLS = {"bash", "sh", "dash", "zsh"}
_SHELL_WORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time", "exec"}
_SHELL_WORDS |= {"sudo", "command", "env"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = re.compile(r"\$\(|&&|\|\||[;|()`]")
_VARIABLE_PREFIX = re.compile(
    r"^(\$\{\{[^}]*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)/"
)
_ENTRY_TOKEN = re.compile(r"(?:^|/)((?:scripts|tools|harness|\.github)/[\w./-]+)")

#: A full object id, SHA-1 (40 hex) or SHA-256 (64 hex), in lower case; and an evidence digest.
#: Both are applied with ``fullmatch`` only: ``$`` under ``match`` also matches before a trailing
#: newline, so ``"<40 hex>\n"`` would pass as a full commit id.
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

#: The fields every GitHub receipt carries: the merge verdict's output, the reuse receipt of a
#: push to main and the release manifest. A local receipt names no controller.
RECEIPT_FIELDS: tuple[str, ...] = (
    "controller_revision",
    "head_sha",
    "tree_oid",
    "external_revisions",
    "toolchain",
    "evidence_digests",
)


class Refused(ValueError):
    """The input cannot be trusted. The message says why."""


ChangeKind = Literal["added", "deleted", "modified", "renamed"]


@dataclass(frozen=True)
class Change:
    path: str
    kind: ChangeKind
    old_path: str | None = None


@dataclass(frozen=True)
class Comparison:
    base: str
    head: str
    changes: tuple[Change, ...]

    @property
    def trusted(self) -> bool:
        """True when the candidate changed no definition, so its own run counts."""
        return not self.changes


@dataclass(frozen=True)
class Receipt:
    controller_revision: str
    head_sha: str
    tree_oid: str
    external_revisions: Mapping[str, str]
    toolchain: Mapping[str, str]
    evidence_digests: Mapping[str, str]


# ── git ───────────────────────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # The repository is named explicitly, so a GIT_DIR or index a calling hook exported must
    # not redirect the read.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _git_out(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


#: The modes of a file whose text a command can read: a regular file, executable or not.
_REGULAR_MODES = ("100644", "100755")


@dataclass(frozen=True)
class Entry:
    """One path of a tree: its git mode and its object id.

    Both are compared. A definition whose content is unchanged but whose mode is (an executable
    bit, or a file that became a symlink or a gitlink) runs differently, so it is a change.
    """

    mode: str
    oid: str


def _blobs(repo: Path, rev: str) -> dict[str, Entry]:
    """``path -> Entry`` for every path in the revision's tree, gitlinks included."""
    out = _git_out(repo, "ls-tree", "-r", "-z", "--full-tree", rev)
    blobs: dict[str, Entry] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, _kind, oid = meta.split()
        blobs[path] = Entry(mode=mode, oid=oid)
    return blobs


def _read(repo: Path, entry: Entry) -> str:
    """A regular file's text. A symlink or a gitlink has none a command reads here."""
    if entry.mode not in _REGULAR_MODES:
        return ""
    return _git_out(repo, "cat-file", "blob", entry.oid)


# ── Definitions ───────────────────────────────────────────────────────────────────────────────


def _is_test(path: str) -> bool:
    if path in CONTRACT_TABLE:
        return False
    name = PurePosixPath(path).name
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or name == "conftest.py"
        or (name.startswith("test_") and name.endswith(".py"))
        or ".spec." in name
        or ".test." in name
    )


def _normalise(word: str) -> str:
    word = _VARIABLE_PREFIX.sub("", word.strip("'\""))
    while word.startswith("./"):
        word = word[2:]
    return word


def _resolve(word: str, workdir: str, tracked: Mapping[str, Entry]) -> str | None:
    word = _normalise(word)
    if not word or word.startswith("-"):
        return None
    for candidate in (str(PurePosixPath(workdir) / word) if workdir else None, word):
        if candidate and candidate in tracked and not _is_test(candidate):
            return candidate
    return None


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _command_entries(words: list[str], workdir: str, tracked: Mapping[str, Entry]) -> set[str]:
    while words and (words[0] in _SHELL_WORDS or _ASSIGNMENT.match(words[0])):
        words = words[1:]
    if words[:2] == ["uv", "run"]:
        words = words[2:]
        while words and words[0].startswith("-"):
            words = words[1:]
    if not words:
        return set()
    found: set[str] = set()
    program = words[0]
    resolved = _resolve(program, workdir, tracked)
    if resolved:
        found.add(resolved)
    base = PurePosixPath(_normalise(program)).name
    if _INTERPRETER.match(base):
        rest = iter(words[1:])
        for word in rest:
            if word == "-m":
                module = next(rest, "").replace(".", "/")
                for suffix in (".py", "/__main__.py", "/__init__.py"):
                    hit = _resolve(module + suffix, workdir, tracked)
                    if hit:
                        found.add(hit)
                break
            if word in ("-c", "-e") or (word == "-n" and base in _SHELLS):
                break
            if word.startswith("-"):
                continue
            hit = _resolve(word, workdir, tracked)
            if hit:
                found.add(hit)
            break
    elif base == "make":
        if "Makefile" in tracked:
            found.add("Makefile")
        if "-f" in words[:-1]:
            hit = _resolve(words[words.index("-f") + 1], workdir, tracked)
            if hit:
                found.add(hit)
    elif base in ("npm", "npx"):
        for manifest in ("package.json", str(PurePosixPath(workdir) / "package.json")):
            if manifest in tracked:
                found.add(manifest)
    return found


def _script_entries(text: str, workdir: str, tracked: Mapping[str, Entry]) -> set[str]:
    found: set[str] = set()
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        for segment in _SEPARATORS.split(line):
            found |= _command_entries(_words(segment), workdir, tracked)
    for token in re.findall(r"[\w./${}-]+", joined):
        match = _ENTRY_TOKEN.search(token)
        if match:
            hit = _resolve(match.group(1), "", tracked)
            if hit:
                found.add(hit)
    return found


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _workflow_entries(text: str, tracked: Mapping[str, Entry]) -> set[str]:
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError:
        return set()
    if not isinstance(workflow, Mapping):
        return set()
    found: set[str] = set()
    default_dir = str(
        ((workflow.get("defaults") or {}).get("run") or {}).get("working-directory", "")
    )
    for job in (workflow.get("jobs") or {}).values():
        if not isinstance(job, Mapping):
            continue
        job_dir = str(((job.get("defaults") or {}).get("run") or {}).get("working-directory", ""))
        steps = job.get("steps") or []
        uses = [job.get("uses")] + [s.get("uses") for s in steps if isinstance(s, Mapping)]
        for ref in uses:
            if isinstance(ref, str) and ref.startswith("./"):
                local = _normalise(ref.split("@", 1)[0]).rstrip("/")
                found |= {p for p in tracked if p == local or p.startswith(local + "/")}
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            workdir = str(step.get("working-directory") or job_dir or default_dir)
            if isinstance(step.get("run"), str):
                found |= _script_entries(step["run"], workdir, tracked)
            for text_value in _strings(step.get("with") or {}):
                found |= _script_entries(text_value, "", tracked) & {
                    p for p in tracked if p.startswith(_ENTRY_DIRS)
                }
    return found


def entry_points(
    repo: Path, rev: str, *, helpers: Mapping[str, tuple[str, ...]] = REVIEWED_HELPERS
) -> frozenset[str]:
    """The files a command in the revision's workflows runs, with the helpers they load.

    Until the catalogue (#3909) lands, a workflow step is a catalogue command. A step names an
    entry point when it runs a tracked file, hands one to an interpreter, runs ``make`` (the
    ``Makefile``) or ``npm``/``npx`` (the ``package.json`` it reads), uses a local action, or
    names a file under ``scripts/``, ``tools/``, ``harness/`` or ``.github/``. Tests are code
    under test and never count, except the contract table.
    """
    tracked = _blobs(repo, rev)
    return _entry_points(repo, tracked, helpers)


def _entry_points(
    repo: Path, tracked: Mapping[str, Entry], helpers: Mapping[str, tuple[str, ...]]
) -> frozenset[str]:
    found: set[str] = set()
    for path, entry in tracked.items():
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            found |= _workflow_entries(_read(repo, entry), tracked)
    for point in list(found):
        for helper in helpers.get(point, ()):
            if helper.endswith("/"):
                found |= {p for p in tracked if p.startswith(helper) and not _is_test(p)}
            elif helper in tracked:
                found.add(helper)
    return frozenset(found)


def _definitions(
    repo: Path, tracked: Mapping[str, Entry], helpers: Mapping[str, tuple[str, ...]]
) -> set[str]:
    roots = {p for p in tracked if p.startswith(DEFINITION_ROOTS)}
    table = {p for p in CONTRACT_TABLE if p in tracked}
    return roots | table | set(_entry_points(repo, tracked, helpers))


def definition_paths(
    repo: Path, rev: str, *, helpers: Mapping[str, tuple[str, ...]] = REVIEWED_HELPERS
) -> frozenset[str]:
    """Every definition file in the revision's tree."""
    return frozenset(_definitions(repo, _blobs(repo, rev), helpers))


def compare(
    repo: Path,
    *,
    base: str,
    head: str,
    helpers: Mapping[str, tuple[str, ...]] = REVIEWED_HELPERS,
) -> Comparison:
    """The definition files whose path, mode or content differs between ``base`` and ``head``.

    Both trees' definitions are compared, so a candidate that deletes the command naming a
    file still has that file compared, and an added, deleted or renamed file is a change. Each
    path is keyed on its mode and object id together, so a changed executable bit, or a file
    that becomes a symlink or a gitlink, is a change. A rename is a path that moved with both
    unchanged.
    """
    base_blobs, head_blobs = _blobs(repo, base), _blobs(repo, head)
    paths = _definitions(repo, base_blobs, helpers) | _definitions(repo, head_blobs, helpers)
    added = {p for p in paths if p in head_blobs and p not in base_blobs}
    deleted = {p for p in paths if p in base_blobs and p not in head_blobs}
    changes: list[Change] = [
        Change(p, "modified")
        for p in paths
        if p in base_blobs and p in head_blobs and base_blobs[p] != head_blobs[p]
    ]
    for old in sorted(deleted):
        twin = next((p for p in sorted(added) if head_blobs[p] == base_blobs[old]), None)
        if twin is not None:
            added.discard(twin)
            changes.append(Change(twin, "renamed", old_path=old))
        else:
            changes.append(Change(old, "deleted"))
    changes += [Change(p, "added") for p in added]
    return Comparison(base=base, head=head, changes=tuple(sorted(changes, key=lambda c: c.path)))


# ── Receipts and reachability ─────────────────────────────────────────────────────────────────


def require_on_main(repo: Path, commit: str, *, main: str = "origin/main") -> None:
    """Refuse a commit that is not an ancestor of ``main`` (``main`` itself included).

    Only a full commit id is accepted, so a branch or tag name cannot stand in for one. A
    shallow clone cannot prove ancestry, so it refuses too.
    """
    if not _SHA.fullmatch(commit):
        raise Refused(f"{commit!r} is not a full commit id")
    if _git_out(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise Refused("a shallow clone cannot show that a commit is on main")
    if _git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        raise Refused(f"{commit} is not a commit in this repository")
    proc = _git(repo, "merge-base", "--is-ancestor", commit, main)
    if proc.returncode == 1:
        raise Refused(f"{commit} is not on {main}")
    if proc.returncode != 0:
        raise Refused(f"cannot tell whether {commit} is on {main}: {proc.stderr.strip()}")


def _mapping(
    data: Mapping[str, object], name: str, value: re.Pattern[str] | None
) -> dict[str, str]:
    raw = data[name]
    if not isinstance(raw, Mapping) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in raw.items()
    ):
        raise Refused(f"{name} must map names to non-empty strings")
    if value is not None:
        bad = [k for k, v in raw.items() if not value.fullmatch(v)]
        if bad:
            raise Refused(f"{name} has a malformed value for {', '.join(sorted(bad))}")
    return dict(raw)


def check_receipt(repo: Path, data: Mapping[str, object], *, main: str = "origin/main") -> Receipt:
    """The receipt, if it names a controller on ``main`` and its head's own tree.

    ``external_revisions`` may be empty, for a run that used no other repository. The toolchain
    and the evidence digests may not: a receipt that records neither says nothing.
    """
    missing = [name for name in RECEIPT_FIELDS if name not in data]
    if missing:
        raise Refused(f"the receipt lacks {', '.join(missing)}")
    for name in ("controller_revision", "head_sha", "tree_oid"):
        value = data[name]
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise Refused(f"{name} must be a full object id, got {value!r}")
    receipt = Receipt(
        controller_revision=str(data["controller_revision"]),
        head_sha=str(data["head_sha"]),
        tree_oid=str(data["tree_oid"]),
        external_revisions=_mapping(data, "external_revisions", _SHA),
        toolchain=_mapping(data, "toolchain", None),
        evidence_digests=_mapping(data, "evidence_digests", _DIGEST),
    )
    if not receipt.toolchain:
        raise Refused("the receipt names no toolchain")
    if not receipt.evidence_digests:
        raise Refused("the receipt names no evidence")
    require_on_main(repo, receipt.controller_revision, main=main)
    tree = _git(repo, "rev-parse", "--verify", "--quiet", f"{receipt.head_sha}^{{tree}}")
    if tree.returncode != 0:
        raise Refused(f"head {receipt.head_sha} is not a commit in this repository")
    if tree.stdout.strip() != receipt.tree_oid:
        raise Refused(f"tree {receipt.tree_oid} is not the tree of head {receipt.head_sha}")
    return receipt
