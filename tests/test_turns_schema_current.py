"""The typed turn's JSON schema and web types: generated from ``personalclaw.turns``, and current.

``scripts/generate_turn_types.py`` (``make turns-schema``) renders ``schemas/turns.schema.json``
from the classes and ``web/src/lib/generated/turns.ts`` from the schema (shared contracts §3.11: the
frontend's types are the backend's). The first test fails until both files match the classes, so a
change to a turn's shape is a reviewed diff in all three; the rest show the renderings follow the
classes (a planted field shows up in both) and agree with ``to_dict``.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from personalclaw import turns
from scripts import generate_turn_types as gen


def test_the_committed_schema_and_web_types_are_current() -> None:
    assert (
        gen.SCHEMA.read_text(encoding="utf-8") == gen.render_schema()
    ), "turns.schema.json is out of date: run `make turns-schema`"
    assert (
        gen.WEB.read_text(encoding="utf-8") == gen.render_ts()
    ), "web/src/lib/generated/turns.ts is out of date: run `make turns-schema`"


def test_a_planted_field_shows_up_in_both(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclasses.dataclass(frozen=True)
    class PlantedPart(turns.Part):
        KIND = "planted"
        colour: str = ""

    monkeypatch.setitem(turns.PART_KINDS, "planted", PlantedPart)
    schema = json.loads(gen.render_schema())
    assert schema["$defs"]["PlantedPart"]["properties"]["colour"] == {"type": "string"}
    assert {"$ref": "#/$defs/PlantedPart"} in schema["$defs"]["Part"]["oneOf"]
    assert "  colour?: string" in gen.render_ts()
    assert "| PlantedPart" in gen.render_ts()


def test_check_says_which_file_drifted(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    schema, web = tmp_path / "turns.schema.json", tmp_path / "turns.ts"
    schema.write_text(gen.render_schema(), encoding="utf-8")
    web.write_text("// stale\n", encoding="utf-8")
    monkeypatch.setattr(gen, "SCHEMA", schema)
    monkeypatch.setattr(gen, "WEB", web)
    monkeypatch.setattr(gen, "REPO", tmp_path)
    assert gen.main(["--check"]) == 1
    assert "turns.ts" in capsys.readouterr().err and web.read_text() == "// stale\n"
    assert gen.main([]) == 0 and web.read_text(encoding="utf-8") == gen.render_ts()


def test_what_the_schema_requires_is_what_to_dict_always_writes() -> None:
    defs = json.loads(gen.render_schema())["$defs"]
    empty_tool = turns.ToolPart(id="p_1", at="t", updated_at="t")
    assert sorted(empty_tool.to_dict()) == sorted(defs["ToolPart"]["required"])
    note = turns.NoticePart(id="p_2", at="t", updated_at="t")
    assert sorted(note.to_dict()) == sorted(defs["NoticePart"]["required"])
