"""Every boolean a request body carries is the JSON true or false, and a stored switch reads as the
word it spells.

``bool("false")`` is True. A door that read its switch or its consent by truthiness turned on what
its caller turned off: the one-link pack import read ``"consent": "false"`` as consent, a trigger
toggled with ``"enabled": "false"`` was switched on, and an inbox update sent
``"mute_thread": "false"`` muted the thread. Its stored half kept the defect after the request was
gone: a provider instance saved with ``"enabled": "false"`` was read back on, and
``security.egress.allow_private: "false"`` in ``config.json`` let the egress guard reach private
addresses.

The two sources take two rules, for one reason. A request body can always carry a real boolean, so
a door reads its booleans through ``request_validation`` and refuses anything else with a 400 that
names the field (``bool_field`` / ``require_bool`` / ``optional_bool``, or a hand-written
``not isinstance(value, bool)`` refusal of the same kind). A stored setting cannot be refused once
it is written, so it reads as the word it spells, with the switch's safe value for a value that
spells neither (``safety_flags.strict_bool``); ``config.json`` does it once, for every boolean the
schema declares, in its validation pass.

This census keeps both so:

* :data:`BODY_BOOLEANS` pins every boolean a door reads, with what an omitted field means there. A
  door that starts reading one fails :func:`test_every_body_boolean_is_pinned` until it is pinned,
  and a read of one by truthiness fails the ban below it.
* :data:`LOADER_BUDGET` is the exact set of record loaders that still read a boolean with
  ``bool()``: records the product writes itself. A new one fails, and so does an app manifest's.
* Every boolean in the ``config.json`` schema is driven through the loader with text in it.
* An MCP server's ``disabled`` switch has one reader, ``mcp_status.switched_off``
  (``tests/rails/test_request_boolean_census_tree.py``).

``tests/test_tool_boolean_census.py`` is the sibling for a model's tool arguments, which arrive as
text as often as not and are read as the word they spell (``safety_flags.yes_or_no``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from request_boolean_census_shared import BODY_BOOLEANS, _loader_misreads, _scan
from table_rows import row

pytestmark = pytest.mark.security

#: Fields that grant a consent, publish, or widen what runs. An omitted one is never a yes.
_WIDENING = {
    "consent",
    "always_allow",
    "skip_preflight",
    "force",
    "redo_effects",
    "send_capable",
    "published",
    "trusted",
    "auto_apply",
    # Keeping a conversation is a standing grant to an outside client.
    "persistent_sessions",
}


def test_an_omitted_consent_or_widening_field_is_never_a_yes():
    assert not {
        k: v for k, v in BODY_BOOLEANS.items() if k[2] in _WIDENING and v is True
    }, "a field that grants consent or widens what runs must default to no, or be required"


#: Each shape in which ``"false"`` reads as a yes.
_MISREAD_SHAPES = [
    "async def f(request):\n    body = await request.json()\n    return bool(body.get('flag'))",
    "async def f(request):\n    body = await json_object_body(request)\n"
    "    if body.get('flag'):\n        return 1",
    "async def f(request):\n    body = await json_object_body(request)\n"
    "    return body.get('flag') is not False",
    "async def f(request):\n    body = await json_object_body(request)\n"
    "    return body.get('flag', True)",
    "async def f(request):\n    body = await json_object_body(request)\n"
    "    x = body.get('flag')\n    return 1 if x else 0",
    "def helper(body):\n    return not body['flag']",
    "async def f(request):\n    body = await json_object_body(request)\n"
    "    for k in ('flag', 'other'):\n        if body[k]:\n            return 1",
]


def test_the_ban_sees_every_shape_of_a_misread(subtests):
    """The ban's own control: each shape in which ``"false"`` reads as a yes is caught."""
    assert len(_MISREAD_SHAPES) == 7, "a shape was dropped from the table"
    assert len(_MISREAD_SHAPES) == len(set(_MISREAD_SHAPES)), "a shape is listed twice"
    for shape in _MISREAD_SHAPES:
        with row(subtests, shape=shape):
            _, misreads = _scan(shape)
            assert any(field == "flag" for _, field, _, _ in misreads), shape


#: A read through each reader, and one behind a hand-written refusal, with what the scan declares.
_DECLARED_SHAPES = [
    (
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    return bool_field(body, 'flag', default=False)",
        False,
    ),
    (
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    return require_bool(body, 'flag')",
        "required",
    ),
    (
        "async def f(request):\n    body = await request.json()\n    v = body.get('flag')\n"
        "    if not isinstance(v, bool):\n        return refuse()\n    return 1 if v else 0",
        "checked",
    ),
    (
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    for k in ('flag',):\n        optional_bool(body, k)",
        "optional",
    ),
]


def test_the_scan_declares_a_reader_and_a_checked_read(subtests):
    """A read through a reader, or behind a hand-written refusal, is declared and is no misread."""
    assert len(_DECLARED_SHAPES) == 4, "a shape was dropped from the table"
    assert {how for _, how in _DECLARED_SHAPES} == set(BODY_BOOLEANS.values()) - {True, None}
    for shape, how in _DECLARED_SHAPES:
        with row(subtests, declares=how):
            declared, misreads = _scan(shape)
            assert declared.get(("f", "flag")) == how
            assert not misreads, misreads


# ── Stored settings ──────────────────────────────────────────────────────────────────────────


#: Each shape of a record loader reading a switch by truthiness.
_LOADER_MISREAD_SHAPES = [
    "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
    "        return cls(on=bool(d.get('on', True)))",
    "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
    "        return cls(on=d.get('on', True))",
    "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
    "        return cls(on=d.get('on') is not False)",
]


def test_the_loader_scan_sees_a_truthiness_read(subtests):
    assert len(_LOADER_MISREAD_SHAPES) == 3, "a shape was dropped from the table"
    assert len(_LOADER_MISREAD_SHAPES) == len(set(_LOADER_MISREAD_SHAPES))
    for shape in _LOADER_MISREAD_SHAPES:
        with row(subtests, shape=shape):
            assert _loader_misreads(shape) == {("R", "on")}


def test_the_loader_scan_passes_a_strict_read():
    shape = (
        "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
        "        return cls(on=strict_bool(d.get('on'), field='on', default=False))"
    )
    assert _loader_misreads(shape) == set()


def _schema_booleans() -> list[tuple[str, ...]]:
    from personalclaw.config.schema import JSON_SCHEMA

    leaves: list[tuple[str, ...]] = []

    def walk(node: dict[str, Any], path: tuple[str, ...]) -> None:
        kind = node.get("type")
        if "boolean" in (kind if isinstance(kind, list) else [kind]):
            leaves.append(path)
        for key, child in (node.get("properties") or {}).items():
            walk(child, (*path, key))
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            walk(extra, (*path, "*"))

    walk(JSON_SCHEMA, ())
    return leaves


_SCHEMA_BOOLEANS = _schema_booleans()


#: A boolean that is on only with another on (`SkillsConfig` switches refinement off without it).
_PREREQUISITES: dict[tuple[str, ...], dict[tuple[str, ...], object]] = {
    ("skills", "auto_refine_on_deviation"): {("skills", "auto_create_from_sessions"): True},
}


def _config_with(paths: list[tuple[str, ...]], value: object) -> dict[str, Any]:
    """A ``config.json`` holding *value* at every one of *paths*, and each one's prerequisites."""
    doc: dict[str, Any] = {}
    for path in paths:
        for at, setting in {**_PREREQUISITES.get(path, {}), path: value}.items():
            node = doc
            for key in at[:-1]:
                node = node.setdefault("an-agent" if key == "*" else key, {})
            node[at[-1]] = setting
    return doc


def _loaded(path: tuple[str, ...], cfg: Any) -> object:
    node: object = cfg.to_dict()
    for key in path:
        assert isinstance(node, dict), path
        node = node.get("an-agent" if key == "*" else key)
    return node


def _readings(paths: list[tuple[str, ...]], value: object) -> dict[tuple[str, ...], object]:
    """What each of *paths* reads after one load of a ``config.json`` holding *value* at all."""
    from personalclaw.config import validation
    from personalclaw.config.loader import AppConfig, config_path

    validation._STRIP_MEMO.clear()
    config_path().write_text(json.dumps(_config_with(paths, value)), encoding="utf-8")
    cfg = AppConfig.load()
    return {path: _loaded(path, cfg) for path in paths}


def test_the_config_schema_declares_booleans_at_every_depth():
    """Non-vacuity: the drive below covers the two-level fields and the deeper ones."""
    assert len(_SCHEMA_BOOLEANS) >= 100
    assert ("security", "egress", "allow_private") in _SCHEMA_BOOLEANS


def test_a_config_boolean_written_as_false_text_reads_false(subtests):
    """Every boolean ``config.json`` holds: a text spelling of false reads as false at any depth
    (a switch the owner turned off stays off), a text spelling of true reads as the field's
    default (text never turns a surface on), and a real boolean is itself.

    One load writes a spelling into every boolean at once, so each row is read from it. A boolean
    that is on only with another on is loaded alone, with that other one switched on.
    """
    from personalclaw.config.loader import config_dir

    config_dir()  # the test's own home, made on first ask
    together = [p for p in _SCHEMA_BOOLEANS if p not in _PREREQUISITES]
    groups = [together, *([p] for p in _SCHEMA_BOOLEANS if p in _PREREQUISITES)]
    sent = (None, True, "false", " Off ", False, "true")
    read: dict[tuple[str, ...], dict[object, object]] = {path: {} for path in _SCHEMA_BOOLEANS}
    for group in groups:
        for value in sent:
            for path, reading in _readings(group, value).items():
                read[path][value] = reading
    assert len(read) == len(_SCHEMA_BOOLEANS), "a schema boolean was not driven"
    for path in _SCHEMA_BOOLEANS:
        with row(subtests, path=".".join(path)):
            reading = read[path]
            assert reading[True] is True
            assert reading["false"] is False
            assert reading[" Off "] is False
            assert reading[False] is False
            assert reading["true"] == reading[None], "text turned the field on"
