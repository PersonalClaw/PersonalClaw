"""OU-13 — local + LAN Ollama detection, the opt-in scan, and the bind route.

Two halves:

* the detection logic (`personalclaw.local_model_detect`): localhost known-true /
  known-false; and for the LAN sweep the safety-critical invariants — it probes ONLY
  private (RFC-1918) addresses, surfaces an endpoint ONLY after a live probe, is
  bounded in both host count and wall-clock, and fabricates nothing when the network
  is silent.
* the route surface (`/api/onboarding/local-model[/scan|/bind]`): the scan is the
  ONLY trigger that runs the sweep (nothing scans on a GET), a scan fault surfaces no
  endpoints, and the bind refuses any endpoint that is not loopback / RFC-1918 before
  it ever reaches the credential-free seed path.

The credential-free config.json contract itself is pinned in
`test_seed_local_model.py::test_no_credential_is_written_anywhere` — the bind route
delegates to that exact `bind_local_model`, so it inherits the guarantee rather than
re-deriving it here.
"""

from __future__ import annotations

import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import local_model_detect as lmd
from personalclaw import seed_local_model as slm
from personalclaw.dashboard.handlers.local_model import register_local_model_routes
from personalclaw.seed_local_model import BOUND, SKIPPED_NO_SERVER, BindResult


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep the SEL audit log the scan/bind routes write inside a throwaway home."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    yield tmp_path


# ── detection: localhost ──────────────────────────────────────────────────────


def test_detect_localhost_none_when_unreachable(monkeypatch):
    monkeypatch.setattr(slm, "_probe_models", lambda endpoint, *, timeout=0.0: None)
    assert lmd.detect_localhost() is None


def test_detect_localhost_none_when_reachable_but_no_model(monkeypatch):
    # Reachable but nothing pulled → nothing to bind → NOT detected (no card).
    monkeypatch.setattr(slm, "_probe_models", lambda endpoint, *, timeout=0.0: [])
    assert lmd.detect_localhost() is None


def test_detect_localhost_returns_chat_model(monkeypatch):
    monkeypatch.setattr(
        slm,
        "_probe_models",
        lambda endpoint, *, timeout=0.0: [
            {
                "model": "llama3.2:3b",
                "modified_at": "2026-01-02T00:00:00Z",
                "details": {"families": ["llama"]},
            },
        ],
    )
    got = lmd.detect_localhost()
    assert got is not None
    assert got.endpoint == lmd.DEFAULT_ENDPOINT
    assert got.model == "llama3.2:3b"


# ── detection: the LAN sweep safety rails ──────────────────────────────────────


def test_scan_probes_only_private_addresses():
    probed: list[str] = []

    def prober(endpoint: str) -> str | None:
        probed.append(endpoint)
        return None

    lmd.scan_local_network(
        candidates=["192.168.1.10", "10.0.0.5", "8.8.8.8", "127.0.0.1", "169.254.1.1"],
        prober=prober,
    )
    # Only the two RFC-1918 privates are contacted; public, loopback and link-local
    # are dropped BEFORE a connection is opened.
    assert sorted(probed) == ["http://10.0.0.5:11434", "http://192.168.1.10:11434"]


def test_scan_returns_only_live_probed_endpoints():
    def prober(endpoint: str) -> str | None:
        return "qwen2.5:0.5b" if "192.168.1.10" in endpoint else None

    found = lmd.scan_local_network(candidates=["192.168.1.10", "10.0.0.5"], prober=prober)
    assert [e.endpoint for e in found] == ["http://192.168.1.10:11434"]
    assert found[0].model == "qwen2.5:0.5b"


def test_scan_empty_when_nothing_answers():
    found = lmd.scan_local_network(candidates=["192.168.1.10", "10.0.0.5"], prober=lambda e: None)
    assert found == []


def test_scan_no_candidates_returns_empty():
    assert lmd.scan_local_network(candidates=[], prober=lambda e: None) == []


def test_scan_respects_max_hosts():
    probed: list[str] = []
    cands = [f"10.0.0.{i}" for i in range(1, 40)]

    def prober(endpoint: str) -> str | None:
        probed.append(endpoint)
        return None

    lmd.scan_local_network(candidates=cands, prober=prober, max_hosts=5)
    assert len(probed) == 5


def test_scan_is_time_bounded():
    def slow(endpoint: str) -> str | None:
        time.sleep(0.6)
        return "m"

    start = time.monotonic()
    found = lmd.scan_local_network(candidates=["192.168.1.10"], prober=slow, budget_secs=0.1)
    elapsed = time.monotonic() - start
    # The whole-sweep wall clock cut the slow probe off — the call returns fast and a
    # black-holed host cannot hang a first-run wizard step.
    assert elapsed < 0.5
    assert found == []


def test_candidate_hosts_excludes_own_network_and_broadcast():
    hosts = lmd._candidate_hosts(["192.168.1.10"], max_hosts=300)  # noqa: SLF001 — unit under test
    assert "192.168.1.10" not in hosts  # our own address is not probed
    assert "192.168.1.0" not in hosts and "192.168.1.255" not in hosts  # network / broadcast
    assert all(h.startswith("192.168.1.") for h in hosts)
    assert len(hosts) == 253  # a /24 has 254 usable hosts, minus our own


# ── the route surface ──────────────────────────────────────────────────────────


def _app() -> web.Application:
    app = web.Application()
    register_local_model_routes(app)
    return app


@pytest.mark.asyncio
async def test_get_reports_not_detected(monkeypatch):
    monkeypatch.setattr(lmd, "detect_localhost", lambda: None)
    async with TestClient(TestServer(_app())) as c:
        body = await (await c.get("/api/onboarding/local-model")).json()
    assert body == {"detected": False}


@pytest.mark.asyncio
async def test_get_reports_detected_endpoint(monkeypatch):
    monkeypatch.setattr(
        lmd,
        "detect_localhost",
        lambda: lmd.DetectedEndpoint("http://localhost:11434", "llama3.2:3b"),
    )
    async with TestClient(TestServer(_app())) as c:
        body = await (await c.get("/api/onboarding/local-model")).json()
    assert body == {"detected": True, "endpoint": "http://localhost:11434", "model": "llama3.2:3b"}


@pytest.mark.asyncio
async def test_scan_is_the_only_scan_trigger(monkeypatch):
    """A GET performs a loopback probe only; the outbound sweep runs solely on POST."""
    ran = {"scan": 0}

    def fake_scan(**_):
        ran["scan"] += 1
        return [lmd.DetectedEndpoint("http://192.168.1.50:11434", "qwen2.5:0.5b")]

    monkeypatch.setattr(lmd, "detect_localhost", lambda: None)
    monkeypatch.setattr(lmd, "scan_local_network", fake_scan)
    async with TestClient(TestServer(_app())) as c:
        await c.get("/api/onboarding/local-model")
        assert ran["scan"] == 0  # the GET never scans the network
        body = await (await c.post("/api/onboarding/local-model/scan")).json()
    assert ran["scan"] == 1
    assert body == {
        "endpoints": [{"endpoint": "http://192.168.1.50:11434", "model": "qwen2.5:0.5b"}]
    }


@pytest.mark.asyncio
async def test_scan_fault_surfaces_no_endpoints(monkeypatch):
    def boom(**_):
        raise RuntimeError("interface enumeration blew up")

    monkeypatch.setattr(lmd, "scan_local_network", boom)
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post("/api/onboarding/local-model/scan")
        assert resp.status == 200
        assert await resp.json() == {"endpoints": []}  # fail closed, never a partial guess


@pytest.mark.asyncio
async def test_bind_refuses_a_public_endpoint(monkeypatch):
    called = {"bind": 0}
    monkeypatch.setattr(
        slm, "bind_local_model", lambda **_: called.__setitem__("bind", called["bind"] + 1)
    )
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind", json={"endpoint": "http://8.8.8.8:11434"}
        )
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == "local_model_endpoint_invalid"
    assert called["bind"] == 0  # never reaches the bind path for a public URL


@pytest.mark.asyncio
async def test_bind_accepts_localhost_and_reports_the_model(monkeypatch):
    monkeypatch.setattr(
        slm,
        "bind_local_model",
        lambda **kw: BindResult(
            status=BOUND,
            detail="bound",
            endpoint=kw["endpoint"],
            model="llama3.2:3b",
            provider_name="Local Ollama",
        ),
    )
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind", json={"endpoint": "http://localhost:11434"}
        )
        assert resp.status == 200
        body = await resp.json()
    assert body == {"ok": True, "status": BOUND, "model": "llama3.2:3b", "provider": "Local Ollama"}


@pytest.mark.asyncio
async def test_bind_accepts_a_private_lan_endpoint(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        slm,
        "bind_local_model",
        lambda **kw: seen.update(kw)
        or BindResult(
            status=BOUND,
            detail="bound",
            endpoint=kw["endpoint"],
            model="qwen2.5:0.5b",
            provider_name="Local Ollama",
        ),
    )
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind", json={"endpoint": "http://192.168.1.50:11434"}
        )
        assert resp.status == 200
    assert seen["endpoint"] == "http://192.168.1.50:11434"


@pytest.mark.asyncio
async def test_bind_reports_a_skip_as_a_failure_with_its_reason(monkeypatch):
    monkeypatch.setattr(
        slm,
        "bind_local_model",
        lambda **kw: BindResult(
            status=SKIPPED_NO_SERVER, detail="no Ollama answered", endpoint=kw["endpoint"]
        ),
    )
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind", json={"endpoint": "http://localhost:11434"}
        )
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == "local_model_bind_failed"
    assert "no Ollama answered" in body["error"]["message"]


@pytest.mark.asyncio
async def test_bind_rejects_a_bad_body():
    async with TestClient(TestServer(_app())) as c:
        assert (await c.post("/api/onboarding/local-model/bind", data="not json")).status == 400
        assert (
            await c.post("/api/onboarding/local-model/bind", json={"endpoint": ""})
        ).status == 400
