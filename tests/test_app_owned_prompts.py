"""App-owned prompts — an app/extension SHIPS and OWNS the prompts it uses.

Covers the platform capability (A-series): an app declares prompt/snippet
definition files in its manifest's ``prompts`` list; on enable they seed into the
native prompt store (idempotent, non-clobbering) and their use-cases join the
bindable vocabulary; on disable they're removed and the use-cases unregistered.
Plus the two migrated bundled apps (web-tools, native-knowledge) prove the
round-trip end to end with BYTE-IDENTICAL rendering, and an app may register
MULTIPLE providers (same or different kinds).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager, prompt_registry
from personalclaw.apps.manifest import AppManifest
from personalclaw.prompt_providers.base import PromptVariable
from personalclaw.prompt_providers.engine import render
from personalclaw.prompt_providers.runtime import render_use_case_prompt
from personalclaw.providers import prompt_use_cases as puc
from personalclaw.providers.loader import BUNDLED_DIR


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolated config dir + fresh prompt-provider + app-prompt registries."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    import personalclaw.prompt_providers.registry as preg

    preg._providers.clear()
    prompt_registry.clear()
    from personalclaw.providers import registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    # Seeding is intentionally exercised here, so the skip flag must be OFF.
    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    yield tmp_path
    preg._providers.clear()
    prompt_registry.clear()


# ── the two real bundled apps: install → seed → resolve → disable ─────────────


# web-tools is a SEPARATED app now (not baseline-bundled), so its prompts seed only
# when installed — covered by test_web_extract_renders_* below. native-knowledge is
# still a core-platform bundled app whose prompts seed as baseline.
_APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


@pytest.mark.parametrize(
    "use_cases,prompt_names",
    [
        (
            [
                "knowledge_extraction",
                "knowledge_insights",
                "knowledge_intent_match",
                "knowledge_skill_synthesis",
            ],
            [
                "task-knowledge-extraction",
                "task-knowledge-insights",
                "task-knowledge-intent-match",
                "task-knowledge-skill-synthesis",
            ],
        ),
    ],
)
def test_bundled_apps_seed_their_prompts_as_baseline(use_cases, prompt_names):
    """A core-platform bundled app (native-knowledge) OWNS its prompts and seeds them
    as part of the shipped baseline — resolvable through the prompt provider without a
    separate install, exactly like core prompts."""
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()  # seeds core + bundled-app prompts
    prov = get_prompt_provider("native")

    for uc, name in zip(use_cases, prompt_names):
        assert prov.get_prompt(name) is not None, f"{name} not seeded"
        assert uc in puc.valid_prompt_use_cases()
        # The use-case binds to (and resolves to) the app-owned prompt by default.
        assert puc.active_prompt_ref(uc) == f"native:{name}"


def test_web_extract_renders_byte_identical_to_pre_migration_literal():
    """The app-seeded web_extract prompt renders identically to the original md."""
    if not (_APPS_DIR / "web-tools").is_dir():  # standalone core clone
        pytest.skip("web-tools app dir not present (standalone clone)")
    app_manager.install(_APPS_DIR / "web-tools", origin="local", confirm=True)

    # The pre-migration literal (config/prompts/task-web_extract.md content) with
    # its declared variables — the migrated prompt must render the same bytes.
    expected_content = (
        "Extract structured data from the page content below. Return ONLY a single "
        "JSON object — no prose, no markdown fence — matching exactly what is requested.\n\n"
        "WHAT TO EXTRACT:\n{{instructions}}\n\n"
        "PAGE TITLE: {{title}}\nPAGE URL: {{url}}\n\n"
        "PAGE CONTENT:\n{{content}}\n\n"
        "Respond with the JSON object only. Use null for fields not present on the page."
    )
    variables = [
        PromptVariable(name="instructions", type="textarea", required=True),
        PromptVariable(name="title", default="(none)"),
        PromptVariable(name="url", default=""),
        PromptVariable(name="content", type="textarea", required=True),
    ]
    values = {"instructions": "name + price", "title": "T", "url": "http://x", "content": "BODY"}
    expected = render(expected_content, variables, values)
    assert render_use_case_prompt("web_extract", values) == expected


def test_knowledge_intent_match_renders_byte_identical():
    """The newly-migrated intent-match prompt == the original build_match_prompt."""
    from personalclaw.knowledge.intents import FIELD_TYPES, Intent, build_match_prompt

    app_manager.install(BUNDLED_DIR / "native-knowledge", origin="builtin", confirm=True)

    intent = Intent(id="homelab", goal="anything that improves my homelab")
    content = "Proxmox + ZFS tuning. " * 80
    # build_match_prompt now routes through render_use_case_prompt; reconstruct the
    # pre-migration literal independently and assert equality.
    expected = (
        "You are evaluating whether a saved knowledge item is relevant to a user's "
        "standing interest, and if so, extracting the structured payload they'd want.\n\n"
        f"USER'S INTENT (natural language):\n{intent.goal}\n\n"
        "TASK:\n"
        "1. Decide if THIS content is genuinely relevant to that intent. Be strict — "
        "tangential or no-match content is NOT relevant.\n"
        "2. If relevant, write a one- or two-sentence `takeaway` capturing what the user "
        "gets from this item with respect to their intent.\n"
        "3. If relevant, extract a SMALL set of structured `fields` (0-6) that best "
        "capture the useful specifics. You choose the field names. Each field has a "
        f"`type` from: {', '.join(FIELD_TYPES)}. Use `number` for quantities, `date` for "
        "dates (ISO 8601), `url` for links, `boolean` for yes/no, `tags` for a list of "
        "short labels (value = array of strings), `string` otherwise.\n\n"
        "Return ONLY JSON of this shape:\n"
        '{"relevant": true|false, "takeaway": "...", '
        '"fields": [{"name": "...", "type": "string", "value": ...}]}\n'
        'If not relevant, return {"relevant": false}.\n\n'
        f"CONTENT:\n{content[:12000]}\n\nJSON:"
    )
    assert build_match_prompt(intent, content) == expected


def test_knowledge_skill_synthesis_renders_byte_identical():
    app_manager.install(BUNDLED_DIR / "native-knowledge", origin="builtin", confirm=True)
    goal, digest = "how to invest", "- Buy VTI\n- Hold long"
    expected = (
        "You are turning a user's standing knowledge interest into a reusable agent skill "
        "(a SKILL.md procedure). The skill should help an agent act on NEW items related "
        "to this interest the way the gathered examples suggest.\n\n"
        f"INTENT (what the user tracks):\n{goal}\n\n"
        f"WHAT IT HAS GATHERED SO FAR:\n{digest}\n\n"
        "Respond in EXACTLY this format, with these three header lines verbatim:\n"
        "DESCRIPTION: <one concise line>\n"
        "TRIGGERS: <comma-separated keywords>\n"
        "PROCEDURE:\n<markdown steps the agent should follow>"
    )
    rendered = render_use_case_prompt("knowledge_skill_synthesis", {"goal": goal, "digest": digest})
    assert rendered == expected


def test_no_apps_installed_leaves_core_vocabulary_intact():
    """Robust when empty: with no app prompts, the vocabulary is the core catalog."""
    from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS

    assert prompt_registry.use_cases() == ()
    assert puc.all_prompt_use_cases() == tuple(p.use_case for p in BUNDLED_PROMPTS)
    # The migrated use-cases are NOT in the core catalog.
    core = {p.use_case for p in BUNDLED_PROMPTS}
    for uc in (
        "web_extract",
        "knowledge_extraction",
        "knowledge_insights",
        "knowledge_intent_match",
        "knowledge_skill_synthesis",
    ):
        assert uc not in core


# ── a fixture app that ships its own prompt + snippet ─────────────────────────


def _prompt_app(tmp_path: Path, *, with_snippet: bool = True) -> Path:
    d = tmp_path / "src" / "promptly"
    pdir = d / "prompts"
    pdir.mkdir(parents=True)
    prompts = ["prompts/greet.yaml"]
    (pdir / "greet.yaml").write_text(
        textwrap.dedent("""\
        _entity: prompt
        use_case: app_greet
        name: app-greet
        kind: user
        category: internal
        description: A fixture app-owned prompt.
        content: |-
          Hello {{who}}.
          {{> app-sig}}
    """),
        encoding="utf-8",
    )
    if with_snippet:
        prompts.append("prompts/sig.yaml")
        (pdir / "sig.yaml").write_text(
            textwrap.dedent("""\
            _entity: snippet
            name: app-sig
            description: A fixture app-owned snippet.
            content: '-- from the promptly app'
        """),
            encoding="utf-8",
        )
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": "promptly",
                "version": "1.0.0",
                "displayName": "Promptly",
                "description": "ships its own prompt + snippet",
                "prompts": prompts,
            }
        ),
        encoding="utf-8",
    )
    return d


def test_provideless_app_seeds_prompt_and_snippet_and_resolves(tmp_path):
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    res = app_manager.install(_prompt_app(tmp_path), confirm=True)
    assert res.ok, res.error

    _ensure_default_providers_registered()
    prov = get_prompt_provider("native")
    assert prov.get_prompt("app-greet") is not None
    assert prov.get_snippet("app-sig") is not None  # the app's snippet seeded too

    assert "app_greet" in puc.valid_prompt_use_cases()
    rendered = render_use_case_prompt("app_greet", {"who": "world"})
    # The {{> app-sig}} include resolves to the app's own snippet content.
    assert rendered == "Hello world.\n-- from the promptly app"

    # Disable removes the prompt AND the snippet + unregisters the use-case.
    assert app_manager.disable("promptly") is True
    assert prov.get_prompt("app-greet") is None
    assert prov.get_snippet("app-sig") is None
    assert "app_greet" not in prompt_registry.use_cases()


def test_seed_is_non_clobbering_of_user_edits(tmp_path):
    """A user-edited app prompt is NOT overwritten on a re-seed (mirrors core)."""
    from personalclaw.apps.prompt_seed import seed_app_prompts
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    res = app_manager.install(_prompt_app(tmp_path, with_snippet=False), confirm=True)
    assert res.ok, res.error
    _ensure_default_providers_registered()
    prov = get_prompt_provider("native")

    # Simulate a user edit, then re-seed (e.g. a restart) — the edit must survive.
    prov.update_prompt(
        "app-greet", PromptTemplate(name="app-greet", kind="user", content="EDITED {{who}}")
    )
    manifest = AppManifest.from_json_file(manager.app_dir("promptly") / "app.json")
    seed_app_prompts(manifest, manager.app_dir("promptly"))
    assert prov.get_prompt("app-greet").content == "EDITED {{who}}"


def test_skip_flag_suppresses_app_prompt_seeding(tmp_path, monkeypatch):
    from personalclaw.apps.prompt_seed import seed_app_prompts
    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")
    app_dir = _prompt_app(tmp_path, with_snippet=False)
    manifest = AppManifest.from_json_file(app_dir / "app.json")
    seed_app_prompts(manifest, app_dir)
    _ensure_default_providers_registered()
    assert get_prompt_provider("native").get_prompt("app-greet") is None
    assert "app_greet" not in prompt_registry.use_cases()
