"""Doctor framework doctrine + probe tests (PLATFORM-RESILIENCE §1).

The doctrine (§1.3) is the whole point of the tier ladder, so it is pinned here as
executable invariants:

* a tier-3 capability failure degrades ONLY that capability — never ``core_ok``,
  never ``restart_suggested`` (§1.3.1);
* a tier-2 (cheap-RPC) failure short-circuits the tier-3 packs and IS the only
  failure that suggests a restart;
* a probe that raises becomes an ``ok=False`` row, never a 500 (the AUTO-R15
  framework invariant);
* secrets are masked out of ``detail``/``evidence`` before they leave a probe.

Success-criterion #1 (gateway healthy, a capability dead → core OK, that
capability failed at tier 3, no restart) is asserted directly.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import (
    DoctorContext,
    Probe,
    ProbeResult,
    Tier,
    run_capability,
    run_doctor,
)

# ── probe builders (tiny injected probes; the framework is tested in isolation) ──


def _ok(cap: str, tier: Tier, detail: str = "ok") -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        return ProbeResult(ok=True, detail=detail)

    return Probe(f"{cap}.ok.{int(tier)}", cap, tier, _run, f"{cap} ok")


def _fail(cap: str, tier: Tier, detail: str = "boom") -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        return ProbeResult(ok=False, detail=detail)

    return Probe(f"{cap}.fail.{int(tier)}", cap, tier, _run, f"{cap} fail")


def _raiser(cap: str, tier: Tier, exc: Exception) -> Probe:
    async def _run(ctx: DoctorContext) -> ProbeResult:
        raise exc

    return Probe(f"{cap}.raise.{int(tier)}", cap, tier, _run, f"{cap} raise")


# ── the doctrine ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capability_failure_never_marks_core_or_suggests_restart():
    """§1.3.1 — a tier-3 capability failure degrades ONLY that capability's row.

    Success-criterion #1: gateway healthy (core tiers pass) but a capability dead →
    core_ok stays True, restart is NOT suggested, only that capability is 'worst'.
    """
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _fail("local-models", Tier.CAPABILITY, "ollama dead; HF cache wiped"),
        _ok("memory", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is True  # doctrine: capability failure ≠ core failure
    assert rep["restart_suggested"] is False  # a capability never justifies restart
    assert rep["ok"] is False  # overall not-ok (a capability is down)...
    assert rep["worst"] == "local-models"  # ...and it's named
    assert rep["capabilities"]["local-models"]["ok"] is False
    assert rep["capabilities"]["memory"]["ok"] is True
    assert rep["skipped_capabilities"] == []  # core healthy → nothing skipped


@pytest.mark.asyncio
async def test_cheap_rpc_failure_short_circuits_and_suggests_restart():
    """A tier-2 failure is a CORE failure: tier-3 packs are skipped and restart is
    the (only) suggested remedy."""
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _fail("core", Tier.CHEAP_RPC, "status snapshot unreadable"),
        _ok("memory", Tier.CAPABILITY),  # must NOT run
        _fail("local-models", Tier.CAPABILITY),  # must NOT run
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is False
    assert rep["restart_suggested"] is True  # cheap-RPC failure is the restart trigger
    # tier-3 packs were skipped, not run:
    assert "memory" not in rep["capabilities"]
    assert "local-models" not in rep["capabilities"]
    assert set(rep["skipped_capabilities"]) == {"memory", "local-models"}


@pytest.mark.asyncio
async def test_socket_failure_short_circuits_without_restart_flag():
    """A tier-1 (socket) failure is a core failure and short-circuits, but only the
    cheap-RPC tier sets restart_suggested — a socket failure does not."""
    probes = [
        _ok("core", Tier.PROCESS),
        _fail("core", Tier.SOCKET, "port not connectable"),
        _ok("core", Tier.CHEAP_RPC),  # not reached
        _ok("memory", Tier.CAPABILITY),  # skipped
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)

    assert rep["core_ok"] is False
    assert rep["restart_suggested"] is False  # only cheap-RPC failure flags restart
    assert rep["skipped_capabilities"] == ["memory"]


@pytest.mark.asyncio
async def test_probe_that_raises_becomes_ok_false_never_propagates():
    """The AUTO-R15 framework invariant: a probe exception is an ok=False row, not a
    raised error — run_doctor must never propagate a probe's bug."""
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _raiser("memory", Tier.CAPABILITY, RuntimeError("db handle exploded")),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)  # must not raise

    row = rep["capabilities"]["memory"]["probes"][0]
    assert row["ok"] is False
    assert "RuntimeError" in row["detail"]
    assert rep["core_ok"] is True  # a capability probe raising still isn't core


@pytest.mark.asyncio
async def test_secrets_masked_in_detail_and_evidence():
    """A credential in a probe's human output is run through ``redact()`` before it
    leaves the probe. Uses an AWS-key-shaped token — a pattern ``redact()``
    genuinely masks — to prove the masking is actually wired (not that redact()
    covers every shape; that's security.py's contract, not the Doctor's)."""

    async def _leaky(ctx: DoctorContext) -> ProbeResult:
        secret = "AKIA" + "B" * 16  # AWS access-key shape → redact() masks it
        return ProbeResult(ok=False, detail=f"auth failed with {secret}")

    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        Probe("model-providers.leak", "model-providers", Tier.CAPABILITY, _leaky, "leaky"),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)
    detail = rep["capabilities"]["model-providers"]["probes"][0]["detail"]
    assert "AKIABBBB" not in detail  # the raw credential must be gone
    assert "REDACTED" in detail  # ...replaced by the redaction marker


@pytest.mark.asyncio
async def test_all_healthy_report_is_ok():
    probes = [
        _ok("core", Tier.PROCESS),
        _ok("core", Tier.SOCKET),
        _ok("core", Tier.CHEAP_RPC),
        _ok("memory", Tier.CAPABILITY),
    ]
    rep = await run_doctor(DoctorContext(), probes=probes)
    assert rep["ok"] is True and rep["core_ok"] is True
    assert rep["worst"] == "" and rep["restart_suggested"] is False


# ── run_capability (the single-card re-probe) ────────────────────────────────


@pytest.mark.asyncio
async def test_run_capability_unknown_is_flagged():
    result = await run_capability("nope", DoctorContext())
    assert result["unknown"] is True and result["ok"] is True and result["probes"] == []


@pytest.mark.asyncio
async def test_run_capability_runs_only_that_capability(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_PROBES",
        [_ok("memory", Tier.CAPABILITY), _fail("channels", Tier.CAPABILITY)],
    )
    result = await run_capability("channels", DoctorContext())
    assert result["capability"] == "channels" and result["ok"] is False
    assert [p["capability"] for p in result["probes"]] == ["channels"]


# ── the real probe packs (against an isolated tmp home) ──────────────────────


@pytest.mark.asyncio
async def test_builtin_probes_registered():
    ids = {p.id for p in doctor.all_probes()}
    assert {
        "gateway.process",
        "gateway.socket",
        "gateway.status",
        "memory.store",
        "channels.transports",
        "local-models.providers",
        "apps.backends",
        "serving-fs.dist",
        "model-providers.health",
    } <= ids


@pytest.mark.asyncio
async def test_socket_probe_skips_when_no_port():
    """A standalone doctor run with no bound port is not a socket failure."""
    res = await doctor._probe_gateway_socket(DoctorContext(port=0))
    assert res.ok is True and "no gateway port" in res.detail


@pytest.mark.asyncio
async def test_memory_probe_fresh_home_is_ok(tmp_path, monkeypatch):
    """No memory.db yet (fresh install) → ok with a 'fresh install' note, not a fail."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    res = await doctor._probe_memory(DoctorContext(home=tmp_path))
    assert res.ok is True
    assert res.evidence["db_present"] is False


@pytest.mark.asyncio
async def test_memory_probe_detects_faiss_desync(tmp_path):
    """faiss ids.json count disagreeing with embedded row count → a failed row."""
    import json
    import sqlite3

    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE episodic_memories (id TEXT, is_deleted INTEGER, embedding BLOB)")
    # two embedded, non-deleted rows
    conn.execute("INSERT INTO episodic_memories VALUES ('a', 0, X'00')")
    conn.execute("INSERT INTO episodic_memories VALUES ('b', 0, X'00')")
    conn.commit()
    conn.close()
    # faiss sidecar claims only ONE indexed id → desync
    (tmp_path / "memory.ids.json").write_text(json.dumps(["a"]), encoding="utf-8")

    res = await doctor._probe_memory(DoctorContext(home=tmp_path))
    assert res.ok is False
    assert "desync" in res.detail
    assert res.evidence["embedded_count"] == 2 and res.evidence["faiss_ids"] == 1


@pytest.mark.asyncio
async def test_serving_fs_probe_flags_copy_shadowing_symlink(tmp_path, monkeypatch):
    """A real-directory static/dist copy (not a symlink) is the stale-SPA bug-class
    and must be flagged."""
    import personalclaw

    # Point the probe's package-dir resolution at a fake pkg with a COPY dist.
    fake_pkg = tmp_path / "pkg"
    (fake_pkg / "static" / "dist").mkdir(parents=True)
    (fake_pkg / "static" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(personalclaw, "__file__", str(fake_pkg / "__init__.py"))

    res = await doctor._probe_serving_fs(DoctorContext(home=tmp_path))
    assert res.ok is False
    assert res.evidence["dist"]["kind"] == "copy"
    assert "stale SPA" in res.detail
