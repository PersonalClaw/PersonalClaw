#!/usr/bin/env python3
"""Census: which ``todo`` atoms in ``docs/roadmap/atomic/dag.json`` are already on ``main``?

Why this exists
---------------
An atom's ``status`` in ``dag.json`` is authored by hand and flipped by hand at integration
time. Two mechanical facts make it drift *systematically* in one direction — atoms stay
``todo`` after their code has landed:

* The merge train **cherry-picks per commit**, so a PR can read ``CLOSED`` with
  ``mergedAt: null`` while its content sits on ``main``. **PR state is not evidence.**
* ``git cherry origin/main <branch>`` prints ``+`` (unmerged) for a commit whose content
  landed via cherry-pick or squash. **``git cherry`` is not evidence either.**

So the only reliable questions are: *does the atom's own named deliverable exist on the
ref?* and *does the plan's execution log already say the work is complete?* Five atoms
(``DCU-2``, ``PHF-7``, ``DFE-2``, ``PCS-7``, ``MRT-5``) were each found this way by
accident, one wasted roadmap tick at a time. This tool does it for all of them at once.

    python3 tools/audit_landed_atoms.py                  # the four-bucket census
    python3 tools/audit_landed_atoms.py --bucket clean   # just the flippable ones
    python3 tools/audit_landed_atoms.py --atom DCU-2 -v  # one atom, with its keys
    python3 tools/audit_landed_atoms.py --branches       # + the stranded-branch scan
    python3 tools/audit_landed_atoms.py --json           # machine-readable

    # DESTRUCTIVE, opt-in: is each landed wire actually ASSERTED by anything?
    python3 tools/audit_landed_atoms.py --check-wires APE-3
    python3 tools/audit_landed_atoms.py --check-wires        # the LANDED-AND-CLEAN bucket

It **never** edits the roadmap. ``dag.json`` is owner-maintained; flips happen at
integration. Exit status is ``0`` for any census, however imperfect the roadmap looks, and
non-zero **only** when one of the tool's own vacuity assertions fails (see ``self_check``) or
a wire check cannot restore the tree it mutated (exit ``3``).

A LANDED-AND-CLEAN verdict is a claim about **presence**, and presence is not the same as
being load-bearing. ``--check-wires`` closes that gap by neutralising the atom's production
call statements and re-running a bounded selection; see "wire check" below. It is deliberately
never part of a default run — it writes to ``src/`` and it costs minutes.

The three shapes of evidence
----------------------------
``deliverable evidence`` — keys are extracted from the atom's own ``title``/``scope``/
``done_when`` (module paths, snake_case symbols, CamelCase types, ``make`` targets, env
vars) and looked up in a corpus built from the ref's tree. The corpus **deliberately
excludes ``docs/``**: the plan text that the keys came from lives there, so including it
would make every atom look landed. That is the central false-positive trap.

``log verdict`` — the owning plan's ``## Execution log`` is split into entries, each entry
classified into a small closed set (``LogVerdict``), and each attributed to the atom ids it
names. Only the atom's **own** plan adjudicates it, and an id in an entry's headline
outranks one buried in a body. Precedence is **last entry wins by file position**, because
these logs are append-ordered and later entries supersede earlier ones (``PHF-7`` is logged
PARTIAL at one point and "all five clauses now MET" further down the same file).

``code caveat`` — not every finding lands in the plan log. The repo also records "shipped
but nothing calls it" as a ratchet test or a module comment, which a log scanner cannot see.
``DCU-2`` is the measured case: its log says "COMPLETE (all three clauses); flip it when the
PR lands", while ``tests/test_computer_use_call_sites.py`` on ``main`` — committed *after*
that entry — proves its three screens have zero production callers. This signal is
**one-directional**: it can only move an atom OUT of LANDED-AND-CLEAN, never into it.

Bucketing is the conjunction of these, and it is deliberately conservative:

===================  =====================================================================
LANDED-AND-CLEAN     deliverable on the ref **and** the log says complete-but-unmerged
LANDED-BUT-GATED     deliverable on the ref **but** the log names a gate (owner call,
                     unmet clause, unbuilt scope, a control with no production caller)
NOT LANDED           no deliverable evidence on the ref
UNDECIDABLE CHEAPLY  the two signals disagree, or one is missing. Reported as unknown on
                     purpose: a wrong "landed" claim costs more than an honest gap.
===================  =====================================================================

Precision, stated up front: this is a grep. It cannot read a ``done_when``. It answers
"is the named thing there and does the log already say so", which is exactly the signal
the five known misses would have needed — and nothing more. ``--verify-known`` scores it
against those five.

What the wire check cannot see
------------------------------
Stated here rather than discovered later. The locator is AST over the atom's deliverable
modules, so it is blind to:

* **dynamic dispatch** — ``getattr(mod, name)()``, a registry/dict of handlers, a callback
  passed as a value, ``functools.partial``, anything invoked through ``__call__``.
* **decorators and framework registration** — a route, a hook, an event subscriber or a CLI
  command whose "caller" is a decorator at import time. There is no call statement to delete.
* **re-exports and indirection** — a symbol reached through ``__init__``'s ``from .x import y``
  is resolved to the re-exporting module, not the defining one, so its wires split across two
  names. A wrapper module that forwards under a different name is a different symbol.
* **methods** — resolution is module-level functions only. ``self.foo()`` and ``obj.foo()``
  need the receiver's type, which is beyond an AST pass.
* **value-consuming calls** — ``x = f()``, ``if f():``, ``return f()``. Out of scope on
  purpose: neutralising them measures a NameError cascade, not the wire.
* **rails that live outside the derived selection** — a green is bounded by the files chosen,
  and the report prints how many it did not run. It never means "the suite catches this".
* **cross-repo consumers** — ``PersonalClawApps`` and the ``web/`` frontend are not run.

Consequences: an UNRAILED verdict on a wire of one of those shapes may be a false positive
(the caller might be reached another way), while an UNRAILED verdict on a plain side-effect
call statement is a measurement. RAILED is the stronger direction — a red is a red.
"""

from __future__ import annotations

import argparse
import ast
import atexit
import bisect
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DAG_PATH = REPO_ROOT / "docs" / "roadmap" / "atomic" / "dag.json"
PLANS_DIR = REPO_ROOT / "docs" / "roadmap" / "plans"
DEFAULT_REF = "origin/main"

# The five atoms found by hand, each code-complete on main while reading ``todo``.
# They are this tool's ground truth; ``--verify-known`` measures against them and the
# result is *reported*, never tuned away.
KNOWN_LANDED = ("DCU-2", "PHF-7", "DFE-2", "PCS-7", "MRT-5")

# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

# Evidence lives in shipped trees only. ``docs/`` is excluded on purpose: the atom's own
# scope/done_when prose is committed under docs/roadmap/, so any key would "match" itself.
CORPUS_PREFIXES = (
    "src/",
    "tests/",
    "web/src/",
    "web/e2e/",
    "web/public/",
    "desktop/",
    "scripts/",
    "apps/",
    ".github/",
)
CORPUS_FILES = ("Makefile", "pyproject.toml", "package.json", "web/package.json")
CORPUS_SKIP = ("node_modules/", "web/dist/", "/__snapshots__/", ".min.js")
# ``tests/`` is corpus, but it is not *impl*: a symbol that appears only in a test proves a
# test exists, not that a deliverable shipped. The two are counted separately.
IMPL_PREFIXES = tuple(p for p in CORPUS_PREFIXES if p != "tests/") + CORPUS_FILES


def _git(args: Sequence[str], cwd: Path = REPO_ROOT) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _ref_exists(ref: str, cwd: Path = REPO_ROOT) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def resolve_ref(ref: str, cwd: Path = REPO_ROOT) -> tuple[str, str]:
    """Resolve ``ref`` to something that exists here, and SAY when it is not what was asked.

    ``origin/main`` exists in a developer clone and does **not** exist under
    ``actions/checkout``, which fetches the PR's merge preview into a detached ``HEAD``
    without creating remote-tracking refs. So the tool ran green locally for its author and
    exited 2 with ``INTERNAL ERROR: ... Not a valid object name origin/main`` on every CI run —
    a hard failure of the whole census over an environment assumption, not over any roadmap
    fact.

    The fallback is deliberately narrow and deliberately LOUD.

    Narrow in two ways. It tries only the same branch under a different spelling, then
    ``HEAD`` — which under ``actions/checkout`` IS main-plus-this-PR. And it applies **only to
    the default ref**: a ref the caller typed explicitly and that does not exist is a caller
    mistake, so it raises. Falling back there would answer a question about ``origin/feature-x``
    with a census of whatever happened to be checked out, and the report would look entirely
    normal — the exact silent-wrong-subject failure this tool exists to prevent.

    Loud because the returned note is printed: "audited the landed tree" and "audited this
    PR's merge preview" are different claims, and a reader must not have to guess which one
    they are holding.
    """
    if _ref_exists(ref, cwd):
        return ref, ""
    name = ref.rsplit("/", 1)[-1]
    candidates = [f"refs/remotes/{ref}", f"refs/remotes/origin/{name}", name]
    if ref == DEFAULT_REF:
        candidates.append("HEAD")
    for cand in candidates:
        if _ref_exists(cand, cwd):
            return cand, f"{ref!r} does not exist here; auditing {cand!r} instead"
    raise RuntimeError(
        f"{ref!r} is not a valid commit here, and nor is any fallback "
        f"({', '.join(candidates)}) — name a ref this clone actually has"
    )


def _in_corpus(path: str) -> bool:
    if any(skip in path for skip in CORPUS_SKIP):
        return False
    return path.startswith(CORPUS_PREFIXES) or path in CORPUS_FILES


@dataclass
class Corpus:
    """Every text blob of the ref that could hold a deliverable, held once in memory.

    One ``ls-tree`` + one ``git archive`` beats ~4000 ``git grep`` invocations and, more
    importantly, makes every lookup answer from the *same* snapshot even if the shared
    ``.git`` moves under a long run (the merge train does move it).

    Lookups run against a single concatenated buffer rather than a per-file loop. That is a
    ~40x difference at this size: ``str.find`` over one 50 MiB string is a memchr scan,
    while 4000 keys x 3500 files is 14M interpreted iterations (measured: >2 min, which is
    long enough that an operator stops running the census at all).
    """

    ref: str
    paths: tuple[str, ...]
    blobs: dict[str, str]
    _joined: str = field(default="", repr=False)
    _starts: list[int] = field(default_factory=list, repr=False)
    _names: list[str] = field(default_factory=list, repr=False)
    _cache: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict, repr=False)
    _lines: set[str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._joined:
            return
        chunks: list[str] = []
        cursor = 0
        # \x00 fences the files so no needle can straddle a boundary and score a phantom hit
        for name, text in self.blobs.items():
            self._names.append(name)
            self._starts.append(cursor)
            chunks.append(text)
            chunks.append("\x00")
            cursor += len(text) + 1
        self._joined = "".join(chunks)

    @property
    def total_bytes(self) -> int:
        return sum(len(v) for v in self.blobs.values())

    def lines(self) -> set[str]:
        """Every stripped non-trivial line in the corpus, for whole-line membership tests.

        Built lazily: only the branch scan needs it, and it costs a second and ~200 MiB.
        """
        if self._lines is None:
            acc: set[str] = set()
            for text in self.blobs.values():
                for line in text.splitlines():
                    stripped = line.strip()
                    if len(stripped) > 3:
                        acc.add(stripped)
            self._lines = acc
        return self._lines

    def find(self, needle: str) -> tuple[list[str], list[str]]:
        """Return (impl paths, test paths) whose contents contain ``needle``."""
        hit = self._cache.get(needle)
        if hit is not None:
            return hit
        impl: list[str] = []
        tests: list[str] = []
        seen: set[str] = set()
        pos = self._joined.find(needle)
        while pos != -1:
            idx = bisect.bisect_right(self._starts, pos) - 1
            name = self._names[idx]
            if name not in seen:
                seen.add(name)
                (impl if name.startswith(IMPL_PREFIXES) else tests).append(name)
            # skip to the end of this file; one hit per file is all we report
            pos = self._joined.find(needle, self._starts[idx] + len(self.blobs[name]) + 1)
        self._cache[needle] = (impl, tests)
        return impl, tests

    def path_exists(self, suffix: str) -> list[str]:
        """Paths in the ref whose tail matches ``suffix`` (atoms cite partial paths)."""
        suffix = suffix.lstrip("./")
        return [p for p in self.paths if p == suffix or p.endswith("/" + suffix)]

    def dir_exists(self, frag: str) -> list[str]:
        """Files in the ref living under a cited directory fragment.

        Anchored at a path boundary: a bare substring test lets ``2/`` match
        ``.../a17c3f92/status.json``.
        """
        frag = frag.strip("/").lstrip("./") + "/"
        return [p for p in self.paths if p.startswith(frag) or ("/" + frag) in p][:6]

    def make_targets(self) -> set[str]:
        text = self.blobs.get("Makefile", "")
        return set(re.findall(r"^([a-zA-Z][\w-]*):", text, re.M))


MAX_BLOB_BYTES = 2_000_000  # a lockfile or a fixture dump is not a deliverable


def load_corpus(ref: str) -> Corpus:
    """Stream the ref's tracked text through ``git archive``.

    ``git cat-file --batch`` is the obvious tool here and it **deadlocks**: feeding ~3500
    object names into its stdin fills its 64 KiB stdout pipe long before the write finishes,
    so the writer blocks on stdin while git blocks on stdout. (Measured: a silent hang, not
    an error — the worst failure shape.) ``git archive`` streams one way and needs no
    interleaving, and because it only ever emits *tracked* files it also excludes
    ``node_modules``/``web/dist`` for free.
    """
    all_paths = tuple(_git(["ls-tree", "-r", "--name-only", ref]).splitlines())

    proc = subprocess.Popen(
        ["git", "archive", "--format=tar", ref],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout
    blobs: dict[str, str] = {}
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        for member in tar:
            if not member.isfile() or not _in_corpus(member.name):
                continue
            if member.size > MAX_BLOB_BYTES:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            blobs[member.name] = handle.read().decode("utf-8", errors="replace")
    proc.stdout.close()
    proc.wait()
    return Corpus(ref=ref, paths=all_paths, blobs=blobs)


# ---------------------------------------------------------------------------
# atoms
# ---------------------------------------------------------------------------


@dataclass
class Atom:
    id: str
    title: str
    status: str
    scope: str
    done_when: str
    deps: list[str]
    plan_code: str
    plan_name: str
    plan_status: str

    @property
    def prose(self) -> str:
        return f"{self.title}\n{self.scope}\n{self.done_when}"

    @property
    def is_retirement(self) -> bool:
        """Does this atom's deliverable consist of REMOVING something?

        For these, finding the named thing on the ref is **inverted** evidence: ``SV-11``
        ("retire the interim commit-watcher cron script") names
        ``selfqa/scripts/selfqa_commit_watch.py``, and that file existing is precisely the
        proof the atom is *not* done. Scoring it as presence-evidence gets the sign wrong,
        so these are annotated rather than silently mis-scored.
        """
        text = f"{self.title} {self.scope} {self.done_when}".lower()
        return any(
            verb in text
            for verb in (
                "retire ",
                "retires ",
                "delete legacy",
                "delete the",
                "deletes the",
                "remove the second",
            )
        )

    @property
    def plan_file(self) -> str:
        """The plan markdown that owns this atom's execution log.

        The catalog's ``plan`` field is the file stem verbatim; checked for all 70 plans.
        """
        return f"{self.plan_name}.md"


def load_atoms(dag_path: Path = DAG_PATH) -> list[Atom]:
    """Read every atom out of the catalog.

    The shape matters and has burned a probe before: atoms are **not** top-level. The file
    is ``{"plans": [{"plan", "code", "status", "atoms": [...]}, ...], "dag": {...}}``, so a
    probe keyed on ``id``/``name``/``title`` at the top level finds nothing and returns a
    *false zero* that looks exactly like a clean roadmap. ``self_check`` refuses that.
    """
    data = json.loads(dag_path.read_text())
    if "plans" not in data:
        raise VacuityError(
            f"{dag_path} has no top-level 'plans' key (keys: {sorted(data)}); "
            "the catalog shape changed and this tool would silently census nothing"
        )
    atoms: list[Atom] = []
    for plan in data["plans"]:
        for raw in plan.get("atoms", []):
            atoms.append(
                Atom(
                    id=raw["id"],
                    title=raw.get("title", ""),
                    status=raw.get("status", ""),
                    scope=raw.get("scope", ""),
                    done_when=raw.get("done_when", ""),
                    deps=list(raw.get("deps", [])),
                    plan_code=plan.get("code", ""),
                    plan_name=plan.get("plan", ""),
                    plan_status=plan.get("status", ""),
                )
            )
    return atoms


# ---------------------------------------------------------------------------
# key extraction
# ---------------------------------------------------------------------------

# Words that look like a deliverable and are not one. Every entry here is a measured
# false positive from a real atom's prose, not a guess.
STOP_SYMBOLS = {
    # project / vendor nouns that match the CamelCase shape
    "PersonalClaw",
    "OpenAI",
    "OpenRouter",
    "GitHub",
    "JavaScript",
    "TypeScript",
    "JsonSchema",
    "MacOS",
    "IPhone",
    "AppStore",
    "PyPI",
    # roadmap vocabulary
    "SessionQueue",
    "DoneWhen",
    "LandedAndClean",
    # prose snake_case that is roadmap-speak, not code
    "done_when",
    "soul_guardrail",
    "no_op",
    "round_trip",
    "end_to_end",
    "read_only",
    "read_write",
    "opt_in",
    "opt_out",
    "e_g",
    "i_e",
}
STOP_PATH_SUFFIXES = (".md", ".rst", ".txt")

# Extension alternation is ORDER-SENSITIVE: with ``jsx?`` before ``json``, the regex matched
# "js" out of "routing_policy.json" and reported the deliverable as ``routing_policy.js`` —
# still found, by substring, but named wrongly in the evidence line. Longest first.
RE_PATH = re.compile(
    r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|tsx|jsx|json|ts|js|yaml|yml|sh|toml|cfg|ini|css))"
)
# A cited directory (``web/src/lib/``, ``documents/parsers/``) is a deliverable too, but the
# first segment must look like a package name. A permissive ``[\w.-]+/`` matched the ``2/``
# in a prose fraction and then "found" it inside a fixture path — a pure phantom.
RE_DIR = re.compile(r"(?<![\w/])((?:[a-z_][\w.-]{2,}/){1,})(?![\w])")
RE_SNAKE = re.compile(r"(?<![\w.])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w])")
RE_CAMEL = re.compile(r"(?<![\w.])([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)(?![\w])")
# lowerCamelCase is the frontend's convention and half the UI atoms name their deliverable
# only that way (``llmFriendlyMessage``, ``uiCapabilities``, ``designSystem``). Omitting it
# made 37 of 129 atoms yield zero keys, i.e. silently unmeasurable.
RE_LOWER_CAMEL = re.compile(r"(?<![\w.])([a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+)(?![\w])")
RE_MAKE = re.compile(r"\bmake ([a-z][a-z0-9-]{2,})")
RE_ENV = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")


@dataclass
class Key:
    text: str
    kind: str  # path | symbol | make | env
    found_impl: list[str] = field(default_factory=list)
    found_test: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.found_impl or self.found_test)

    @property
    def in_impl(self) -> bool:
        return bool(self.found_impl)


def extract_keys(atom: Atom) -> list[Key]:
    """Pull the atom's own named deliverables out of its prose.

    Generic words are useless here — every atom's done_when says "test" and "config". What
    discriminates is the atom's *own* nouns: the module it adds, the symbol it defines, the
    make target it registers, the env var it reads.
    """
    text = atom.prose
    # strip line/range citations so ``stats.py:42-84`` reads as ``stats.py``
    text = re.sub(r"(\.\w+):\d+(?:-\d+)?", r"\1", text)
    keys: dict[tuple[str, str], Key] = {}

    def add(raw: str, kind: str) -> None:
        raw = raw.strip("`*_.,;:()[]{}\"'")
        if not raw or raw in STOP_SYMBOLS:
            return
        if kind == "path" and raw.lower().endswith(STOP_PATH_SUFFIXES):
            return
        keys.setdefault((raw, kind), Key(text=raw, kind=kind))

    paths = set()
    for m in RE_PATH.finditer(text):
        paths.add(m.group(1))
        add(m.group(1), "path")
    for m in RE_DIR.finditer(text):
        raw = m.group(1)
        if raw.count("/") >= 1 and not raw.startswith(("http", "//")):
            paths.add(raw)
            add(raw, "dir")
    for m in RE_MAKE.finditer(text):
        add(m.group(1), "make")
    for m in RE_ENV.finditer(text):
        add(m.group(1), "env")
    # a bare filename inside a path is not also a symbol
    path_words = {w for p in paths for w in re.split(r"[/.]", p)}
    for regex in (RE_SNAKE, RE_CAMEL, RE_LOWER_CAMEL):
        for m in regex.finditer(text):
            tok = m.group(1)
            if tok in path_words or f"{tok}.py" in paths:
                continue
            if len(tok) < 5:
                continue
            add(tok, "symbol")
    return list(keys.values())


def probe(keys: Iterable[Key], corpus: Corpus) -> None:
    targets = corpus.make_targets()
    for key in keys:
        if key.kind in ("path", "dir"):
            hits = (
                corpus.dir_exists(key.text) if key.kind == "dir" else corpus.path_exists(key.text)
            )
            key.found_impl = [h for h in hits if h.startswith(IMPL_PREFIXES)]
            key.found_test = [h for h in hits if h not in key.found_impl]
            if not hits and key.kind == "path":
                # the cited path may not exist under that exact prefix; try the basename
                impl, tst = corpus.find(Path(key.text).name)
                key.found_impl, key.found_test = impl[:4], tst[:4]
        elif key.kind == "make":
            if key.text in targets:
                key.found_impl = ["Makefile"]
        else:
            impl, tst = corpus.find(key.text)
            key.found_impl, key.found_test = impl[:6], tst[:6]


# ---------------------------------------------------------------------------
# execution-log verdicts
# ---------------------------------------------------------------------------


class LogVerdict:
    FLIP = "FLIP_WHEN_MERGED"
    PARTIAL = "PARTIAL_OR_UNMET"
    GATED = "OWNER_OR_BLOCKED"
    ACTIVE = "IN_FLIGHT"
    NONE = "NO_SIGNAL"


# Matched against WHITESPACE-NORMALISED text. This is load-bearing: the canonical phrase
# is written wrapped across two source lines ("Atom stays `todo` only\n  because this code
# is unmerged"), so every line-oriented grep for it misses.
FLIP_PATTERNS = (
    r"only because (?:this|the) code is unmerged",
    r"only because this code is not (?:yet )?(?:on|merged)",
    r"flip it when (?:the|this) pr lands",
    r"flip (?:it|this) when it lands",
)
PARTIAL_PATTERNS = (
    r"\bpartial\b",
    r"recorded unmet",
    r"\bunmet\b",
    r"is not met",
    r"are not met",
    r"\bremaining\b",
    r"\bunmeasured\b",
    r"could not drive",
    r"stays `?todo`? (?:with|on) ",
    r"zero production (?:importers|consumers|callers)",
    r"no production (?:importer|consumer|caller)",
)
GATED_PATTERNS = (
    r"\bblocked\b",
    r"owner scope decision",
    r"owner decision",
    r"owner task",
    r"owner-gated",
    r"owner call",
    r"awaiting the owner",
)
ACTIVE_PATTERNS = (r"\bin flight\b", r"\bin-flight\b", r"pr open\b")
SUPERSEDED = re.compile(r"superseded", re.I)

HEADLINE = 260  # chars of an entry treated as its headline (where its subject is named)

# An entry starts at a heading, a bolded bullet, or a dated bullet. Splitting here rather
# than using a character window around the mention is what stops **entry bleed**: with a
# +/-420 char window, a passing mention of `DCU-3` picked up the neighbouring `DCU-2`
# "flip it when the PR lands" entry and reported DCU-3 as flippable. Measured, not feared.
ENTRY_START = re.compile(
    r"^(?:#{2,6} |-\s+\*\*|-\s+\[\d{4}-\d{2}-\d{2}\]|\*\*\d{4}-\d{2}-\d{2})", re.M
)
MENTION = re.compile(r"`([A-Z][A-Z0-9]{0,7}\d*-\d+)`")
# code comments do not backtick the id, so the caveat scan needs a bare form
MENTION_BARE = re.compile(r"\b([A-Z][A-Z0-9]{1,7}-\d+)\b")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@dataclass
class LogHit:
    verdict: str
    position: int
    excerpt: str
    plan_file: str
    headline: bool


def _verdict_for(chunk: str) -> str:
    low = _normalise(chunk).lower()
    if any(re.search(p, low) for p in FLIP_PATTERNS):
        return LogVerdict.FLIP
    if any(re.search(p, low) for p in GATED_PATTERNS):
        return LogVerdict.GATED
    if any(re.search(p, low) for p in PARTIAL_PATTERNS):
        return LogVerdict.PARTIAL
    if any(re.search(p, low) for p in ACTIVE_PATTERNS):
        return LogVerdict.ACTIVE
    return LogVerdict.NONE


def scan_plan_logs(plans_dir: Path = PLANS_DIR) -> dict[str, list[LogHit]]:
    """Classify every log entry across the plan corpus and attribute it to atom ids.

    Entries here are multi-paragraph and inconsistently started (``### 2026-08-22 —``,
    ``- **2026-08-22 —``, ``- [2026-08-16][DAS-10]``, and bare ``- **REMAINING``), and one
    entry routinely names *other* atoms in its body. So attribution is two-tier: an id in
    the entry's headline is its subject, an id only in the body is a cross-reference. If an
    atom is ever a headline subject, its body mentions are discarded — otherwise the long
    body of a neighbour's entry outvotes the atom's own verdict.
    """
    hits: dict[str, list[LogHit]] = {}
    for path in sorted(plans_dir.glob("*.md")):
        raw = path.read_text(errors="replace")
        bounds = [m.start() for m in ENTRY_START.finditer(raw)] or [0]
        bounds.append(len(raw))
        for start, end in zip(bounds, bounds[1:]):
            chunk = raw[start:end]
            verdict = _verdict_for(chunk)
            if verdict == LogVerdict.NONE:
                continue
            if SUPERSEDED.search(chunk):
                continue  # the log itself retracted this entry
            head = chunk[:HEADLINE]
            head_ids = {m.group(1) for m in MENTION.finditer(head)}
            body_ids = {m.group(1) for m in MENTION.finditer(chunk)} - head_ids
            excerpt = _normalise(head).strip()
            for atom_id in head_ids:
                hits.setdefault(atom_id, []).append(
                    LogHit(verdict, start, excerpt, path.name, True)
                )
            for atom_id in body_ids:
                hits.setdefault(atom_id, []).append(
                    LogHit(verdict, start, excerpt, path.name, False)
                )
    return hits


def decide_log(hits: Sequence[LogHit], own_plan_file: str = "") -> tuple[str, LogHit | None]:
    """Last verdict-bearing entry wins, headline subjects outranking cross-references.

    Plan logs are append-ordered, so a later entry supersedes an earlier one even when both
    carry the same date. ``PHF-7`` is exactly this: logged PARTIAL, then "all five clauses
    now MET ... flip it when this PR lands" further down the same file on the same day.

    Only the atom's **own** plan adjudicates it. A foreign plan naming the atom is making a
    dependency remark, not a ruling. Measured: ``CRE-7``, ``LMMV-7`` and ``WF2AUT-12`` were
    each reported gated by a *frontier survey* entry in ``WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS``
    that merely listed them, and ``EI-7``/``WF2WOR-12`` inherited ``PHF-2``'s PARTIAL. The
    bucket happened to be the safe one; the stated reason was simply false, which is worse
    than no reason at all.
    """
    if own_plan_file:
        hits = [h for h in hits if h.plan_file == own_plan_file]
    if not hits:
        return LogVerdict.NONE, None
    heads = [h for h in hits if h.headline]
    if heads:
        last = max(heads, key=lambda h: h.position)
        return last.verdict, last
    # Body-only: the atom was named inside another entry of its own plan. Asymmetric on
    # purpose — a cross-reference may keep an atom OUT of the flippable bucket (safe
    # direction) but may never put it in. `DCU-3` is the measured case: it appears in the
    # body of `DCU-2`'s "flip it when the PR lands" entry and inherited that verdict.
    last = max(hits, key=lambda h: h.position)
    if last.verdict == LogVerdict.FLIP:
        return LogVerdict.NONE, None
    return last.verdict, last


# ---------------------------------------------------------------------------
# bucketing
# ---------------------------------------------------------------------------

# A finding does not always land in the plan's execution log. The repo also records
# "shipped but nothing calls it" directly in code — as a ratchet test or a module comment —
# and that is invisible to a log scanner. Measured, and it is not a corner case: ``DCU-2``'s
# execution log says "COMPLETE (all three clauses); flip it when the PR lands", while
# ``tests/test_computer_use_call_sites.py`` on ``main`` proves ``check_app``,
# ``check_input_target`` and ``require_computer_use`` have **zero production callers**. The
# log entry is older than the finding, so log-only adjudication calls DCU-2 flippable when it
# is landed-but-inert. This signal exists to catch exactly that, and it is deliberately
# one-directional: it can only move an atom OUT of LANDED-AND-CLEAN, never into it.
INERT_NOTE = re.compile(
    r"(?:zero|no) production (?:caller|callers|importer|importers|consumer|consumers)|\binert\b",
    re.I,
)
INERT_WINDOW = 500

CLEAN = "LANDED-AND-CLEAN"
GATED = "LANDED-BUT-GATED"
OPEN = "NOT-LANDED"
UNKNOWN = "UNDECIDABLE-CHEAPLY"
BUCKETS = (CLEAN, GATED, OPEN, UNKNOWN)


@dataclass
class Verdict:
    atom: Atom
    keys: list[Key]
    log_verdict: str
    log_hit: LogHit | None
    bucket: str
    evidence: str
    why: str

    @property
    def impl_keys(self) -> list[Key]:
        return [k for k in self.keys if k.in_impl]

    def key_summary(self, limit: int = 4) -> str:
        found = [k for k in self.keys if k.in_impl]
        if not found:
            return "—"
        shown = found[:limit]
        extra = f" +{len(found) - limit}" if len(found) > limit else ""
        return ", ".join(f"{k.text}@{(k.found_impl or ['?'])[0]}" for k in shown) + extra


def score_evidence(keys: Sequence[Key]) -> tuple[str, str]:
    """STRONG / WEAK / ABSENT / NO-KEYS, plus a one-line reason carrying the numbers."""
    if not keys:
        return "NO-KEYS", "no deliverable name could be extracted from the atom's prose"
    paths = [k for k in keys if k.kind in ("path", "dir")]
    named = [k for k in keys if k.kind in ("symbol", "make", "env")]
    n_impl = sum(1 for k in named if k.in_impl)
    paths_ok = sum(1 for k in paths if k.in_impl)
    ratio = (n_impl / len(named)) if named else 0.0
    detail = (
        f"{n_impl}/{len(named)} named symbols in impl, {paths_ok}/{len(paths)} cited paths exist"
    )
    if paths and paths_ok == len(paths) and (not named or ratio >= 0.5):
        return "STRONG", detail
    if ratio >= 0.65 and n_impl >= 3:
        if not paths:
            # Symbol-only evidence is the weakest kind that still scores STRONG, because an
            # atom names the surface it INTEGRATES WITH as readily as the thing it builds.
            # Measured false positive: `EI-2` ("`docker` provider") matched
            # allowed_write_paths / egress_tier / safety_profile / SandboxSpec — all
            # pre-existing — while `sandbox_providers/` on main still holds only `none.py`.
            # The bucket is unchanged (it can never reach LANDED-AND-CLEAN without a FLIP
            # log entry); the caveat is printed so the reader does not over-read the score.
            detail += (
                "; SYMBOL-ONLY (no path named — may be the integration"
                " surface, not the deliverable)"
            )
        return "STRONG", detail
    if ratio >= 0.34 or paths_ok:
        return "WEAK", detail
    return "ABSENT", detail


def scan_code_caveats(corpus: Corpus) -> dict[str, list[tuple[str, str]]]:
    """Atom ids named beside an inertness note in shipped code (not in ``docs/``)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for path, text in corpus.blobs.items():
        for m in INERT_NOTE.finditer(text):
            window = text[max(0, m.start() - INERT_WINDOW) : m.end() + INERT_WINDOW]
            excerpt = _normalise(window[max(0, INERT_WINDOW - 160) : INERT_WINDOW + 200]).strip()
            for atom_id in set(MENTION_BARE.findall(window)):
                out.setdefault(atom_id, []).append((path, excerpt))
    return out


def classify(
    atom: Atom,
    keys: list[Key],
    hits: Sequence[LogHit],
    code_caveats: Sequence[tuple[str, str]] = (),
) -> Verdict:
    log_verdict, log_hit = decide_log(hits, own_plan_file=atom.plan_file)
    evidence, detail = score_evidence(keys)

    if log_verdict == LogVerdict.FLIP:
        if evidence in ("STRONG", "WEAK") and code_caveats:
            where, excerpt = code_caveats[0]
            bucket = GATED
            why = (
                f"log says complete-but-unmerged, but {where} on the ref records an "
                f"inertness gap NEWER than that entry: “{excerpt[:200]}”"
            )
        elif evidence in ("STRONG", "WEAK"):
            bucket, why = CLEAN, f"log says complete-but-unmerged; {detail}"
        else:
            bucket = UNKNOWN
            why = (
                "log says complete-but-unmerged but no deliverable name is visible on the "
                f"ref ({detail}) — the two signals disagree"
            )
    elif log_verdict in (LogVerdict.PARTIAL, LogVerdict.GATED):
        label = (
            "an unmet clause" if log_verdict == LogVerdict.PARTIAL else "an owner call / BLOCKED"
        )
        if evidence in ("STRONG", "WEAK"):
            bucket, why = GATED, f"deliverable present but the log names {label}; {detail}"
        else:
            bucket, why = OPEN, f"log names {label} and no deliverable on the ref; {detail}"
    elif evidence == "STRONG":
        bucket = UNKNOWN
        why = (
            f"deliverable names are on the ref ({detail}) but no execution-log entry "
            "adjudicates the done_when — grep cannot certify a criterion"
        )
        if atom.is_retirement:
            why = (
                f"RETIREMENT atom: the named thing still EXISTS on the ref ({detail}), which "
                "is evidence AGAINST completion, not for it"
            )
    elif evidence == "NO-KEYS":
        bucket, why = UNKNOWN, detail
    else:
        bucket, why = OPEN, f"no deliverable evidence on the ref; {detail}"

    return Verdict(atom, keys, log_verdict, log_hit, bucket, evidence, why)


def census(
    ref: str = DEFAULT_REF,
    statuses: Sequence[str] = ("todo",),
    dag_path: Path = DAG_PATH,
    plans_dir: Path = PLANS_DIR,
) -> tuple[list[Verdict], Corpus]:
    atoms = [a for a in load_atoms(dag_path) if a.status in statuses]
    corpus = load_corpus(ref)
    hits = scan_plan_logs(plans_dir)
    caveats = scan_code_caveats(corpus)
    out: list[Verdict] = []
    for atom in atoms:
        keys = extract_keys(atom)
        probe(keys, corpus)
        out.append(classify(atom, keys, hits.get(atom.id, []), caveats.get(atom.id, [])))
    return out, corpus


# ---------------------------------------------------------------------------
# stranded branches
# ---------------------------------------------------------------------------


@dataclass
class Branch:
    name: str
    atom_id: str
    commits_ahead: int
    carries_new_content: bool | None
    note: str
    added_code_lines: int = 0
    unlanded_code_lines: int = 0
    unlanded_doc_lines: int = 0
    sample: tuple[str, ...] = ()
    new_files: tuple[str, ...] = ()  # code files the branch adds that the ref does not have
    shape: str = ""  # NEW-DELIVERABLE | SUPERSEDED-DRAFT | ""


# Only these trees answer "does the branch carry code that is not on main". A branch's own
# execution-log entry and its dag.json row routinely never land (the owner folds those in at
# integration), so counting docs/ would mark every drained branch as stranded.
BRANCH_CODE_PATHS = (
    "src",
    "tests",
    "web",
    "Makefile",
    "scripts",
    "desktop",
    "apps",
    ".github",
    "pyproject.toml",
)


def _branch_token(atom_id: str) -> str:
    code, _, num = atom_id.rpartition("-")
    return f"{code}{num}".lower()


def scan_branches(atom_ids: Sequence[str], ref: str, corpus: Corpus) -> list[Branch]:
    """Which leftover branches carry code that is NOT on ``ref``?

    ``git cherry`` is not usable here — it compares patch-ids, so a commit whose content
    landed by cherry-pick or squash still prints ``+``.

    ``git apply --reverse --check`` is the obvious next idea and it is **also** wrong, in the
    unsafe direction: it needs the surrounding *context* lines to still match, so once main
    has moved elsewhere in the same file the reverse fails even though every added line is
    already upstream. Measured: it reported ``feature-dcu2-target-policy`` as carrying new
    content while ``git rebase origin/main`` on a throwaway copy rebased it to **zero**
    commits.

    So the test is whole-line coverage: take the branch's cumulative three-dot diff over the
    code trees, and ask whether every non-trivial line it *adds* already exists somewhere in
    the ref. Context-free, so main moving cannot fool it, and it agrees with rebase on every
    case checked by hand.
    """
    tokens = {_branch_token(a): a for a in atom_ids}
    landed = corpus.lines()
    listing = _git(
        ["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"]
    )
    seen: dict[str, str] = {}
    for name in listing.splitlines():
        short = name.split("/", 1)[1] if name.startswith("origin/") else name
        if not short.startswith(("feature-", "improvement-", "bugfix-")):
            continue
        for seg in short.split("-"):
            if seg in tokens:
                seen.setdefault(short, tokens[seg])
                break

    def added_lines(spec: Sequence[str]) -> list[str]:
        diff = _git(["diff", f"{ref}...{branch}", "--", *spec])
        return [
            ln[1:].strip()
            for ln in diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++") and len(ln[1:].strip()) > 3
        ]

    out: list[Branch] = []
    for branch, atom_id in sorted(seen.items()):
        try:
            ahead = int(_git(["rev-list", "--count", f"{ref}..{branch}"]).strip() or 0)
        except RuntimeError:
            out.append(Branch(branch, atom_id, -1, None, "ref unreadable"))
            continue
        if ahead == 0:
            out.append(Branch(branch, atom_id, 0, False, "no commits beyond the ref"))
            continue

        code = added_lines(BRANCH_CODE_PATHS)
        unlanded = [ln for ln in code if ln not in landed]
        doc_unlanded = sum(1 for ln in added_lines(["docs"]) if ln not in landed)

        touched = [
            p
            for p in _git(
                ["diff", "--name-only", f"{ref}...{branch}", "--", *BRANCH_CODE_PATHS]
            ).splitlines()
            if p
        ]
        on_ref = set(corpus.paths)
        new_files = tuple(p for p in touched if p not in on_ref)

        shape = ""
        if not code:
            note, carries = "touches no code tree", False
        elif unlanded:
            carries = True
            # A branch whose unlanded lines all sit in files the ref ALREADY has is usually a
            # superseded draft, not stranded work: main holds a newer take on the same file.
            # Measured: `feature-phf7-scripted-binding` differs only by the OLD env-var name
            # (`PERSONALCLAW_SCRIPTED_LLM`; main ships `..._MODEL_SCRIPT`) and a shorter test
            # (376 lines vs main's 476). Reporting that as stranded work would be a lie by
            # omission, so the shape is reported next to the count.
            shape = "NEW-DELIVERABLE" if new_files else "SUPERSEDED-DRAFT?"
            note = (
                f"carries {len(unlanded)} of {len(code)} added code lines not on the ref [{shape}]"
            )
            if new_files:
                note += f"; adds {len(new_files)} file(s) absent from the ref"
        else:
            note, carries = f"all {len(code)} added code lines are on the ref", False
        if carries is False and doc_unlanded:
            note += f" (its {doc_unlanded} plan-log/dag lines never landed)"
        out.append(
            Branch(
                branch,
                atom_id,
                ahead,
                carries,
                note,
                added_code_lines=len(code),
                unlanded_code_lines=len(unlanded),
                unlanded_doc_lines=doc_unlanded,
                sample=tuple(unlanded[:3]),
                new_files=new_files[:4],
                shape=shape,
            )
        )
    return out


# ---------------------------------------------------------------------------
# wire check: would DELETING this caller be caught?
# ---------------------------------------------------------------------------
#
# The census above asks "is the atom's named deliverable on the ref, and does the log say so".
# That question is one level too shallow, and ``APE-3`` is the measured proof: the census
# scored it LANDED-AND-CLEAN, a deep audit confirmed all five done_when clauses hold
# behaviourally — and then found that deleting ``start_worker_watchdog()`` from
# ``providers/loader.py::load_all_extensions`` **and** ``_stop_worker(name)`` from both
# ``app_manager.disable`` and ``app_manager.force_uninstall`` left **116 tests green** across
# test_app_worker_runtime, test_app_background_contract, test_app_manager_lifecycle,
# test_app_manager_update, test_apps_import_boundary, test_spawn_ceiling_audit,
# test_spawn_hazard_audit and test_inert_surface_baseline.
#
# The importers existed. They were named. Nothing asserted them. A wire that exists but is
# asserted by nothing is one refactor away from a wire that does not exist, and the census
# called that CLEAN. So the check here is not "is there a production caller?" but "would the
# suite notice if the caller went away?" — answered the only way it can be answered honestly:
# neutralise the call, run a bounded selection, observe.
#
# This is destructive and slow, so it is strictly opt-in (``--check-wires``) and never part of
# a default census run. Everything below is built around three properties:
#
#   never leave a mutated tree   file-copy snapshot + hash-verified restore, wired to atexit
#                                AND to SIGINT/SIGTERM. ``git checkout -- <file>`` is never
#                                used: it would also revert a sibling's unrelated edit.
#   bounded                      a derived, capped, per-wire selection — never ``make test``.
#                                What was NOT run is reported, because a green from a narrow
#                                selection is exactly the false comfort this check removes.
#   refuse rather than guess     a dirty target file, an unparseable module, a call site that
#                                is not uniquely locatable, an empty selection, a red or
#                                empty baseline, a mutation that does not verify, or a
#                                collection error after mutation → REFUSED, with the reason
#                                named. A silent skip that reads as "wire is railed" is the
#                                defect; a refusal that names its reason is a good outcome.

MUT_MARK = "pass  # WIRECHECK-MUTATED"
WIRE_RAILED = "RAILED"
WIRE_UNRAILED = "UNRAILED"
WIRE_REFUSED = "REFUSED"

# Words that pass the >=4-char filter and carry no discriminating signal about a deliverable.
_PROSE_STOP = {
    "also",
    "another",
    "beside",
    "both",
    "does",
    "each",
    "from",
    "have",
    "into",
    "must",
    "only",
    "same",
    "than",
    "that",
    "then",
    "this",
    "used",
    "uses",
    "when",
    "with",
    "without",
}


def _module_dotted(rel: str) -> str:
    """``src/personalclaw/apps/x.py`` → ``personalclaw.apps.x`` (the import path)."""
    return rel[len("src/") : -len(".py")].replace("/", ".")


def _prose_tokens(atom: Atom) -> set[str]:
    words = re.findall(r"[a-z]{4,}", f"{atom.title} {atom.scope} {atom.done_when}".lower())
    return {w for w in words if w not in _PROSE_STOP}


def _symbol_tokens(symbol: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", symbol)
    return {t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if len(t) >= 4}


def _token_overlap(symbol: str, prose: set[str]) -> int:
    """How much of the symbol's own name appears in the atom's done_when vocabulary.

    Prefix-tolerant in both directions so ``stop`` matches ``stops`` and ``notify`` matches
    ``notifies`` — plan prose conjugates and code does not.
    """
    hits = 0
    for st in _symbol_tokens(symbol):
        if any(st == pt or pt.startswith(st) or st.startswith(pt) for pt in prose):
            hits += 1
    return hits


@dataclass(frozen=True)
class CallSite:
    """One neutralisable production call statement."""

    path: str  # repo-relative
    lineno: int  # 1-based, inclusive
    end_lineno: int  # 1-based, inclusive
    col: int  # indent of the statement
    end_col: int  # column just past the statement on its last line
    text: str  # the source of the statement, normalised to one line

    @property
    def where(self) -> str:
        span = (
            f"{self.lineno}"
            if self.lineno == self.end_lineno
            else f"{self.lineno}-{self.end_lineno}"
        )
        return f"{self.path}:{span}"


@dataclass
class Wire:
    """A production symbol plus every side-effect-only call statement that invokes it."""

    module: str  # dotted module that DEFINES the symbol
    symbol: str
    def_path: str  # repo-relative path of the defining module
    sites: tuple[CallSite, ...]
    relevance: float
    cross_module: bool
    annotated: bool

    @property
    def name(self) -> str:
        return f"{self.module.rsplit('.', 1)[-1]}::{self.symbol}"

    @property
    def site_modules(self) -> tuple[str, ...]:
        return tuple(sorted({_module_dotted(s.path) for s in self.sites}))


def annotated_modules(atom_id: str, root: Path) -> list[str]:
    """Production modules that name the atom id in their own text.

    This is the load-bearing derivation, and it is not a heuristic reach: this repo
    annotates the seam it builds (``# APE-3: the same sweep shape for app background
    WORKERS``). It matters because an atom's prose frequently does NOT name the symbol
    that carries its done_when — ``APE-3`` says "survives a crash (watchdog)" and never
    writes ``start_worker_watchdog``, so key extraction alone cannot reach the wire.
    """
    pattern = re.compile(rf"\b{re.escape(atom_id)}\b")
    out: list[str] = []
    for path in sorted((root / "src").rglob("*.py")):
        try:
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                out.append(str(path.relative_to(root)))
        except OSError:
            continue
    return out


def keyed_modules(atom: Atom, root: Path) -> list[str]:
    """Production modules the atom's own path keys name. The fallback when no annotation."""
    out: set[str] = set()
    for key in extract_keys(atom):
        if key.kind != "path" or not key.text.endswith(".py"):
            continue
        tail = key.text.lstrip("./")
        for path in (root / "src").rglob("*.py"):
            rel = str(path.relative_to(root))
            if rel.endswith("/" + tail) or rel.endswith("/" + Path(tail).name):
                out.add(rel)
    return sorted(out)


def _enclosing_defs(tree: ast.AST) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, node.end_lineno or node.lineno))
    return out


def _binding_map(tree: ast.AST, defining: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Local name → dotted module, for imports that reach a defining module.

    Resolution is by BINDING, not by bare attribute name. That distinction is worth 24 wires
    against 572: matching ``ast.Attribute`` callees on ``.attr`` alone made every
    ``x.update()``, ``t.start()``, ``p.wait()`` and ``time.sleep()`` in the tree look like a
    call into the atom's module.
    """
    from_imports: dict[str, str] = {}
    mod_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in defining:
            for alias in node.names:
                from_imports[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in defining:
                    mod_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
    return from_imports, mod_aliases


def _statement_text(lines: Sequence[str], site_lineno: int, end_lineno: int) -> str:
    raw = "".join(lines[site_lineno - 1 : end_lineno])
    return _normalise(raw).strip()


def _defining_funcs(
    root: Path, modules: Sequence[str]
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    defining: dict[str, str] = {}  # dotted module -> repo-relative path
    funcs: dict[tuple[str, str], str] = {}  # (dotted module, symbol) -> path
    for rel in modules:
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        dotted = _module_dotted(rel)
        defining[dotted] = rel
        for node in tree.body:  # module level only: a method needs its receiver resolved
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs[(dotted, node.name)] = rel
    return defining, funcs


def resolve_sites(
    root: Path,
    defining: dict[str, str],
    funcs: dict[tuple[str, str], str],
    atom_id: str = "",
    scope: Sequence[str] | None = None,
) -> tuple[dict[tuple[str, str], list[CallSite]], set[tuple[str, str]]]:
    """Locate every side-effect-only call statement in ``src/`` that reaches ``funcs``.

    Also used, post-mutation, to prove the sites are GONE — same resolver, so the
    verification cannot disagree with the locator about what a call site is.
    """
    atom_re = re.compile(rf"\b{re.escape(atom_id)}\b") if atom_id else None
    found: dict[tuple[str, str], list[CallSite]] = {}
    annotated_sites: set[tuple[str, str]] = set()
    candidates = (
        [root / p for p in scope] if scope is not None else sorted((root / "src").rglob("*.py"))
    )
    for path in candidates:
        rel = str(path.relative_to(root))
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines(keepends=True)
        me = _module_dotted(rel)
        from_imports, mod_aliases = _binding_map(tree, defining)
        defs = _enclosing_defs(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
                continue
            func = node.value.func
            target: tuple[str, str] | None = None
            if isinstance(func, ast.Name):
                if func.id in from_imports and (from_imports[func.id], func.id) in funcs:
                    target = (from_imports[func.id], func.id)
                elif me in defining and (me, func.id) in funcs:
                    target = (me, func.id)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                dotted = mod_aliases.get(func.value.id)
                if dotted and (dotted, func.attr) in funcs:
                    target = (dotted, func.attr)
            if target is None:
                continue
            # a recursive call inside the symbol's own body is not a wire into it
            if any(name == target[1] and start <= node.lineno <= end for name, start, end in defs):
                continue
            site = CallSite(
                path=rel,
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                col=node.col_offset,
                end_col=node.end_col_offset or 0,
                text=_statement_text(lines, node.lineno, node.end_lineno or node.lineno),
            )
            found.setdefault(target, []).append(site)
            window = "".join(lines[max(0, node.lineno - 11) : node.lineno + 10])
            if atom_re is not None and atom_re.search(window):
                annotated_sites.add(target)
    return found, annotated_sites


def find_wires(atom: Atom, root: Path, modules: Sequence[str]) -> list[Wire]:
    """Every side-effect-only call, in ``src/``, of a module-level function of ``modules``.

    Scope is deliberately narrow: only ``Expr(Call)`` statements — a call made purely for its
    effect. Two reasons, and neither is convenience. (1) These are exactly the wires that go
    unrailed: nothing consumes their value, so no type checker and no return-value assertion
    notices their absence. Both APE-3 sites are of this shape. (2) They are the only shape
    that can be neutralised into ``pass`` while staying syntactically valid AND semantically
    honest — rewriting ``x = f()`` to ``x = None`` measures a NameError-shaped cascade, not
    the wire. Value-consuming calls are therefore OUT of scope and reported as such.
    """
    defining, funcs = _defining_funcs(root, modules)
    if not funcs:
        return []
    prose = _prose_tokens(atom)
    found, annotated_sites = resolve_sites(root, defining, funcs, atom.id)

    wires: list[Wire] = []
    for (dotted, symbol), sites in found.items():
        cross = any(_module_dotted(s.path) != dotted for s in sites)
        annotated = (dotted, symbol) in annotated_sites
        score = (
            2.0 * _token_overlap(symbol, prose)
            + (2.0 if cross else 0.0)
            + (2.0 if annotated else 0.0)
            - 0.2 * min(len(sites), 6)
        )
        wires.append(
            Wire(
                module=dotted,
                symbol=symbol,
                def_path=funcs[(dotted, symbol)],
                sites=tuple(sorted(sites, key=lambda s: (s.path, s.lineno))),
                relevance=round(score, 2),
                cross_module=cross,
                annotated=annotated,
            )
        )
    wires.sort(key=lambda w: (-w.relevance, w.symbol))
    return wires


# ---------------------------------------------------------------------------
# bounded test selection
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    files: tuple[str, ...]
    reasons: dict[str, str]
    cut: tuple[str, ...]  # scored above zero, dropped by the cap
    total_test_files: int

    @property
    def tiers(self) -> str:
        acc: dict[str, int] = {}
        for why in self.reasons.values():
            for tier in why.split("+"):
                acc[tier] = acc.get(tier, 0) + 1
        return " · ".join(f"{k}:{v}" for k, v in sorted(acc.items()))


# This check's OWN suite names production symbols — as ground truth for the locator, not as
# rails on the wire. Selecting it would (a) score a self-reference at the top tier and (b)
# re-enter the whole check recursively inside a child pytest. Measured: adding APE-3's wires to
# this file's docstrings put `test_audit_landed_atoms.py` first in APE-3's own selection.
SELECTION_EXCLUDE = ("tests/test_audit_landed_atoms.py",)


def _test_index(root: Path) -> dict[str, tuple[str, set[str]]]:
    """test file → (its text, the dotted modules it imports)."""
    out: dict[str, tuple[str, set[str]]] = {}
    for path in sorted((root / "tests").glob("test_*.py")):
        rel = str(path.relative_to(root))
        if rel in SELECTION_EXCLUDE:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
                for alias in node.names:  # `from personalclaw.apps import app_manager`
                    imports.add(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
        out[rel] = (text, imports)
    return out


def select_tests(
    wire: Wire, atom: Atom, index: dict[str, tuple[str, set[str]]], cap: int
) -> Selection:
    """Derive a capped selection, and record precisely what it left out.

    Weighted so that a signal SPECIFIC to this wire outranks a generic one. ``app_manager``
    appears in ~40 test files, so scoring "mentions the module" as highly as "mentions the
    symbol" buries the two suites that actually drive the seam under three dozen neighbours —
    measured while building this, on this atom.
    """
    relevant = {wire.module, *wire.site_modules}
    stems = {m.rsplit(".", 1)[-1] for m in relevant}
    scored: list[tuple[float, str, str]] = []
    for rel, (text, imports) in index.items():
        score = 0.0
        why: list[str] = []
        if wire.symbol in text:
            score += 100  # a textual rail can only live in a file that names the symbol
            why.append("symbol")
        if re.search(rf"\b{re.escape(atom.id)}\b", text):
            score += 50
            why.append("atom-id")
        stem = Path(rel).stem
        if any(s in stem for s in stems):
            score += 40  # tests/test_app_manager_*.py is about app_manager
            why.append("name")
        if imports & relevant:
            score += 20  # to observe the effect you generally have to drive the module
            why.append("import")
        if score:
            scored.append((score, rel, "+".join(why)))
    scored.sort(key=lambda t: (-t[0], t[1]))
    keep = scored[:cap]
    return Selection(
        files=tuple(rel for _, rel, _ in keep),
        reasons={rel: why for _, rel, why in keep},
        cut=tuple(rel for _, rel, _ in scored[cap:]),
        total_test_files=len(index),
    )


# ---------------------------------------------------------------------------
# snapshot / restore
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Snapshot:
    """File-copy snapshot with hash-verified restore. The tree is never left mutated.

    ``git checkout -- <file>`` is deliberately NOT the restore mechanism: it restores the
    INDEX's version, so it would silently discard an unrelated edit that happened to be in
    the same file, and it fails open in a detached/rebasing worktree. A byte copy taken
    before the write, restored and then hash-compared, is the only restore that can be
    *verified*.

    Restore is wired to ``atexit`` and to SIGINT/SIGTERM, so a Ctrl-C or a harness kill
    restores rather than stranding a ``pass  # WIRECHECK-MUTATED`` in a shared checkout.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = Path(tempfile.mkdtemp(prefix="wirecheck-snap-"))
        self.originals: dict[str, str] = {}  # rel -> sha256 before mutation
        self.failures: list[str] = []
        self._armed = False
        self._prev_handlers: dict[int, object] = {}

    def arm(self) -> None:
        if self._armed:
            return
        self._armed = True
        atexit.register(self._on_exit)
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(sig)

            def handler(signum: int, frame: object, _prev: object = previous) -> None:
                self.restore()
                if callable(_prev):
                    _prev(signum, frame)  # type: ignore[operator]
                raise SystemExit(130)

            try:
                signal.signal(sig, handler)
            except ValueError:  # not the main thread
                continue
            self._prev_handlers[int(sig)] = previous

    def disarm(self) -> None:
        """Put the process's own handlers back.

        Arming is process-global, so a Snapshot that never disarms leaves its handler (and its
        ``atexit`` hook) installed for everything that runs afterwards — including, when this
        runs under pytest, every later test in the session.
        """
        if not self._armed:
            return
        self._armed = False
        atexit.unregister(self._on_exit)
        for signum, previous in self._prev_handlers.items():
            try:
                signal.signal(signum, previous)  # type: ignore[arg-type]
            except (ValueError, TypeError):  # pragma: no cover
                pass
        self._prev_handlers.clear()

    def take(self, rel: str) -> None:
        if rel in self.originals:
            return
        src = self.root / rel
        if src.is_symlink():
            raise WireRefusal(f"{rel} is a symlink; refusing to mutate through it")
        self.arm()
        shutil.copy2(src, self.dir / rel.replace("/", "__"))
        self.originals[rel] = _sha256(src)

    def restore(self) -> list[str]:
        """Copy every snapshot back and VERIFY byte identity. Returns unrecovered files."""
        broken: list[str] = []
        for rel, want in list(self.originals.items()):
            live = self.root / rel
            backup = self.dir / rel.replace("/", "__")
            try:
                shutil.copy2(backup, live)
                _drop_pycache(live)
                if _sha256(live) != want:
                    broken.append(rel)
            except OSError as exc:  # pragma: no cover - filesystem failure
                broken.append(f"{rel} ({exc})")
        if broken:
            self.failures = broken
        else:
            self.originals.clear()
        return broken

    def _on_exit(self) -> None:  # pragma: no cover - process teardown
        if self.originals:
            broken = self.restore()
            if broken:
                print(
                    "audit_landed_atoms: RESTORE FAILED for "
                    f"{', '.join(broken)} — originals are in {self.dir}",
                    file=sys.stderr,
                )
                return
        if not self.failures:  # last resort: never leave a copy of source in $TMPDIR
            shutil.rmtree(self.dir, ignore_errors=True)

    def discard(self) -> None:
        self.disarm()
        if not self.failures:
            shutil.rmtree(self.dir, ignore_errors=True)


def _drop_pycache(path: Path) -> None:
    """Remove the module's cached bytecode.

    ``shutil.copy2`` restores the ORIGINAL mtime, so a ``.pyc`` compiled from the mutated
    source can end up looking valid for the restored file — a stale-bytecode false green in
    whatever runs next. Children also get ``PYTHONDONTWRITEBYTECODE=1``; this is the other half.
    """
    cache = path.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{path.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


class WireRefusal(RuntimeError):
    """This wire cannot be measured safely or honestly. Named reason, never a silent skip."""


def _progress(msg: str) -> None:
    """A wire check is minutes long; silence for minutes is indistinguishable from a hang."""
    print(f"  … {msg}", file=sys.stderr, flush=True)


def _indent(text: str, lines: int = 8) -> str:
    """The last few lines of a run, for a refusal that has to be diagnosable ONCE.

    A refusal whose cause is not reproducible (measured: one 12-file baseline exited 1 with
    "425 passed" and no failing node, and a direct re-run of the identical selection exited 0)
    is only debuggable if the refusal carries the output. Otherwise the next occurrence starts
    the investigation from zero.
    """
    tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "\n".join(f"                 | {ln[:140]}" for ln in tail)


# ---------------------------------------------------------------------------
# mutation
# ---------------------------------------------------------------------------


def mutate(root: Path, wire: Wire, snap: Snapshot) -> None:
    """Replace every call statement of ``wire`` with ``pass  # WIRECHECK-MUTATED``.

    ``pass`` and not a truncation: chopping a multi-line call breaks the parse, and a parse
    break yields a COLLECTION ERROR, which is not evidence about the wire — it is evidence
    about the mutation. Verified after the write by re-parsing.

    Refuses unless each statement OWNS its lines (only whitespace before it, only whitespace
    or a comment after it). Two calls on one line, or a call inside a lambda/comprehension,
    are not uniquely locatable, and guessing there is how a check starts reporting on
    something other than what it changed.
    """
    by_file: dict[str, list[CallSite]] = {}
    for site in wire.sites:
        by_file.setdefault(site.path, []).append(site)
    for rel, sites in by_file.items():
        path = root / rel
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines(keepends=True)
        for site in sorted(sites, key=lambda s: -s.lineno):
            head = lines[site.lineno - 1][: site.col]
            if head.strip():
                raise WireRefusal(
                    f"{site.where} does not own its line (prefix {head.strip()!r}); "
                    "not uniquely locatable"
                )
            # exact, by column: `f(); g()` would pass a "line ends with ')'" test and then
            # have its SIBLING statement deleted along with it.
            rest = lines[site.end_lineno - 1][site.end_col :].strip()
            if rest and not rest.startswith("#"):
                raise WireRefusal(
                    f"{site.where} shares its line with {rest[:40]!r}; not uniquely locatable"
                )
            newline = "\n"
            if lines[site.end_lineno - 1].endswith("\r\n"):
                newline = "\r\n"
            lines[site.lineno - 1 : site.end_lineno] = [
                f"{' ' * site.col}{MUT_MARK}: {wire.symbol}{newline}"
            ]
        snap.take(rel)
        path.write_text("".join(lines), encoding="utf-8", newline="")
        _drop_pycache(path)

    # falsify the mutation: it must parse, the marker must be there, and the wire's call
    # sites must be GONE. A mutation that silently did not land reports the wire as railed.
    for rel, sites in by_file.items():
        text = (root / rel).read_text(encoding="utf-8")
        try:
            ast.parse(text)
        except SyntaxError as exc:
            raise WireRefusal(f"mutating {rel} broke the parse ({exc}); restored") from exc
        if text.count(MUT_MARK) != len(sites):
            raise WireRefusal(
                f"{rel} holds {text.count(MUT_MARK)} mutation markers, expected {len(sites)}"
            )
    defining, funcs = _defining_funcs(root, [wire.def_path])
    still, _ = resolve_sites(root, defining, funcs, scope=sorted(by_file))
    live = still.get((wire.module, wire.symbol), [])
    if live:
        raise WireRefusal(
            f"{wire.name} still has {len(live)} live call site(s) after mutation "
            f"({', '.join(s.where for s in live)}); the mutation did not land"
        )


# ---------------------------------------------------------------------------
# bounded pytest run
# ---------------------------------------------------------------------------

RE_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|deselected)")
# This project's ``addopts`` carry ``--color=yes``, so pytest emits colour even into a pipe and
# the short-summary lines arrive as ``\x1b[31mFAILED\x1b[0m tests/…``. A ``startswith("FAILED ")``
# parse therefore matched NOTHING while the counts line still parsed — so a mutated run that
# reported "2 failed" came back with an empty ``failed_ids`` and the wire was scored UNRAILED.
# Measured on the real APE-3 ground-truth case, which is the one case whose answer was known.
# ``--color=no`` is passed below AND the escapes are stripped here; ``_attribution_failed``
# turns any residual mismatch into a refusal instead of a false UNRAILED.
RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class RunResult:
    returncode: int
    counts: dict[str, int]
    failed_ids: tuple[str, ...]
    error_ids: tuple[str, ...]
    seconds: float
    timed_out: bool
    tail: str

    @property
    def collected(self) -> int:
        return sum(v for k, v in self.counts.items() if k != "deselected")

    @property
    def green(self) -> bool:
        return (
            not self.timed_out
            and self.returncode == 0
            and not self.failed_ids
            and not self.error_ids
        )

    @property
    def attribution_failed(self) -> bool:
        """The run reported a red that the parser could not attribute to a node id.

        The vacuity floor on red DETECTION. Without it, any change to pytest's summary format
        silently degrades every verdict to UNRAILED — the safe-looking answer, and the wrong
        one. This is not hypothetical: ``--color=yes`` in this project's ``addopts`` did exactly
        that, and the synthetic fixture missed it because a fresh ini has no colour.
        """
        if self.timed_out:
            return False
        reds = self.counts.get("failed", 0) + self.counts.get("error", 0)
        if reds and not (self.failed_ids or self.error_ids):
            return True
        return self.returncode != 0 and not reds and self.collected > 0

    @property
    def summary(self) -> str:
        if self.timed_out:
            return f"TIMED OUT after {self.seconds:.0f}s"
        parts = [f"{v} {k}" for k, v in self.counts.items()]
        return ", ".join(parts) or f"no counts (rc={self.returncode})"


def run_pytest(root: Path, files: Sequence[str], timeout: int, with_cov: bool = False) -> RunResult:
    """Run exactly ``files``, in its own process group, with a hard timeout.

    Cleanup is scoped to THIS process group — ``os.killpg`` on the child's own pgid. Never a
    pattern kill: four other agents share this machine and a bare ``pkill -f pytest`` took a
    sibling's suite out this week.

    ``-n0`` on purpose. xdist reshuffles which worker gets which test, and a narrowed
    selection reshuffles it differently from the full suite, which is its own source of
    cross-test isolation leaks. Determinism matters more than speed for a selection this size.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-n0",
        "-q",
        "-rfE",
        "--tb=line",
        "--color=no",  # the project's addopts force colour; ANSI breaks node-id attribution
        "-p",
        "no:cacheprovider",
    ]
    if not with_cov:
        cmd.append("--no-cov")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root)])
    # never the real home, whatever the suite's own fixtures do or fail to do
    home = tempfile.mkdtemp(prefix="wirecheck-home-")
    env["PERSONALCLAW_HOME"] = home
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # its own process group, so the kill below cannot reach us
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover
            proc.kill()
        out, _ = proc.communicate()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    out = RE_ANSI.sub("", out or "")  # belt and braces: --color=no is not the only source
    counts: dict[str, int] = {}
    for match in RE_COUNTS.finditer(out):
        counts[match.group(2).rstrip("s")] = int(match.group(1))
    failed = tuple(
        ln.split(" ", 1)[1].split(" - ")[0].strip()
        for ln in out.splitlines()
        if ln.startswith("FAILED ")
    )
    errored = tuple(
        ln.split(" ", 1)[1].split(" - ")[0].strip()
        for ln in out.splitlines()
        if ln.startswith("ERROR ")
    )
    return RunResult(
        returncode=proc.returncode,
        counts=counts,
        failed_ids=failed,
        error_ids=errored,
        seconds=time.monotonic() - started,
        timed_out=timed_out,
        tail="\n".join(out.splitlines()[-25:]),
    )


# ---------------------------------------------------------------------------
# the check itself
# ---------------------------------------------------------------------------


@dataclass
class WireCheck:
    wire: Wire
    status: str
    selection: Selection | None
    baseline: RunResult | None
    mutated: RunResult | None
    reason: str

    @property
    def caught_by(self) -> tuple[str, ...]:
        if self.mutated is None:
            return ()
        return self.mutated.failed_ids


def check_wire(
    root: Path,
    atom: Atom,
    wire: Wire,
    index: dict[str, tuple[str, set[str]]],
    snap: Snapshot,
    *,
    cap: int,
    timeout: int,
    with_cov: bool,
    baseline_cache: dict[tuple[str, ...], RunResult],
) -> WireCheck:
    selection = select_tests(wire, atom, index, cap)
    if not selection.files:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            None,
            None,
            "no test file scored above zero for this wire — nothing to run, and a "
            "vacuous run must never read as 'railed'",
        )
    for rel in selection.files:  # a mistyped path yields "no tests ran", an UNRUN leg
        if not (root / rel).is_file():
            return WireCheck(
                wire, WIRE_REFUSED, selection, None, None, f"selected {rel} does not exist"
            )
    dirty = _git(
        ["status", "--porcelain", "--", *sorted({s.path for s in wire.sites})], cwd=root
    ).strip()
    if dirty:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            None,
            None,
            f"target file(s) are dirty in the working tree ({dirty.splitlines()[0]}); "
            "refusing to mutate over an uncommitted edit",
        )

    key = tuple(selection.files)
    baseline = baseline_cache.get(key)
    _progress(f"{wire.name}: baseline over {len(selection.files)} file(s)")
    if baseline is None:
        baseline = run_pytest(root, selection.files, timeout, with_cov)
        baseline_cache[key] = baseline
    else:
        _progress(f"{wire.name}: baseline reused from cache")
    if baseline.timed_out:
        return WireCheck(
            wire, WIRE_REFUSED, selection, baseline, None, f"baseline {baseline.summary}"
        )
    if baseline.collected == 0:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            None,
            "the selection collected ZERO tests — an UNRUN leg, not a green",
        )
    if baseline.attribution_failed:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            None,
            f"the baseline reported {baseline.summary} (rc={baseline.returncode}) but no node id "
            "could be attributed — the summary parser is broken, or pytest failed the session "
            "without failing a test, so no verdict is trustworthy. LAST LINES:\n"
            f"{_indent(baseline.tail)}",
        )
    if not baseline.green:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            None,
            f"baseline is not green ({baseline.summary}); a red after mutation could not be "
            "attributed to the wire. If these are async workflow suites, retry --with-cov "
            f"(--no-cov reds some). first: {(baseline.failed_ids + baseline.error_ids)[:2]}",
        )

    _progress(f"{wire.name}: baseline {baseline.summary} in {baseline.seconds:.0f}s — mutating")
    try:
        mutate(root, wire, snap)
    except WireRefusal as exc:
        snap.restore()
        return WireCheck(wire, WIRE_REFUSED, selection, baseline, None, str(exc))
    try:
        mutated = run_pytest(root, selection.files, timeout, with_cov)
    finally:
        broken = snap.restore()
        if broken:
            raise VacuityError(
                f"RESTORE FAILED for {', '.join(broken)}; originals are in {snap.dir}"
            )

    if mutated.timed_out:
        return WireCheck(
            wire, WIRE_REFUSED, selection, baseline, mutated, f"mutated run {mutated.summary}"
        )
    if mutated.attribution_failed:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            mutated,
            f"the mutated run reported {mutated.summary} (rc={mutated.returncode}) but no node id "
            "could be attributed. Reporting UNRAILED here would be a FALSE green: the suite did "
            f"notice and the parser did not. Fix the parser, not the verdict. LAST LINES:\n"
            f"{_indent(mutated.tail)}",
        )
    if mutated.error_ids:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            mutated,
            f"mutated run produced {len(mutated.error_ids)} COLLECTION/SETUP ERROR(s) "
            f"({mutated.error_ids[:2]}) — that measures the mutation, not the wire",
        )
    if mutated.collected < baseline.collected:
        return WireCheck(
            wire,
            WIRE_REFUSED,
            selection,
            baseline,
            mutated,
            f"mutated run collected {mutated.collected} tests vs the baseline's "
            f"{baseline.collected}; the selection changed under the mutation",
        )
    if mutated.failed_ids:
        return WireCheck(
            wire,
            WIRE_RAILED,
            selection,
            baseline,
            mutated,
            f"{len(mutated.failed_ids)} test(s) red when the call is removed",
        )
    return WireCheck(
        wire,
        WIRE_UNRAILED,
        selection,
        baseline,
        mutated,
        f"all {mutated.collected} selected tests stayed green with the call removed",
    )


@dataclass
class AtomWireReport:
    atom: Atom
    modules: tuple[str, ...]
    module_source: str
    total_wires: int
    checks: tuple[WireCheck, ...]
    refusal: str = ""


def check_atom_wires(
    root: Path,
    atom: Atom,
    *,
    max_wires: int,
    cap: int,
    timeout: int,
    with_cov: bool,
    only_symbols: Sequence[str] = (),
    snap: Snapshot | None = None,
    baseline_cache: dict[tuple[str, ...], RunResult] | None = None,
) -> AtomWireReport:
    modules = annotated_modules(atom.id, root)
    source = "atom-id annotations in src/"
    if not modules:
        modules = keyed_modules(atom, root)
        source = "path keys from the atom's prose"
    if not modules:
        return AtomWireReport(
            atom,
            (),
            "none",
            0,
            (),
            f"no production module names {atom.id} and no path key resolves to one — "
            "the atom's deliverable modules are not locatable, so no wire can be derived",
        )
    wires = find_wires(atom, root, modules)
    if only_symbols:
        wanted = {s.split("::")[-1] for s in only_symbols}
        wires = [w for w in wires if w.symbol in wanted]
    if not wires:
        return AtomWireReport(
            atom,
            tuple(modules),
            source,
            0,
            (),
            "no side-effect-only call statement resolves into these modules "
            "(value-consuming calls are out of scope by design)",
        )
    owns_snapshot = snap is None
    snap = snap or Snapshot(root)
    cache = baseline_cache if baseline_cache is not None else {}
    try:
        checks = [
            check_wire(
                root,
                atom,
                wire,
                _TEST_INDEX_CACHE.setdefault(str(root), _test_index(root)),
                snap,
                cap=cap,
                timeout=timeout,
                with_cov=with_cov,
                baseline_cache=cache,
            )
            for wire in wires[:max_wires]
        ]
    finally:
        # Only the creator disposes: the CLI passes ONE snapshot across every atom and discards
        # it itself. Measured: without this, 22 `wirecheck-snap-*` directories accumulated in
        # $TMPDIR over one session, several still holding a copy of a source file. A check whose
        # promise is "leaves nothing behind" must mean the whole filesystem, not just the tree.
        if owns_snapshot:
            snap.discard()
    return AtomWireReport(atom, tuple(modules), source, len(wires), tuple(checks))


_TEST_INDEX_CACHE: dict[str, dict[str, tuple[str, set[str]]]] = {}


def render_wire_reports(reports: Sequence[AtomWireReport], root: Path) -> str:
    head = _git(["rev-parse", "--short", "HEAD"], cwd=root).strip()
    dirty = "DIRTY" if _git(["status", "--porcelain"], cwd=root).strip() else "clean"
    lines = [
        "WIRE CHECK — would deleting the caller be caught?",
        f"  working tree {root} · HEAD {head} ({dirty}) · pytest -n0",
        "  NOTE: this measures the WORKING TREE, not --ref. The census above measures the ref.",
        "",
    ]
    for rep in reports:
        lines.append(f"{rep.atom.id}  {rep.atom.title[:70]}")
        if rep.refusal:
            lines.append(f"  REFUSED: {rep.refusal}")
            lines.append("")
            continue
        lines.append(
            f"  deliverable modules ({len(rep.modules)}) via {rep.module_source}: "
            + ", ".join(m[len("src/personalclaw/") :] for m in rep.modules[:8])
        )
        lines.append(
            f"  wires derived: {rep.total_wires} · checked: {len(rep.checks)} · "
            f"NOT checked: {rep.total_wires - len(rep.checks)} (raise --max-wires)"
        )
        for i, chk in enumerate(rep.checks, 1):
            w = chk.wire
            flags = []
            if w.cross_module:
                flags.append("cross-module")
            if w.annotated:
                flags.append(f"{rep.atom.id}-annotated")
            lines.append(
                f"  [{i}/{len(rep.checks)}] {w.name}  "
                f"({len(w.sites)} site(s), relevance {w.relevance}"
                + (", " + ", ".join(flags) if flags else "")
                + ")"
            )
            for site in w.sites:
                lines.append(f"        site {site.where}  `{site.text[:70]}`")
            sel = chk.selection
            if sel:
                lines.append(
                    f"        selection: {len(sel.files)} file(s) [{sel.tiers}] — "
                    + ", ".join(Path(f).stem for f in sel.files[:6])
                    + (f" +{len(sel.files) - 6} more" if len(sel.files) > 6 else "")
                )
                lines.append(
                    f"        NOT RUN: {len(sel.cut)} further scored file(s) cut by the cap, "
                    f"and {sel.total_test_files - len(sel.files)} of {sel.total_test_files} "
                    "test files overall — a green here is bounded by this selection"
                )
            if chk.baseline:
                lines.append(
                    f"        baseline: {chk.baseline.summary} ({chk.baseline.seconds:.0f}s)"
                )
            if chk.mutated:
                lines.append(
                    f"        mutated:  {chk.mutated.summary} ({chk.mutated.seconds:.0f}s)"
                )
            lines.append(f"        VERDICT: {chk.status} — {chk.reason}")
            for node in chk.caught_by[:4]:
                lines.append(f"                 caught by: {node}")
        lines.append("")
    tally: dict[str, int] = {}
    for rep in reports:
        for chk in rep.checks:
            tally[chk.status] = tally.get(chk.status, 0) + 1
        if rep.refusal:
            tally[WIRE_REFUSED] = tally.get(WIRE_REFUSED, 0) + 1
    lines.append(
        "WIRE SUMMARY  " + " · ".join(f"{k}={v}" for k, v in sorted(tally.items()))
        if tally
        else "WIRE SUMMARY  nothing measured"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# vacuity assertions
# ---------------------------------------------------------------------------


class VacuityError(RuntimeError):
    """The tool cannot trust its own measurement. Distinct from an imperfect roadmap."""


# A census that matches nothing is indistinguishable from a clean roadmap, so each stage
# asserts it can still see. These are the tool's own health, not the roadmap's.
SENTINEL_PRESENT = "require_computer_use"  # a real symbol on main
SENTINEL_ABSENT = "zzq_no_such_symbol_qzz"


def self_check(
    verdicts: Sequence[Verdict], corpus: Corpus, log_hits: dict[str, list[LogHit]]
) -> list[str]:
    problems: list[str] = []
    if not verdicts:
        problems.append(
            "zero atoms selected — either every atom is done or the catalog shape changed; "
            "a false zero here is the exact bug this tool exists to avoid"
        )
    if len(corpus.blobs) < 500:
        problems.append(
            f"corpus holds only {len(corpus.blobs)} files (<500): the tree read is broken"
        )
    if corpus.total_bytes < 1_000_000:
        problems.append(f"corpus holds only {corpus.total_bytes} bytes (<1MB): blobs did not load")
    impl, _ = corpus.find(SENTINEL_PRESENT)
    if not impl:
        problems.append(
            f"sentinel {SENTINEL_PRESENT!r} not found in the corpus: content search is dead "
            "(every atom would score NOT-LANDED)"
        )
    if corpus.find(SENTINEL_ABSENT)[0]:
        problems.append(f"sentinel {SENTINEL_ABSENT!r} WAS found: content search matches anything")
    if "test-e2e" not in corpus.make_targets():
        problems.append("Makefile target scan found no 'test-e2e': make-target probe is dead")
    if not any(h.verdict == LogVerdict.FLIP for hs in log_hits.values() for h in hs):
        problems.append(
            "no execution-log entry matched any FLIP pattern: the canonical "
            "'stays todo only because this code is unmerged' phrasing changed or the "
            "whitespace normalisation broke"
        )
    else:
        # "At least one FLIP" is too weak to be a rail. Measured: replacing the whitespace
        # normaliser with a no-op silently dropped ONE of the five known cases (PCS-7, whose
        # phrase wraps differently) and the >=1 assertion stayed green. So the fixture is the
        # five hand-verified atoms, each of which must still be seen as FLIP by its OWN plan.
        for atom_id in KNOWN_LANDED:
            own = [
                h for h in log_hits.get(atom_id, []) if h.verdict == LogVerdict.FLIP and h.headline
            ]
            if not own:
                problems.append(
                    f"{atom_id}'s complete-but-unmerged log entry is no longer detected: log "
                    "scanning has regressed against hand-verified ground truth"
                )
    if not any(h.verdict == LogVerdict.GATED for hs in log_hits.values() for h in hs):
        problems.append("no execution-log entry matched any GATED pattern: gate detection is dead")
    # The extractor rail must measure the EXTRACTOR, not the roadmap's prose style. Some
    # atoms are legitimately prose-only ("public launch announced after the gate is met")
    # and yield nothing no matter how good the regexes are, so a global keyless ratio is a
    # roadmap statistic masquerading as a health check. The five known-landed atoms are
    # key-rich by construction, so they are the fixture.
    by_id = {v.atom.id: v for v in verdicts}
    for atom_id in KNOWN_LANDED:
        v = by_id.get(atom_id)
        if v is None:
            continue  # not in this selection; --verify-known reports that separately
        if len(v.keys) < 3:
            problems.append(
                f"key extractor found only {len(v.keys)} keys in {atom_id}, a known "
                "key-rich atom (expected >=3): extraction has regressed"
            )
    keyless = [v.atom.id for v in verdicts if not v.keys]
    if verdicts and len(keyless) > 0.6 * len(verdicts):
        problems.append(
            f"{len(keyless)}/{len(verdicts)} atoms yielded no extractable key "
            f"(>60%): the key extractor is not reading the prose ({keyless[:6]})"
        )
    return problems


def _run_wire_check(args: argparse.Namespace, atom_ids: Sequence[str]) -> int:
    """Driver for ``--check-wires``. Owns the ONE snapshot the whole run restores from."""
    root = REPO_ROOT
    wanted = {a.upper() for a in atom_ids}
    atoms = [a for a in load_atoms(args.dag) if a.id in wanted]
    unknown = wanted - {a.id for a in atoms}
    if unknown:
        print(
            f"audit_landed_atoms: no such atom in {args.dag}: {', '.join(sorted(unknown))}",
            file=sys.stderr,
        )
        return 2
    if len(atoms) > args.max_atoms:
        print(
            f"audit_landed_atoms: {len(atoms)} atoms requested but --max-atoms is "
            f"{args.max_atoms}. Each wire costs two pytest runs; raise the cap deliberately.",
            file=sys.stderr,
        )
        return 2

    snap = Snapshot(root)
    cache: dict[tuple[str, ...], RunResult] = {}
    reports: list[AtomWireReport] = []
    try:
        for atom in atoms:
            reports.append(
                check_atom_wires(
                    root,
                    atom,
                    max_wires=args.max_wires,
                    cap=args.max_test_files,
                    timeout=args.wire_timeout,
                    with_cov=args.with_cov,
                    only_symbols=tuple(args.wire or ()),
                    snap=snap,
                    baseline_cache=cache,
                )
            )
    except VacuityError as exc:
        print(f"audit_landed_atoms: {exc}", file=sys.stderr)
        return 3
    finally:
        leftover = snap.restore()
        if leftover:
            print(
                f"audit_landed_atoms: RESTORE FAILED for {', '.join(leftover)}; "
                f"originals are in {snap.dir}",
                file=sys.stderr,
            )
        else:
            snap.discard()

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "atoms": [
                        {
                            "id": r.atom.id,
                            "modules": list(r.modules),
                            "wires_derived": r.total_wires,
                            "refusal": r.refusal,
                            "checks": [
                                {
                                    "wire": c.wire.name,
                                    "sites": [s.where for s in c.wire.sites],
                                    "status": c.status,
                                    "reason": c.reason,
                                    "selected": list(c.selection.files) if c.selection else [],
                                    "not_run_cut": len(c.selection.cut) if c.selection else 0,
                                    "caught_by": list(c.caught_by),
                                }
                                for c in r.checks
                            ],
                        }
                        for r in reports
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render_wire_reports(reports, root))
    # A finding is not an error: UNRAILED is the tool working. Only a broken measurement
    # (restore failure, VacuityError) is non-zero, exactly as for the census.
    return 0


def verify_known(verdicts: Sequence[Verdict]) -> tuple[list[str], list[str], list[str]]:
    """Score the tool against the five atoms found by hand. Report, never tune."""
    by_id = {v.atom.id: v for v in verdicts}
    detected, missed, absent = [], [], []
    for atom_id in KNOWN_LANDED:
        v = by_id.get(atom_id)
        if v is None:
            absent.append(atom_id)
        elif v.bucket in (CLEAN, GATED):
            detected.append(f"{atom_id}:{v.bucket}")
        else:
            missed.append(f"{atom_id}:{v.bucket}")
    return detected, missed, absent


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _row(v: Verdict) -> str:
    return (
        f"  {v.atom.id:<10} {v.atom.plan_code:<7} {v.evidence:<7} {v.log_verdict:<17} "
        f"{v.atom.title[:58]}"
    )


def render(
    verdicts: Sequence[Verdict],
    corpus: Corpus,
    only: str | None = None,
    verbose: bool = False,
) -> str:
    lines = [
        f"ATOM CENSUS — ref {corpus.ref} · {len(verdicts)} atoms · "
        f"corpus {len(corpus.blobs)} files / {corpus.total_bytes // 1024} KiB (docs/ excluded)",
        "",
    ]
    for bucket in BUCKETS:
        rows = [v for v in verdicts if v.bucket == bucket]
        if only and bucket != only:
            continue
        lines.append(f"{bucket}  ({len(rows)})")
        if not rows:
            lines.append("  — none —")
        for v in sorted(rows, key=lambda x: (x.atom.plan_code, x.atom.id)):
            lines.append(_row(v))
            lines.append(f"             why: {v.why}")
            if v.impl_keys:
                lines.append(f"             found: {v.key_summary()}")
            if v.log_hit:
                lines.append(
                    f"             log[{v.log_hit.plan_file}]: …{v.log_hit.excerpt[:300]}…"
                )
            if verbose:
                lines.append(
                    "             keys: "
                    + ", ".join(f"{k.text}({k.kind}{'+' if k.in_impl else '-'})" for k in v.keys)
                )
        lines.append("")
    return "\n".join(lines)


def render_branches(branches: Sequence[Branch]) -> str:
    lines = ["STRANDED BRANCHES (mapped to a selected atom)", ""]
    carrying = [b for b in branches if b.carries_new_content]
    clean = [b for b in branches if b.carries_new_content is False]
    lines.append(f"  GENUINELY STRANDED — carries code NOT on the ref: {len(carrying)}")
    for b in sorted(carrying, key=lambda x: -x.unlanded_code_lines):
        lines.append(f"    {b.atom_id:<10} {b.name:<52} +{b.commits_ahead} — {b.note}")
        for f in b.new_files:
            lines.append(f"                 new file: {f}")
        for s in b.sample:
            lines.append(f"                 unlanded: {s[:96]}")
    lines.append(f"  DRAINED — safe to delete, every added code line is upstream: {len(clean)}")
    for b in clean:
        lines.append(f"    {b.atom_id:<10} {b.name:<52} +{b.commits_ahead} — {b.note}")
    unknown = [b for b in branches if b.carries_new_content is None]
    if unknown:
        lines.append(f"  undecidable: {len(unknown)}")
        for b in unknown:
            lines.append(f"    {b.atom_id:<10} {b.name:<52} — {b.note}")
    return "\n".join(lines)


def to_json(verdicts: Sequence[Verdict], corpus: Corpus, branches: Sequence[Branch]) -> str:
    return json.dumps(
        {
            "ref": corpus.ref,
            "corpus_files": len(corpus.blobs),
            "atoms": [
                {
                    "id": v.atom.id,
                    "plan": v.atom.plan_code,
                    "title": v.atom.title,
                    "bucket": v.bucket,
                    "evidence": v.evidence,
                    "log_verdict": v.log_verdict,
                    "why": v.why,
                    "found": [
                        {"key": k.text, "kind": k.kind, "impl": k.found_impl[:3]}
                        for k in v.keys
                        if k.in_impl
                    ],
                    "log_excerpt": v.log_hit.excerpt if v.log_hit else None,
                    "log_file": v.log_hit.plan_file if v.log_hit else None,
                }
                for v in verdicts
            ],
            "branches": [
                {
                    "name": b.name,
                    "atom": b.atom_id,
                    "commits_ahead": b.commits_ahead,
                    "carries_new_content": b.carries_new_content,
                    "note": b.note,
                }
                for b in branches
            ],
        },
        indent=2,
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--ref", default=DEFAULT_REF, help="git ref to treat as landed (default origin/main)"
    )
    ap.add_argument(
        "--status",
        action="append",
        default=None,
        help="atom status to census; repeatable (default: todo)",
    )
    ap.add_argument("--atom", action="append", default=None, help="restrict to these atom ids")
    ap.add_argument(
        "--bucket",
        choices=["clean", "gated", "open", "unknown"],
        default=None,
        help="print one bucket only",
    )
    ap.add_argument("--branches", action="store_true", help="also scan leftover branches")
    ap.add_argument(
        "--verify-known", action="store_true", help="score against the five known cases"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every extracted key")
    # Explicit flags, not monkeypatchable module globals: ``census`` binds these as default
    # arguments, so rebinding ``DAG_PATH`` at module level is a silent no-op. Its own test
    # caught that, which is the only reason these exist.
    ap.add_argument("--dag", type=Path, default=DAG_PATH, help="atom catalog to read")
    ap.add_argument("--plans-dir", type=Path, default=PLANS_DIR, help="plan markdown directory")
    # --- the wire check: DESTRUCTIVE and slow, therefore never part of a default run ---
    ap.add_argument(
        "--check-wires",
        nargs="*",
        metavar="ATOM",
        default=None,
        help="DESTRUCTIVE, opt-in: for each atom, neutralise its production call sites in the "
        "WORKING TREE, run a bounded test selection and report whether anything reds. With no "
        "atom ids, checks the LANDED-AND-CLEAN bucket (which is what the census is claiming). "
        "The tree is snapshotted to file copies and hash-verified on restore.",
    )
    ap.add_argument(
        "--wire",
        action="append",
        default=None,
        metavar="SYMBOL",
        help="restrict --check-wires to these symbols (repeatable; `module::sym` or `sym`)",
    )
    ap.add_argument("--max-wires", type=int, default=5, help="wires checked per atom (default 5)")
    ap.add_argument(
        "--max-test-files", type=int, default=14, help="cap on the derived selection (default 14)"
    )
    ap.add_argument(
        "--max-atoms", type=int, default=4, help="cap on atoms per --check-wires run (default 4)"
    )
    ap.add_argument(
        "--wire-timeout",
        type=int,
        default=900,
        help="per-pytest-run timeout, seconds (default 900)",
    )
    ap.add_argument(
        "--with-cov",
        action="store_true",
        help="run the selection WITH coverage (--no-cov reds some async workflow suites)",
    )
    args = ap.parse_args(argv)

    statuses = tuple(args.status or ("todo",))
    if args.check_wires is not None and args.check_wires:
        # Explicit atoms: skip the corpus entirely. It answers a question about --ref, and the
        # wire check answers one about the working tree; conflating the two refs in one report
        # is how a reader ends up believing a ref's census adjudicated a tree's rails.
        return _run_wire_check(args, args.check_wires)
    try:
        ref, ref_note = resolve_ref(args.ref)
        if ref_note:
            print(f"audit_landed_atoms: {ref_note}", file=sys.stderr)
        verdicts, corpus = census(
            ref=ref, statuses=statuses, dag_path=args.dag, plans_dir=args.plans_dir
        )
        log_hits = scan_plan_logs(args.plans_dir)
    except (VacuityError, RuntimeError) as exc:
        print(f"audit_landed_atoms: INTERNAL ERROR: {exc}", file=sys.stderr)
        return 2

    problems = self_check(verdicts, corpus, log_hits)
    if problems:
        print(
            "audit_landed_atoms: VACUITY CHECK FAILED — the census cannot be trusted:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    if args.atom:
        wanted = {a.upper() for a in args.atom}
        verdicts = [v for v in verdicts if v.atom.id in wanted]

    branches: list[Branch] = []
    if args.branches:
        # `ref`, not `args.ref` — the census above already resolved it, and a branch scan
        # measured against a ref the census did not use compares two different trees.
        branches = scan_branches([v.atom.id for v in verdicts], ref, corpus)

    only = {"clean": CLEAN, "gated": GATED, "open": OPEN, "unknown": UNKNOWN}.get(args.bucket or "")
    if args.json:
        print(to_json(verdicts, corpus, branches))
    else:
        print(render(verdicts, corpus, only=only, verbose=args.verbose))
        if args.branches:
            print(render_branches(branches))
        counts = {b: sum(1 for v in verdicts if v.bucket == b) for b in BUCKETS}
        print("SUMMARY  " + " · ".join(f"{k}={v}" for k, v in counts.items()))

    if args.verify_known:
        detected, missed, absent = verify_known(verdicts)
        total = len(detected) + len(missed)
        rate = (len(missed) / total) if total else 0.0
        print(
            f"\nKNOWN-CASE SCORE  detected={len(detected)} missed={len(missed)} "
            f"not-selected={len(absent)}  miss-rate={rate:.0%}"
        )
        for label, group in (("detected", detected), ("MISSED", missed), ("not-selected", absent)):
            if group:
                print(f"  {label}: {', '.join(group)}")

    if args.check_wires is not None:  # bare --check-wires: audit the bucket the census claims
        clean = [v.atom.id for v in verdicts if v.bucket == CLEAN]
        if not clean:
            print("\nno LANDED-AND-CLEAN atom to wire-check.")
            return 0
        print(
            f"\n--check-wires with no atom ids → the LANDED-AND-CLEAN bucket ({len(clean)}): "
            f"{', '.join(clean)}"
        )
        return _run_wire_check(args, clean[: args.max_atoms])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
