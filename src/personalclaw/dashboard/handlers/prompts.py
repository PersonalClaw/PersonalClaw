"""Prompts (Agent SOPs) and Skills API handlers."""

import logging
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.security import redact_credentials, redact_exfiltration_urls

from ._shared import _get_skills, _list_marketplace_skills

logger = logging.getLogger(__name__)

MAX_PROMPT_BYTES = 100_000  # 100 KB — public constant, imported across dashboard + gateway + tests


def _sel():
    """Late-binding sel() — allows monkeypatching at parent package level."""
    import personalclaw.dashboard.handlers as _pkg
    return _pkg.sel()


# ── Prompts (Agent SOPs) ──


def _provider_prompt_to_listing(tpl: "Any") -> dict[str, Any]:
    """Adapter: format a PromptTemplate into the listing shape, including the
    structured fields (kind, title, description, variables, tags) so the UI can
    render them.
    """
    out = {
        "name": tpl.name,
        "fullName": tpl.name,
        "kind": tpl.kind,
        "title": tpl.title,
        "description": tpl.description,
        "path": "",  # provider-managed; opaque to clients
        "package": "",
        "source": tpl.source,
        "variables": [v.to_dict() for v in tpl.variables],
        "tags": list(tpl.tags),
        "updated_at": getattr(tpl, "updated_at", 0.0),
    }
    # Runnable "campaign template" (#17): surface launch_spec so the list/detail can
    # show the "runnable" badge + fill-and-launch. Omitted when empty (plain prompt).
    spec = getattr(tpl, "launch_spec", None)
    if spec:
        out["launch_spec"] = dict(spec)
    return out


def _snippet_to_listing(snip: "Any") -> dict[str, Any]:
    """Adapter: format a PromptSnippet into the listing shape."""
    return {
        "name": snip.name,
        "title": snip.title,
        "description": snip.description,
        "source": snip.source,
        "variables": [v.to_dict() for v in snip.variables],
        "tags": list(snip.tags),
        "updated_at": getattr(snip, "updated_at", 0.0),
    }


def _snippet_resolver(provider: "Any"):
    """A name → PromptSnippet resolver for the render engine's {{> name}} includes."""
    return lambda n: provider.get_snippet(n) if provider is not None else None


def _snippet_usages(provider: "Any", name: str) -> dict[str, list[str]]:
    """Which prompts + OTHER snippets include ``{{> name}}`` directly. Used to warn
    before deleting an in-use snippet (deleting it would make every includer render a
    ``[missing snippet: name]`` marker) and to surface the reverse dependency in the
    detail view. Best-effort: any provider error yields empty lists."""
    from personalclaw.prompt_providers.engine import included_snippet_names

    prompts: list[str] = []
    snippets: list[str] = []
    try:
        for p in provider.list_prompts():
            if name in included_snippet_names(p.content):
                prompts.append(p.name)
        for s in provider.list_snippets():
            if s.name != name and name in included_snippet_names(s.content):
                snippets.append(s.name)
    except Exception:
        logger.debug("snippet usage scan failed for %s", name, exc_info=True)
    return {"prompts": prompts, "snippets": snippets}


def _list_provider_prompts(kind: str = "") -> list[dict[str, Any]]:
    from personalclaw.prompt_providers import get_default_provider
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    provider = get_default_provider()
    if provider is None:
        return []
    prompts = provider.list_prompts()
    if kind:
        prompts = [p for p in prompts if p.kind == kind]
    return [_provider_prompt_to_listing(p) for p in prompts]


async def api_prompts(request: web.Request) -> web.Response:
    """GET /api/prompts[?kind=system|user] — list prompt templates via the provider."""
    kind_q = request.query.get("kind", "")
    kind = kind_q.strip() if isinstance(kind_q, str) else ""
    prompts = _list_provider_prompts(kind)
    _sel().log_tool_invocation(
        session_key='', agent='api', source='dashboard',
        tool_name='api_prompts_list', tool_kind='prompt', outcome='ok',
        metadata={'count': len(prompts), 'kind': kind or 'all'},
    )
    return web.json_response(prompts)


def _get_default_prompt_provider():
    """Return the registered native PromptProvider, ensuring the default
    bundled provider has been registered before first use.
    """
    from personalclaw.prompt_providers import get_default_provider
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    return get_default_provider()


async def api_prompt_detail(request: web.Request) -> web.Response:
    """GET /api/prompts/{name} — read a prompt template via the PromptProvider."""
    raw = request.match_info["name"]
    bare = raw.split("/", 1)[-1] if "/" in raw else raw

    provider = _get_default_prompt_provider()
    tpl = provider.get_prompt(bare) if provider is not None else None
    if tpl is None:
        _sel().log_tool_invocation(
            session_key='', agent='api', source='dashboard',
            tool_name='api_prompt_detail', tool_kind='prompt', outcome='not_found',
            metadata={'name': raw},
        )
        return web.json_response({"error": "not found"}, status=404)

    content, _ = redact_credentials(tpl.content)
    content, _ = redact_exfiltration_urls(content)
    # The full variable set the fill-in UI must render = the prompt's own vars ∪
    # the vars of every snippet it transitively includes (host wins on name clash).
    from personalclaw.prompt_providers.engine import included_snippet_names, merged_variables

    merged = merged_variables(tpl, _snippet_resolver(provider))
    _sel().log_tool_invocation(
        session_key='', agent='api', source='dashboard',
        tool_name='api_prompt_detail', tool_kind='prompt', outcome='ok',
        metadata={'name': bare, 'source': 'provider'},
    )
    return web.json_response({
        **_provider_prompt_to_listing(tpl),
        "content": content,
        "merged_variables": [v.to_dict() for v in merged],
        "includes": included_snippet_names(tpl.content),
    })


def _build_prompt_template(body: dict[str, Any], default_name: str = "") -> Any:
    """Construct a PromptTemplate from a request body, raising ValueError on
    bad shape so callers can return a 400.
    """
    from personalclaw.prompt_providers.base import PromptTemplate

    raw_name = body.get("name") or default_name or ""
    if not isinstance(raw_name, str):
        raise ValueError("'name' must be a string")
    name = raw_name.strip()
    if not name:
        raise ValueError("Missing 'name' field")
    return PromptTemplate.from_dict({
        "name": name,
        "kind": body.get("kind") or "",  # from_dict infers system/user when blank
        "title": body.get("title") or "",
        "description": body.get("description") or "",
        "content": body.get("content") or "",
        "variables": body.get("variables") or [],
        "tags": body.get("tags") or [],
        # Runnable template (#17): a launch_spec turns a prompt into a campaign
        # template. from_dict ignores a non-dict, so a plain prompt stays plain.
        "launch_spec": body.get("launch_spec") or {},
    })


async def api_prompt_create(request: web.Request) -> web.Response:
    """POST /api/prompts — create a new prompt template via the registered provider."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    try:
        tpl = _build_prompt_template(body)
        provider.create_prompt(tpl)
    except ValueError as exc:
        msg = str(exc)
        status = 409 if "already exists" in msg else 400
        return web.json_response({"error": msg}, status=status)
    return web.json_response({"ok": True, "name": tpl.name, "prompt": tpl.to_dict()})


async def api_prompt_save(request: web.Request) -> web.Response:
    """PUT /api/prompts/{name} — update an existing prompt template."""
    raw = request.match_info["name"]
    bare = raw.split("/", 1)[-1] if "/" in raw else raw
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    try:
        tpl = _build_prompt_template(body, default_name=bare)
        provider.update_prompt(bare, tpl)
    except FileNotFoundError:
        return web.json_response({"error": "not found"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "prompt": tpl.to_dict()})


async def api_prompt_delete(request: web.Request) -> web.Response:
    """DELETE /api/prompts/{name} — remove a user prompt via the provider."""
    raw = request.match_info["name"]
    bare = raw.split("/", 1)[-1] if "/" in raw else raw

    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    if not provider.delete_prompt(bare):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_prompt_render(request: web.Request) -> web.Response:
    """POST /api/prompts/{name}/render — render a prompt template with the
    supplied variable values. Returns the substituted content.
    """
    raw = request.match_info["name"]
    bare = raw.split("/", 1)[-1] if "/" in raw else raw
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    values = body.get("variables") or {}
    if not isinstance(values, dict):
        return web.json_response({"error": "variables must be an object"}, status=400)

    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    tpl = provider.get_prompt(bare)
    if tpl is None:
        return web.json_response({"error": "not found"}, status=404)
    from personalclaw.prompt_providers.engine import render_template
    from personalclaw.prompt_providers.base import PromptRenderError
    try:
        # Compose-aware: resolve {{> snippet}} includes through the provider.
        rendered = render_template(tpl, values, resolver=_snippet_resolver(provider))
    except PromptRenderError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    rendered, _ = redact_credentials(rendered)
    rendered, _ = redact_exfiltration_urls(rendered)
    return web.json_response({"name": bare, "rendered": rendered})


# Fields a template's launch_spec may carry through to the loop create body. The
# rendered content becomes `task`; everything else is a loop-launch knob. Kept to the
# spine + goal knobs a fill-and-launch needs — the same fields LoopComposer sends.
_LAUNCH_SPEC_FIELDS = (
    "kind", "agent", "model", "provider", "provider_agent", "reasoning_effort",
    "execution", "roster", "strategy_id", "intake_rigor", "attended", "autopilot",
    "max_cycles", "skill_ids", "workflow_ids", "project_id", "success_criteria",
    "kind_config",
)


async def api_campaign_template_launch(request: web.Request) -> web.Response:
    """POST /api/prompts/{name}/launch {variables} — instantiate a RUNNABLE template
    (#17): render the content with the supplied values into a task, then create + start
    a loop from the template's ``launch_spec``.

    Pure composition of existing seams — the Prompts render engine + the loop
    create/start path (``_build_loop_from_body`` → ``store.create`` → ``manager.start``),
    exactly what LoopComposer + the loop action handler do. No new engine, no dual path:
    a template without a ``launch_spec`` is a plain prompt and can't launch (400)."""
    raw = request.match_info["name"]
    bare = raw.split("/", 1)[-1] if "/" in raw else raw
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    values = body.get("variables") or {}
    if not isinstance(values, dict):
        return web.json_response({"error": "variables must be an object"}, status=400)

    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    tpl = provider.get_prompt(bare)
    if tpl is None:
        return web.json_response({"error": "not found"}, status=404)
    spec = tpl.launch_spec if isinstance(tpl.launch_spec, dict) else {}
    if not spec:
        return web.json_response(
            {"error": "This template isn't runnable — it has no launch spec."}, status=400)

    # 1) Render the task via the SAME engine the render endpoint uses (compose-aware).
    from personalclaw.prompt_providers.engine import render_template
    from personalclaw.prompt_providers.base import PromptRenderError
    try:
        task = render_template(tpl, values, resolver=_snippet_resolver(provider))
    except PromptRenderError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    task = task.strip()
    if len(task) < 12:
        return web.json_response(
            {"error": "The rendered task is too short — fill in more of the template."}, status=400)

    # 2) Build a loop create body from the launch_spec (over the rendered task). Allow
    # a per-launch project_id override (a template run scoped to the active project).
    create_body: dict[str, Any] = {k: spec[k] for k in _LAUNCH_SPEC_FIELDS if k in spec}
    create_body["task"] = task
    if body.get("project_id"):
        create_body["project_id"] = str(body["project_id"])
    if not str(create_body.get("name") or "").strip():
        create_body["name"] = tpl.title or bare
    # Tag the loop's origin so the launched run is identifiable as a template instance.
    kc = dict(create_body.get("kind_config") or {})
    kc.setdefault("origin", "campaign_template")
    kc.setdefault("template_name", bare)
    create_body["kind_config"] = kc

    # 3) Create + start via the loop seam — reused verbatim (validation included), never
    # re-implemented. Import here to avoid a module-load cycle (loop_routes imports late).
    from personalclaw.dashboard.handlers import loop_routes as LR
    from personalclaw.loop import kinds, manager, store, validation
    from personalclaw.loop.loop import KINDS
    kinds.ensure_loaded()
    kind = str(create_body.get("kind", "goal")).strip().lower() or "goal"
    if kind not in KINDS:
        return web.json_response({"error": f"Unknown loop kind: {kind!r}"}, status=400)
    create_body["kind"] = kind
    v = validation.validate(create_body, agent_exists=LR._agent_exists(create_body))
    if not v.can_start:
        return web.json_response({"error": "Validation failed", **v.to_dict()}, status=400)
    loop = store.create(LR._build_loop_from_body(create_body))

    # Launch-time re-validation (a kind may block start, e.g. brownfield-no-workspace).
    strat = kinds.get_or_none(loop.kind)
    blocker = getattr(strat, "launch_blocker", None)
    reason = blocker(loop) if blocker else None
    if reason:
        # Leave the created (unstarted) draft so the user can fix + launch from the UI.
        return web.json_response({"error": reason, "loop_id": loop.id, "started": False}, status=422)

    from personalclaw.autonudge import get_instance
    svc = get_instance()
    if svc is None:
        return web.json_response({"error": "autonudge unavailable", "loop_id": loop.id, "started": False}, status=503)
    await manager.start(request.app["state"], svc, loop.id)
    return web.json_response({"ok": True, "loop_id": loop.id, "kind": loop.kind, "started": True}, status=201)


async def api_prompt_preview(request: web.Request) -> web.Response:
    """POST /api/prompts/preview — render ARBITRARY (unsaved) template content
    through the real engine, for the live authoring preview.

    Body: ``{content, variables?: [PromptVariable], values?: {name: value},
    kind?}``. Returns ``{rendered, ok, error?, detected_variables, includes}`` —
    the same render path the runtime uses, so the preview can never drift from
    what the agent receives (the key lesson from peer prompt UIs)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    content = body.get("content")
    if not isinstance(content, str):
        return web.json_response({"error": "content must be a string"}, status=400)
    values = body.get("values") or {}
    if not isinstance(values, dict):
        return web.json_response({"error": "values must be an object"}, status=400)

    from personalclaw.prompt_providers.base import PromptRenderError, PromptVariable
    from personalclaw.prompt_providers.engine import (
        extract_inline_variables,
        included_snippet_names,
        render,
    )

    variables = [
        PromptVariable.from_dict(v) for v in (body.get("variables") or []) if isinstance(v, dict) and v.get("name")
    ]
    provider = _get_default_prompt_provider()
    resolver = _snippet_resolver(provider) if provider is not None else None

    detected = [v.to_dict() for v in extract_inline_variables(content)]
    includes = included_snippet_names(content)
    try:
        rendered = render(content, variables, values, resolver=resolver)
    except PromptRenderError as exc:
        return web.json_response({
            "ok": False, "error": str(exc),
            "detected_variables": detected, "includes": includes,
        })
    rendered, _ = redact_credentials(rendered)
    rendered, _ = redact_exfiltration_urls(rendered)
    return web.json_response({
        "ok": True, "rendered": rendered,
        "detected_variables": detected, "includes": includes,
    })


async def api_prompt_syntax(_request: web.Request) -> web.Response:
    """GET /api/prompts/syntax — the template-language reference the authoring UI
    renders as a click-to-insert cheatsheet + autocomplete source. Generated from
    the live engine so docs can never drift from behavior."""
    from personalclaw.prompt_providers.engine import BUILT_IN_FUNCTIONS

    # Function catalog grouped by category (name + a one-line signature/desc).
    fn_meta: dict[str, tuple[str, str, str]] = {
        # name: (category, signature, description)
        "upper": ("string", "upper(s)", "Uppercase."),
        "lower": ("string", "lower(s)", "Lowercase."),
        "capitalize": ("string", "capitalize(s)", "Capitalize the first letter."),
        "title": ("string", "title(s)", "Title-case each word."),
        "trim": ("string", "trim(s)", "Strip leading/trailing whitespace."),
        "replace": ("string", "replace(s, a, b)", "Replace occurrences of a with b."),
        "length": ("string", "length(s)", "Length of a string/list/dict."),
        "truncate": ("string", "truncate(s, n=80)", "Truncate to n chars with an ellipsis."),
        "split": ("string", "split(s, sep=',')", "Split a string into a list."),
        "substring": ("string", "substring(s, start, end?)", "Slice a string by index."),
        "join": ("array", "join(xs, sep=', ')", "Join a list into a string."),
        "first": ("array", "first(xs)", "First element."),
        "last": ("array", "last(xs)", "Last element."),
        "count": ("array", "count(xs)", "Number of elements."),
        "sort": ("array", "sort(xs)", "Sorted copy."),
        "slice": ("array", "slice(xs, start, end?)", "Sub-list by index."),
        "push": ("array", "push(xs, item)", "List with item appended."),
        "filter": ("array", "filter(xs)", "Drop falsy elements."),
        "map": ("array", "map(xs, prop)", "Pluck a property from each item."),
        "contains": ("array", "contains(coll, item)", "Membership test (string/list/dict)."),
        "min": ("array", "min(xs)", "Smallest element."),
        "max": ("array", "max(xs)", "Largest element."),
        "keys": ("object", "keys(o)", "Object keys."),
        "values": ("object", "values(o)", "Object values."),
        "entries": ("object", "entries(o)", "[[key, value], …] pairs."),
        "get": ("object", "get(o, 'a.b', fallback?)", "Nested dot-path access with a fallback."),
        "json": ("util", "json(v)", "Serialize to JSON."),
        "parse": ("util", "parse(s)", "Parse a JSON string."),
        "default": ("util", "default(v, fallback)", "Fallback when v is empty/None."),
        "uuid": ("util", "uuid()", "Random UUID."),
        "date": ("util", "date()", "Current UTC ISO-8601 timestamp."),
        "timestamp": ("util", "timestamp()", "Current UTC unix seconds."),
        "add": ("math", "add(a, b)", "a + b."),
        "subtract": ("math", "subtract(a, b)", "a - b."),
        "multiply": ("math", "multiply(a, b)", "a × b."),
        "divide": ("math", "divide(a, b)", "a ÷ b (0 on divide-by-zero)."),
        "round": ("math", "round(a, n=0)", "Round to n decimals."),
        "abs": ("math", "abs(a)", "Absolute value."),
        "if": ("logic", "if(cond, a, b)", "Inline ternary — a when cond is truthy, else b."),
        "unless": ("logic", "unless(cond, a, b)", "Inverse ternary — b when cond is truthy, else a."),
        "isString": ("type", "isString(v)", "True if v is a string."),
        "isNumber": ("type", "isNumber(v)", "True if v is a number."),
        "isBoolean": ("type", "isBoolean(v)", "True if v is a boolean."),
        "isArray": ("type", "isArray(v)", "True if v is a list."),
        "isObject": ("type", "isObject(v)", "True if v is an object."),
        "isEmpty": ("type", "isEmpty(v)", "True if v is empty/None."),
    }
    functions = [
        {
            "name": name,
            "category": fn_meta.get(name, ("util", f"{name}(…)", ""))[0],
            "signature": fn_meta.get(name, ("util", f"{name}(…)", ""))[1],
            "description": fn_meta.get(name, ("util", f"{name}(…)", ""))[2],
            "insert": f"{name}()",
        }
        for name in sorted(BUILT_IN_FUNCTIONS)
    ]
    constructs = [
        {"category": "variable", "label": "Variable", "snippet": "{{ name }}",
         "description": "Insert a variable's value. Dot-paths and list indexes work: {{ user.name }}, {{ items.0 }}."},
        {"category": "variable", "label": "Typed variable", "snippet": "{{ name::text }}",
         "description": "Declare a variable's input type inline: text, textarea, number, boolean, or select::[a, b]."},
        {"category": "conditional", "label": "If / elif / else", "snippet": "{% if cond %}\n…\n{% elif other %}\n…\n{% else %}\n…\n{% endif %}",
         "description": "Branch on a condition. Operators: == != > < >= <=, membership (x in y), booleans (and / or / not), grouping ( )."},
        {"category": "loop", "label": "For loop", "snippet": "{% for item in items %}\n{{ item }}\n{% endfor %}",
         "description": "Iterate a list/object/string. Inside: loop.index, loop.index1, loop.first, loop.last, loop.length."},
        {"category": "function", "label": "Function call", "snippet": "{{ upper(name) }}",
         "description": "Call a built-in. Calls nest: {{ upper(trim(name)) }}."},
        {"category": "include", "label": "Include snippet", "snippet": "{{> snippet-name }}",
         "description": "Inline a reusable snippet (recursive, cycle-safe)."},
        {"category": "comment", "label": "Comment", "snippet": "{# note #}",
         "description": "A comment — stripped from the output."},
        {"category": "whitespace", "label": "Whitespace trim", "snippet": "{%- … -%}",
         "description": "A leading/trailing '-' trims adjacent whitespace: {{- x -}}, {%- if … -%}."},
    ]
    return web.json_response({"functions": functions, "constructs": constructs})


# ── Prompt snippets (reusable {{> name}} fragments) ──


def _build_snippet(body: dict[str, Any], default_name: str = "") -> Any:
    from personalclaw.prompt_providers.base import PromptSnippet

    raw_name = body.get("name") or default_name or ""
    if not isinstance(raw_name, str):
        raise ValueError("'name' must be a string")
    name = raw_name.strip()
    if not name:
        raise ValueError("Missing 'name' field")
    return PromptSnippet.from_dict({
        "name": name,
        "title": body.get("title") or "",
        "description": body.get("description") or "",
        "content": body.get("content") or "",
        "variables": body.get("variables") or [],
        "tags": body.get("tags") or [],
    })


async def api_snippets(request: web.Request) -> web.Response:
    """GET /api/prompt-snippets — list reusable snippets via the provider."""
    provider = _get_default_prompt_provider()
    snippets = provider.list_snippets() if provider is not None else []
    return web.json_response([_snippet_to_listing(s) for s in snippets])


async def api_snippet_detail(request: web.Request) -> web.Response:
    """GET /api/prompt-snippets/{name} — read a snippet (with redacted content)."""
    bare = request.match_info["name"]
    provider = _get_default_prompt_provider()
    snip = provider.get_snippet(bare) if provider is not None else None
    if snip is None:
        return web.json_response({"error": "not found"}, status=404)
    content, _ = redact_credentials(snip.content)
    content, _ = redact_exfiltration_urls(content)
    return web.json_response({**_snippet_to_listing(snip), "content": content,
                              "used_by": _snippet_usages(provider, bare)})


async def api_snippet_create(request: web.Request) -> web.Response:
    """POST /api/prompt-snippets — create a snippet."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    try:
        snip = _build_snippet(body)
        provider.create_snippet(snip)
    except ValueError as exc:
        msg = str(exc)
        status = 409 if "already exists" in msg else 400
        return web.json_response({"error": msg}, status=status)
    return web.json_response({"ok": True, "name": snip.name, "snippet": snip.to_dict()})


async def api_snippet_save(request: web.Request) -> web.Response:
    """PUT /api/prompt-snippets/{name} — update a snippet."""
    bare = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    try:
        snip = _build_snippet(body, default_name=bare)
        provider.update_snippet(bare, snip)
    except FileNotFoundError:
        return web.json_response({"error": "not found"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "snippet": snip.to_dict()})


async def api_snippet_delete(request: web.Request) -> web.Response:
    """DELETE /api/prompt-snippets/{name} — remove a snippet.

    Refuses while the snippet is still included by any prompt/snippet (deleting it
    would make every includer render a ``[missing snippet: name]`` marker — silent
    breakage). The caller must remove the ``{{> name}}`` references first. A `?force=1`
    override is honored for the rare intentional orphan-delete."""
    bare = request.match_info["name"]
    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    if provider.get_snippet(bare) is None:
        return web.json_response({"error": "not found"}, status=404)
    force = str(request.query.get("force", "")).strip() in ("1", "true")
    if not force:
        usages = _snippet_usages(provider, bare)
        refs = usages["prompts"] + usages["snippets"]
        if refs:
            return web.json_response({
                "error": f"Snippet is included by {len(refs)} item(s): {', '.join(refs[:6])}"
                         f"{'…' if len(refs) > 6 else ''}. Remove those {{{{> {bare}}}}} references first, or pass force=1.",
                "used_by": usages,
            }, status=409)
    if not provider.delete_snippet(bare):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_snippet_render(request: web.Request) -> web.Response:
    """POST /api/prompt-snippets/{name}/render — preview a snippet standalone."""
    bare = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    values = body.get("variables") or {}
    if not isinstance(values, dict):
        return web.json_response({"error": "variables must be an object"}, status=400)
    provider = _get_default_prompt_provider()
    if provider is None:
        return web.json_response({"error": "no prompt provider registered"}, status=503)
    snip = provider.get_snippet(bare)
    if snip is None:
        return web.json_response({"error": "not found"}, status=404)
    from personalclaw.prompt_providers.base import PromptRenderError
    from personalclaw.prompt_providers.engine import render_snippet
    try:
        rendered = render_snippet(snip, values, resolver=_snippet_resolver(provider))
    except PromptRenderError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    rendered, _ = redact_credentials(rendered)
    rendered, _ = redact_exfiltration_urls(rendered)
    return web.json_response({"name": bare, "rendered": rendered})


# ── Prompt use-case bindings ──


async def api_prompt_bindings(_request: web.Request) -> web.Response:
    """GET /api/prompts/bindings — which prompt serves each runtime use-case.

    Returns the use-case vocabulary, the current binding per use-case (or the
    bundled default when unbound), and the available prompts to pick from.
    """
    from personalclaw.providers.prompt_use_cases import (
        DEFAULT_PROMPT_NAME,
        DEFAULT_PROMPT_PROVIDER,
        active_prompt_ref,
        all_prompt_use_cases,
        load_active_prompts,
    )

    active = load_active_prompts()
    default_ref = f"{DEFAULT_PROMPT_PROVIDER}:{DEFAULT_PROMPT_NAME}"
    # The full bindable vocabulary = core catalog UNION app-owned use-cases, so an
    # app that ships its own prompts surfaces them in the bindings UI too.
    use_cases = all_prompt_use_cases()
    bindings = [
        {
            "use_case": uc,
            "ref": active.get(uc, ""),          # "" = unbound (uses the use-case default)
            # The prompt that actually resolves: bound ref, else this use-case's
            # own tailored bundled prompt (NOT the shared chat default).
            "effective_ref": active_prompt_ref(uc),
        }
        for uc in use_cases
    ]
    return web.json_response(
        {
            "use_cases": list(use_cases),
            "default_ref": default_ref,
            "bindings": bindings,
            "available": _list_provider_prompts(),
        }
    )


async def api_prompt_bindings_save(request: web.Request) -> web.Response:
    """PUT /api/prompts/bindings — set the prompt bound to one use-case.

    Body: ``{"use_case": "<uc>", "ref": "<provider:prompt>" | ""}``. An empty
    ``ref`` clears the binding (the use-case falls back to the bundled default).
    """
    from personalclaw.providers.prompt_use_cases import (
        load_active_prompts,
        save_active_prompts,
        split_ref,
        valid_prompt_use_cases,
    )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    use_case = body.get("use_case", "")
    ref = body.get("ref", "")
    if use_case not in valid_prompt_use_cases():
        return web.json_response({"error": f"unknown use_case: {use_case!r}"}, status=400)
    if ref and not isinstance(ref, str):
        return web.json_response({"error": "ref must be a string"}, status=400)

    active = load_active_prompts()
    if ref:
        parsed = split_ref(ref)
        if not parsed:
            return web.json_response(
                {"error": "ref must be '<provider>:<prompt-name>'"}, status=400
            )
        provider_name, prompt_name = parsed
        # Validate the target prompt exists before binding.
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_prompt_provider,
        )

        _ensure_default_providers_registered()
        provider = get_prompt_provider(provider_name)
        if provider is None or provider.get_prompt(prompt_name) is None:
            return web.json_response(
                {"error": f"prompt not found: {ref!r}"}, status=404
            )
        active[use_case] = ref
    else:
        active.pop(use_case, None)  # clear → falls back to default
    save_active_prompts(active)
    _sel().log_api_access(
        caller=request.get("user") or "dashboard",
        operation="prompt.binding.set",
        outcome="ok",
        source="dashboard",
        resources=f"{use_case}={ref or '(default)'}",
    )
    return await api_prompt_bindings(request)


# ── Skills ──


async def api_skill_detail(request: web.Request) -> web.Response:
    """GET/PUT /api/skills/{name} — get or update a skill. (Listing is served by
    handlers/skills.py::api_skills_list; deletion by api_skills_delete.)"""
    state: DashboardState = request.app["state"]
    name = request.match_info["name"]
    skills = _get_skills(state)

    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "JSON body must be an object"}, status=400)
        content = body.get("content", "")
        if not content:
            return web.json_response({"error": "content is required"}, status=400)
        ok = skills.update_skill(name, content)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"ok": True})

    # GET
    content = skills.load_skill(name)
    mkt_prefix = name.startswith("marketplace/")
    if content is None and mkt_prefix:
        bare_name = name.split("/", 1)[1]  # strip prefix
        for s in await _list_marketplace_skills():
            if s["name"] == bare_name or s["key"] == name:
                if s["path"]:
                    from personalclaw.hooks import validate_file_path  # noqa: F811
                    resolved = validate_file_path(s["path"])
                    if resolved is None:
                        return web.json_response({"error": "access denied"}, status=403)
                    try:
                        content = Path(resolved).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
                break
    if content is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"name": name, "content": content})


async def api_skills_create(request: web.Request) -> web.Response:
    """POST /api/skills — create a new skill."""
    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    name = body.get("name", "")
    content = body.get("content", "")
    if not isinstance(name, str) or not isinstance(content, str):
        return web.json_response({"error": "name/content must be strings"}, status=400)
    name = name.strip()
    content = content.strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)
    if not content:
        return web.json_response({"error": "content is required"}, status=400)
    # Sanitize name: lowercase, alphanumeric + hyphens + slashes for nesting
    safe_name = re.sub(r"[^a-z0-9\-/]", "-", name.lower()).strip("-").strip("/")
    safe_name = re.sub(r"/+", "/", safe_name)  # collapse multiple slashes
    if not safe_name:
        return web.json_response({"error": "invalid skill name"}, status=400)
    skills = _get_skills(state)
    ok = skills.create_skill(safe_name, content)
    if not ok:
        return web.json_response({"error": f"skill '{safe_name}' already exists"}, status=409)
    return web.json_response({"ok": True, "name": safe_name})
