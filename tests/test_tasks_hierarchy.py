"""Tests for the Project / TaskList hierarchy store."""

from unittest.mock import patch

import pytest

from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.tasks.models import BUILTIN_PROJECTS, Project, TaskList


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
        assert all(p.is_builtin_project() for p in projects if p.name in BUILTIN_PROJECTS)

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

    def test_guard_refusals_say_builtin_not_the_default(self, store):
        # #638: two projects are protected, so a refusal saying "THE default
        # project" (singular article) was wrong for each — and clashed with the
        # UI's own vocabulary, which badges these rows "Built-in". Both guards
        # must name the concept the flag actually means, for every protected
        # project, and the word "default" must not resurface in either message
        # (these strings travel verbatim to the HTTP 400 the dashboard shows).
        store.ensure_defaults()
        for name in BUILTIN_PROJECTS:
            p = store.get_project_by_name(name)
            with pytest.raises(ValueError) as del_err:
                store.delete_project(p.id)
            assert f"built-in project '{name}' cannot be deleted" in str(del_err.value)
            assert "default" not in str(del_err.value)
            with pytest.raises(ValueError) as ren_err:
                store.update_project(p.id, name="Something Else")
            assert f"built-in project '{name}' cannot be renamed" in str(ren_err.value)
            assert "default" not in str(ren_err.value)

    def test_default_project_rename_refused_and_no_duplicate(self, store):
        # Renaming a default is refused (its identity is its name), and the refusal must
        # NOT leave a re-seeded duplicate behind: the original stays, one Personal only.
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be renamed"):
            store.update_project(personal.id, name="Renamed")
        personals = [p for p in store.list_projects() if p.name == "Personal"]
        assert len(personals) == 1
        assert personals[0].id == personal.id
        assert store.get_project_by_name("Renamed") is None

    def test_default_project_non_name_update_still_works(self, store):
        # Only the name is frozen on a default — brief/workspace_dir/status still update.
        store.ensure_defaults()
        personal = store.get_project_by_name("Personal")
        u = store.update_project(
            personal.id, brief="Catch-all", workspace_dir="/tmp/x", status="archived"
        )
        assert u.name == "Personal"
        assert u.brief == "Catch-all" and u.workspace_dir == "/tmp/x" and u.status == "archived"
        # A no-op name (same value) must not trip the rename guard either.
        assert store.update_project(personal.id, name="Personal").name == "Personal"

    def test_stray_default_flagged_project_is_deletable(self, store):
        # A project carrying a sticky stored is_default:true but NOT holding a protected
        # name (e.g. a renamed/duplicated leftover from an older home) must be cleanable —
        # the delete guard keys on the live protected names, not the stored flag.
        store.ensure_defaults()
        p = store.create_project("Leftover")
        p.is_builtin = True
        store._write_project(p)
        assert store.get_project(p.id).is_builtin_project() is True
        assert store.delete_project(p.id) is True
        assert store.get_project(p.id) is None
        # A project literally named Personal stays undeletable even so.
        personal = store.get_project_by_name("Personal")
        with pytest.raises(ValueError, match="cannot be deleted"):
            store.delete_project(personal.id)


class TestProjectCrud:
    def test_create_and_get(self, store):
        p = store.create_project("Website")
        assert p.name == "Website"
        assert not p.is_builtin
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
            json.dumps({"id": "chore", "name": "Chore", "is_builtin": True})
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

    def test_duplicate_name_in_same_project_rejected(self, store):
        # Task lists match projects (#777): a duplicate name within one project is refused, so a
        # project can never hold two same-named lists and the General auto-attach stays unambiguous.
        p = store.create_project("Proj")
        store.create_task_list("Dup", project_id=p.id)
        with pytest.raises(ValueError, match="already exists in this project"):
            store.create_task_list("Dup", project_id=p.id)

    def test_same_name_in_different_projects_allowed(self, store):
        # Uniqueness is PER PROJECT — every project legitimately has its own "General".
        a = store.create_project("A")
        b = store.create_project("B")
        store.create_task_list("Shared", project_id=a.id)
        tl = store.create_task_list("Shared", project_id=b.id)
        assert tl.project_id == b.id


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


class TestGeneralAutoAttach:
    def test_attaches_to_oldest_general_among_grandfathered_duplicates(self, store):
        # `create_task_list` now rejects duplicate names, but a project migrated from before that
        # check could carry two "General" lists. A `project_id`-only task must land on the ORIGINAL
        # (oldest) one deterministically, not whichever id sorts first (#777). Written directly to
        # bypass the new uniqueness check and reproduce the grandfathered shape; the newer list's id
        # sorts BEFORE the older's, so a naive first-match would pick the wrong one.
        from personalclaw.tasks.handlers import _attach_project_general_list

        p = store.create_project("Legacy")
        older = TaskList(
            id="tl-older",
            name="General",
            project_id=p.id,
            created_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        )
        newer = TaskList(
            id="tl-a-newer",
            name="General",
            project_id=p.id,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        store._write_list(older)
        store._write_list(newer)
        body: dict = {"project_id": p.id}
        _attach_project_general_list(body)
        assert body["task_list_id"] == older.id


class TestModelSerialization:
    def test_project_roundtrip(self):
        p = Project(id="p1", name="X", created_at="t", updated_at="t")
        assert Project.from_dict(p.to_dict()).name == "X"

    def test_default_name_implies_default_flag(self):
        p = Project.from_dict({"id": "p1", "name": "Personal"})
        assert p.is_builtin_project() is True

    def test_tasklist_roundtrip(self):
        tl = TaskList(id="tl1", name="L", project_id="p1")
        assert TaskList.from_dict(tl.to_dict()).project_id == "p1"


class TestWorkspaceBindGuard:
    """A bound workspace becomes a WRITE target for generated agent files and the cwd of an
    unsandboxed worker, so create/update refuse an unsafe path at bind time (#358)."""

    _UNSAFE = [
        "/",  # OS/system root
        "~",  # the home directory itself
        "relative/dir",  # a relative path
        "~/.ssh",  # a credential directory
    ]

    @pytest.mark.parametrize("bad", _UNSAFE)
    def test_create_refuses_unsafe_workspace(self, store, bad):
        with pytest.raises(ValueError):
            store.create_project("Bound", workspace_dir=bad)
        # the refusal wrote nothing — no project was created.
        assert store.get_project_by_name("Bound") is None

    @pytest.mark.parametrize("bad", _UNSAFE)
    def test_update_refuses_unsafe_workspace(self, store, bad):
        p = store.create_project("Bound")  # safe: no workspace_dir bound
        with pytest.raises(ValueError):
            store.update_project(p.id, workspace_dir=bad)
        # the previous (empty) binding survived the refusal.
        assert store.get_project(p.id).workspace_dir == ""

    def test_safe_absolute_workspace_still_binds(self, store, tmp_path):
        # vacuity: the guard is not rejecting everything — a normal absolute dir still binds,
        # and clearing the binding (empty) stays legal.
        d = tmp_path / "repo"
        d.mkdir()
        p = store.create_project("Bound", workspace_dir=str(d))
        assert p.workspace_dir == str(d)
        assert store.update_project(p.id, workspace_dir="").workspace_dir == ""
