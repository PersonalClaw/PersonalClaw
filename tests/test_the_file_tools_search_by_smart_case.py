"""The file tools search by smart case: a pattern with no capital letter matches any case.

A model asked when the dentist appointment was and what time pickup was that day. ``grep`` matched
with ``query in line``, so "dentist" never reached the school calendar's "Dentist 16:00 ... Pick up
at 15:15", and the regular expression ``pickup|pick-up|pick up`` never reached "Pick up": the model
answered with a time from somewhere else and said there was no note for that day. ``glob`` had the
same blind spot for file names. Now a pattern with no capital letter ignores case, one with a
capital keeps it (the rule ripgrep and fd call smart case), and ``ignore_case`` overrides either.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

CALENDAR = "Home/school-fall-2026.md"
DAILY = "Daily/2026-09-21.md"


@pytest.fixture
def notes(tmp_path):
    (tmp_path / "Home").mkdir()
    (tmp_path / CALENDAR).write_text(
        "| Day | Plan |\n| Tue 13 Oct | Dentist 16:00. Pick up at 15:15 |\n", encoding="utf-8"
    )
    (tmp_path / "Daily").mkdir()
    (tmp_path / DAILY).write_text("ask the dentist when to pick up the forms\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def named(tmp_path):
    """Files whose NAMES carry capitals, for the patterns that match names."""
    home_dir, notes_dir = tmp_path / "Home", tmp_path / "notes"
    home_dir.mkdir()
    notes_dir.mkdir()
    (home_dir / "School-Fall-2026.md").write_text("dentist on the 13th\n", encoding="utf-8")
    (notes_dir / "README.MD").write_text("dentist forms\n", encoding="utf-8")
    (notes_dir / "plan.md").write_text("dentist reminder\n", encoding="utf-8")
    return tmp_path


async def _call(root, tool: str, **args) -> str:
    result = await NativeBuiltinToolProvider(root).invoke(tool, args)
    assert result.success, result.error
    return result.output


# ── grep: what a line holds ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lowercase_query_finds_the_word_capitalised(notes):
    out = await _call(notes, "grep", query="dentist")
    assert f"{CALENDAR}:2:" in out and "Dentist 16:00" in out
    assert f"{DAILY}:1:" in out


@pytest.mark.asyncio
async def test_a_lowercase_regex_finds_the_phrase_capitalised(notes):
    out = await _call(notes, "grep", query="pickup|pick-up|pick up", regex=True)
    assert f"{CALENDAR}:2:" in out and "Pick up at 15:15" in out
    assert f"{DAILY}:1:" in out


@pytest.mark.asyncio
async def test_a_query_with_a_capital_keeps_its_case(notes):
    lower = await _call(notes, "grep", query="pick up")
    assert f"{CALENDAR}:2:" in lower and f"{DAILY}:1:" in lower

    out = await _call(notes, "grep", query="Pick up")
    assert f"{CALENDAR}:2:" in out
    assert DAILY not in out, "the daily note says 'pick up' in lower case"

    regex = await _call(notes, "grep", query=r"Pick\s+up", regex=True)
    assert f"{CALENDAR}:2:" in regex and DAILY not in regex


@pytest.mark.asyncio
async def test_regex_syntax_is_not_a_capital_letter(notes):
    """Only the letters a regular expression matches count: ``\\W`` and a group's name do not."""
    out = await _call(notes, "grep", query=r"pick\Wup", regex=True)
    assert f"{CALENDAR}:2:" in out and f"{DAILY}:1:" in out

    group = await _call(notes, "grep", query=r"(?P<Verb>pick) up", regex=True)
    assert f"{CALENDAR}:2:" in group and f"{DAILY}:1:" in group

    klass = await _call(notes, "grep", query=r"[A-Z]entist", regex=True)
    assert f"{CALENDAR}:2:" in klass and DAILY not in klass, "a capital in a class is a capital"


@pytest.mark.asyncio
async def test_ignore_case_false_matches_case_exactly(notes):
    # Smart case alone ignores case for these queries ...
    assert f"{CALENDAR}:2:" in await _call(notes, "grep", query="dentist")
    assert f"{CALENDAR}:2:" in await _call(notes, "grep", query="pick up", regex=True)

    # ... and ignore_case=false turns that off.
    out = await _call(notes, "grep", query="dentist", ignore_case=False)
    assert f"{DAILY}:1:" in out and CALENDAR not in out

    regex = await _call(notes, "grep", query="pick up", regex=True, ignore_case=False)
    assert f"{DAILY}:1:" in regex and CALENDAR not in regex


@pytest.mark.asyncio
async def test_ignore_case_true_ignores_a_capital(notes):
    out = await _call(notes, "grep", query="DENTIST", ignore_case=True)
    assert f"{CALENDAR}:2:" in out and f"{DAILY}:1:" in out

    regex = await _call(notes, "grep", query="PICK UP", regex=True, ignore_case=True)
    assert f"{CALENDAR}:2:" in regex and f"{DAILY}:1:" in regex


@pytest.mark.asyncio
async def test_ignore_case_written_as_a_word_reads_as_that_word(notes):
    """A built-in tool reads its arguments leniently, and ``bool("false")`` is True."""
    out = await _call(notes, "grep", query="dentist", ignore_case="false")
    assert f"{DAILY}:1:" in out and CALENDAR not in out

    out = await _call(notes, "grep", query="DENTIST", ignore_case=" True ")
    assert f"{CALENDAR}:2:" in out and f"{DAILY}:1:" in out

    refused = await NativeBuiltinToolProvider(notes).invoke(
        "grep", {"query": "dentist", "ignore_case": "sometimes"}
    )
    assert not refused.success and "ignore_case" in (refused.error or "")


@pytest.mark.asyncio
async def test_a_case_blind_search_still_stops_at_max_results(tmp_path):
    (tmp_path / "many.txt").write_text("\n".join(["Needle here"] * 10) + "\n", encoding="utf-8")
    out = await _call(tmp_path, "grep", query="needle", max_results=3)
    assert out.count("many.txt:") == 3
    assert "max_results=3" in out and "more matches may exist" in out


@pytest.mark.asyncio
async def test_grep_reads_its_glob_by_the_same_rule(named):
    lower = await _call(named, "grep", query="dentist", glob="**/*.md")
    assert "notes/README.MD:1:" in lower and "notes/plan.md:1:" in lower

    upper = await _call(named, "grep", query="dentist", glob="**/*.MD")
    assert "notes/README.MD:1:" in upper and "plan.md" not in upper

    exact = await _call(named, "grep", query="dentist", glob="**/*.md", ignore_case=False)
    assert "notes/plan.md:1:" in exact and "README.MD" not in exact


# ── glob: what a file is called ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_glob_a_lowercase_pattern_finds_a_capitalised_name(named):
    out = await _call(named, "glob", pattern="**/*school*")
    assert "Home/School-Fall-2026.md" in out

    md = await _call(named, "glob", pattern="**/*.md")
    assert {"Home/School-Fall-2026.md", "notes/README.MD", "notes/plan.md"} <= set(md.split("\n"))


@pytest.mark.asyncio
async def test_glob_a_pattern_with_a_capital_keeps_its_case(named):
    assert "Home/School-Fall-2026.md" in await _call(named, "glob", pattern="**/*school*")
    assert "Home/School-Fall-2026.md" in await _call(named, "glob", pattern="**/*School*")
    assert await _call(named, "glob", pattern="**/*SCHOOL*") == "(no matches)"
    # Exact on every platform: a file system that ignores case itself still does not match.
    assert await _call(named, "glob", pattern="HOME/*") == "(no matches)"


@pytest.mark.asyncio
async def test_glob_ignore_case_overrides_either_way(named):
    out = await _call(named, "glob", pattern="**/*SCHOOL*", ignore_case=True)
    assert "Home/School-Fall-2026.md" in out
    assert await _call(named, "glob", pattern="**/*school*", ignore_case=False) == "(no matches)"

    refused = await NativeBuiltinToolProvider(named).invoke(
        "glob", {"pattern": "**/*", "ignore_case": "sometimes"}
    )
    assert not refused.success and "ignore_case" in (refused.error or "")


# ── what the model is told ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grep_and_glob_offer_ignore_case_and_say_the_rule(tmp_path):
    tools = {t.name: t for t in await NativeBuiltinToolProvider(tmp_path).list_tools()}
    for name in ("grep", "glob"):
        tool = tools[name]
        assert tool.parameters["properties"]["ignore_case"] == {"type": "boolean"}, name
        assert "ignore_case" not in tool.parameters.get("required", []), name
        assert "capital letter" in tool.description, name
        assert "optional ignore_case (bool)" in tool.description, name
    assert "a regex escape such as" in tools["grep"].description
