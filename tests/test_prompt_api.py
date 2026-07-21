"""API-handler tests for the prompt snippet routes, compose-aware render, and the
merged-variable + kind fields on prompt list/detail."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import (
    api_campaign_template_launch,
    api_prompt_detail,
    api_prompt_preview,
    api_prompt_render,
    api_prompt_syntax,
    api_prompts,
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
