"""The verdict store: a job's clean pass, recorded under a key of every input it ran on, so a later
run of exactly those inputs may stand on it instead of running the job again.

Nothing consults the store yet: ``python -m tools.ci run`` and ``run-job`` will. This module holds
the rules they will follow.

**The key.** :func:`key` is the sha256 of the canonical JSON (sorted keys, no spaces, UTF-8) of a
:class:`KeyInputs`: the job id, the text of its catalogue entry and the catalogue's version, the
exact argv of every step, a digest of the selection (the node ids or the shard list), the commit's
tree, each partner repository's commit, each lock file's digest, the toolchain's versions, the OS,
the architecture, the runner or container identity, the advisory policy, every other declared
input, and the host capabilities the caller read. A change to any one of them is a different key,
so a pass on other inputs is never found. The OS and architecture have no default and may not be
empty, so no two platforms share a key; an empty runner means the bare host, no container.

**The class.** A job's reuse class is the catalogue's, read by the catalogue's one parser,
:func:`tools.ci.catalogue.parse_reuse`: ``never`` (also when the entry names none), ``tree``
(reused for an identical key) or ``ttl:<hours>`` (reused for an identical key recorded less than
that many hours ago). :func:`reuse_class` reads a job as ``never`` whatever it declares when its
security is anything but ``ordinary``, or when it is live: marked ``live``, or reading one of the
catalogue's live feeds. Live and security evidence is never reused, with no TTL.

**What is recorded.** :meth:`Store.record` records only a pass (``success``) that needed no rerun
(:attr:`Result.rerun` is ``False``. Nothing feeds the store a ``run-job`` record yet; when one does,
the record's ``rerun`` of null, which every success carries, is to map to ``False``, and a
diagnostic rerun is only ever on a red job, which is never recorded) and whose quarantined-failure
list is present and empty, from a checkout that was clean when the run began and is still clean, on
the same head and tree, when the pass is recorded. Clean means
``git status --porcelain --untracked-files=all`` printed nothing, so an untracked file counts even
where ``status.showUntrackedFiles`` hides it. The entry goes to a temporary file in the store's own
folder, is flushed to disk, and only then takes its name, so a half-written entry is never under
an entry's name.

The pass is recorded only if :meth:`Store.lookup` would read the entry back, so ``recorded``
means it may be reused.

**What is read.** :meth:`Store.lookup` returns a pass only from a complete entry of this store's
version whose own key, recomputed from the inputs it holds, is its file's name. Anything torn,
foreign, unreadable, nested too deep to read, given a key twice or dated by no finite number is a
miss, never an error, and a miss runs the job.

**The store** is ``ci-verdicts/`` in the clone's common git folder, so every worktree of a clone
shares one. git runs with no ``GIT_*`` variable at all, so a ``GIT_DIR``, index or configuration a
hook exported neither moves the store nor changes what git reports. It reads the user's own global
and system git configuration, as the user's git would. A local entry never reaches GitHub.

**The decision.** :func:`decide` says, per planned job, whether it is reused or run, and why. It
reads the checkout from the :class:`Start` the run began with, so the receipt's ``dirty`` field and
the replay rule come from one reading. A dirty run replays nothing and records nothing; every job
runs. A job whose inputs name another tree than the checkout's runs. The :class:`Receipt` carries
the run-level ``dirty`` field and each job's decision, class, key inputs and source.

Standard library only, beside the catalogue and the verdict's result words, so a trusted job can
run it on the runner image's own ``python3`` before anything is installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalogue import LIVE_INPUTS, NEVER, Job, Reuse, parse_reuse
from .verdict import SUCCESS

__all__ = [
    "VERSION",
    "Refused",
    "KeyInputs",
    "Result",
    "Start",
    "Outcome",
    "Entry",
    "Store",
    "Source",
    "Decision",
    "Receipt",
    "parse_reuse",
    "reuse_class",
    "key",
    "dirty",
    "begin",
    "decide",
]

#: The entry format's version. An entry of another version is a miss; the version is in the key,
#: so a new version never finds an old version's entries.
VERSION = 1

#: The store's folder, under the clone's common git folder.
FOLDER = "ci-verdicts"

#: A full object id, SHA-1 or SHA-256, matched with ``fullmatch`` so nothing may follow it.
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: A sha256 hex digest: a key, a lock digest, a selection digest, a catalogue version.
_DIGEST = re.compile(r"[0-9a-f]{64}")

_ENTRY_FIELDS = frozenset(
    {"version", "key", "inputs", "verdict", "sha", "tree", "seconds", "skipped", "junit"}
    | {"recorded_at"}
)


class Refused(ValueError):
    """The inputs cannot be keyed, or the store cannot be found. The message says why."""


# ── The key ──────────────────────────────────────────────────────────────────────────────────


def _names(what: str, raw: Mapping[str, str], value: re.Pattern[str] | None = None) -> None:
    for name, item in raw.items():
        if not isinstance(name, str) or not name:
            raise Refused(f"every {what} is named")
        if not isinstance(item, str):
            raise Refused(f"{what} {name} is not text: {item!r}")
        if value is not None and not value.fullmatch(item):
            raise Refused(f"{what} {name} is not a full digest or object id: {item!r}")


@dataclass(frozen=True, kw_only=True)
class KeyInputs:
    """Everything a job's result depends on. Each mapping and the argv are copied, so a caller's
    dict or list changed later cannot change what was keyed. ``os`` and ``arch`` have no default;
    ``runner`` is empty for the bare host, and ``advisory_policy`` when the job reads none."""

    job_id: str
    entry_text: str
    catalogue_version: str
    argv: tuple[tuple[str, ...], ...]
    selection_digest: str
    tree_oid: str
    external_revisions: Mapping[str, str] = field(default_factory=dict)
    lock_digests: Mapping[str, str] = field(default_factory=dict)
    toolchain: Mapping[str, str] = field(default_factory=dict)
    os: str
    arch: str
    runner: str = ""
    advisory_policy: str = ""
    declared: Mapping[str, str] = field(default_factory=dict)
    capabilities: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("external_revisions", "lock_digests", "toolchain", "declared", "capabilities"):
            object.__setattr__(self, name, dict(getattr(self, name)))
        # A step given as one text is kept as it is, for check() to refuse: read as a sequence,
        # "ab" would be the step ("a", "b").
        argv = tuple(step if isinstance(step, str) else tuple(step) for step in self.argv)
        object.__setattr__(self, "argv", argv)

    def check(self) -> None:
        """Refuse inputs that do not name exactly what the job ran on."""
        if not isinstance(self.job_id, str) or not self.job_id:
            raise Refused("the inputs name no job")
        if not self.argv or not all(
            isinstance(step, tuple) and step and all(isinstance(arg, str) for arg in step)
            for step in self.argv
        ):
            raise Refused(f"{self.job_id}: the argv is a non-empty list of non-empty word lists")
        if not isinstance(self.tree_oid, str) or not _SHA.fullmatch(self.tree_oid):
            raise Refused(f"{self.job_id}: tree_oid is not a full object id: {self.tree_oid!r}")
        for name in ("catalogue_version", "selection_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _DIGEST.fullmatch(value):
                raise Refused(f"{self.job_id}: {name} is not a sha256 digest: {value!r}")
        for name in ("entry_text", "os", "arch", "runner", "advisory_policy"):
            if not isinstance(getattr(self, name), str):
                raise Refused(f"{self.job_id}: {name} is not text")
        for name in ("os", "arch"):
            if not getattr(self, name):
                raise Refused(f"{self.job_id}: the inputs name no {name}")
        _names("partner revision", self.external_revisions, _SHA)
        _names("lock digest", self.lock_digests, _DIGEST)
        _names("toolchain version", self.toolchain)
        _names("declared input", self.declared)
        _names("capability", self.capabilities)

    def to_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "entry_text": self.entry_text,
            "catalogue_version": self.catalogue_version,
            "argv": [list(step) for step in self.argv],
            "selection_digest": self.selection_digest,
            "tree_oid": self.tree_oid,
            "external_revisions": dict(self.external_revisions),
            "lock_digests": dict(self.lock_digests),
            "toolchain": dict(self.toolchain),
            "os": self.os,
            "arch": self.arch,
            "runner": self.runner,
            "advisory_policy": self.advisory_policy,
            "declared": dict(self.declared),
            "capabilities": dict(self.capabilities),
        }


def _canonical(record: Mapping[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _key_of(inputs_json: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({"version": VERSION, "inputs": inputs_json})).hexdigest()


def key(inputs: KeyInputs) -> str:
    """The sha256 of the store's version and every input, in canonical JSON."""
    inputs.check()
    return _key_of(inputs.to_json())


def _live(job: Job) -> bool:
    """Marked live, or reading a feed that changes with the time of the run and not the tree.
    The catalogue refuses the second without the first; it is read here too, so an entry that was
    never validated is still not reused."""
    return job.live or bool(LIVE_INPUTS & set(job.external_inputs))


def reuse_class(job: Job) -> Reuse:
    """The class a job is decided by: its catalogue class, except that a job whose security is
    not ``ordinary``, or that is live, is never reused, whatever it declares."""
    if job.security != "ordinary" or _live(job):
        return NEVER
    return job.reuse


# ── The checkout ─────────────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # The repository is named explicitly, so no GIT_* variable a calling hook exported (a GIT_DIR,
    # an index, a configuration) may redirect or change the read. The user's own global and system
    # files are still read, as the user's git reads them.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False, env=env
    )


def _rev(repo: Path, rev: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", rev)
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and _SHA.fullmatch(out) else None


def dirty(repo: Path) -> bool:
    """True when the checkout has any change, staged or not, or any untracked file; and when its
    status cannot be read, since a checkout nobody can read is not known to be clean."""
    proc = _git(repo, "status", "--porcelain", "--untracked-files=all")
    return proc.returncode != 0 or proc.stdout != ""


@dataclass(frozen=True)
class Start:
    """The checkout as a run began: whether it was dirty, and its head and tree."""

    repo: Path
    dirty: bool
    head: str | None
    tree: str | None


def begin(repo: Path) -> Start:
    """Read the checkout as a run begins. A head or tree that cannot be read reads dirty."""
    head = _rev(repo, "HEAD")
    tree = _rev(repo, "HEAD^{tree}")
    return Start(repo, dirty(repo) or head is None or tree is None, head, tree)


# ── The store ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Result:
    """A job's result as run-job reports it. ``quarantined`` is None when the result carried no
    quarantined-failure list at all."""

    verdict: str
    sha: str
    seconds: float
    skipped: tuple[str, ...]
    junit: str
    rerun: bool
    quarantined: tuple[str, ...] | None


@dataclass(frozen=True)
class Outcome:
    recorded: bool
    reason: str


@dataclass(frozen=True)
class Entry:
    """A recorded pass."""

    key: str
    sha: str
    tree: str
    seconds: float
    skipped: tuple[str, ...]
    junit: str
    recorded_at: float


def _refuse_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for name, value in pairs:
        if name in seen:
            raise ValueError(f"{name!r} is given twice")
        seen[name] = value
    return seen


def _number(value: object) -> bool:
    # json.loads reads NaN and Infinity; an age from either is never inside a TTL or past it.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read(raw: bytes) -> object:
    """The JSON document *raw* holds, or None for anything that is not one: not UTF-8, torn or
    given a key twice (a ValueError, each), or nested deeper than the parser goes."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_refuse_repeats)
    except (ValueError, RecursionError):
        return None


def _entry(data: object, wanted: str) -> Entry | None:
    """The entry *data* holds, or None unless it is a complete pass of this version for *wanted*."""
    if not isinstance(data, dict) or set(data) != _ENTRY_FIELDS:
        return None
    version = data["version"]
    # True and 1.0 equal 1 in Python; only the whole number is this version.
    if type(version) is not int or version != VERSION:
        return None
    if data["key"] != wanted or data["verdict"] != SUCCESS:
        return None
    inputs = data["inputs"]
    if not isinstance(inputs, dict):
        return None
    try:
        # Text no UTF-8 holds (a lone surrogate) or nesting too deep to write is no key.
        own = _key_of(inputs)
    except (ValueError, RecursionError):
        return None
    if own != wanted:
        return None
    sha, tree, junit = data["sha"], data["tree"], data["junit"]
    if not all(isinstance(v, str) and _SHA.fullmatch(v) for v in (sha, tree)):
        return None
    if inputs.get("tree_oid") != tree or not isinstance(junit, str):
        return None
    seconds, recorded_at, skipped = data["seconds"], data["recorded_at"], data["skipped"]
    if not _number(seconds) or seconds < 0 or not _number(recorded_at):
        return None
    if not isinstance(skipped, list) or not all(isinstance(s, str) for s in skipped):
        return None
    return Entry(wanted, sha, tree, float(seconds), tuple(skipped), junit, float(recorded_at))


@dataclass(frozen=True)
class Store:
    root: Path

    @classmethod
    def for_repo(cls, repo: Path) -> Store:
        """The store shared by every worktree of *repo*'s clone."""
        # The plain --git-common-dir is relative in an ordinary clone, so ask for it absolute.
        proc = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        common = proc.stdout.strip()
        if proc.returncode != 0 or not common or not Path(common).is_absolute():
            raise Refused(f"{repo} is not in a git clone: {proc.stderr.strip()}")
        return cls(Path(common) / FOLDER)

    def path(self, entry_key: str) -> Path:
        return self.root / f"{entry_key}.json"

    def lookup(self, entry_key: str) -> Entry | None:
        """The recorded pass for *entry_key*, or None for anything that is not one."""
        if not isinstance(entry_key, str) or not _DIGEST.fullmatch(entry_key):
            return None
        try:
            raw = self.path(entry_key).read_bytes()
        except OSError:
            return None
        return _entry(_read(raw), entry_key)

    def record(self, start: Start, inputs: KeyInputs, result: Result, *, now: float) -> Outcome:
        """Record *result* under *inputs*' key if it is a clean pass from a clean checkout."""
        if start.dirty:
            return Outcome(False, "the checkout was dirty when the run began")
        if result.verdict != SUCCESS:
            return Outcome(False, f"the result is {result.verdict!r}, not a pass")
        if result.rerun is not False:
            return Outcome(False, "the pass needed a rerun")
        if result.quarantined is None:
            return Outcome(False, "the result carries no quarantined-failure list")
        if result.quarantined:
            return Outcome(False, f"{len(result.quarantined)} quarantined failure(s)")
        entry_key = key(inputs)
        if inputs.tree_oid != start.tree:
            return Outcome(False, "the inputs name another tree than the run's")
        if result.sha != start.head:
            return Outcome(False, "the result names another head than the run's")
        if dirty(start.repo):
            return Outcome(False, "the checkout became dirty during the run")
        if _rev(start.repo, "HEAD") != start.head or _rev(start.repo, "HEAD^{tree}") != start.tree:
            return Outcome(False, "the head moved during the run")
        record = {
            "version": VERSION,
            "key": entry_key,
            "inputs": inputs.to_json(),
            "verdict": SUCCESS,
            "sha": result.sha,
            "tree": start.tree,
            "seconds": result.seconds,
            "skipped": list(result.skipped),
            "junit": result.junit,
            "recorded_at": now,
        }
        data = _canonical(record)
        if _entry(_read(data), entry_key) is None:
            return Outcome(False, "the result is not one an entry can hold, so none would be read")
        self._write(entry_key, data)
        return Outcome(True, "recorded")

    def _write(self, entry_key: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.root, prefix=f".{entry_key}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path(entry_key))
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


# ── The decision and the receipt ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    """The recorded pass a reused job stands on."""

    sha: str
    age_seconds: float
    skipped: int

    def to_json(self) -> dict[str, Any]:
        return {"sha": self.sha, "age_seconds": self.age_seconds, "skipped": self.skipped}


@dataclass(frozen=True)
class Decision:
    id: str
    reuse: str
    key: str
    inputs: KeyInputs
    reused: bool
    reason: str
    source: Source | None = None
    ttl_age_seconds: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "decision": "reused" if self.reused else "run",
            "reuse": self.reuse,
            "key": self.key,
            "inputs": self.inputs.to_json(),
            "reason": self.reason,
            "source": self.source.to_json() if self.source else None,
            "ttl_age_seconds": self.ttl_age_seconds,
        }


def _age(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


@dataclass(frozen=True)
class Receipt:
    dirty: bool
    jobs: tuple[Decision, ...]

    def to_json(self) -> dict[str, Any]:
        return {"dirty": self.dirty, "jobs": [job.to_json() for job in self.jobs]}

    def lines(self) -> list[str]:
        """One line per planned job, as the run prints them."""
        out = []
        for job in self.jobs:
            if job.reused and job.source is not None:
                out.append(
                    f"reused {job.id} from {job.source.sha[:12]}, {_age(job.source.age_seconds)},"
                    f" {job.source.skipped} skipped"
                )
            else:
                out.append(f"ran {job.id}: {job.reason}")
        return out


def _decide(job: Job, inputs: KeyInputs, store: Store, now: float, start: Start) -> Decision:
    reuse = reuse_class(job)
    entry_key = key(inputs)

    def run(reason: str, ttl_age: float | None = None) -> Decision:
        return Decision(job.id, str(reuse), entry_key, inputs, False, reason, None, ttl_age)

    if start.dirty:
        return run("the run is dirty, so nothing is replayed")
    if reuse == NEVER:
        if job.security == "security":
            return run("security evidence is never reused")
        if job.security != "ordinary":
            return run(f"its security is {job.security!r}, not ordinary, so it is never reused")
        if _live(job):
            return run("live evidence is never reused")
        return run("its class is never")
    if inputs.tree_oid != start.tree:
        return run("the inputs name another tree than the checkout's")
    entry = store.lookup(entry_key)
    if entry is None:
        return run("no recorded pass for this key")
    age = now - entry.recorded_at
    ttl_age = age if reuse.kind == "ttl" else None
    if reuse.kind == "ttl":
        if age < 0:
            return run("the recorded pass is dated in the future", ttl_age)
        if reuse.hours is None or age >= reuse.hours * 3600:
            return run(f"the recorded pass is {_age(age)} old, past its {reuse}", ttl_age)
    elif reuse.kind != "tree":
        return run(f"its class {reuse} is not one this store reuses")
    source = Source(entry.sha, age, len(entry.skipped))
    reason = f"a recorded pass of this key ({reuse})"
    return Decision(job.id, str(reuse), entry_key, inputs, True, reason, source, ttl_age)


def decide(
    jobs: Iterable[Job],
    inputs: Mapping[str, KeyInputs],
    store: Store,
    *,
    now: float,
    start: Start,
) -> Receipt:
    """Decide, per planned job in plan order, whether it is reused or run, from the checkout as
    *start* read it when the run began.

    Every planned job must have its own inputs: a job keyed on nothing is refused, not run on a
    guess."""
    decisions: list[Decision] = []
    for job in jobs:
        mine = inputs.get(job.id)
        if mine is None:
            raise Refused(f"{job.id}: no key inputs for this job")
        if mine.job_id != job.id:
            raise Refused(f"{job.id}: the key inputs are {mine.job_id}'s")
        decisions.append(_decide(job, mine, store, now, start))
    return Receipt(start.dirty, tuple(decisions))
