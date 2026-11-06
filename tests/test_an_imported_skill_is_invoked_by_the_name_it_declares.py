"""An imported skill is invoked by the name its SKILL.md declares.

A skill imported from another agent tool during setup declares `name: trip-planner` and is stored
as `imported/claude_code/trip-planner`. Asked for by its name, or called by the name the skill
declares (`skill_invoke {"name": "trip-planner"}`), the tool answered "no skill named
'trip-planner'. Check the skill index for exact names." The loader looked a skill up by its folder
path alone. `skill_invoke` and `skill_resource` now take the name a skill declares when exactly
one skill the agent may use declares it, and refuse a name more than one declares, naming their
stored keys.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw.mcp_core import _call_tool_inner


def _skill(
    library: Path,
    key: str,
    declared: str,
    body: str = "Pack the route first.",
    packs: str = "boots",
) -> Path:
    folder = library / key
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {declared}\ndescription: Plan a trip.\nresources:\n  - path: checklist.md\n"
        f"    description: what to pack\n---\n\n{body}\n",
        encoding="utf-8",
    )
    (folder / "checklist.md").write_text(f"- {packs}\n", encoding="utf-8")
    (folder / ".pclaw-lock.json").write_text("{}", encoding="utf-8")
    return folder


@pytest.fixture()
def library(tmp_path, monkeypatch) -> Path:
    root = Path(os.path.realpath(tmp_path))
    pc_home = root / "user" / ".personalclaw"
    (pc_home / "workspace").mkdir(parents=True)
    (pc_home / "config.json").write_text("{}", encoding="utf-8")
    skills = pc_home / "skills"
    skills.mkdir()
    (skills / ".usage.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root / "user"))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    _skill(skills, "imported/claude_code/trip-planner", "trip-planner")
    return skills


def test_an_imported_skill_loads_by_the_name_it_declares(library):
    out = _call_tool_inner("skill_invoke", {"name": "trip-planner"})

    assert "no skill named" not in out, out
    assert "Pack the route first." in out, out
    assert out.startswith(
        "[Skill: imported/claude_code/trip-planner]"
    ), "named by the key it is kept under"


def test_its_stored_key_still_loads_it(library):
    out = _call_tool_inner("skill_invoke", {"name": "imported/claude_code/trip-planner"})

    assert "Pack the route first." in out, out


def test_a_resource_of_it_is_read_by_the_name_it_declares(library):
    out = _call_tool_inner("skill_resource", {"skill": "trip-planner", "path": "checklist.md"})

    assert "boots" in out, out


def test_a_name_two_skills_declare_is_refused_naming_both(library):
    _skill(library, "imported/codex/trip-planner", "trip-planner", body="The other one.")

    out = _call_tool_inner("skill_invoke", {"name": "trip-planner"})

    assert "Pack the route first." not in out and "The other one." not in out, out
    assert "imported/claude_code/trip-planner" in out and "imported/codex/trip-planner" in out, out


def test_a_name_no_skill_declares_is_still_no_skill(library):
    out = _call_tool_inner("skill_invoke", {"name": "beach-planner"})

    assert "no skill named 'beach-planner'" in out, out


# ── Through both tools, under an agent's skill list ─────────────────────────────────────────
#
# The Done-when: each case passes through `skill_invoke` and `skill_resource` both, so a
# resolution added to one tool cannot pass, and the agent's skill list is checked on the key the
# name resolves to.

KEY = "imported/claude_code/trip-planner"


def _both(name: str) -> dict[str, str]:
    return {
        "skill_invoke": _call_tool_inner("skill_invoke", {"name": name}),
        "skill_resource": _call_tool_inner(
            "skill_resource", {"skill": name, "path": "checklist.md"}
        ),
    }


def _holding(keys: list[str]):
    from personalclaw.agents import skill_list

    return skill_list.hold(skill_list.AgentSkills.of("researcher", keys))


def test_an_agent_whose_list_holds_the_key_calls_it_by_its_declared_name(library):
    from personalclaw.agents import skill_list

    token = _holding([KEY])
    try:
        out = _both("trip-planner")
    finally:
        skill_list.let_go(token)

    assert "Pack the route first." in out["skill_invoke"], out
    assert "boots" in out["skill_resource"], out


def test_an_agent_whose_list_leaves_it_out_is_refused_through_both_tools(library):
    from personalclaw.agents import skill_list

    token = _holding(["something-else"])
    try:
        by_name, by_key = _both("trip-planner"), _both(KEY)
    finally:
        skill_list.let_go(token)

    for tool, out in {**by_name, **{f"{t} by key": o for t, o in by_key.items()}}.items():
        assert "Pack the route first." not in out and "boots" not in out, (tool, out)
        assert "may use only the skills on its skill list" in out, (tool, out)


def test_an_agents_list_that_holds_one_of_two_declaring_skills_loads_that_one(library):
    """Two skills declare the name, and the agent's list holds one of them by its key: both tools
    load that one, as the name resolves to the only skill the agent may use."""
    from personalclaw.agents import skill_list

    _skill(
        library,
        "imported/codex/trip-planner",
        "trip-planner",
        body="The other one.",
        packs="sandals",
    )
    token = _holding([KEY])
    try:
        out = _both("trip-planner")
    finally:
        skill_list.let_go(token)

    assert "Pack the route first." in out["skill_invoke"], out
    assert "The other one." not in out["skill_invoke"], out
    assert "boots" in out["skill_resource"] and "sandals" not in out["skill_resource"], out


def test_a_name_two_skills_declare_is_refused_through_both_tools(library):
    _skill(library, "imported/codex/trip-planner", "trip-planner", body="The other one.")

    for tool, out in _both("trip-planner").items():
        assert "Pack the route first." not in out and "The other one." not in out, (tool, out)
        assert "boots" not in out, (tool, out)
        assert KEY in out and "imported/codex/trip-planner" in out, (tool, out)


def test_a_use_by_the_declared_name_counts_for_the_stored_key(library):
    from personalclaw.skills.loader import SkillsLoader

    def uses() -> int:
        (row,) = [r for r in SkillsLoader().list_skills(with_usage=True) if r["key"] == KEY]
        return int(row.get("use_count") or 0)

    before = uses()
    _both("trip-planner")

    assert uses() == before + 2, "a use through each tool, by the declared name"
