"""API-handler tests for the prompt snippet routes, compose-aware render, and the
merged-variable + kind fields on prompt list/detail."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import (
    api_campaign_template_launch,
    api_prompt_detail,
    api_prompt_preview,
    api_prompt_render,
    api_prompt_syntax,
    api_prompts,
    api_skill_detail,
    api_snippet_create,
    api_snippet_delete,
    api_snippet_detail,
    api_snippet_render,
    api_snippets,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


@pytest.fixture(autouse=True)
def _mock_sel(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())


def _req(name=None, body=None, query=None):
    r = MagicMock()
    if name is not None:
        r.match_info = {"name": name}
    r.query = query or {}
    if body is not None:

        async def _json():
            return body

        r.json = _json
    return r


def _body(resp):
    return json.loads(resp.body.decode())


def _provider():
    from personalclaw.prompt_providers import get_default_provider
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    return get_default_provider()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── snippet CRUD over the API ────────────────────────────────────────────────


def test_snippet_create_list_get():
    resp = _run(
        api_snippet_create(
            _req(
                body={
                    "name": "sig",
                    "title": "Signature",
                    "content": "— {{author}}",
                    "variables": [{"name": "author", "type": "text"}],
                }
            )
        )
    )
    assert resp.status == 200
    assert _body(resp)["name"] == "sig"

    listing = _body(_run(api_snippets(_req())))
    assert [s["name"] for s in listing] == ["sig"]

    detail = _body(_run(api_snippet_detail(_req("sig"))))
    assert detail["content"] == "— {{author}}"
    assert detail["variables"][0]["name"] == "author"


def test_snippet_create_duplicate_409():
    _run(api_snippet_create(_req(body={"name": "x", "content": "a"})))
    resp = _run(api_snippet_create(_req(body={"name": "x", "content": "b"})))
    assert resp.status == 409


def test_snippet_delete_blocked_while_in_use():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate

    prov.create_snippet(PromptSnippet(name="sig", content="— sig"))
    prov.create_prompt(PromptTemplate(name="letter", kind="user", content="Hi.\n{{> sig}}"))
    # In-use → 409, snippet NOT deleted, used_by names the includer.
    resp = _run(api_snippet_delete(_req("sig")))
    assert resp.status == 409
    body = _body(resp)
    assert "letter" in body["used_by"]["prompts"]
    assert prov.get_snippet("sig") is not None
    # detail surfaces used_by too
    detail = _body(_run(api_snippet_detail(_req("sig"))))
    assert detail["used_by"]["prompts"] == ["letter"]
    # force=1 overrides the guard
    forced = _run(api_snippet_delete(_req("sig", query={"force": "1"})))
    assert forced.status == 200
    assert prov.get_snippet("sig") is None


def test_snippet_delete_unused_ok():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptSnippet

    prov.create_snippet(PromptSnippet(name="orphan", content="nobody includes me"))
    resp = _run(api_snippet_delete(_req("orphan")))
    assert resp.status == 200
    assert prov.get_snippet("orphan") is None


def test_snippet_detail_missing_404():
    resp = _run(api_snippet_detail(_req("ghost")))
    assert resp.status == 404


def test_snippet_render_standalone():
    _run(
        api_snippet_create(
            _req(body={"name": "sig", "content": "— {{author}}", "variables": [{"name": "author"}]})
        )
    )
    resp = _run(api_snippet_render(_req("sig", body={"variables": {"author": "Ada"}})))
    assert _body(resp)["rendered"] == "— Ada"


# ── compose-aware prompt render (includes a snippet) ─────────────────────────


def test_prompt_render_resolves_snippet_include():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate, PromptVariable

    prov.create_snippet(
        PromptSnippet(name="sig", content="— {{author}}", variables=[PromptVariable(name="author")])
    )
    prov.create_prompt(
        PromptTemplate(
            name="letter",
            kind="user",
            content="Dear {{who}},\n{{> sig}}",
            variables=[PromptVariable(name="who")],
        )
    )
    resp = _run(
        api_prompt_render(_req("letter", body={"variables": {"who": "Sam", "author": "Ada"}}))
    )
    assert _body(resp)["rendered"] == "Dear Sam,\n— Ada"


# ── detail surfaces kind, merged variables + includes ────────────────────────


def test_prompt_detail_merged_variables_and_includes():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate, PromptVariable

    prov.create_snippet(
        PromptSnippet(name="sig", content="— {{author}}", variables=[PromptVariable(name="author")])
    )
    prov.create_prompt(
        PromptTemplate(
            name="letter",
            kind="user",
            content="{{who}} {{> sig}}",
            variables=[PromptVariable(name="who")],
        )
    )
    detail = _body(_run(api_prompt_detail(_req("letter"))))
    assert detail["kind"] == "user"
    names = [v["name"] for v in detail["merged_variables"]]
    assert names == ["who", "author"]  # host var first, snippet var merged in
    assert detail["includes"] == ["sig"]


def test_prompt_list_kind_filter():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptTemplate

    prov.create_prompt(PromptTemplate(name="sysp", kind="system", content="x"))
    prov.create_prompt(PromptTemplate(name="usrp", kind="user", content="y"))
    sys_only = _body(_run(api_prompts(_req(query={"kind": "system"}))))
    assert [p["name"] for p in sys_only] == ["sysp"]
    user_only = _body(_run(api_prompts(_req(query={"kind": "user"}))))
    assert [p["name"] for p in user_only] == ["usrp"]


# ── context integration: a system prompt composes a snippet include ──────────


def test_apply_runtime_vars_resolves_snippet_include():
    """ContextBuilder._apply_runtime_vars renders {{bot_name}} AND {{> snippet}}
    through the one engine path, so a system prompt can compose shared fragments."""
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptSnippet

    prov.create_snippet(PromptSnippet(name="safety", content="Be careful, {{bot_name}}."))

    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    builder = ContextBuilder(
        memory=MemoryStore(workspace=Path.home() / "ws"),
        skills=SkillsLoader(skills_path=Path.home() / "skills", install_builtins=False),
    )
    builder._bot_name_override = "Claude"  # _bot_name is a live-config property (92a3d43)
    out = builder._apply_runtime_vars("Hi from {{bot_name}}.\n{{> safety}}", "dash:1")
    assert out == "Hi from Claude.\nBe careful, Claude."


def test_apply_runtime_vars_missing_snippet_marker():
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    _provider()  # ensure provider registered (no such snippet)
    builder = ContextBuilder(
        memory=MemoryStore(workspace=Path.home() / "ws"),
        skills=SkillsLoader(skills_path=Path.home() / "skills", install_builtins=False),
    )
    out = builder._apply_runtime_vars("X {{> nope}}", "dash:1")
    assert out == "X [missing snippet: nope]"


# ── live preview + syntax reference (authoring UI endpoints) ─────────────────


def test_prompt_preview_renders_unsaved_content():
    _provider()
    body = {
        "content": "Hi {{ name }}!\n{% if vip %}VIP{% elif n > 5 %}many{% else %}hi{% endif %} {{ upper(s) }}",  # noqa: E501
        "variables": [
            {"name": "vip", "type": "boolean"},
            {"name": "n", "type": "number"},
            {"name": "s", "type": "text"},
            {"name": "name", "type": "text"},
        ],
        "values": {"name": "Ada", "vip": False, "n": 9, "s": "go"},
    }
    resp = _run(api_prompt_preview(_req(body=body)))
    d = _body(resp)
    assert d["ok"] is True
    assert d["rendered"] == "Hi Ada!\nmany GO"


def test_prompt_preview_detects_inline_typed_variables():
    _provider()
    body = {"content": "{{ city::text }} {{ mood::select::[happy, sad] }}", "values": {}}
    d = _body(_run(api_prompt_preview(_req(body=body))))
    names = [(v["name"], v["type"]) for v in d["detected_variables"]]
    assert names == [("city", "text"), ("mood", "select")]


def test_prompt_preview_reports_render_error():
    _provider()
    body = {"content": "{% for x in xs %}{{ x }}", "variables": [], "values": {"xs": [1]}}
    d = _body(_run(api_prompt_preview(_req(body=body))))
    assert d["ok"] is False and d["error"]


def test_prompt_syntax_lists_functions_and_constructs():
    d = _body(_run(api_prompt_syntax(_req())))
    names = {f["name"] for f in d["functions"]}
    # Covers original + newly-adopted built-ins.
    assert {"upper", "join", "if", "contains", "get", "uuid"} <= names
    assert all({"name", "category", "signature", "insert"} <= set(f) for f in d["functions"])
    labels = {c["label"] for c in d["constructs"]}
    assert "If / elif / else" in labels and "Include snippet" in labels


# ── runnable "campaign template" launch (#17) ────────────────────────────────


class TestCampaignTemplateLaunch:
    """POST /api/prompts/{name}/launch — render a runnable template + create+start a
    loop from its launch_spec. Composes the render engine + the loop create/start seam;
    the seam is stubbed so this is a pure handler-contract check (no live worker)."""

    def _stub_loop_seam(self, monkeypatch, *, can_start=True, blocker=None):
        """Stub validation.validate + store.create + manager.start + autonudge so no
        real loop engine runs. Returns a list recording the created loop + start call."""
        created: list = []

        class _V:
            def __init__(self, ok):
                self.can_start = ok

            def to_dict(self):
                return {"errors": ["blocked"] if not self.can_start else []}

        monkeypatch.setattr(
            "personalclaw.loop.validation.validate", lambda body, **kw: _V(can_start)
        )

        def _create(loop):
            loop.id = "cafe1234"
            created.append(loop)
            return loop

        monkeypatch.setattr("personalclaw.loop.store.create", _create)

        async def _start(state, svc, lid):
            created.append(("started", lid))

        monkeypatch.setattr("personalclaw.loop.manager.start", _start)
        monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: object())
        # Only override the launch_blocker on the resolved strategy (leave the real
        # kind machinery — default_kind_config etc. — intact so _build_loop_from_body
        # works). Wrap the real get_or_none so the returned strategy reports `blocker`.
        import personalclaw.loop.kinds as K

        real_get = K.get_or_none

        def _wrapped(kind):
            strat = real_get(kind)
            if strat is not None:
                monkeypatch.setattr(
                    strat, "launch_blocker", staticmethod(lambda _lb: blocker), raising=False
                )
            return strat

        monkeypatch.setattr("personalclaw.loop.kinds.get_or_none", _wrapped)
        return created

    def test_launch_plain_prompt_rejected(self):
        prov = _provider()
        from personalclaw.prompt_providers.base import PromptTemplate

        prov.create_prompt(
            PromptTemplate(name="plain", kind="user", content="just text, not runnable")
        )
        r = _run(api_campaign_template_launch(_req("plain", body={"variables": {}})))
        assert r.status == 400 and "runnable" in _body(r)["error"].lower()

    def test_launch_missing_404(self):
        _provider()
        r = _run(api_campaign_template_launch(_req("ghost", body={"variables": {}})))
        assert r.status == 404

    def test_launch_renders_and_starts_loop(self, monkeypatch):
        prov = _provider()
        from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable

        prov.create_prompt(
            PromptTemplate(
                name="teardown",
                kind="user",
                content="Competitive teardown of {{company}} — focus on {{angle}}.",
                variables=[
                    PromptVariable(name="company", type="text", required=True),
                    PromptVariable(name="angle", type="text", default="pricing"),
                ],
                launch_spec={"kind": "goal", "intake_rigor": "minimal", "agent": ""},
            )
        )
        created = self._stub_loop_seam(monkeypatch)
        r = _run(
            api_campaign_template_launch(
                _req("teardown", body={"variables": {"company": "Acme", "angle": "positioning"}})
            )
        )
        assert r.status == 201
        d = _body(r)
        assert d["ok"] is True and d["started"] is True and d["loop_id"] == "cafe1234"
        # The rendered task carried BOTH filled variables into the created loop.
        loop = created[0]
        assert "Acme" in loop.task and "positioning" in loop.task
        assert loop.kind == "goal"
        # Origin-tagged as a template instance (identifiable in the loop list).
        assert loop.kind_config.get("origin") == "campaign_template"
        assert loop.kind_config.get("template_name") == "teardown"
        assert ("started", "cafe1234") in created

    def test_launch_blocked_kind_leaves_draft_unstarted(self, monkeypatch):
        prov = _provider()
        from personalclaw.prompt_providers.base import PromptTemplate

        prov.create_prompt(
            PromptTemplate(
                name="blocked",
                kind="user",
                content="Do the work on {{repo}} thoroughly please.",
                launch_spec={"kind": "code"},
            )
        )
        self._stub_loop_seam(monkeypatch, blocker="Pick a workspace first.")
        r = _run(api_campaign_template_launch(_req("blocked", body={"variables": {"repo": "x"}})))
        assert r.status == 422
        d = _body(r)
        assert (
            d["started"] is False
            and d["loop_id"] == "cafe1234"
            and "workspace" in d["error"].lower()
        )


# ── PUT /api/skills/{name} input validation (#787 C1) ────────────────────────


class TestSkillDetailPut:
    """PUT /api/skills/{name} must reject a non-string ``content`` with a clean
    400 rather than letting ``write_text`` raise deep in the loader → 500. The
    create path already guards this at prompts.py:444/:926; the PUT branch was
    the lone site missing the type check (#787 C1)."""

    def _put_req(self, tmp_path, name, body):
        from personalclaw.skills import SkillsLoader

        loader = SkillsLoader(skills_path=tmp_path, install_builtins=False)
        state = SimpleNamespace(context_builder=SimpleNamespace(skills=loader))
        r = _req(name=name, body=body)
        r.method = "PUT"
        r.app = {"state": state}
        return r, loader

    def test_put_non_string_content_returns_400(self, tmp_path):
        r, loader = self._put_req(tmp_path, "editable", {"content": 12345})
        loader.create_skill("editable", "# Original\nUntouched.")
        resp = _run(api_skill_detail(r))
        assert resp.status == 400
        assert "string" in _body(resp)["error"].lower()
        # The bogus PUT must not have overwritten the skill on disk.
        assert loader.load_skill("editable") == "# Original\nUntouched."

    def test_put_valid_string_content_succeeds(self, tmp_path):
        r, loader = self._put_req(tmp_path, "editable", {"content": "# Updated\nNew body."})
        loader.create_skill("editable", "# Original\nUntouched.")
        resp = _run(api_skill_detail(r))
        assert resp.status == 200
        assert _body(resp)["ok"] is True
        assert loader.load_skill("editable") == "# Updated\nNew body."


# ── #635: one value-map contract across render/preview (both wire keys accepted) ──
#
# Render/launch/snippet-render documented `variables`; preview documented `values`
# (its `variables` key is the DECLARATIONS list). Each read only its own key: the
# same well-formed map got a false "missing required variable" on render and a
# silent no-substitution preview. These rails pin the shared contract: documented
# key wins, the sibling dict is accepted as an alias, a declarations LIST is never
# swallowed as a value map, and a non-dict under the documented key still 400s.


def _seed_greet():
    prov = _provider()
    from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable

    prov.create_prompt(
        PromptTemplate(
            name="greet",
            kind="user",
            content="Hi {{who}}!",
            variables=[PromptVariable(name="who")],
        )
    )


def test_render_accepts_the_values_alias():
    _seed_greet()
    resp = _run(api_prompt_render(_req("greet", body={"values": {"who": "Sam"}})))
    assert resp.status == 200
    assert _body(resp)["rendered"] == "Hi Sam!"


def test_render_documented_key_wins_over_the_alias():
    _seed_greet()
    resp = _run(
        api_prompt_render(
            _req("greet", body={"variables": {"who": "Doc"}, "values": {"who": "Alias"}})
        )
    )
    assert resp.status == 200
    assert _body(resp)["rendered"] == "Hi Doc!"


def test_render_still_rejects_a_non_dict_under_the_documented_key():
    _seed_greet()
    resp = _run(api_prompt_render(_req("greet", body={"variables": "nope"})))
    assert resp.status == 400
    assert "variables must be an object" in _body(resp)["error"]


def test_preview_accepts_the_variables_alias_when_it_is_a_map():
    resp = _run(
        api_prompt_preview(_req(body={"content": "Hi {{who}}!", "variables": {"who": "Sam"}}))
    )
    assert resp.status == 200
    body = _body(resp)
    assert body["ok"] is True
    assert "Hi Sam!" in body["rendered"]


def test_preview_never_swallows_a_declarations_list_as_the_value_map():
    # `variables` as a LIST is the declarations payload preview already reads —
    # it must keep meaning that. A list misrouted into the value map would 400
    # ("values must be an object"); the 200/ok here plus the unsubstituted body
    # proves it stayed a declarations list and the render ran with no values.
    resp = _run(
        api_prompt_preview(_req(body={"content": "Hi {{who}}!", "variables": [{"name": "who"}]}))
    )
    assert resp.status == 200
    body = _body(resp)
    assert body["ok"] is True
    assert "Hi Sam!" not in (body.get("rendered") or "")


def test_snippet_render_accepts_the_values_alias():
    _run(
        api_snippet_create(
            _req(
                body={"name": "sig2", "content": "— {{author}}", "variables": [{"name": "author"}]}
            )
        )
    )
    resp = _run(api_snippet_render(_req("sig2", body={"values": {"author": "Ada"}})))
    assert resp.status == 200
    assert _body(resp)["rendered"] == "— Ada"
