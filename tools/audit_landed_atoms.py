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

It **never** edits the roadmap. ``dag.json`` is owner-maintained; flips happen at
integration. Exit status is ``0`` for any census, however imperfect the roadmap looks, and
non-zero **only** when one of the tool's own vacuity assertions fails (see ``self_check``).

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
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
import tarfile
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
    args = ap.parse_args(argv)

    statuses = tuple(args.status or ("todo",))
    try:
        verdicts, corpus = census(
            ref=args.ref, statuses=statuses, dag_path=args.dag, plans_dir=args.plans_dir
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
        branches = scan_branches([v.atom.id for v in verdicts], args.ref, corpus)

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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
