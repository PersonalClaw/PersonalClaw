"""Tests for the prompt mini-language render engine (engine.py) + evolved model.

Covers: variable substitution + coercion, dot-paths, built-in functions,
conditionals, loops, comments, snippet includes (recursive + depth + cycle),
merged-variable resolution, and the model's kind/title/type migration.
"""

import pytest

from personalclaw.prompt_providers.base import (
    PromptRenderError,
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
    normalize_variable_type,
)
from personalclaw.prompt_providers.engine import (
    extract_inline_variables,
    included_snippet_names,
    merged_variables,
    parse_type_decl,
    render,
)


def _var(name, **kw):
    return PromptVariable(name=name, **kw)


# ── variables + coercion ─────────────────────────────────────────────────────


def test_basic_variable_substitution():
    out = render("Hello {{who}}!", [_var("who")], {"who": "world"})
    assert out == "Hello world!"


def test_missing_required_raises():
    with pytest.raises(PromptRenderError):
        render("Hi {{who}}", [_var("who", required=True)], {})


def test_missing_optional_uses_default_then_empty():
    assert render("[{{x}}]", [_var("x", default="d")], {}) == "[d]"
    assert render("[{{x}}]", [_var("x")], {}) == "[]"


def test_undeclared_braces_pass_through():
    # No variable named `x` declared and not in values → literal braces survive.
    assert render("keep {{x}} literal", [], {}) == "keep {{x}} literal"


def test_number_and_boolean_coercion():
    assert render("{{n}}", [_var("n", type="number")], {"n": "42"}) == "42"
    assert render("{{b}}", [_var("b", type="boolean")], {"b": "yes"}) == "true"


def test_number_rejects_bool():
    with pytest.raises(PromptRenderError):
        render("{{n}}", [_var("n", type="number")], {"n": True})


def test_select_validates_options():
    assert render("{{c}}", [_var("c", type="select", options=["a", "b"])], {"c": "a"}) == "a"
    with pytest.raises(PromptRenderError):
        render("{{c}}", [_var("c", type="select", options=["a", "b"])], {"c": "z"})


def test_dot_path_lookup():
    out = render("{{user.name}}", [], {"user": {"name": "Ada"}})
    assert out == "Ada"


# ── built-in functions ───────────────────────────────────────────────────────


def test_function_upper_and_join():
    assert render("{{upper(x)}}", [_var("x")], {"x": "hi"}) == "HI"
    assert render("{{join(xs)}}", [], {"xs": ["a", "b", "c"]}) == "a, b, c"


def test_function_default_and_truncate():
    assert render("{{default(x, 'none')}}", [_var("x")], {"x": ""}) == "none"
    assert render("{{truncate(x, 3)}}", [], {"x": "abcdef"}) == "abc…"


def test_unknown_function_raises():
    with pytest.raises(PromptRenderError):
        render("{{bogus(x)}}", [], {"x": 1})


# ── conditionals ─────────────────────────────────────────────────────────────


def test_if_truthy():
    tpl = "{% if vip %}Welcome VIP{% else %}Hello{% endif %}"
    assert render(tpl, [_var("vip", type="boolean")], {"vip": True}) == "Welcome VIP"
    assert render(tpl, [_var("vip", type="boolean")], {"vip": False}) == "Hello"


def test_if_comparison():
    tpl = "{% if n >= 3 %}many{% else %}few{% endif %}"
    assert render(tpl, [_var("n", type="number")], {"n": 5}) == "many"
    assert render(tpl, [_var("n", type="number")], {"n": 1}) == "few"


def test_if_string_equality():
    tpl = "{% if role == 'admin' %}root{% endif %}"
    assert render(tpl, [_var("role")], {"role": "admin"}) == "root"
    assert render(tpl, [_var("role")], {"role": "user"}) == ""


def test_nested_if():
    tpl = "{% if a %}{% if b %}both{% else %}only-a{% endif %}{% endif %}"
    assert (
        render(tpl, [_var("a", type="boolean"), _var("b", type="boolean")], {"a": True, "b": False})
        == "only-a"
    )


# ── loops ────────────────────────────────────────────────────────────────────


def test_for_loop():
    tpl = "{% for item in items %}- {{item}}\n{% endfor %}"
    assert render(tpl, [], {"items": ["x", "y"]}) == "- x\n- y\n"


def test_for_loop_index_helpers():
    tpl = "{% for i in xs %}{{loop.index1}}:{{i}}{% if loop.last %}.{% else %}, {% endif %}{% endfor %}"  # noqa: E501
    assert render(tpl, [], {"xs": ["a", "b"]}) == "1:a, 2:b."


def test_for_over_missing_is_empty():
    assert render("{% for x in nope %}{{x}}{% endfor %}", [], {}) == ""


def test_loop_iteration_cap():
    big = list(range(1001))
    with pytest.raises(PromptRenderError):
        render("{% for x in xs %}.{% endfor %}", [], {"xs": big})


# ── comments ─────────────────────────────────────────────────────────────────


def test_comments_stripped():
    assert render("a{# hidden #}b", [], {}) == "ab"


# ── snippet includes ─────────────────────────────────────────────────────────


def _resolver(snippets):
    by_name = {s.name: s for s in snippets}
    return lambda n: by_name.get(n)


def test_include_inlines_snippet():
    snip = PromptSnippet(name="greet", content="Hello {{who}}")
    out = render("{{> greet}}!", [_var("who")], {"who": "Sam"}, resolver=_resolver([snip]))
    assert out == "Hello Sam!"


def test_include_missing_marker():
    out = render("{{> nope}}", [], {}, resolver=_resolver([]))
    assert out == "[missing snippet: nope]"


def test_include_recursive():
    a = PromptSnippet(name="a", content="A{{> b}}")
    b = PromptSnippet(name="b", content="B")
    out = render("{{> a}}", [], {}, resolver=_resolver([a, b]))
    assert out == "AB"


def test_include_cycle_raises():
    a = PromptSnippet(name="a", content="{{> b}}")
    b = PromptSnippet(name="b", content="{{> a}}")
    with pytest.raises(PromptRenderError):
        render("{{> a}}", [], {}, resolver=_resolver([a, b]))


def test_include_depth_cap():
    # a→a self reference is a cycle; build a deep non-cyclic chain instead.
    snips = [PromptSnippet(name=f"s{i}", content=f"{{{{> s{i+1}}}}}") for i in range(8)]
    snips.append(PromptSnippet(name="s8", content="deep"))
    with pytest.raises(PromptRenderError):
        render("{{> s0}}", [], {}, resolver=_resolver(snips))


def test_snippet_variable_renders_in_host():
    snip = PromptSnippet(name="sig", content="— {{author}}", variables=[_var("author")])
    out = render("Body.\n{{> sig}}", [], {"author": "Ada"}, resolver=_resolver([snip]))
    assert out == "Body.\n— Ada"


# ── merged variables ─────────────────────────────────────────────────────────


def test_merged_variables_union_host_wins():
    snip = PromptSnippet(
        name="sig",
        content="{{author}} {{shared}}",
        variables=[_var("author"), _var("shared", description="from snippet")],
    )
    tpl = PromptTemplate(
        name="p",
        content="{{shared}} {{> sig}}",
        variables=[_var("shared", description="from host")],
    )
    merged = merged_variables(tpl, _resolver([snip]))
    names = [v.name for v in merged]
    assert names == ["shared", "author"]  # host's shared first, snippet's author added
    assert merged[0].description == "from host"  # host wins on collision


def test_included_snippet_names_order_deduped():
    assert included_snippet_names("{{> a}} {{> b}} {{> a}}") == ["a", "b"]


# ── model: kind / title / type migration ─────────────────────────────────────


def test_template_kind_defaults_and_title_humanized():
    t = PromptTemplate.from_dict({"name": "system-chat", "content": "x"})
    assert t.kind == "system"  # name looks like a system prompt
    assert t.title == "System Chat"
    u = PromptTemplate.from_dict({"name": "my-thing", "content": "x"})
    assert u.kind == "user"


def test_template_explicit_kind_preserved():
    t = PromptTemplate.from_dict({"name": "x", "kind": "system", "content": "y"})
    assert t.kind == "system"


def test_legacy_variable_types_migrated():
    assert normalize_variable_type("string") == "text"
    assert normalize_variable_type("file_path") == "text"
    # "text" is valid in the new vocab (single-line) — NOT remapped to textarea,
    # else newly-written single-line vars would be corrupted on every load.
    assert normalize_variable_type("text") == "text"
    assert normalize_variable_type("textarea") == "textarea"
    assert normalize_variable_type("select") == "select"
    with pytest.raises(ValueError):
        normalize_variable_type("nonsense")


def test_variable_from_dict_migrates_type():
    v = PromptVariable.from_dict({"name": "p", "type": "string"})
    assert v.type == "text"


def test_roundtrip_to_dict_from_dict():
    t = PromptTemplate(
        name="x",
        kind="user",
        title="X",
        content="{{a}}",
        variables=[_var("a", type="number", required=True)],
        tags=["t"],
    )
    t2 = PromptTemplate.from_dict(t.to_dict())
    assert t2.kind == "user" and t2.title == "X" and t2.variables[0].type == "number"


def test_snippet_roundtrip():
    s = PromptSnippet(name="sig", content="{{author}}", variables=[_var("author")])
    s2 = PromptSnippet.from_dict(s.to_dict())
    assert s2.name == "sig" and s2.variables[0].name == "author"


# ── elif chains ──────────────────────────────────────────────────────────────


def test_elif_chain():
    tpl = "{% if n == 1 %}one{% elif n == 2 %}two{% elif n == 3 %}three{% else %}many{% endif %}"
    v = [_var("n", type="number")]
    assert render(tpl, v, {"n": 1}) == "one"
    assert render(tpl, v, {"n": 2}) == "two"
    assert render(tpl, v, {"n": 3}) == "three"
    assert render(tpl, v, {"n": 9}) == "many"


def test_elif_no_else_falls_to_empty():
    tpl = "{% if n == 1 %}one{% elif n == 2 %}two{% endif %}"
    assert render(tpl, [_var("n", type="number")], {"n": 5}) == ""


def test_elif_first_match_wins_and_nested_intact():
    tpl = "{% if a %}A{% elif b %}{% if c %}BC{% else %}B{% endif %}{% else %}E{% endif %}"
    v = [_var(x, type="boolean") for x in "abc"]
    assert render(tpl, v, {"a": False, "b": True, "c": True}) == "BC"
    assert render(tpl, v, {"a": False, "b": True, "c": False}) == "B"
    assert render(tpl, v, {"a": True, "b": True, "c": True}) == "A"
    assert render(tpl, v, {"a": False, "b": False, "c": False}) == "E"


# ── boolean operators + membership ───────────────────────────────────────────


def test_boolean_and_or_not():
    b = lambda *n: [_var(x, type="boolean") for x in n]  # noqa: E731
    assert (
        render("{% if a and b %}Y{% else %}N{% endif %}", b("a", "b"), {"a": True, "b": True})
        == "Y"
    )
    assert (
        render("{% if a and b %}Y{% else %}N{% endif %}", b("a", "b"), {"a": True, "b": False})
        == "N"
    )
    assert (
        render("{% if a or b %}Y{% else %}N{% endif %}", b("a", "b"), {"a": False, "b": True})
        == "Y"
    )
    assert render("{% if not a %}Y{% else %}N{% endif %}", b("a"), {"a": False}) == "Y"


def test_boolean_grouping_and_precedence():
    v = [_var(x, type="boolean") for x in "abc"]
    # or binds looser than and: a or (b and c)
    assert (
        render(
            "{% if a or b and c %}Y{% else %}N{% endif %}", v, {"a": True, "b": False, "c": False}
        )
        == "Y"
    )
    # parens override
    assert (
        render(
            "{% if (a or b) and c %}Y{% else %}N{% endif %}", v, {"a": True, "b": False, "c": False}
        )
        == "N"
    )
    assert (
        render(
            "{% if (a or b) and c %}Y{% else %}N{% endif %}", v, {"a": True, "b": False, "c": True}
        )
        == "Y"
    )


def test_boolean_mixes_with_comparisons():
    assert (
        render(
            "{% if n == 1 or n == 2 %}Y{% else %}N{% endif %}", [_var("n", type="number")], {"n": 2}
        )
        == "Y"
    )


def test_in_and_contains():
    assert render('{% if "x" in tags %}Y{% else %}N{% endif %}', [], {"tags": ["x", "y"]}) == "Y"
    assert render('{% if "z" in tags %}Y{% else %}N{% endif %}', [], {"tags": ["x", "y"]}) == "N"
    assert (
        render('{% if name contains "lib" %}Y{% else %}N{% endif %}', [], {"name": "claw-lib"})
        == "Y"
    )
    assert render('{% if "k" in obj %}Y{% else %}N{% endif %}', [], {"obj": {"k": 1}}) == "Y"


# ── whitespace control ───────────────────────────────────────────────────────


def test_whitespace_control_trims():
    assert render("a\n{%- if true %}b{% endif %}", []) == "ab"
    assert render("{% if true %}b{% endif -%}\n   c", []) == "bc"
    assert render("x   {{- y }}", [], {"y": "Z"}) == "xZ"
    assert render("{{ y -}}   z", [], {"y": "Z"}) == "Zz"


def test_whitespace_control_absent_is_noop():
    # A template with no '-' markers must be untouched by the WS pass.
    tpl = "line1\n{% if a %} kept {% endif %}\nline2"
    assert render(tpl, [_var("a", type="boolean")], {"a": True}) == "line1\n kept \nline2"


# ── new built-in functions ───────────────────────────────────────────────────


def test_new_string_array_functions():
    assert render('{{ split("a,b,c") }}', []) == '["a", "b", "c"]'
    assert render('{{ join(split("a,b,c"), "-") }}', []) == "a-b-c"
    assert render("{{ substring(s, 0, 3) }}", [], {"s": "abcdef"}) == "abc"
    assert render("{{ slice(xs, 1) }}", [], {"xs": [1, 2, 3]}) == "[2, 3]"
    assert render("{{ contains(tags, 'x') }}", [], {"tags": ["x"]}) == "true"
    assert render("{{ min(n) }}", [], {"n": [3, 1, 2]}) == "1"
    assert render("{{ max(n) }}", [], {"n": [3, 1, 2]}) == "3"


def test_new_object_functions():
    assert render("{{ keys(o) }}", [], {"o": {"a": 1, "b": 2}}) == '["a", "b"]'
    assert render("{{ get(o, 'a.b', 'fb') }}", [], {"o": {"a": {"b": "deep"}}}) == "deep"
    assert render("{{ get(o, 'a.x', 'fb') }}", [], {"o": {"a": {}}}) == "fb"


def test_math_and_type_functions():
    assert render("{{ divide(10, 4) }}", []) == "2.5"
    assert render("{{ divide(1, 0) }}", []) == "0"
    assert render("{{ abs(n) }}", [], {"n": -5}) == "5"
    assert render("{{ isNumber(n) }}", [], {"n": 5}) == "true"
    assert render("{{ isEmpty(s) }}", [_var("s")], {"s": ""}) == "true"


def test_ternary_functions():
    v = [_var("flag", type="boolean")]
    assert render('{{ if(flag, "Y", "N") }}', v, {"flag": True}) == "Y"
    assert render('{{ if(flag, "Y", "N") }}', v, {"flag": False}) == "N"
    assert render('{{ unless(flag, "Y", "N") }}', v, {"flag": False}) == "Y"


def test_nested_function_calls():
    assert render("{{ upper(trim(s)) }}", [], {"s": "  hi  "}) == "HI"


# ── inline ::type variable declarations ──────────────────────────────────────


def test_inline_type_suffix_stripped_at_render():
    assert render("Hi {{ name::text }}", [_var("name")], {"name": "Ada"}) == "Hi Ada"
    assert (
        render("Tone {{ tone::select::[formal, casual] }}", [_var("tone")], {"tone": "casual"})
        == "Tone casual"
    )
    assert render("X {{ x::[a, b] }}", [_var("x")], {"x": "a"}) == "X a"


def test_extract_inline_variables():
    content = "A {{ city::text }} B {{ mood::select::[happy, sad] }} C {{ city::text }} {{ plain }}"
    got = [(v.name, v.type, v.options) for v in extract_inline_variables(content)]
    assert got == [("city", "text", []), ("mood", "select", ["happy", "sad"])]


def test_parse_type_decl_forms():
    assert parse_type_decl("textarea") == ("textarea", [])
    assert parse_type_decl("string") == ("text", [])
    assert parse_type_decl("number") == ("number", [])
    assert parse_type_decl("select::[a, b, c]") == ("select", ["a", "b", "c"])
    assert parse_type_decl("[x, y]") == ("select", ["x", "y"])
    assert parse_type_decl("bogus") == ("text", [])
