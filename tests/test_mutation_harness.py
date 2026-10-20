"""The mutation residue rail must survive the process being KILLED (#2710).

A mutation run that dies partway through used to leave the mutation **in the source** and
nothing noticed: every later suite honestly passed against mutated code, and an uncovered
mutation made everything green. The obvious repair — revert in a ``finally:`` — cannot fix
it, because ``SIGKILL`` runs no ``finally:``, no ``atexit`` handler and no signal handler.

So the rail these tests defend is on-disk state written BEFORE the first edit, and the
central case here does not simulate a crash — it starts a real subprocess, waits for it to
mutate a real file, sends it a real ``SIGKILL``, and then asserts from the parent that the
residue is (a) actually in the tree, (b) reported, and (c) recoverable byte-exactly. A test
that only called ``session.close()`` and checked the result would pass against a
``finally:``-only implementation, which is precisely the design this issue rejected.

Vacuity floor, stated because a rail that cannot fail is the way our gates die: every
"detected" assertion here is paired with the same call on a clean tree, so a
``check_clean`` that returned "not clean" unconditionally would fail these tests too. The
pre-commit case likewise asserts the hook exits 0 with no session outstanding.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from scripts import mutation_harness as MH

_ORIGINAL = "VALUES = (\n    'alpha',\n    'beta',\n)\n"
_MUTATED = "VALUES = (\n    'alpha',\n    'gamma',\n)\n"


def _tree(root: Path) -> Path:
    """A tiny source tree with one mutable file."""
    pkg = root / "src"
    pkg.mkdir(parents=True, exist_ok=True)
    target = pkg / "supply.py"
    target.write_text(_ORIGINAL, encoding="utf-8")
    return target


def _spec(**over):
    base = {"path": "src/supply.py", "old": "'beta'", "new": "'gamma'", "label": "beta->gamma"}
    base.update(over)
    return MH.MutationSpec.from_dict(base)


class TestTheUndoIsOnDiskBeforeTheFirstEdit:
    """The property that makes recovery independent of the mutating process."""

    def test_the_pristine_copy_and_journal_exist_before_any_mutation_is_written(self, tmp_path):
        target = _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        # Nothing mutated yet, but opening already made the residue signal real.
        assert session.journal_path.is_file()
        assert target.read_text(encoding="utf-8") == _ORIGINAL

        session.apply(_spec())
        assert target.read_text(encoding="utf-8") == _MUTATED

        journal = json.loads(session.journal_path.read_text(encoding="utf-8"))
        entry = journal["files"][0]
        backup = session.session_dir / entry["pristine_copy"]
        # The backup holds the PRISTINE bytes, not the mutated ones — a backup taken after
        # the write would "restore" the mutation, which is the failure inverted.
        assert backup.read_text(encoding="utf-8") == _ORIGINAL
        assert entry["mutated_sha256"], "the mutated digest must be journalled"

    def test_a_second_session_over_a_mutated_tree_is_refused(self, tmp_path):
        """Otherwise session two records the MUTATED bytes as pristine, making it permanent."""
        _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        session.apply(_spec())
        with pytest.raises(MH.MutationError, match="already outstanding"):
            MH.MutationSession.open(tmp_path)

    def test_an_ambiguous_mutation_is_refused_rather_than_applied_to_the_first_hit(self, tmp_path):
        target = tmp_path / "src" / "dup.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\ny = 1\n", encoding="utf-8")
        session = MH.MutationSession.open(tmp_path)
        with pytest.raises(MH.MutationError, match="occurs 2 times"):
            session.apply(_spec(path="src/dup.py", old="1", new="2"))
        assert target.read_text(encoding="utf-8") == "x = 1\ny = 1\n"


class TestASigkilledRunIsDetectedAndRecoverable:
    """The case a ``finally:`` cannot pass. Real subprocess, real SIGKILL."""

    @staticmethod
    def _driver(harness_path: Path, root: Path) -> str:
        return textwrap.dedent(f"""
            import importlib.util, sys, time
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("mh", r"{harness_path}")
            mh = importlib.util.module_from_spec(spec)
            # Register BEFORE exec: @dataclass resolves sys.modules[cls.__module__].
            sys.modules["mh"] = mh
            spec.loader.exec_module(mh)
            session = mh.MutationSession.open(Path(r"{root}"))
            try:
                session.apply(mh.MutationSpec(path="src/supply.py", old="'beta'", new="'gamma'"))
                Path(r"{root}").joinpath("MUTATED").write_text("ready")
                # Park forever. The parent SIGKILLs us here, so neither the `finally:`
                # below nor any atexit hook will run — which is the whole point.
                while True:
                    time.sleep(0.05)
            finally:
                session.close()
            """)

    def test_the_residue_is_in_the_tree_reported_and_restored_byte_exactly(self, tmp_path):
        target = _tree(tmp_path)
        harness = Path(MH.__file__).resolve()
        driver = tmp_path / "driver.py"
        driver.write_text(self._driver(harness, tmp_path), encoding="utf-8")

        proc = subprocess.Popen([sys.executable, str(driver)])
        try:
            ready = tmp_path / "MUTATED"
            deadline = time.time() + 30
            while not ready.exists():
                assert proc.poll() is None, "the driver exited before it mutated the file"
                assert time.time() < deadline, "the driver never reported a mutation"
                time.sleep(0.02)
            os.kill(proc.pid, signal.SIGKILL)
        finally:
            proc.wait(timeout=30)

        assert proc.returncode == -signal.SIGKILL, "the driver must have been KILLED, not exited"

        # (a) the tree really is mutated — the defect, reproduced.
        assert target.read_text(encoding="utf-8") == _MUTATED

        # (b) something notices. This is the clause #2710 named as missing.
        report = MH.check_clean(tmp_path)
        assert report.clean is False
        assert "src/supply.py" in report.paths
        assert "TREE MAY BE MUTATED" in report.message

        # (c) a DIFFERENT process can undo it byte-exactly.
        adopted = MH.MutationSession.load(tmp_path)
        assert adopted is not None
        assert adopted.close() == ["src/supply.py"]
        assert target.read_text(encoding="utf-8") == _ORIGINAL

        # ...and the tree now reads clean, so the check is not a constant.
        assert MH.check_clean(tmp_path).clean is True

    def test_a_clean_tree_reports_clean(self, tmp_path):
        """The vacuity floor for every "detected" assertion above."""
        _tree(tmp_path)
        report = MH.check_clean(tmp_path)
        assert report.clean is True
        assert report.paths == ()

    def test_a_session_directory_with_an_unreadable_journal_is_still_reported(self, tmp_path):
        """Refusing to parse a corrupt journal must not read as "clean"."""
        _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        session.apply(_spec())
        session.journal_path.write_text("{not json", encoding="utf-8")
        report = MH.check_clean(tmp_path)
        assert report.clean is False
        assert "unreadable" in report.message


class TestRestoreWillNotEatRealWork:
    def test_a_file_edited_after_the_mutation_is_refused_not_overwritten(self, tmp_path):
        target = _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        session.apply(_spec())
        target.write_text(_MUTATED + "# a real edit made afterwards\n", encoding="utf-8")
        with pytest.raises(MH.MutationError, match="edited after the mutation"):
            session.restore()
        assert "a real edit made afterwards" in target.read_text(encoding="utf-8")

    def test_force_discards_the_edit_when_the_human_asks(self, tmp_path):
        target = _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        session.apply(_spec())
        target.write_text("something else entirely\n", encoding="utf-8")
        assert session.restore(force=True) == ["src/supply.py"]
        assert target.read_text(encoding="utf-8") == _ORIGINAL

    def test_restore_is_idempotent(self, tmp_path):
        target = _tree(tmp_path)
        session = MH.MutationSession.open(tmp_path)
        session.apply(_spec())
        assert session.restore() == ["src/supply.py"]
        assert session.restore() == []
        assert target.read_text(encoding="utf-8") == _ORIGINAL


class TestScoringAMutationSet:
    def test_a_command_that_fails_counts_as_caught_and_one_that_passes_as_survived(self, tmp_path):
        _tree(tmp_path)
        # The "test suite": greps the file for the mutated token. It FAILS (exit 1) while the
        # mutation is present, which is a caught mutation.
        catcher = tmp_path / "catch.py"
        catcher.write_text(
            "import sys, pathlib\n"
            "text = pathlib.Path('src/supply.py').read_text()\n"
            "sys.exit(1 if 'gamma' in text else 0)\n",
            encoding="utf-8",
        )
        outcomes = MH.run_mutations(
            tmp_path, [_spec()], [sys.executable, str(catcher)], stream=sys.stdout
        )
        assert [o.caught for o in outcomes] == [True]
        # The session closed cleanly, so the tree is pristine and nothing is outstanding.
        assert (tmp_path / "src" / "supply.py").read_text(encoding="utf-8") == _ORIGINAL
        assert MH.check_clean(tmp_path).clean is True

        blind = tmp_path / "blind.py"
        blind.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        survived = MH.run_mutations(
            tmp_path, [_spec()], [sys.executable, str(blind)], stream=sys.stdout
        )
        assert [o.caught for o in survived] == [False]

    def test_the_cli_run_exits_non_zero_when_a_mutation_survives(self, tmp_path):
        """A survivor is a finding — a zero exit would let a caller read it as "all caught"."""
        _tree(tmp_path)
        spec_file = tmp_path / "mutations.json"
        spec_file.write_text(json.dumps([_spec().to_dict()]), encoding="utf-8")
        blind = tmp_path / "blind.py"
        blind.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        code = MH.main(
            [
                "--root",
                str(tmp_path),
                "run",
                "--spec",
                str(spec_file),
                "--command",
                sys.executable,
                str(blind),
            ]
        )
        assert code == 1


class TestThePreCommitHookRefusesAnOutstandingSession:
    """The rail's teeth: a residue must block the commit that would carry it to main."""

    @staticmethod
    def _fixture_repo(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        for name in ("run_precommit.sh", "mutation_harness.py"):
            shutil.copy(Path(MH.__file__).resolve().parent / name, repo / "scripts" / name)
        os.chmod(repo / "scripts" / "run_precommit.sh", 0o755)
        # The hook prefers `.venv/bin/python`, exactly as a contributor's checkout has it.
        # Pointing it at THIS interpreter also keeps the fixture off the machine's stray
        # `python3` (3.9 on some dev boxes), so a failure here is the rail, not a toolchain.
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "python").symlink_to(sys.executable)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        return repo

    def _hook(self, repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", "scripts/run_precommit.sh"],
            cwd=repo,
            capture_output=True,
            text=True,
        )

    def test_it_passes_with_no_session_and_refuses_with_one(self, tmp_path):
        repo = self._fixture_repo(tmp_path)

        # Vacuity floor: the hook must be green on a clean tree, or "refuses" means nothing.
        clean = self._hook(repo)
        assert clean.returncode == 0, clean.stdout + clean.stderr

        # Now the deliberately broken tree: a real session, opened by the real harness.
        target = repo / "src" / "supply.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_ORIGINAL, encoding="utf-8")
        session = MH.MutationSession.open(repo)
        session.apply(_spec())

        refused = self._hook(repo)
        assert refused.returncode == 1
        combined = refused.stdout + refused.stderr
        assert "TREE MAY BE MUTATED" in combined
        assert "mutation_harness.py restore" in combined
