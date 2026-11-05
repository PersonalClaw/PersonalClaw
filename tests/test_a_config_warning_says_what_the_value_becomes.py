"""A config warning says what becomes of the value it warns about, and that is what happens.

A value of the wrong type warns "…; using default". Two levels down or less it was: the value was
removed and the field's default read in its place. Deeper, it was left in place, so the warning
was untrue, and a section that read it with a bare `int()` raised out of `AppConfig.load()`:
`agent.self_qa.max_scenarios_per_fire: "three"` made every config read fail, after a warning
that the default was in use.

Every numeric field that deep outside `guardrails.budgets` is now read with `_safe_int` /
`_safe_float`, so text (and a boolean, or a value that is not finite) takes its shipped default and
a quoted number is read as the number, and the warning says which. A boolean is repaired at any
depth, so a deep one the repair removes says "using default" (text that spells false is read as
false, and says so). A deep value of any other kind is left for its section's reader, and the
warning says that: removing it would read an agent's unreadable tool list as no list, which allows
every tool. A spending ceiling is read as the number it spells, and one that names none (text that
is no number, a boolean, a negative number, "nan", infinity) stops the config loading, and written
as text its warning says so: an unknown ceiling is not "no limit".
"""

from __future__ import annotations

import json
import logging
import math

import pytest

from personalclaw.config import validation
from personalclaw.config.loader import AppConfig
from personalclaw.config.schema import JSON_SCHEMA


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    validation._STRIP_MEMO.clear()
    return tmp_path


def _load(home, payload: dict, caplog) -> tuple[AppConfig, list[str]]:
    (home / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=validation.logger.name):
        cfg = AppConfig.load()
    return cfg, [r.getMessage() for r in caplog.records if r.getMessage().startswith("Config:")]


def test_a_deep_value_of_the_wrong_type_is_read_as_its_default(home, caplog):
    cfg, warned = _load(home, {"agent": {"self_qa": {"max_scenarios_per_fire": "three"}}}, caplog)

    assert cfg.agent.self_qa.max_scenarios_per_fire == 3
    (line,) = [w for w in warned if "agent.self_qa.max_scenarios_per_fire" in w]
    assert line.endswith("; using default"), line


def test_so_is_one_in_another_section(home, caplog):
    cfg, warned = _load(home, {"guardrails": {"breaker": {"failure_threshold": "many"}}}, caplog)

    assert cfg.guardrails.breaker.failure_threshold == 5
    assert any(
        "guardrails.breaker.failure_threshold" in w and w.endswith("; using default")
        for w in warned
    )


def test_a_spend_ceiling_it_cannot_read_says_the_config_does_not_load(home, caplog):
    data = {"guardrails": {"budgets": {"max_tokens_per_run": "lots"}}}
    messages, _repairs = validation._diagnose_and_strip(data)

    (line,) = [m for m in messages if "guardrails.budgets.max_tokens_per_run" in m]
    assert "using default" not in line
    assert line.endswith(
        "a spending ceiling that cannot be read is not replaced by its default (no limit): "
        "the config does not load until it is fixed"
    ), line
    assert (
        data["guardrails"]["budgets"]["max_tokens_per_run"] == "lots"
    ), "left for its section to refuse"
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        AppConfig.load()


@pytest.mark.parametrize(
    ("key", "written", "read"),
    [("max_tokens_per_day", "200000", 200000), ("max_dollars_per_day", "5", 5.0)],
)
def test_a_spend_ceiling_written_as_a_quoted_number_is_read_as_it(home, caplog, key, written, read):
    """The budgets reader parses a quoted number, so the gateway starts with that ceiling: the
    warning says so, rather than that the config does not load."""
    cfg, warned = _load(home, {"guardrails": {"budgets": {key: written}}}, caplog)

    assert getattr(cfg.guardrails.budgets, key) == read
    (line,) = [w for w in warned if f"guardrails.budgets.{key}" in w]
    assert line.endswith(f"; read as {read}"), line


@pytest.mark.parametrize(
    ("key", "written"),
    [
        ("max_dollars_per_day", "nan"),
        ("max_dollars_per_day", "inf"),
        ("max_dollars_per_day", "-inf"),
        ("max_dollars_per_day", True),
        ("max_dollars_per_day", "-5"),
        ("max_tokens_per_day", "-200000"),
    ],
)
def test_a_spend_ceiling_that_names_no_ceiling_does_not_load(home, caplog, key, written):
    """`float("nan")` and a negative number, floored at 0, were 0, no limit, and infinity is no
    limit too: a ceiling that names no finite ceiling of 0 or more is refused like one that is no
    number at all, and its warning says so ("read as -5.0" was a ceiling that ran with no limit)."""
    data = {"guardrails": {"budgets": {key: written}}}
    messages, _repairs = validation._diagnose_and_strip(data)

    (line,) = [m for m in messages if f"guardrails.budgets.{key}" in m]
    assert line.endswith("the config does not load until it is fixed"), line
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        AppConfig.load()


@pytest.mark.parametrize(
    ("key", "written", "said"),
    [
        ("max_dollars_per_day", math.nan, "finite"),
        ("max_dollars_per_day", -5, "0 or more"),
        ("max_tokens_per_run", math.inf, "finite"),
        ("max_tokens_per_run", math.nan, "max_tokens_per_run is a finite number"),
        ("max_tokens_per_run", -0.5, "0 or more"),
    ],
)
def test_a_json_number_that_names_no_ceiling_does_not_load(home, key, written, said):
    """A negative number and, at a dollar ceiling, JSON's NaN and Infinity are numbers to the
    schema, so nothing warns: the reader refuses them, naming why. At a token ceiling NaN and
    Infinity fail the schema and warn, and the reader names the key (`int()` raised its own words),
    and -0.5 is refused as written, where `int()` truncated it to 0, which is no limit."""
    (home / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {key: written}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=said):
        AppConfig.load()


def test_a_negative_fraction_at_a_token_ceiling_warns_the_refusal(home, caplog):
    data = {"guardrails": {"budgets": {"max_tokens_per_run": -0.5}}}
    messages, _repairs = validation._diagnose_and_strip(data)

    (line,) = [m for m in messages if "guardrails.budgets.max_tokens_per_run" in m]
    assert line.endswith("the config does not load until it is fixed"), line


@pytest.mark.parametrize("written", [[5], None], ids=["a list", "null"])
def test_a_ceiling_of_another_type_does_not_load_naming_its_key(home, written):
    """A list or a null raised `float()`'s own TypeError out of `load()`."""
    (home / "config.json").write_text(
        json.dumps({"guardrails": {"budgets": {"max_dollars_per_day": written}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="guardrails.budgets.max_dollars_per_day"):
        AppConfig.load()


def test_a_budgets_section_that_is_not_an_object_takes_its_default(home, caplog):
    """Only a value INSIDE the refusing section is refused. The section itself, written as
    something other than an object, is a value two keys deep like any other."""
    cfg, warned = _load(home, {"guardrails": {"budgets": 5}}, caplog)

    assert cfg.guardrails.budgets.max_tokens_per_run == 0
    (line,) = [w for w in warned if "'guardrails.budgets'" in w]
    assert line.endswith("; using default"), line


def test_a_deep_quoted_number_is_read_as_the_number(home, caplog):
    """A quoted number three keys deep is parsed by its section's reader, as on main. Removing it
    loaded the default instead: 1.0 for "0.25", 10 for "20"."""
    cfg, warned = _load(
        home,
        {
            "resilience": {"remediation": {"max_cost_usd": "0.25"}},
            "guardrails": {"autonomy": {"clean_approvals": "20"}},
        },
        caplog,
    )

    assert cfg.resilience.remediation.max_cost_usd == 0.25
    assert cfg.guardrails.autonomy.clean_approvals == 20
    (cost,) = [w for w in warned if "resilience.remediation.max_cost_usd" in w]
    assert cost.endswith("; read as 0.25"), cost
    (approvals,) = [w for w in warned if "guardrails.autonomy.clean_approvals" in w]
    assert approvals.endswith("; read as 20"), approvals


def test_an_agents_tool_list_that_is_not_a_list_is_left_for_its_reader(home, caplog):
    """`agents.<name>.tools: "read_file"` (what `config set agents.researcher.tools read_file`
    writes) stays for the tool-list reader, which refuses it: the agent may use no tool. Removed,
    it read as no list, which allows every tool (`test_an_agent_is_held_to_its_tool_list`)."""
    data = {"agents": {"researcher": {"tools": "read_file"}}}
    messages, _repairs = validation._diagnose_and_strip(data)

    assert data["agents"]["researcher"]["tools"] == "read_file"
    (line,) = [m for m in messages if "agents.researcher.tools" in m]
    assert line.endswith("; left as written, for its section to read"), line


@pytest.mark.parametrize(
    ("path", "written", "loaded"),
    [
        (("security", "egress", "allow_private"), "yes", False),
        (("external_access", "mcp", "allow_remote"), "true", False),
        (("resilience", "remediation", "enabled"), 0, True),
    ],
    ids=lambda v: ".".join(v) if isinstance(v, tuple) else repr(v),
)
def test_a_deep_boolean_the_repair_removes_says_using_default(home, caplog, path, written, loaded):
    """`_repair_boolean` removes a deep boolean that does not spell false, and its section reads
    the default: "left as written" misstated a security switch that read closed."""
    payload: dict = {}
    node = payload
    for step in path[:-1]:
        node = node.setdefault(step, {})
    node[path[-1]] = written
    cfg, warned = _load(home, payload, caplog)

    value = cfg
    for step in path:
        value = getattr(value, step)
    assert value is loaded
    (line,) = [w for w in warned if ".".join(path) in w]
    assert line.endswith("; using default"), line


def test_a_deep_boolean_written_as_no_is_read_as_false(home, caplog):
    cfg, warned = _load(home, {"resilience": {"remediation": {"enabled": "no"}}}, caplog)

    assert cfg.resilience.remediation.enabled is False
    (line,) = [w for w in warned if "resilience.remediation.enabled" in w]
    assert "read as false" in line and "using default" not in line, line


def _deep_numeric_paths(schema: dict, path: tuple = ()) -> list[tuple]:
    found: list[tuple] = []
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if len(path) >= 3 and {"integer", "number"} & set(kinds):
        found.append(path)
    for key, sub in (schema.get("properties") or {}).items():
        found += _deep_numeric_paths(sub, (*path, key))
    if isinstance(schema.get("items"), dict):
        found += _deep_numeric_paths(schema["items"], (*path, 0))
    return found


def _payload(path: tuple, value: object) -> dict:
    """A config holding *value* at *path*, a list of one entry where a step is an index."""
    root: dict = {}
    node: dict = root
    for step, nxt in zip(path, (*path[1:], None)):
        if isinstance(step, int):
            continue  # the entry the step before made, and stepped into
        if nxt is None:
            node[step] = value
        elif isinstance(nxt, int):
            # A projection rule is read only with a pattern, so the entry carries one.
            node[step] = [{"match_regex": "probe"}]
            node = node[step][0]
        else:
            node[step] = {}
            node = node[step]
    return root


DEEP_NUMERIC = [p for p in _deep_numeric_paths(JSON_SCHEMA) if p[:2] != ("guardrails", "budgets")]


def test_the_schema_has_deep_numeric_fields_to_check():
    # The walk below is only a guard while it finds the fields: 18 at this writing.
    assert len(DEEP_NUMERIC) >= 18, DEEP_NUMERIC


def _schema_node(path: tuple) -> dict:
    node = JSON_SCHEMA
    for step in path:
        node = node["items"] if isinstance(step, int) else node["properties"][step]
    return node


def _loaded(cfg: object, path: tuple) -> object:
    value = cfg
    for step in path:
        if isinstance(step, int):
            value = value[step]  # type: ignore[index]
        else:
            value = getattr(value, step)
    return value


@pytest.mark.parametrize("written", ["not a number", "inf", "nan", True, math.inf])
@pytest.mark.parametrize("path", DEEP_NUMERIC, ids=lambda p: ".".join(map(str, p)))
def test_every_deep_numeric_field_takes_its_default_on_text(home, caplog, path, written):
    """What lets the warning say "using default" three keys deep: each such read falls back, to
    the default the schema states, for text, a value that is not finite, and a boolean. JSON's
    Infinity is a number to the schema at a float field, so nothing warns there, and it overflowed
    `int()` at an integer one."""
    cfg, warned = _load(home, _payload(path, written), caplog)

    dotted = ".".join(str(p) for p in path)
    if not (written == math.inf and _schema_node(path).get("type") == "number"):
        lines = [w for w in warned if f"'{dotted}'" in w]
        assert lines and all(w.endswith("; using default") for w in lines), (dotted, warned)
    assert _loaded(cfg, path) == _schema_node(path)["default"], dotted


def test_a_top_level_section_value_still_takes_its_default(home, caplog):
    cfg, warned = _load(home, {"session": {"pool_size": "two"}}, caplog)

    assert cfg.session.pool_size == 0
    assert any("session.pool_size" in w and w.endswith("; using default") for w in warned)


def test_the_cached_read_repairs_the_same_deep_value(home, caplog):
    payload = {"agent": {"self_qa": {"max_scenarios_per_fire": "three"}}}

    _load(home, payload, caplog)
    cfg, _ = _load(home, payload, caplog)

    assert cfg.agent.self_qa.max_scenarios_per_fire == 3


def test_the_gateways_boot_step_starts_with_a_deep_value_it_cannot_read(home):
    """The boot step (`cli_server._boot_config`) is what a gateway's start runs first. With a
    deep value its section cannot read, it raised, and the gateway never served."""
    from personalclaw.cli_server import _boot_config

    (home / "config.json").write_text(
        json.dumps({"guardrails": {"breaker": {"failure_threshold": "five"}}}), encoding="utf-8"
    )

    assert _boot_config().guardrails.breaker.failure_threshold == 5


FLOAT_DEEP = [p for p in DEEP_NUMERIC if _schema_node(p).get("type") == "number"]


@pytest.mark.parametrize("path", FLOAT_DEEP, ids=lambda p: ".".join(map(str, p)))
def test_a_number_no_float_holds_takes_its_default(home, caplog, path):
    """10**400 is an integer to JSON and a number to the schema, so nothing warns, and `float()`
    of it overflows: `_safe_float` takes the default."""
    cfg, _warned = _load(home, _payload(path, 10**400), caplog)

    assert _loaded(cfg, path) == _schema_node(path)["default"], path


def test_a_rate_no_float_holds_takes_its_default(home, caplog):
    cfg, _warned = _load(home, {"external_access": {"rate_rps": 10**400}}, caplog)

    assert cfg.external_access.rate_rps == 1.0


def test_a_json_nan_rate_takes_its_default(home, caplog):
    """`external_access.rate_rps: NaN` passes the schema as a number, and the clamp's
    `min(1000.0, nan)` read it as 1000 requests a second, the widest rate: `_num` takes the
    default for a number that is not finite."""
    cfg, _warned = _load(home, {"external_access": {"rate_rps": math.nan}}, caplog)

    assert cfg.external_access.rate_rps == 1.0
