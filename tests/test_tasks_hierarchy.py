"""Tests for the Project / TaskList hierarchy store."""

from unittest.mock import patch

import pytest

from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.tasks.models import DEFAULT_PROJECTS, Project, TaskList


@pytest.fixture()
def store(tmp_path):
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        yield HierarchyStore()


class TestDefaults:
    def test_defaults_seeded(self, store):
        projects = store.list_projects()
        names = {p.name for p in projects}
        assert "Personal" in names
        assert "Repeatable" in names
        assert all(p.is_default_project() for p in projects if p.name in DEFAULT_PROJECTS)

    def test_defaults_idempotent(self, store):
        store.ensure_defaults()
        store.ensure_defaults()
        personals = [p for p in store.list_projects() if p.name == "Personal"]
        assert len(personals) == 1

    def test_default_project_undeletable(self, store):
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be deleted"):
            store.delete_project(personal.id)


class TestProjectCrud:
    def test_create_and_get(self, store):
        p = store.create_project("Website")
        assert p.name == "Website"
        assert not p.is_default
        assert store.get_project(p.id).name == "Website"

    def test_create_duplicate_name_rejected(self, store):
        store.create_project("Website")
        with pytest.raises(ValueError, match="already exists"):
            store.create_project("Website")

    def test_create_empty_name_rejected(self, store):
        with pytest.raises(ValueError, match="required"):
            store.create_project("   ")

    def test_update_name(self, store):
        p = store.create_project("Old")
        updated = store.update_project(p.id, name="New")
        assert updated.name == "New"
        assert store.get_project(p.id).name == "New"

    def test_update_to_duplicate_name_rejected(self, store):
        store.create_project("A")
        b = store.create_project("B")
        with pytest.raises(ValueError, match="already exists"):
            store.update_project(b.id, name="A")

    def test_delete_custom_project(self, store):
        p = store.create_project("Temp")
        assert store.delete_project(p.id) is True
        assert store.get_project(p.id) is None

    def test_find_or_create(self, store):
        a = store.find_or_create_project("Reused")
        b = store.find_or_create_project("Reused")
        assert a.id == b.id


class TestProjectEntity:
    """The first-class Project: context dir, workspace binding, new fields."""

    def test_context_dir_created_with_project(self, store, tmp_path):
        p = store.create_project("Ctx")
        ctx = store.context_dir(p.id)
        assert ctx.is_dir()
        assert ctx == tmp_path / "projects" / p.id / "context"

    def test_project_json_lives_in_per_project_dir(self, store, tmp_path):
        p = store.create_project("Layout")
        assert (tmp_path / "projects" / p.id / "project.json").is_file()

    def test_create_with_workspace_dir(self, store):
        p = store.create_project("Bound", workspace_dir="/tmp/repo")
        assert p.workspace_dir == "/tmp/repo"
        assert store.get_project(p.id).workspace_dir == "/tmp/repo"
        assert store.create_project("Free").workspace_dir == ""

    def test_update_workspace_and_status_and_lock(self, store):
        p = store.create_project("W")
        u = store.update_project(p.id, workspace_dir="/tmp/x", status="archived", name_locked=True)
        assert u.workspace_dir == "/tmp/x" and u.status == "archived" and u.name_locked is True
        re = store.get_project(p.id)
        assert re.status == "archived" and re.name_locked is True

    def test_update_invalid_status_rejected(self, store):
        p = store.create_project("S")
        with pytest.raises(ValueError, match="status must be"):
            store.update_project(p.id, status="bogus")

    def test_delete_removes_project_dir(self, store, tmp_path):
        p = store.create_project("Gone")
        store.context_dir(p.id)  # ensure context exists
        pdir = tmp_path / "projects" / p.id
        assert pdir.is_dir()
        assert store.delete_project(p.id) is True
        assert not pdir.exists()

    def test_worktrees_dir(self, store, tmp_path):
        p = store.create_project("WT")
        wt = store.worktrees_dir(p.id)
        assert wt.is_dir() and wt == tmp_path / "projects" / p.id / "worktrees"


class TestMigration:
    """One-time migration to the projects/<id>/ layout (clean break, idempotent)."""

    def test_migrates_old_store_deletes_legacy_orphans_renames_chore(self, tmp_path):
        import json

        # OLD layout: legacy flat orphans at projects/ + the old tasks/projects store.
        (tmp_path / "projects").mkdir(parents=True)
        (tmp_path / "projects" / "deadbeef.json").write_text(
            json.dumps({"id": "deadbeef", "name": "Use below report", "phases": [1, 2, 3]})
        )
        (tmp_path / "tasks" / "projects").mkdir(parents=True)
        (tmp_path / "tasks" / "projects" / "chore.json").write_text(
            json.dumps({"id": "chore", "name": "Chore", "is_default": True})
        )
        (tmp_path / "tasks" / "projects" / "p-keep0001.json").write_text(
            json.dumps({"id": "p-keep0001", "name": "Real Work"})
        )
        with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
            store = HierarchyStore()
            names = {p.name for p in store.list_projects()}
        # legacy orphan gone, old store dir gone, Chore folded to Personal, real kept
        assert "Use below report" not in names
        assert not list((tmp_path / "projects").glob("*.json"))  # no flat files left
        assert not (tmp_path / "tasks" / "projects").exists()
        assert "Personal" in names and "Real Work" in names and "Chore" not in names
        assert (tmp_path / "projects" / "p-keep0001" / "project.json").is_file()

    def test_migration_idempotent(self, tmp_path):
        with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
            store = HierarchyStore()
            store.list_projects()
            before = {p.id for p in store.list_projects()}
            store.migrate_layout()
            after = {p.id for p in store.list_projects()}
        assert before == after


class TestTaskListRouting:
    def test_repeatable_routes_to_repeatable_project(self, store):
        tl = store.create_task_list("Weekly review", repeatable=True)
        repeatable = store.get_project_by_name("Repeatable")
        assert tl.project_id == repeatable.id

    def test_explicit_project_id(self, store):
        p = store.create_project("Proj")
        tl = store.create_task_list("List", project_id=p.id)
        assert tl.project_id == p.id

    def test_unknown_project_id_rejected(self, store):
        with pytest.raises(ValueError, match="no project with id"):
            store.create_task_list("List", project_id="p-nope")

    def test_project_name_find_or_create(self, store):
        tl = store.create_task_list("List", project_name="Fresh")
        fresh = store.get_project_by_name("Fresh")
        assert fresh is not None
        assert tl.project_id == fresh.id

    def test_no_project_routes_to_personal(self, store):
        tl = store.create_task_list("Orphan list")
        personal = store.get_project_by_name("Personal")
        assert tl.project_id == personal.id

    def test_empty_name_rejected(self, store):
        with pytest.raises(ValueError, match="required"):
            store.create_task_list("  ")


class TestTaskListCrud:
    def test_list_by_project(self, store):
        p = store.create_project("Proj")
        store.create_task_list("L1", project_id=p.id)
        store.create_task_list("L2", project_id=p.id)
        store.create_task_list("Other")  # → Personal
        assert len(store.list_task_lists(project_id=p.id)) == 2

    def test_update_moves_to_another_project(self, store):
        a = store.create_project("A")
        b = store.create_project("B")
        tl = store.create_task_list("L", project_id=a.id)
        store.update_task_list(tl.id, project_id=b.id)
        assert store.get_task_list(tl.id).project_id == b.id

    def test_delete(self, store):
        tl = store.create_task_list("L")
        assert store.delete_task_list(tl.id) is True
        assert store.get_task_list(tl.id) is None

    def test_delete_project_cascades_lists(self, store):
        p = store.create_project("Proj")
        tl = store.create_task_list("L", project_id=p.id)
        store.delete_project(p.id)
        assert store.get_task_list(tl.id) is None


class TestModelSerialization:
    def test_project_roundtrip(self):
        p = Project(id="p1", name="X", created_at="t", updated_at="t")
        assert Project.from_dict(p.to_dict()).name == "X"

    def test_default_name_implies_default_flag(self):
        p = Project.from_dict({"id": "p1", "name": "Personal"})
        assert p.is_default_project() is True

    def test_tasklist_roundtrip(self):
        tl = TaskList(id="tl1", name="L", project_id="p1")
        assert TaskList.from_dict(tl.to_dict()).project_id == "p1"
