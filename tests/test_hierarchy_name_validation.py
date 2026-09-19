"""Project/task-list names are validated ONCE, the same way on create and update (#456, #514).

Two adjacent methods disagreed, and in opposite directions. Measured on `origin/main` before the
fix, through the real store:

    CREATE  find_or_create_project(123)      -> AttributeError: 'int' object has no attribute
                                                'strip'      (an unhandled 500)
            find_or_create_project(None)     -> AttributeError            "
    UPDATE  update_project(id, name=123)     -> 200, persisted '123'
            update_project(id, name=None)    -> 200, persisted 'None'
            update_project(id, name={'a':1}) -> 200, persisted "{'a': 1}"   <- Python repr, in JSON
            update_project(id, name='K'*3000)-> 200, persisted 3000 chars

Coercion is the more damaging half: it does not refuse a bad type, it *invents* a plausible value.
A caller sending {"name": null} meaning "clear it" got a project literally called "None".

`agent_instructions_template` was worse still -- assigned with no coercion AND no check, so a dict
round-tripped into project.json as a nested object where every reader expects a string.

VERIFIED NOT A DEFECT at this SHA (do not re-add machinery for these):
  * a non-string `project_id` is already refused -- `record_path` raises `UnsafeRecordId` naming the
    type, and `dashboard/invalid_id_gate.py` maps it to a 400 for every route. #456's PosixPath
    TypeError was fixed by #455.
  * `status` still goes through `str()`, but is then checked against a closed set, so a dict is
    refused anyway. Asserted below so that stays true.
"""

from __future__ import annotations

import pytest

from personalclaw.tasks import hierarchy as H
from personalclaw.tasks.hierarchy import MAX_NAME_LEN, HierarchyStore, clean_name, require_text

#: Every non-string a JSON body can carry. Named so a new case cannot be added to one verb only.
NON_STRINGS: tuple[object, ...] = (123, 1.5, True, None, {"a": 1}, ["a"], 0, False)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Both bindings, and the redirect is asserted — these tests write projects to disk."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "config_dir", lambda: tmp_path)
    store = HierarchyStore()
    assert store._projects_dir().is_relative_to(tmp_path), "the store must be redirected"
    return tmp_path


@pytest.fixture()
def store():
    return HierarchyStore()


# ── the shared validator ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_require_text_refuses_every_non_string(bad):
    with pytest.raises(ValueError, match="must be a string"):
        require_text(bad, field="name")


def test_require_text_names_the_field_and_the_type():
    # The message is the whole point of a 400 over a 500: it says what to send instead.
    with pytest.raises(ValueError) as e:
        require_text({"a": 1}, field="project name")
    assert "project name" in str(e.value) and "dict" in str(e.value)


def test_require_text_strips_but_keeps_an_empty_string_representable():
    assert require_text("  hi  ", field="x") == "hi"
    assert require_text("   ", field="x") == ""


def test_clean_name_refuses_empty_and_over_long():
    with pytest.raises(ValueError, match="required"):
        clean_name("   ", field="name")
    with pytest.raises(ValueError, match=f"at most {MAX_NAME_LEN}"):
        clean_name("K" * (MAX_NAME_LEN + 1), field="name")


def test_clean_name_accepts_exactly_the_limit():
    # Vacuity guard: the cap must be inclusive, or a legitimate 200-char name is refused.
    assert len(clean_name("K" * MAX_NAME_LEN)) == MAX_NAME_LEN


def test_over_long_is_refused_not_truncated():
    # Truncating is the same invent-a-value mistake as the str() coercion: the user never asked
    # for a name they did not type, and a truncated name can collide with an existing one.
    with pytest.raises(ValueError):
        clean_name("K" * 3000)


# ── both verbs, both nouns: the asymmetry is gone ──────────────────────────────────────────


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_create_project_refuses_a_non_string_name(store, bad):
    with pytest.raises(ValueError):
        store.create_project(name=bad)


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_update_project_refuses_a_non_string_name_instead_of_coercing(store, bad):
    p = store.create_project(name="Real")
    with pytest.raises(ValueError):
        store.update_project(p.id, name=bad)
    # 🔑 And nothing was written: the old code persisted 'None' / "{'a': 1}" here.
    assert store.get_project(p.id).name == "Real"


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_find_or_create_project_refuses_a_non_string(store, bad):
    with pytest.raises(ValueError):
        store.find_or_create_project(bad)


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_create_task_list_refuses_a_non_string_name(store, bad):
    with pytest.raises(ValueError):
        store.create_task_list(name=bad)


@pytest.mark.parametrize("bad", NON_STRINGS)
def test_update_task_list_refuses_a_non_string_name(store, bad):
    tl = store.create_task_list(name="Real")
    with pytest.raises(ValueError):
        store.update_task_list(tl.id, name=bad)
    assert store.get_task_list(tl.id).name == "Real"


def test_both_verbs_refuse_an_over_long_name(store):
    long = "K" * 3000
    with pytest.raises(ValueError, match="at most"):
        store.create_project(name=long)
    p = store.create_project(name="Real")
    with pytest.raises(ValueError, match="at most"):
        store.update_project(p.id, name=long)
    assert len(store.get_project(p.id).name) == 4


# ── the fields that were assigned with no check at all ─────────────────────────────────────


def test_agent_instructions_template_refuses_a_dict_on_a_project(store):
    p = store.create_project(name="Real")
    with pytest.raises(ValueError, match="agent_instructions_template"):
        store.update_project(p.id, agent_instructions_template={"a": 1})
    assert store.get_project(p.id).agent_instructions_template == ""


def test_agent_instructions_template_refuses_a_dict_on_a_task_list(store):
    tl = store.create_task_list(name="Real")
    with pytest.raises(ValueError, match="agent_instructions_template"):
        store.update_task_list(tl.id, agent_instructions_template={"a": 1})


def test_brief_refuses_a_dict_rather_than_storing_its_repr(store):
    p = store.create_project(name="Real")
    with pytest.raises(ValueError, match="brief"):
        store.update_project(p.id, brief={"a": 1})


def test_a_template_is_not_length_capped(store):
    # It is a template BODY, not a label — the name cap must not leak onto it.
    p = store.create_project(name="Real")
    big = "x" * (MAX_NAME_LEN * 10)
    assert len(
        store.update_project(p.id, agent_instructions_template=big).agent_instructions_template
    ) == len(big)


# ── what must keep working ─────────────────────────────────────────────────────────────────


def test_a_normal_name_round_trips_unchanged(store):
    # Vacuity guard for the whole file: ordinary use must be untouched.
    p = store.create_project(name="  PhD Applications  ")
    assert p.name == "PhD Applications"
    assert store.update_project(p.id, name="PhD Applications 2026").name == "PhD Applications 2026"


def test_unicode_and_punctuation_survive(store):
    fancy = "Ámbar — R&D $100 / 50%"
    assert store.create_project(name=fancy).name == fancy


def test_an_empty_name_still_falls_back_to_the_default_project(store):
    # A documented fallback, distinct from a wrong TYPE: empty means "the default project".
    assert store.find_or_create_project("   ").name == "Personal"


def test_status_is_still_fail_closed_for_a_wrong_type(store):
    # It coerces with str() but then checks a closed set, so it needs no type gate of its own.
    p = store.create_project(name="Real")
    with pytest.raises(ValueError, match="status"):
        store.update_project(p.id, status={"a": 1})


def test_a_non_string_project_id_is_refused_by_the_record_id_guard(store):
    # #455's fix, asserted here so this file records WHY no type gate was added for ids.
    from personalclaw.record_ids import UnsafeRecordId

    with pytest.raises(UnsafeRecordId):
        store.create_task_list(name="x", project_id=999)
