"""TR1/TR2 — per-turn tool retrieval (ToolRetriever).

Surfaces a per-turn projection of the catalog (core ∪ top-K ∪ structural ∪
sticky), fails OPEN (full catalog on any issue / when the catalog fits K), and is
a no-op until the catalog exceeds K. Mirrors skills surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass

from personalclaw.agents.native.tool_retrieval import ToolRetriever


@dataclass
class _Def:
    name: str
    description: str = ""
    core: bool = False


def _catalog(n: int, *, prefix: str = "tool") -> list[_Def]:
    return [_Def(name=f"{prefix}_{i}", description=f"does thing {i}") for i in range(n)]


def test_noop_when_catalog_fits_k():
    defs = _catalog(10)
    r = ToolRetriever(defs, k=48)
    sel = r.select("anything at all")
    assert len(sel) == len(defs)  # everything fits → full set


def test_reduces_when_catalog_exceeds_k_keyword_match():
    defs = _catalog(100)
    defs.append(_Def(name="weather_lookup", description="get the current weather forecast"))
    r = ToolRetriever(defs, k=20)
    sel = r.select("what is the weather forecast today")
    names = {d.name for d in sel}
    assert len(sel) <= 20
    assert "weather_lookup" in names  # keyword/semantic match surfaced


def test_core_always_included():
    defs = _catalog(100)
    defs.append(_Def(name="ask_user", description="ask the user a question"))  # core by name frag
    defs.append(_Def(name="tool_result_get", description="fetch full result"))  # core
    r = ToolRetriever(defs, k=20)
    names = {d.name for d in r.select("unrelated query about databases")}
    assert "ask_user" in names and "tool_result_get" in names


def test_explicit_core_flag_included():
    defs = _catalog(100)
    defs.append(_Def(name="zzz_special", description="x", core=True))
    r = ToolRetriever(defs, k=15)
    assert "zzz_special" in {d.name for d in r.select("nothing relevant")}


def test_structural_hint_url_surfaces_web_tools():
    defs = _catalog(100)
    defs.append(_Def(name="web_fetch", description="fetch a page"))
    r = ToolRetriever(defs, k=20)
    names = {d.name for d in r.select("please fetch https://example.com/page")}
    assert "web_fetch" in names  # URL → web hint


def test_sticky_set_keeps_used_tool():
    defs = _catalog(100)
    defs.append(_Def(name="obscure_db_tool", description="query the database"))
    r = ToolRetriever(defs, k=20)
    r.mark_used("obscure_db_tool")
    # a totally unrelated later turn must still surface the sticky tool
    names = {d.name for d in r.select("write a poem about the sea")}
    assert "obscure_db_tool" in names


def test_fail_open_on_selector_error(monkeypatch):
    defs = _catalog(100)
    r = ToolRetriever(defs, k=20)
    monkeypatch.setattr(r, "_select", lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    assert len(r.select("x")) == len(defs)  # error → full catalog


def test_mark_used_ignores_unknown():
    r = ToolRetriever(_catalog(5), k=48)
    r.mark_used("not_a_tool")  # no raise
    assert "not_a_tool" not in r._sticky


# ── search escape hatch (the agent can find a hidden tool) ──


def test_reduced_and_hidden_count():
    small = ToolRetriever(_catalog(10), k=48)
    assert small.reduced() is False
    big = ToolRetriever(_catalog(100), k=20)
    assert big.reduced() is True
    big.select("a query about thing 3")
    assert big.hidden_count() > 0  # some tools were not surfaced


def test_search_finds_any_tool_including_hidden():
    defs = _catalog(100)
    defs.append(_Def(name="obscure_db_migrate", description="run a database migration"))
    r = ToolRetriever(defs, k=20)
    # search the FULL catalog by capability, regardless of what a turn surfaced
    hits = r.search("database migration")
    names = [h["name"] for h in hits]
    assert "obscure_db_migrate" in names
    assert all(set(h.keys()) == {"name", "description"} for h in hits)


def test_search_empty_query_returns_catalog_sample():
    r = ToolRetriever(_catalog(50), k=20)
    hits = r.search("", limit=10)
    assert len(hits) == 10  # no query → a bounded sample, never empty


def test_tool_search_is_core():
    # a tool literally named tool_search would be core; the synthetic def added by
    # the runtime is named tool_search → _is_core matches it via the frag.
    from personalclaw.agents.native.tool_retrieval import _is_core

    assert _is_core("tool_search", _Def(name="tool_search")) is True


# ── universal coding primitives are core (bash/grep/read can't be hidden) ──

# git/tests/lint run via bash now (shell-first) — not their own tools. The core
# primitives are the file tools + bash + the discovery/orientation tools.
_PRIMITIVES = ("bash", "read_file", "write_file", "edit_file", "grep", "glob", "list_dir")


def test_primitives_are_core():
    from personalclaw.agents.native.tool_retrieval import _is_core

    for name in _PRIMITIVES:
        assert _is_core(name, _Def(name=name)) is True, f"{name} must be core"


def test_primitives_surface_even_on_unrelated_turn():
    # The bug the user hit: a large MCP catalog hid `bash`, so the model said
    # "no shell tool exists". Primitives must survive a totally unrelated query.
    defs = [_Def(name=n) for n in _PRIMITIVES]
    defs += [
        _Def(name=f"mcp__toolbox__Tool{i}", description=f"catalog tool {i}") for i in range(80)
    ]
    r = ToolRetriever(defs, k=20)
    names = {d.name for d in r.select("write a haiku about the ocean")}
    for p in _PRIMITIVES:
        assert p in names, f"{p} was hidden on an unrelated turn"


def test_core_exact_match_no_substring_false_positives():
    # Regression: the old frag list had "ask", which matched "task_*" (so every
    # task tool was silently core) — and a bare "read" would match MCP *Read* tools.
    from personalclaw.agents.native.tool_retrieval import _is_core

    assert _is_core("task_create", _Def(name="task_create")) is False
    assert _is_core("task_list", _Def(name="task_list")) is False
    assert (
        _is_core("mcp__toolbox__ReadDocuments", _Def(name="mcp__toolbox__ReadDocuments")) is False
    )


def test_shell_structural_hint_surfaces_bash():
    defs = [_Def(name=f"mcp__x__T{i}", description=f"thing {i}") for i in range(80)]
    defs.append(_Def(name="some_exec_tool", description="runs a shell command in a sandbox"))
    r = ToolRetriever(defs, k=20)
    # "run the command" should fire the shell hint → exec-ish tools surface
    names = {d.name for d in r.select("please run the command npm test in the terminal")}
    assert "some_exec_tool" in names


# ── progressive disclosure: the catalog of non-surfaced tools (PT1) ──


@dataclass
class _ProvDef:
    name: str
    description: str = ""
    provider: str = ""


def test_catalog_lists_excluded_tools_grouped_by_provider():
    defs = [
        _ProvDef(name=f"a_tool_{i}", description=f"does {i}", provider="alpha") for i in range(30)
    ]
    defs += [
        _ProvDef(name=f"b_tool_{i}", description=f"does {i}", provider="beta") for i in range(30)
    ]
    r = ToolRetriever(defs, k=20)
    cat = r.catalog(exclude={"a_tool_0", "b_tool_0"})
    assert "[alpha]" in cat and "[beta]" in cat  # grouped by provider
    assert "a_tool_5: does 5" in cat  # name + description line
    assert "a_tool_0" not in cat and "b_tool_0" not in cat  # excluded (surfaced) tools omitted


def test_catalog_bounded_and_points_at_search_when_huge():
    # a giant fleet → the catalog caps and summarizes the overflow.
    defs = [
        _ProvDef(name=f"p{p}_t{i}", description="x" * 80, provider=f"prov{p}")
        for p in range(20)
        for i in range(30)
    ]  # 600 tools
    r = ToolRetriever(defs, k=20)
    cat = r.catalog(exclude=set(), max_chars=1500)
    assert len(cat) <= 1500 + 200  # bounded (+ the overflow summary line)
    assert "tool_search" in cat  # overflow points the model at discovery


def test_catalog_empty_when_nothing_excluded_fits():
    r = ToolRetriever([_ProvDef(name="only", description="d", provider="p")], k=20)
    assert r.catalog(exclude={"only"}) == ""


# ── PT2: tool_search uses the semantic path (capability match w/o keywords) ──


def test_search_is_generous_no_gate():
    # search returns matches without the selection score-gate; a weak keyword hit
    # still appears (discovery is generous, selection is strict).
    defs = _catalog(50)
    defs.append(_Def(name="thumbnail_maker", description="generate a small preview"))
    r = ToolRetriever(defs, k=20)
    hits = r.search("preview")
    assert any(h["name"] == "thumbnail_maker" for h in hits)
