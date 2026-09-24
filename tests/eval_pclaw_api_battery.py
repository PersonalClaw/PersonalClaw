"""The 5-task PClaw-driving eval battery (PLATFORM-LEGIBILITY §3.2).

Quarkdown's published eval shape, sized for one machine: five representative
driving tasks — create+wire a trigger, add+verify a knowledge item, drive an app
backend route, bind a model to a use case, author+install a skill — each scored on
whether an agent names the RIGHT tool/route with the EXACT parameters and includes
the mandatory verify-after-mutate step (the "silent miss" guard).

This module is the checked-in regression harness the plan requires. It carries:

* :data:`TASKS` — the prompts an eval subject answers (with and without the
  `pclaw-api` skill + offline reference in context).
* :data:`ANSWER_KEY` — the ground truth, VERIFIED against code.
* :func:`score_answers` — grades a subject's structured answers against the key.
* the regression test below — asserts the answer key still matches the LIVE
  manifest, so a future signature change (rename a param, move a route) breaks this
  battery and forces the reference + key to be regenerated together. That is the
  battery's standing value after the one-time eval: it can't silently rot.

The eval RUN itself (spawning fresh context-free subagents for each arm and
checking ≥4/5 first-try + 0 silent misses) is an operator action recorded in the
plan's execution log; this file is the scored contract it runs against.
"""

from __future__ import annotations

import asyncio
from typing import Any

# ── The tasks (identical text for both arms; only the context differs) ───────
TASKS: list[dict[str, str]] = [
    {
        "id": "trigger",
        "prompt": (
            "Register a follow-up trigger so that after you finish deploying the "
            "website, a later turn re-checks that the site is live. Which tool do "
            "you call, with exactly which parameters, and how do you confirm it "
            "took?"
        ),
    },
    {
        "id": "knowledge",
        "prompt": (
            "Save a deployment runbook into the knowledge base, then confirm it is "
            "retrievable. Which tool creates it (with which parameters), and which "
            "tool do you use to verify retrieval?"
        ),
    },
    {
        "id": "app_route",
        "prompt": (
            "An installed app 'growth' exposes a backend route to list its "
            "artifacts. How does an agent drive that app backend route — through "
            "which surface — and where do you look up the exact route? (Name the "
            "manifest surface and the invocation path; do NOT reach into the app "
            "process directly.)"
        ),
    },
    {
        "id": "model_bind",
        "prompt": (
            "Bind a specific model to the 'chat' use case via the HTTP API, then "
            "confirm the binding. Which route (method + path), what request body "
            "shape, and which route confirms it?"
        ),
    },
    {
        "id": "skill",
        "prompt": (
            "Persist a reusable how-to ('Deploy the website') as a skill, then "
            "confirm it is in the skill index. Which tool authors it (with which "
            "parameters), and which tool verifies it?"
        ),
    },
]

# ── Ground truth (verified against code 2026-07-25) ──────────────────────────
# Each key: the accepted primary tool/route names, the REQUIRED parameters an
# answer must name, and the tool/route that satisfies the verify-after-mutate
# loop. ``accept`` lists alternatives that are also correct (e.g. schedule_add is
# a valid way to wire recurring follow-up work).
ANSWER_KEY: dict[str, dict[str, Any]] = {
    "trigger": {
        "primary": "hook_register",
        "accept": {"hook_register", "schedule_add", "schedule_natural"},
        "required_params": {"hook_register": {"hook_id", "context_summary"}},
        "verify_terms": {"list", "read back", "re-read", "hook", "confirm", "schedule_list"},
    },
    "knowledge": {
        "primary": "knowledge_create",
        "accept": {"knowledge_create"},
        "required_params": {"knowledge_create": set()},  # all optional; title+content expected
        "expected_params": {"knowledge_create": {"title", "content"}},
        "verify_tools": {"knowledge_search", "knowledge_get"},
    },
    "app_route": {
        "surface_terms": {"app_surfaces", "manifest"},
        "invocation_terms": {"app-route tool", "proxy", "/apps/", "app_route", "app route tool"},
        "negative_terms": {"directly", "process"},  # the answer must warn against direct access
    },
    "model_bind": {
        "route": ("PUT", "/api/models/active/{use_case}"),
        "body_key": "models",
        "verify_route": ("GET", "/api/models/active"),
    },
    "skill": {
        "primary": "skill_remember",
        "accept": {"skill_remember"},
        "required_params": {"skill_remember": {"title", "body"}},
        "verify_tools": {"skill_search"},
    },
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _params_of(answer: dict[str, Any]) -> set[str]:
    raw = answer.get("params", []) or []
    if isinstance(raw, dict):
        raw = list(raw.keys())
    return {_norm(str(p)) for p in raw}


def _text_of(answer: dict[str, Any]) -> str:
    """Flatten an answer's free-text fields for term matching."""
    parts = [str(answer.get(k, "")) for k in ("tool", "route", "verify", "notes", "answer")]
    parts.append(" ".join(str(x) for x in (answer.get("params", []) or [])))
    return _norm(" ".join(parts))


def _score_trigger(a: dict[str, Any]) -> tuple[bool, bool]:
    key = ANSWER_KEY["trigger"]
    tool = _norm(a.get("tool", ""))
    text = _text_of(a)
    correct_tool = tool in key["accept"] or any(t in text for t in key["accept"])
    params_ok = True
    if tool == "hook_register":
        params_ok = key["required_params"]["hook_register"] <= _params_of(a)
    verified = any(t in text for t in key["verify_terms"])
    return (correct_tool and params_ok and verified), (correct_tool and not verified)


def _score_knowledge(a: dict[str, Any]) -> tuple[bool, bool]:
    key = ANSWER_KEY["knowledge"]
    tool = _norm(a.get("tool", ""))
    text = _text_of(a)
    correct_tool = tool == "knowledge_create" or "knowledge_create" in text
    verified = any(t in text for t in key["verify_tools"])
    return (correct_tool and verified), (correct_tool and not verified)


def _score_app_route(a: dict[str, Any]) -> tuple[bool, bool]:
    key = ANSWER_KEY["app_route"]
    text = _text_of(a)
    surface_ok = any(t in text for t in key["surface_terms"])
    invoke_ok = any(t in text for t in key["invocation_terms"])
    warns = any(t in text for t in key["negative_terms"])
    # correct = names the manifest surface AND the proxy/app-route invocation.
    return (surface_ok and invoke_ok), (invoke_ok and not warns)


def _score_model_bind(a: dict[str, Any]) -> tuple[bool, bool]:
    key = ANSWER_KEY["model_bind"]
    text = _text_of(a)
    method, path = key["route"]
    route_ok = _norm(method) in text and "/api/models/active/" in text
    body_ok = key["body_key"] in text
    vmethod, vpath = key["verify_route"]
    verified = "/api/models/active" in text and "get" in text
    return (route_ok and body_ok and verified), (route_ok and not verified)


def _score_skill(a: dict[str, Any]) -> tuple[bool, bool]:
    key = ANSWER_KEY["skill"]
    tool = _norm(a.get("tool", ""))
    text = _text_of(a)
    correct_tool = tool == "skill_remember" or "skill_remember" in text
    params_ok = key["required_params"]["skill_remember"] <= _params_of(a) or (
        "title" in text and "body" in text
    )
    verified = any(t in text for t in key["verify_tools"])
    return (correct_tool and params_ok and verified), (correct_tool and not verified)


_SCORERS = {
    "trigger": _score_trigger,
    "knowledge": _score_knowledge,
    "app_route": _score_app_route,
    "model_bind": _score_model_bind,
    "skill": _score_skill,
}


def score_answers(answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Grade a subject's answers. Returns per-task correct/silent-miss + totals.

    ``answers`` maps task id → ``{tool?, route?, params?, verify?, notes?}``. A
    task is *correct* when the right tool/route + exact params + a verify step are
    all present; a *silent miss* is naming the right action but omitting the
    verify step (claiming done without reading the entity back).
    """
    per_task: dict[str, dict[str, bool]] = {}
    correct = 0
    silent = 0
    for task in TASKS:
        tid = task["id"]
        a = answers.get(tid, {}) or {}
        ok, miss = _SCORERS[tid](a)
        per_task[tid] = {"correct": ok, "silent_miss": miss}
        correct += int(ok)
        silent += int(miss)
    return {
        "per_task": per_task,
        "first_try_correct": correct,
        "total": len(TASKS),
        "silent_misses": silent,
        "passes_bar": correct >= 4 and silent == 0,
    }


# ── Regression test: the answer key must still match the live manifest ────────


def _live_tools() -> dict[str, Any]:
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers import registry as prov_reg
    from personalclaw.providers.loader import BUNDLED_DIR
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        for d in sorted(BUNDLED_DIR.iterdir()):
            mf = d / "app.json"
            if not mf.exists():
                continue
            m = AppManifest.from_json_file(mf)
            if m.provider:
                reg.register(m, enabled=True)
        from personalclaw.tool_providers.registry import list_all_tools

        live = asyncio.run(list_all_tools())
        return {t.name: t for t in live if t.provider != "mcp"}
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None


def _required_of(tool) -> set[str]:
    params = tool.parameters or {}
    return set(params.get("required", []) if isinstance(params, dict) else [])


def _props_of(tool) -> set[str]:
    params = tool.parameters or {}
    props = params.get("properties", {}) if isinstance(params, dict) else {}
    return set(props.keys())


def test_answer_key_matches_the_live_manifest():
    """A signature change must break THIS battery, not silently invalidate it.

    Asserts every tool the key names still exists, every required param the key
    demands is still required (and every expected param still exists), and the
    model-binding route still registers with the documented shape. If a future
    change moves any of these, this reddens — regenerate the reference + key.
    """
    tools = _live_tools()
    problems: list[str] = []

    # hook_register: required params the key checks.
    hr = tools.get("hook_register")
    if not hr:
        problems.append("hook_register no longer exists")
    else:
        need = ANSWER_KEY["trigger"]["required_params"]["hook_register"]
        missing = need - _required_of(hr)
        if missing:
            problems.append(f"hook_register no longer requires {sorted(missing)}")

    # knowledge_create + its verify tools exist; expected params present.
    kc = tools.get("knowledge_create")
    if not kc:
        problems.append("knowledge_create no longer exists")
    else:
        exp = ANSWER_KEY["knowledge"]["expected_params"]["knowledge_create"]
        absent = exp - _props_of(kc)
        if absent:
            problems.append(f"knowledge_create lost params {sorted(absent)}")
    for vt in ANSWER_KEY["knowledge"]["verify_tools"]:
        if vt not in tools:
            problems.append(f"knowledge verify tool {vt} no longer exists")

    # skill_remember: required params + verify tool.
    sr = tools.get("skill_remember")
    if not sr:
        problems.append("skill_remember no longer exists")
    else:
        need = ANSWER_KEY["skill"]["required_params"]["skill_remember"]
        missing = need - _required_of(sr)
        if missing:
            problems.append(f"skill_remember no longer requires {sorted(missing)}")
    for vt in ANSWER_KEY["skill"]["verify_tools"]:
        if vt not in tools:
            problems.append(f"skill verify tool {vt} no longer exists")

    assert not problems, "Eval battery answer key drifted from the manifest:\n" + "\n".join(
        problems
    )


def test_model_bind_route_still_registers():
    """The model-binding route the key names still registers with its shape."""
    import ast
    from pathlib import Path

    import personalclaw.dashboard.handlers.model_registry as mr

    src = Path(mr.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            verb = node.func.attr
            if verb in {"add_get", "add_put", "add_post"} and node.args:
                p = node.args[0]
                if isinstance(p, ast.Constant) and isinstance(p.value, str):
                    literals.add((verb, p.value))
    assert ("add_put", "/api/models/active/{use_case}") in literals
    assert ("add_get", "/api/models/active") in literals


def test_scorer_grades_a_perfect_paper():
    """A correct answer sheet passes the bar; a verify-omitting sheet is a silent miss."""
    perfect: dict[str, dict[str, Any]] = {
        "trigger": {
            "tool": "hook_register",
            "params": ["hook_id", "context_summary"],
            "verify": "list hooks and confirm it is there",
        },
        "knowledge": {
            "tool": "knowledge_create",
            "params": ["title", "content"],
            "verify": "knowledge_search for the title",
        },
        "app_route": {
            "answer": "look up the route in the manifest app_surfaces and call the "
            "app-route tool through the proxy; do not hit the app process directly"
        },
        "model_bind": {
            "route": "PUT /api/models/active/chat",
            "notes": "body {models: [provider:model_id]}; confirm with GET /api/models/active",
        },
        "skill": {
            "tool": "skill_remember",
            "params": ["title", "body"],
            "verify": "skill_search to confirm",
        },
    }
    result = score_answers(perfect)
    assert result["first_try_correct"] == 5, result
    assert result["silent_misses"] == 0, result
    assert result["passes_bar"]

    # Drop every verify step → correct tools, but silent misses, bar fails.
    no_verify: dict[str, dict[str, Any]] = {tid: {**a} for tid, a in perfect.items()}
    for tid in ("trigger", "knowledge", "skill"):
        no_verify[tid] = {k: v for k, v in perfect[tid].items() if k != "verify"}
    graded = score_answers(no_verify)
    assert graded["silent_misses"] >= 1
    assert not graded["passes_bar"]
