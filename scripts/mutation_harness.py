#!/usr/bin/env python3
"""Crash-safe mutation harness — a killed mutation run cannot leave a silent residue.

Mutation testing here has always been hand-driven: every "N mutations applied, N caught"
claim in the tree came from a throwaway script. That is why a ``pkill``ed run once left a
renamed identifier inside a module-level tuple in ``src/personalclaw/supply_chain.py``,
every suite passed against the mutated code, and **nothing noticed** (#2710). The tree
itself was wrong, not merely the evidence — a later commit could have carried the mutation
into ``main``.

## Why a ``finally:`` is not the fix

The obvious repair — revert in a ``finally:`` — is defeated by the exact failure that
caused the incident. ``SIGKILL`` runs no ``finally:``, no ``atexit`` handler and no signal
handler. A rail that only executes inside the mutating process cannot survive that process
being killed, so the recovery path must live **on disk, written before the first edit**.

## The two properties this module provides

**1. Recovery never depends on the mutating process.** Before a single byte is written to a
target file, its pristine bytes are copied into ``.mutation-session/pristine/`` and a
journal naming that copy is fsynced to ``.mutation-session/session.json``. Restoring is a
pure function of what is on disk, so *any* later process — the next harness run, the
pre-commit hook, a human — can undo the mutation byte-exactly. This is the
"restore-from-copy" option #2710 argued for: it removes the failure instead of reporting it.

**2. An unfinished run is LOUD.** The session directory is removed only on clean
completion, so its presence is proof that a mutation run did not finish.
:func:`check_clean` reports it, ``scripts/run_precommit.sh`` refuses the commit, and
``tests/test_mutation_harness.py`` kills a real subprocess mid-mutation to prove the rail
fires. A marker file would only have reported the residue; the journal also repairs it.

Deliberately NOT a marked-residue grep. ``grep -rn "MUTANT\\|FALSIFICATION\\|# PROBE"``
only finds mutants an author remembered to mark, and the residue in the incident carried no
marker because a same-length identifier swap is the whole point of that mutation shape. A
control that depends on the author doing the right thing is the class this repo keeps
failing at.

## Restore refuses to clobber real work

Each applied mutation records the sha256 of both the pristine and the mutated bytes. If a
file's current content matches neither, ``restore`` refuses and prints the pristine copy's
path so the human can diff — a blind overwrite would destroy an edit made after the
mutation, which is the same defect in the other direction.

## Usage

    # One mutation at a time, each scored by a command, tree restored between:
    python scripts/mutation_harness.py run --spec mutations.json \
        --command "pytest -q tests/test_x.py"

    # A manual session: mutate, poke at it by hand, then put it back:
    python scripts/mutation_harness.py apply --spec mutations.json
    python scripts/mutation_harness.py restore

    # Is the tree carrying residue from a run that died? (pre-commit calls this)
    python scripts/mutation_harness.py check

A spec file is a JSON list of ``{"path", "old", "new", "label"?}`` objects. ``old`` must
occur EXACTLY ONCE in the file — an ambiguous mutation is refused rather than applied to
whichever occurrence came first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: Session directory, relative to the repo root. Its mere EXISTENCE means a mutation run did
#: not finish — the whole rail rests on that, so it is created before the first edit and
#: removed only after every restore has been fsynced.
SESSION_DIRNAME = ".mutation-session"
JOURNAL_NAME = "session.json"
PRISTINE_DIRNAME = "pristine"

#: Bumped when the journal shape changes. A journal this version cannot read is reported
#: rather than ignored: refusing to parse a residue journal would resurrect the exact
#: "nothing notices" failure.
JOURNAL_VERSION = 1


class MutationError(RuntimeError):
    """A refusal — an ambiguous mutation, an unreadable journal, an unexpected tree."""


@dataclass(frozen=True)
class MutationSpec:
    """One textual mutation: replace ``old`` with ``new`` in ``path``.

    ``old`` must be unique in the file. A substring that occurs twice would make the
    mutation depend on scan order, and a mutation whose location is not knowable cannot be
    reasoned about when it survives.
    """

    path: str
    old: str
    new: str
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or f"{self.path}: {self.old!r} -> {self.new!r}"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "old": self.old, "new": self.new, "label": self.label}

    @staticmethod
    def from_dict(raw: Any) -> MutationSpec:
        if not isinstance(raw, dict):
            raise MutationError(f"a mutation spec must be an object, got {type(raw).__name__}")
        for key in ("path", "old", "new"):
            if not isinstance(raw.get(key), str) or not raw.get(key):
                raise MutationError(f"mutation spec is missing a non-empty {key!r}")
        if raw["old"] == raw["new"]:
            raise MutationError(f"mutation spec for {raw['path']} is a no-op (old == new)")
        return MutationSpec(
            path=str(raw["path"]),
            old=str(raw["old"]),
            new=str(raw["new"]),
            label=str(raw.get("label", "") or ""),
        )


@dataclass
class _Tracked:
    """A file the session has taken responsibility for restoring."""

    path: str
    pristine_copy: str
    pristine_sha256: str
    #: sha256 of the bytes the harness last WROTE. Empty until a mutation lands, which is
    #: how `restore` tells "killed before mutating" from "killed while mutated".
    mutated_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "pristine_copy": self.pristine_copy,
            "pristine_sha256": self.pristine_sha256,
            "mutated_sha256": self.mutated_sha256,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> _Tracked:
        return _Tracked(
            path=str(raw.get("path", "")),
            pristine_copy=str(raw.get("pristine_copy", "")),
            pristine_sha256=str(raw.get("pristine_sha256", "")),
            mutated_sha256=str(raw.get("mutated_sha256", "") or ""),
        )


# ── durable write primitives ──────────────────────────────────────────────────
#
# Every write below is fsynced, and the containing directory is fsynced after a rename.
# Without the directory fsync a crash can lose the rename itself, which would leave a
# pristine copy the journal names but the filesystem never linked — a restore that cannot
# find its own backup is worse than no backup, because the harness would claim recovery is
# available.


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_bytes_durably(path: Path, data: bytes) -> None:
    """mkstemp + fsync + rename + parent fsync — the repo's durable-write shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── the session ───────────────────────────────────────────────────────────────


@dataclass
class MutationSession:
    """A mutation run whose undo lives on disk before the first edit.

    Open it, apply mutations, close it. Every intermediate state is recoverable by a
    different process, so nothing here depends on this one surviving.
    """

    root: Path
    session_id: str = ""
    started_at: float = 0.0
    tracked: dict[str, _Tracked] = field(default_factory=dict)

    # -- paths ----------------------------------------------------------------

    @property
    def session_dir(self) -> Path:
        return self.root / SESSION_DIRNAME

    @property
    def journal_path(self) -> Path:
        return self.session_dir / JOURNAL_NAME

    @property
    def pristine_dir(self) -> Path:
        return self.session_dir / PRISTINE_DIRNAME

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    def open(cls, root: Path, *, session_id: str = "") -> MutationSession:
        """Start a session, refusing if one is already outstanding.

        Refusing is the point: a second session over a tree the first one mutated would
        record the MUTATED bytes as pristine and make the residue permanent — the recorded
        "undo" would restore the mutation.
        """
        root = Path(root).resolve()
        session = cls(root=root)
        if session.session_dir.exists():
            raise MutationError(
                f"a mutation session is already outstanding at {session.session_dir} — "
                "restore it first (`python scripts/mutation_harness.py restore`)"
            )
        session.session_id = session_id or f"mut_{int(time.time())}_{os.getpid()}"
        session.started_at = time.time()
        session.pristine_dir.mkdir(parents=True, exist_ok=False)
        _fsync_dir(session.session_dir)
        session._write_journal()
        return session

    @classmethod
    def load(cls, root: Path) -> MutationSession | None:
        """Adopt an outstanding session, or None when the tree is clean.

        A session directory with an unreadable or absent journal still returns a session
        (with no tracked files) rather than None: the directory's existence is the residue
        signal, and swallowing it because the journal is corrupt is the failure mode this
        module exists to remove.
        """
        root = Path(root).resolve()
        session = cls(root=root)
        if not session.session_dir.exists():
            return None
        try:
            raw = json.loads(session.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return session
        if not isinstance(raw, dict):
            return session
        session.session_id = str(raw.get("session_id", "") or "")
        try:
            session.started_at = float(raw.get("started_at") or 0.0)
        except (TypeError, ValueError):
            session.started_at = 0.0
        for entry in raw.get("files") or []:
            if isinstance(entry, dict):
                tracked = _Tracked.from_dict(entry)
                if tracked.path:
                    session.tracked[tracked.path] = tracked
        return session

    def _write_journal(self) -> None:
        payload = {
            "version": JOURNAL_VERSION,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "pid": os.getpid(),
            "files": [t.to_dict() for t in self.tracked.values()],
        }
        _write_bytes_durably(
            self.journal_path,
            (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8"),
        )

    # -- tracking + mutation --------------------------------------------------

    def _track(self, rel: str) -> _Tracked:
        """Copy a file's pristine bytes aside and journal them, before any edit.

        Order is load-bearing: copy → fsync copy → journal → fsync journal → *then* mutate.
        A kill at any point after the copy leaves a journal that names a backup which
        exists, which is what makes recovery independent of this process.
        """
        existing = self.tracked.get(rel)
        if existing is not None:
            return existing
        target = self.root / rel
        if not target.is_file():
            raise MutationError(f"{rel} is not a file in {self.root}")
        pristine = target.read_bytes()
        copy_rel = f"{PRISTINE_DIRNAME}/{len(self.tracked):04d}-{Path(rel).name}"
        _write_bytes_durably(self.session_dir / copy_rel, pristine)
        tracked = _Tracked(path=rel, pristine_copy=copy_rel, pristine_sha256=_sha256(pristine))
        self.tracked[rel] = tracked
        self._write_journal()
        return tracked

    def apply(self, spec: MutationSpec) -> None:
        """Apply one mutation, with its undo already durable on disk."""
        tracked = self._track(spec.path)
        target = self.root / spec.path
        text = target.read_text(encoding="utf-8")
        occurrences = text.count(spec.old)
        if occurrences == 0:
            raise MutationError(f"{spec.path}: {spec.old!r} does not occur — nothing to mutate")
        if occurrences > 1:
            raise MutationError(
                f"{spec.path}: {spec.old!r} occurs {occurrences} times — an ambiguous "
                "mutation is refused; make `old` unique so a survivor can be located"
            )
        mutated = text.replace(spec.old, spec.new, 1).encode("utf-8")
        # Journal the mutated digest BEFORE writing it: `restore` uses this to tell an
        # expected mutation from a human edit, and a digest recorded after the write would
        # be missing for exactly the kill this module is about.
        tracked.mutated_sha256 = _sha256(mutated)
        self._write_journal()
        _write_bytes_durably(target, mutated)

    def restore(self, *, force: bool = False) -> list[str]:
        """Put every tracked file back, byte-exactly. Returns the paths restored.

        Refuses a file whose current bytes match neither the pristine nor the mutated
        digest — that is an edit made after the mutation, and overwriting it would destroy
        real work (the mirror image of the defect this module fixes).
        """
        restored: list[str] = []
        problems: list[str] = []
        for tracked in self.tracked.values():
            target = self.root / tracked.path
            backup = self.session_dir / tracked.pristine_copy
            if not backup.is_file():
                problems.append(
                    f"{tracked.path}: its pristine copy {tracked.pristine_copy} is missing "
                    "from the session directory — restore by hand from git"
                )
                continue
            pristine = backup.read_bytes()
            if _sha256(pristine) != tracked.pristine_sha256:
                problems.append(
                    f"{tracked.path}: the pristine copy's digest does not match the journal "
                    "— the backup itself is damaged, restore by hand from git"
                )
                continue
            current = target.read_bytes() if target.is_file() else b""
            current_sha = _sha256(current)
            if current_sha == tracked.pristine_sha256:
                continue  # already pristine — a restore is idempotent
            known = {tracked.pristine_sha256, tracked.mutated_sha256} - {""}
            if current_sha not in known and not force:
                problems.append(
                    f"{tracked.path}: its current content matches neither the pristine nor "
                    f"the mutated bytes, so it was edited after the mutation. Diff it "
                    f"against {backup} and merge by hand, or re-run with --force to "
                    "discard the edit."
                )
                continue
            _write_bytes_durably(target, pristine)
            restored.append(tracked.path)
        if problems:
            raise MutationError("restore could not complete:\n  - " + "\n  - ".join(problems))
        return restored

    def close(self) -> list[str]:
        """Restore, then drop the session directory — the LAST step, deliberately.

        Removing the directory before the restores are durable would erase the residue
        signal while the tree was still mutated, which is precisely the hole #2710 named.
        """
        restored = self.restore()
        shutil.rmtree(self.session_dir, ignore_errors=True)
        _fsync_dir(self.root)
        return restored

    def discard(self) -> None:
        """Drop the session WITHOUT restoring (the tree is already known-pristine)."""
        shutil.rmtree(self.session_dir, ignore_errors=True)
        _fsync_dir(self.root)


# ── the crash-surviving rail ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CleanlinessReport:
    clean: bool
    message: str
    paths: tuple[str, ...] = ()


def check_clean(root: Path) -> CleanlinessReport:
    """Is the tree free of mutation residue?

    ``clean=False`` means a mutation run did not finish. This is the whole answer to
    "nothing notices": the session directory outlives the process that made it, so a kill
    cannot hide the residue, and this check is cheap enough to run on every commit.
    """
    session = MutationSession.load(root)
    if session is None:
        return CleanlinessReport(clean=True, message="no mutation session outstanding")
    mutated = [t.path for t in session.tracked.values() if t.mutated_sha256]
    if session.tracked:
        detail = "\n".join(
            f"    {t.path}  ({'MUTATED' if t.mutated_sha256 else 'tracked, not yet mutated'})"
            for t in session.tracked.values()
        )
    else:
        detail = "    (the journal is unreadable — treat every tracked file as suspect)"
    return CleanlinessReport(
        clean=False,
        message=(
            "a mutation run did not finish — THE TREE MAY BE MUTATED.\n"
            f"  session: {session.session_dir}\n"
            f"  files:\n{detail}\n"
            "  Restore it before committing:\n"
            "    python scripts/mutation_harness.py restore"
        ),
        paths=tuple(mutated),
    )


# ── scoring a mutation set ────────────────────────────────────────────────────


@dataclass
class MutationOutcome:
    spec: MutationSpec
    caught: bool
    exit_code: int
    duration_secs: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation": self.spec.name,
            "path": self.spec.path,
            "caught": self.caught,
            "exit_code": self.exit_code,
            "duration_secs": round(self.duration_secs, 2),
        }


def run_mutations(
    root: Path,
    specs: Sequence[MutationSpec],
    command: Sequence[str],
    *,
    stream: Any = sys.stderr,
) -> list[MutationOutcome]:
    """Apply each mutation alone, score it with ``command``, restore, continue.

    One mutation at a time on purpose: two at once cannot be attributed when the command
    fails, and a survivor is the only interesting output — an unattributable survivor is
    not actionable.

    ``command`` failing (non-zero) means the mutation was CAUGHT. A mutation the command
    passes is a hole in the tests, which is the finding worth reporting.
    """
    outcomes: list[MutationOutcome] = []
    session = MutationSession.open(root)
    try:
        for spec in specs:
            session.apply(spec)
            started = time.time()
            proc = subprocess.run(command, cwd=str(root))
            duration = time.time() - started
            outcome = MutationOutcome(
                spec=spec,
                caught=proc.returncode != 0,
                exit_code=proc.returncode,
                duration_secs=duration,
            )
            outcomes.append(outcome)
            print(
                f"  {'caught  ' if outcome.caught else 'SURVIVED'} {spec.name}",
                file=stream,
            )
            session.restore()
    finally:
        # Best effort only. If this process is KILLED the session directory survives and
        # `check` reports it — that is the design, not a gap.
        session.close()
    return outcomes


def load_specs(path: Path) -> list[MutationSpec]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("mutations")
    if not isinstance(raw, list) or not raw:
        raise MutationError(f"{path} must hold a non-empty JSON list of mutation specs")
    return [MutationSpec.from_dict(entry) for entry in raw]


# ── CLI ───────────────────────────────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _cmd_check(args: argparse.Namespace) -> int:
    report = check_clean(Path(args.root))
    if report.clean:
        print("mutation-check: " + report.message)
        return 0
    print("mutation-check: " + report.message, file=sys.stderr)
    return 1


def _cmd_restore(args: argparse.Namespace) -> int:
    session = MutationSession.load(Path(args.root))
    if session is None:
        print("mutation-restore: no mutation session outstanding — nothing to restore")
        return 0
    restored = session.restore(force=bool(args.force))
    shutil.rmtree(session.session_dir, ignore_errors=True)
    if restored:
        print("mutation-restore: restored " + ", ".join(restored))
    else:
        print("mutation-restore: the tree was already pristine; session cleared")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    specs = load_specs(Path(args.spec))
    session = MutationSession.open(Path(args.root))
    for spec in specs:
        session.apply(spec)
        print(f"  applied {spec.name}")
    print(
        "\nThe tree is MUTATED and the undo is on disk. Put it back with:\n"
        "    python scripts/mutation_harness.py restore"
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    specs = load_specs(Path(args.spec))
    command = args.command if isinstance(args.command, list) else [args.command]
    if len(command) == 1:
        import shlex

        command = shlex.split(command[0])
    outcomes = run_mutations(Path(args.root), specs, command)
    survived = [o for o in outcomes if not o.caught]
    report = {
        "applied": len(outcomes),
        "caught": len(outcomes) - len(survived),
        "survived": len(survived),
        "outcomes": [o.to_dict() for o in outcomes],
    }
    print(json.dumps(report, indent=1))
    # A survivor is a real finding: the command could not tell mutated code from correct
    # code. Exit non-zero so a caller cannot read "green" as "all caught".
    return 1 if survived else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mutation_harness",
        description=(
            "Apply mutations with an on-disk undo, so a killed run cannot leave a silent "
            "residue in the source (#2710)."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(_repo_root()),
        help="repo root the mutations are relative to (default: this repo)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="exit non-zero if a mutation run did not finish")
    check.set_defaults(func=_cmd_check)

    restore = sub.add_parser("restore", help="put every tracked file back, byte-exactly")
    restore.add_argument(
        "--force",
        action="store_true",
        help="discard an edit made after the mutation instead of refusing",
    )
    restore.set_defaults(func=_cmd_restore)

    apply_cmd = sub.add_parser("apply", help="mutate the tree and leave it mutated")
    apply_cmd.add_argument("--spec", required=True, help="JSON file of mutation specs")
    apply_cmd.set_defaults(func=_cmd_apply)

    run = sub.add_parser("run", help="score each mutation with a command, restoring between")
    run.add_argument("--spec", required=True, help="JSON file of mutation specs")
    run.add_argument("--command", required=True, nargs="+", help="the command that should CATCH")
    run.set_defaults(func=_cmd_run)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except MutationError as exc:
        print(f"mutation_harness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
