"""Pack export core — dependency-closure + two-layer redaction (AGENT-PACKS §2.1-2.2, AP-1).

Every test binds an ISOLATED home: ``PERSONALCLAW_HOME`` env var AND a patched
``config_dir`` (stores bind config_dir at import; the env var is the robust lever). No test
ever touches the real ``~/.personalclaw``.

The load-bearing security test is ``test_golden_pack_greps_clean_of_canaries``: a fixture
home with canary secrets planted in every component-store shape is packed, and the pack
bytes are grepped — zero canaries may survive.
"""

from __future__ import annotations

import json
import os
import zipfile
from unittest.mock import patch

import pytest

from personalclaw.packs import deny
from personalclaw.packs.build import (
    SCHEMA_VERSION,
    PackSecretBlocked,
    build_pack,
    preview_pack,
)

# A recognisable planted secret string used across the fixtures. AWS-key-shaped so BOTH
# redaction layers fire on it.
CANARY_AWS = "AKIAIOSFODNN7EXAMPLE"
CANARY_ENV = "SLACK_BOT_TOKEN=xoxb-CANARY-9999999999-planted-do-not-ship"
CANARY_LOCAL = "dashboard-auth-token-CANARY-xyz"


@pytest.fixture
def pack_home(tmp_path):
    """An isolated home seeded across every §1 component store, plus secret files that a
    pack must NEVER open (planted with canaries)."""
    home = tmp_path / ".personalclaw"
    home.mkdir()

    # ── skills (skills/<slug>/SKILL.md) ──
    sk = home / "skills" / "cfo-report"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\nname: cfo-report\ndescription: Build a monthly CFO report\n---\n"
        "# CFO Report\nSteps here.\n"
    )
    sk2 = home / "skills" / "cfo-fetch"
    sk2.mkdir(parents=True)
    (sk2 / "SKILL.md").write_text(
        "---\nname: cfo-fetch\ndescription: Fetch statements\n---\n# Fetch\nSteps.\n"
    )

    # ── agent (agents/<slug>/agent.json) referencing a skill ──
    ag = home / "agents" / "cfo"
    ag.mkdir(parents=True)
    (ag / "agent.json").write_text(
        json.dumps(
            {
                "name": "cfo",
                "description": "Personal CFO",
                "system_prompt": "You are a careful finance assistant.",
                "skills": ["cfo-fetch"],
            }
        )
    )

    # ── workflow template (workflows/defs/<name>/workflow.json) referencing agent + skill ──
    tpl_dir = home / "workflows" / "defs" / "cfo-monthly"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "workflow.json").write_text(
        json.dumps(
            {
                "name": "cfo-monthly",
                "version": 1,
                "root": {
                    "kind": "sequence",
                    "children": [
                        {
                            "kind": "stage",
                            "id": "s1",
                            "config": {"agent": "cfo", "skills": ["cfo-report"]},
                        },
                    ],
                },
            }
        )
    )

    # ── prompt (prompts/<slug>.yaml) ──
    prompts = home / "prompts"
    prompts.mkdir()
    (prompts / "cfo-intro.yaml").write_text(
        "name: cfo-intro\nkind: user\ncontent: |\n  Summarize the finances.\n"
    )

    # ── secret / user-data files that must NEVER be opened ──
    (home / ".env").write_text(CANARY_ENV + "\n")
    (home / ".local_secret").write_text(CANARY_LOCAL)
    (home / "sel_hmac.key").write_text("hmac-CANARY-key")
    (home / "session_map.json").write_text(json.dumps({"dashboard:chat": {"sid": CANARY_AWS}}))
    # A fake memory/knowledge db (bytes carrying a canary) — denied by name and by .db.
    (home / "memory.db").write_bytes(b"SQLite format 3\x00" + CANARY_AWS.encode())
    (home / "knowledge.db").write_bytes(b"SQLite format 3\x00" + CANARY_AWS.encode())
    sess = home / "sessions"
    sess.mkdir()
    (sess / "2026.jsonl").write_text(json.dumps({"text": CANARY_AWS}) + "\n")

    return home


@pytest.fixture
def bound_home(pack_home):
    """Bind the isolated home through BOTH levers (env var + patched config_dir)."""
    with patch("personalclaw.packs.build.config_dir", return_value=pack_home):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(pack_home)}):
            yield pack_home


# ── done_when 1: build_pack writes a schema_version=1 .pclaw ZIP ──


def test_build_pack_writes_schema_v1_zip(bound_home, tmp_path):
    out = tmp_path / "cfo.pclaw"
    result = build_pack(["template:cfo-monthly"], name="cfo", version="1.0.0", out_path=out)
    assert result == out
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "pack.json" in names
        manifest = json.loads(zf.read("pack.json"))
    assert manifest["schema_version"] == SCHEMA_VERSION == 1
    assert manifest["name"] == "cfo"
    assert manifest["version"] == "1.0.0"
    assert manifest["provenance"]["content_hash"]  # non-forgeable provenance present


# ── done_when 2: closure walker resolves a multi-hop reference + demotes an unresolvable edge ──


def test_closure_resolves_multi_hop(bound_home):
    # template:cfo-monthly → agent:cfo → skill:cfo-fetch (transitive), plus template →
    # skill:cfo-report (direct edge). All three components must be pulled into the closure.
    preview = preview_pack(["template:cfo-monthly"])
    refs = {c.ref for c in preview.components}
    assert "template:cfo-monthly" in refs
    assert "agent:cfo" in refs
    assert "skill:cfo-report" in refs  # direct edge from the template
    assert "skill:cfo-fetch" in refs  # transitive edge via the agent
    assert not preview.requirements  # everything resolved


def test_unresolvable_edge_demotes_to_requirement(bound_home):
    # A seed naming a component that does not exist becomes a requirements row, not a drop.
    preview = preview_pack(["template:cfo-monthly", "agent:does-not-exist"])
    req_ids = {(r.kind, r.id) for r in preview.requirements}
    assert ("agent", "does-not-exist") in req_ids
    # And the resolvable seed still shipped.
    assert any(c.ref == "template:cfo-monthly" for c in preview.components)


def test_template_missing_agent_edge_becomes_requirement(tmp_path):
    """A template that references an agent slug NOT present demotes that edge, not the template."""
    home = tmp_path / ".personalclaw"
    tpl = home / "workflows" / "defs" / "orphan"
    tpl.mkdir(parents=True)
    (tpl / "workflow.json").write_text(
        json.dumps(
            {
                "name": "orphan",
                "version": 1,
                "root": {"kind": "stage", "id": "s", "config": {"agent": "ghost"}},
            }
        )
    )
    with patch("personalclaw.packs.build.config_dir", return_value=home):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(home)}):
            preview = preview_pack(["template:orphan"])
    assert any(c.ref == "template:orphan" for c in preview.components)
    assert any((r.kind, r.id) == ("agent", "ghost") for r in preview.requirements)


# ── done_when 3: structural layer never opens a secret file ──


def test_structural_layer_never_ships_secret_files(bound_home, tmp_path):
    out = tmp_path / "p.pclaw"
    build_pack(["template:cfo-monthly"], name="p", out_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    # None of the denied files appear anywhere in the archive.
    joined = "\n".join(names)
    for forbidden in (
        ".env",
        ".local_secret",
        "sel_hmac.key",
        "session_map.json",
        "memory.db",
        "knowledge.db",
        "sessions",
    ):
        assert forbidden not in joined, f"{forbidden} leaked into pack member list"


def test_deny_is_fail_closed():
    # Positive denials.
    for p in (
        ".env",
        ".local_secret",
        "sel_hmac.key",
        "telemetry_salt",
        "session_map.json",
        "memory.db",
        "knowledge.db",
        "learning.db",
        "memory.db-wal",
        "memory.db-shm",
        "sessions/2026.jsonl",
        "workspace/notes.md",
        "cron-history/j.jsonl",
        "runs.db",
        "",
        "   ",
    ):
        assert deny.is_denied(p), f"{p!r} should be denied"
    # Allowed component-store paths.
    for p in (
        "skills/foo/SKILL.md",
        "workflows/defs/x/workflow.json",
        "prompts/p.yaml",
        "agents/a/agent.json",
    ):
        assert not deny.is_denied(p), f"{p!r} should be allowed"


def test_denied_store_reader_returns_none(bound_home):
    """A reader asked for a denied path never opens it — even if the file exists."""
    from personalclaw.packs.build import _read_denied_safe

    assert _read_denied_safe(bound_home, ".env") is None
    assert _read_denied_safe(bound_home, "memory.db") is None
    # An allowed, existing component file DOES read.
    assert _read_denied_safe(bound_home, "prompts/cfo-intro.yaml") is not None


# ── done_when 4: a credential-bearing component is BLOCKED ──


def test_component_with_planted_credential_is_blocked(tmp_path):
    home = tmp_path / ".personalclaw"
    sk = home / "skills" / "leaky"
    sk.mkdir(parents=True)
    # A skill whose PROSE carries an AWS key — a real authoring mistake.
    (sk / "SKILL.md").write_text(
        f"---\nname: leaky\ndescription: leaks\n---\n# Leaky\nUse key {CANARY_AWS} to auth.\n"
    )
    with patch("personalclaw.packs.build.config_dir", return_value=home):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(home)}):
            preview = preview_pack(["skill:leaky"])
            assert preview.has_blocking
            assert any(b.ref == "skill:leaky" for b in preview.blocked)
            assert not preview.components  # the leaky component never shipped
            # build_pack REFUSES rather than shipping a pack missing the requested component.
            with pytest.raises(PackSecretBlocked):
                build_pack(["skill:leaky"], name="leaky", out_path=home / "x.pclaw")
    assert not (home / "x.pclaw").exists()  # nothing written on a refused build


# ── done_when 5: preview renders before write (no file written on preview) ──


def test_preview_writes_nothing(bound_home, tmp_path):
    out = tmp_path / "should-not-exist.pclaw"
    preview = preview_pack(["template:cfo-monthly"], name="cfo", version="2.0.0")
    assert preview.components  # a real plan was computed
    assert not out.exists()
    # The default output location is untouched too.
    assert not (bound_home / "packs").exists()
    # And the tree renders the closure + requirements + blocked sections.
    tree = preview.tree()
    assert "components" in tree and "requirements" in tree and "blocked" in tree


# ── done_when 6: golden-pack round-trip greps clean of planted canaries ──


def test_golden_pack_greps_clean_of_canaries(bound_home, tmp_path):
    """The existential test: pack a home riddled with canary secrets, then grep the pack
    bytes — zero canaries may survive (structural layer never opened the secret files)."""
    out = tmp_path / "golden.pclaw"
    build_pack(["template:cfo-monthly", "prompt:cfo-intro"], name="golden", out_path=out)

    raw = out.read_bytes()
    # Grep the compressed archive AND every decompressed member — a canary must appear in
    # neither.
    for canary in (CANARY_AWS, CANARY_ENV, CANARY_LOCAL, "xoxb-CANARY", "hmac-CANARY"):
        assert canary.encode() not in raw, f"{canary} present in raw pack bytes"

    with zipfile.ZipFile(out) as zf:
        for member in zf.namelist():
            data = zf.read(member)
            for canary in (CANARY_AWS, CANARY_ENV, CANARY_LOCAL, "xoxb-CANARY", "hmac-CANARY"):
                assert canary.encode() not in data, f"{canary} present in member {member}"
        # Positive: the legitimate components DID make it in.
        manifest = json.loads(zf.read("pack.json"))
    kinds = {c["kind"] for c in manifest["components"]}
    assert {"template", "prompt", "agent", "skill"} <= kinds


def test_component_sha_matches_written_bytes(bound_home, tmp_path):
    """Provenance integrity: each manifest sha256 re-derives from the written member bytes."""
    import hashlib

    out = tmp_path / "p.pclaw"
    build_pack(["template:cfo-monthly"], name="p", out_path=out)
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("pack.json"))
        for comp in manifest["components"]:
            data = zf.read(comp["path"])
            assert hashlib.sha256(data).hexdigest() == comp["sha256"]
