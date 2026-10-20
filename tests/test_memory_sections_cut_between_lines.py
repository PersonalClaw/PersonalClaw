"""A memory section that is over its room is cut between lines, never inside one.

The preferences section of a new chat's memory block shrinks with the model's window: at a local
model's 32,768 tokens it is about 1,100 characters. It used to be cut at that character, wherever it
fell, and end in "…[truncated]" — so the last preference the model saw was half of one ("In the
evenings I maintain feedsmi"), which is a different preference, and nothing said how much was
missing. Now whole lines go in, and the block says how many were left out; the source line above
it names the file that holds them.
"""

from __future__ import annotations

from personalclaw.memory import MemoryStore


def test_preferences_over_their_room_are_cut_between_lines_and_the_rest_is_counted(tmp_path):
    store = MemoryStore(workspace=tmp_path / "ws")
    store.init()
    lines = [f"- Preference {n}: answer in the house style number {n}, always." for n in range(40)]
    store.write_preferences("# User Preferences\n\n" + "\n".join(lines) + "\n")

    (block,) = store.render_markdown_context(prefs_cap=1_110, projects_cap=10, history_cap=10)

    shown = block.split("\n", 2)[2]  # below the heading and the source line
    kept, marker = shown.rsplit("\n", 1)
    assert "[truncated]" not in block
    # Every line that is shown is a whole preference…
    for line in kept.splitlines()[2:]:
        assert line in lines
    # …and the rest are counted, not dropped in silence.
    carried = sum(1 for line in kept.splitlines() if line in lines)
    assert 0 < carried < len(lines)
    assert marker == (
        f"…[{len(lines) - carried} more lines not shown: they are in the source named above]"
    )
    assert f"_[source: {store._preferences_file}]_" in block


def test_a_section_that_fits_is_given_whole(tmp_path):
    store = MemoryStore(workspace=tmp_path / "ws")
    store.init()
    text = "# User Preferences\n\n- Short and plain.\n"
    store.write_preferences(text)
    (block,) = store.render_markdown_context(prefs_cap=4_000, projects_cap=10, history_cap=10)
    assert block.endswith(text)
