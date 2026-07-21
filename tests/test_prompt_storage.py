"""Tests for the native prompt provider's storage: snippet CRUD + on-read migration
of legacy prompt records to the current schema (kind/title/variable types)."""

from pathlib import Path

import pytest
import yaml

from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate, PromptVariable
from personalclaw.prompt_providers.native_provider import (
    NativePromptProvider,
    _prompt_path,
    _snippet_path,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


@pytest.fixture()
def provider():
    return NativePromptProvider()


# ── snippet CRUD ─────────────────────────────────────────────────────────────


def test_snippet_create_get_list_delete(provider):
    snip = PromptSnippet(
        name="sig",
        title="Signature",
        content="— {{author}}",
        variables=[PromptVariable(name="author")],
    )
    provider.create_snippet(snip)
    got = provider.get_snippet("sig")
    assert got is not None and got.content == "— {{author}}"
    assert got.variables[0].name == "author"
    assert [s.name for s in provider.list_snippets()] == ["sig"]
    assert provider.delete_snippet("sig") is True
    assert provider.get_snippet("sig") is None


def test_snippet_create_duplicate_raises(provider):
    provider.create_snippet(PromptSnippet(name="x", content="a"))
    with pytest.raises(ValueError):
        provider.create_snippet(PromptSnippet(name="x", content="b"))


def test_snippet_update(provider):
    provider.create_snippet(PromptSnippet(name="x", content="old"))
    provider.update_snippet("x", PromptSnippet(name="x", content="new"))
    assert provider.get_snippet("x").content == "new"


def test_snippet_update_missing_raises(provider):
    with pytest.raises(FileNotFoundError):
        provider.update_snippet("ghost", PromptSnippet(name="ghost", content="x"))


def test_prompts_and_snippets_are_separate_stores(provider):
    provider.create_prompt(PromptTemplate(name="dup", kind="user", content="prompt"))
    provider.create_snippet(PromptSnippet(name="dup", content="snippet"))
    # Same name, different stores → no collision.
    assert provider.get_prompt("dup").content == "prompt"
    assert provider.get_snippet("dup").content == "snippet"


# ── migration on read ────────────────────────────────────────────────────────


def test_legacy_prompt_migrated_on_read(provider, tmp_path):
    # Write a pre-`kind` record with legacy variable types directly to disk.
    path = _prompt_path("legacy")
    path.write_text(
        yaml.safe_dump(
            {
                "name": "legacy",
                "description": "old one",
                "content": "Hi {{who}} {{path}}",
                "variables": [
                    {"name": "who", "type": "string", "required": True},
                    {"name": "path", "type": "file_path"},
                ],
            }
        ),
        encoding="utf-8",
    )

    tpl = provider.get_prompt("legacy")
    assert tpl is not None
    assert tpl.kind == "user"  # no system- prefix → user
    assert tpl.title == "Legacy"  # humanized
    assert tpl.variables[0].type == "text"  # string → text
    assert tpl.variables[1].type == "text"  # file_path → text

    # And the on-disk file was rewritten in the new shape (no dual support).
    rewritten = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert rewritten["kind"] == "user"
    assert rewritten["title"] == "Legacy"
    assert rewritten["variables"][0]["type"] == "text"
    assert rewritten["variables"][1]["type"] == "text"


def test_legacy_system_prompt_kind_inferred(provider):
    path = _prompt_path("system-chat")
    path.write_text(
        yaml.safe_dump({"name": "system-chat", "content": "You are X."}), encoding="utf-8"
    )
    tpl = provider.get_prompt("system-chat")
    assert tpl.kind == "system"  # system- prefix
    rewritten = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert rewritten["kind"] == "system"


def test_current_shape_not_rewritten(provider):
    # A record already in canonical shape must NOT be rewritten (no churn).
    provider.create_prompt(PromptTemplate(name="fresh", kind="user", content="hello"))
    path = _prompt_path("fresh")
    before = path.read_text(encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns
    provider.get_prompt("fresh")  # read → would migrate if shape differed
    assert path.read_text(encoding="utf-8") == before
    assert path.stat().st_mtime_ns == mtime_before


def test_bundled_sha_stamp_survives_read_no_migrate_pingpong(provider):
    """A snippet carrying the seeder's ``bundled_sha`` pristine-stamp must reach a
    fixed point on read — migrate-on-read must PRESERVE the sidecar stamp, not strip
    it. Regression: the canonical payload (from ``to_dict()``) never emits
    ``bundled_sha``, so stripping it made ``canonical != raw`` on EVERY load → a
    rewrite + re-stamp ping-pong that churned the file (and spammed the migrate log)
    on every single prompt/snippet read."""
    import hashlib

    content = "the bundled rule text"
    sha = hashlib.sha256(content.encode()).hexdigest()
    path = _snippet_path("stamped")
    path.write_text(
        yaml.safe_dump(
            {
                "name": "stamped",
                "title": "Stamped",
                "content": content,
                "tags": ["bundled"],
                "bundled_sha": sha,
            }
        ),
        encoding="utf-8",
    )
    # First read may normalize shape once; the SECOND read must not rewrite at all.
    provider.get_snippet("stamped")
    after_first = path.read_text(encoding="utf-8")
    mtime_first = path.stat().st_mtime_ns
    provider.get_snippet("stamped")  # would rewrite if the stamp weren't preserved
    assert path.read_text(encoding="utf-8") == after_first
    assert path.stat().st_mtime_ns == mtime_first
    # And the pristine-stamp is still on disk (so the next seed's pristine-check works).
    assert yaml.safe_load(after_first).get("bundled_sha") == sha


def test_seed_writes_system_kind(provider, monkeypatch, tmp_path):
    # Allow seeding for this test, then confirm seeded prompts are kind=system.
    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    from personalclaw.prompt_providers.native_provider import seed_bundled_system_prompts

    seed_bundled_system_prompts()
    chat = provider.get_prompt("system-chat")
    assert chat is not None and chat.kind == "system"
    assert "bot_name" in [v.name for v in chat.variables]


def test_seed_writes_shared_snippets_and_prompts_include_them(provider, monkeypatch):
    """The bundled system prompts include shared snippets via {{> name}}, which seed
    alongside them and resolve through the compose-aware engine."""
    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    from personalclaw.prompt_providers.engine import render_template
    from personalclaw.prompt_providers.native_provider import seed_bundled_system_prompts

    seed_bundled_system_prompts()

    # All bundled snippets seeded (the 2 from S7 + the 5 atomic ones from the
    # full breakdown).
    for sname in (
        "safety-rules",
        "diff-output",
        "skills-syntax",
        "memory-discipline",
        "parallel-subagents",
        "subagent-orchestration",
        "mcp-reconnect",
    ):
        assert provider.get_snippet(sname) is not None, f"snippet {sname} did not seed"
    assert "git push" in provider.get_snippet("safety-rules").content

    # EVERY bundled system prompt is composed from snippets and renders with every
    # {{> include}} resolved (no leftover marker, no [missing snippet:]).
    def resolver(n):
        return provider.get_snippet(n)

    for pname in ("system-chat", "system-background", "system-code", "system-goal-loop"):
        p = provider.get_prompt(pname)
        assert p is not None and "{{>" in p.content, f"{pname} should include snippets"
        rendered = render_template(p, {"bot_name": "X", "widget_block": ""}, resolver=resolver)
        assert "{{>" not in rendered, f"{pname} left an unresolved include"
        assert "[missing snippet:" not in rendered, f"{pname} references a missing snippet"
    # The chat prompt inlines representative snippet prose end-to-end.
    chat_rendered = render_template(
        provider.get_prompt("system-chat"), {"bot_name": "X", "widget_block": ""}, resolver=resolver
    )
    assert "git push" in chat_rendered and "subagent_run" in chat_rendered


# ── bundled-snippet re-seed: propagate bundled updates, never clobber user edits ──


def test_reseed_updates_pristine_bundled_snippet(provider, monkeypatch):
    """A snippet still pristine from a PRIOR bundled seed (its content hashes to the
    recorded bundled_sha) is refreshed when the bundled source changes — so a security
    rule added to a bundled snippet reaches an existing instance. (#150)"""
    import hashlib

    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    from personalclaw.prompt_providers import native_provider as N

    p = _snippet_path("safety-rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    old = "- an older bundled rule"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "safety-rules",
                "content": old,
                "bundled_sha": hashlib.sha256(old.encode()).hexdigest(),
            }
        )
    )
    N.seed_bundled_snippets()
    # now matches the current bundled safety-rules (which includes the fence rule)
    assert "untrusted_content" in provider.get_snippet("safety-rules").content


def test_reseed_preserves_user_edited_snippet(provider, monkeypatch):
    """A user-edited snippet (content ≠ bundled, no matching stamp) is NEVER clobbered,
    across repeated seeds — the non-clobber guarantee."""
    monkeypatch.delenv("PERSONALCLAW_SKIP_PROMPT_SEED", raising=False)
    from personalclaw.prompt_providers import native_provider as N

    p = _snippet_path("safety-rules")
    p.parent.mkdir(parents=True, exist_ok=True)
    mine = "- MY OWN custom safety rule the bundled update must not overwrite"
    p.write_text(yaml.safe_dump({"name": "safety-rules", "content": mine}))
    N.seed_bundled_snippets()
    assert provider.get_snippet("safety-rules").content.rstrip("\n") == mine
    N.seed_bundled_snippets()  # 2nd seed still preserves
    assert provider.get_snippet("safety-rules").content.rstrip("\n") == mine
