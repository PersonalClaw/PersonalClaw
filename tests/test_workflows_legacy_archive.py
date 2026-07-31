"""Pre-v2 SOP archival — the user's own writing survives the clean break.

WORKFLOWS-V2 Phase 1 deletes the old workflow feature, and the v2 def store lands in
the same `workflows/` parent. The old `<name>/WORKFLOW.md` dirs move once into
`_legacy_sops/` rather than being deleted (they are the user's text) or left in place
(they would sit beside real definitions looking inexplicably ignored).
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.workflows.legacy import archive_legacy_sops


def _sop(root: Path, name: str, body: str = "1. do the thing\n") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "WORKFLOW.md").write_text(body, encoding="utf-8")
    return d


class TestArchiveLegacySops:
    def test_moves_sop_dirs_and_preserves_content(self, tmp_path: Path) -> None:
        wf = tmp_path / "workflows"
        _sop(wf, "release-checklist", "1. tag\n2. publish\n")
        _sop(wf, "weekly-review")

        moved = archive_legacy_sops(wf)

        assert sorted(moved) == ["release-checklist", "weekly-review"]
        assert not (wf / "release-checklist").exists(), "the original must be moved, not copied"
        archived = wf / "_legacy_sops" / "release-checklist" / "WORKFLOW.md"
        assert archived.is_file()
        assert archived.read_text(encoding="utf-8") == "1. tag\n2. publish\n"

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """Runs on every boot — a second pass must find nothing and change nothing."""
        wf = tmp_path / "workflows"
        _sop(wf, "release-checklist")

        assert archive_legacy_sops(wf) == ["release-checklist"]
        assert archive_legacy_sops(wf) == []
        assert archive_legacy_sops(wf) == []
        # Exactly one archived copy — no `-2` suffix from re-archiving itself.
        assert [p.name for p in (wf / "_legacy_sops").iterdir()] == ["release-checklist"]

    def test_ignores_dirs_without_a_workflow_md(self, tmp_path: Path) -> None:
        """The v2 def store will live here too; only legacy SOPs move."""
        wf = tmp_path / "workflows"
        (wf / "defs").mkdir(parents=True)
        (wf / "defs" / "research.json").write_text("{}", encoding="utf-8")
        (wf / "runs").mkdir()

        assert archive_legacy_sops(wf) == []
        assert (wf / "defs" / "research.json").is_file()
        assert (wf / "runs").is_dir()
        assert not (wf / "_legacy_sops").exists(), "no archive dir when there is nothing to archive"

    def test_skips_underscore_dirs(self, tmp_path: Path) -> None:
        """Guards against re-archiving the archive if it ever holds a WORKFLOW.md."""
        wf = tmp_path / "workflows"
        _sop(wf / "_legacy_sops", "already-done")

        assert archive_legacy_sops(wf) == []
        assert (wf / "_legacy_sops" / "already-done" / "WORKFLOW.md").is_file()

    def test_name_collision_keeps_both(self, tmp_path: Path) -> None:
        """A pre-existing archived name must not be clobbered — it is user text."""
        wf = tmp_path / "workflows"
        _sop(wf / "_legacy_sops", "notes", "the ARCHIVED one\n")
        _sop(wf, "notes", "the NEW one\n")

        assert archive_legacy_sops(wf) == ["notes"]
        archive = wf / "_legacy_sops"
        assert (archive / "notes" / "WORKFLOW.md").read_text(
            encoding="utf-8"
        ) == "the ARCHIVED one\n"
        assert (archive / "notes-2" / "WORKFLOW.md").read_text(encoding="utf-8") == "the NEW one\n"

    def test_missing_dir_is_a_no_op(self, tmp_path: Path) -> None:
        """Most homes never had the old feature; this runs at startup regardless."""
        assert archive_legacy_sops(tmp_path / "does-not-exist") == []

    def test_never_raises_on_an_unreadable_entry(self, tmp_path: Path, monkeypatch) -> None:
        """A permissions problem on one stale dir must not stop the gateway booting."""
        wf = tmp_path / "workflows"
        _sop(wf, "broken")
        _sop(wf, "fine")

        real_move = __import__("shutil").move

        def flaky(src: str, dst: str):
            if "broken" in src:
                raise OSError("permission denied")
            return real_move(src, dst)

        monkeypatch.setattr("personalclaw.workflows.legacy.shutil.move", flaky)
        # The good one still moves; the failure is logged, not raised.
        assert archive_legacy_sops(wf) == ["fine"]
        assert (wf / "broken").exists()
