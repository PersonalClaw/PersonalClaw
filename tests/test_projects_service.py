"""Tests for the Project service (resolve/bind + per-unit task-list attach)."""

from unittest.mock import patch

import pytest

from personalclaw import projects as svc
from personalclaw.tasks.hierarchy import HierarchyStore


@pytest.fixture()
def cfg(tmp_path):
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        yield tmp_path


def test_resolve_uses_valid_chosen_id(cfg):
    store = HierarchyStore()
    p = store.create_project("Chosen")
    assert svc.resolve_project_id(p.id) == p.id


def test_resolve_blank_routes_to_personal(cfg):
    pid = svc.resolve_project_id("")
    assert HierarchyStore().get_project(pid).name == "Personal"


def test_resolve_stale_id_falls_back_to_personal(cfg):
    pid = svc.resolve_project_id("p-doesnotexist")
    assert HierarchyStore().get_project(pid).name == "Personal"


def test_resolve_auto_name_creates_named_project(cfg):
    pid = svc.resolve_project_id("", auto_name="Build a budgeting app")
    assert HierarchyStore().get_project(pid).name == "Build a budgeting app"


def test_resolve_auto_name_dedupes_on_collision(cfg):
    store = HierarchyStore()
    store.create_project("Dup")
    pid = svc.resolve_project_id("", auto_name="Dup")
    # a fresh project, NOT the existing one, with a de-duplicated name
    assert pid != store.get_project_by_name("Dup").id
    assert store.get_project(pid).name == "Dup (2)"


def test_ensure_task_list_is_idempotent(cfg):
    store = HierarchyStore()
    p = store.create_project("Proj")
    a = svc.ensure_task_list(p.id, "Work")
    b = svc.ensure_task_list(p.id, "Work")
    assert a == b
    assert store.get_task_list(a).project_id == p.id


def test_maybe_rename_updates_auto_named_project(cfg):
    pid = svc.resolve_project_id("", auto_name="Initial")
    svc.maybe_rename_from(pid, "A Better Title")
    assert HierarchyStore().get_project(pid).name == "A Better Title"


def test_maybe_rename_skips_locked_and_default_and_blank(cfg):
    store = HierarchyStore()
    # locked (user-renamed) is untouched
    p = store.create_project("Mine")
    store.update_project(p.id, name_locked=True)
    svc.maybe_rename_from(p.id, "LLM Title")
    assert store.get_project(p.id).name == "Mine"
    # default catch-all is untouched
    personal = store.find_or_create_project("Personal")
    svc.maybe_rename_from(personal.id, "Renamed Personal")
    assert store.get_project(personal.id).name == "Personal"
    # blank title is a no-op
    q = svc.resolve_project_id("", auto_name="Keep")
    svc.maybe_rename_from(q, "   ")
    assert store.get_project(q).name == "Keep"


def test_maybe_rename_dedupes_on_collision(cfg):
    store = HierarchyStore()
    store.create_project("Taken")
    pid = svc.resolve_project_id("", auto_name="Temp")
    svc.maybe_rename_from(pid, "Taken")
    assert store.get_project(pid).name == "Taken (2)"


def test_context_dir_path_created_and_guards(cfg):
    from pathlib import Path

    p = HierarchyStore().create_project("Ctx")
    cd = svc.context_dir(p.id)
    assert cd.endswith(f"/projects/{p.id}/context") and Path(cd).is_dir()
    # blank / missing ids return "" (not a crash, not a stray dir)
    assert svc.context_dir("") == ""
    assert svc.context_dir("p-missing") == ""
