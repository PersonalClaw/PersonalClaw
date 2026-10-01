"""Local + LAN Ollama detection, the opt-in scan, and the bind route.

Two halves:

* the detection logic (`personalclaw.local_model_detect`): localhost known-true /
  known-false; and for the LAN sweep the safety-critical invariants — it probes ONLY
  private (RFC-1918) addresses, surfaces an endpoint ONLY after a live probe, is
  bounded in both host count and wall-clock, and fabricates nothing when the network
  is silent.
* the route surface (`/api/onboarding/local-model[/scan|/bind]`): the scan is the
  ONLY trigger that runs the sweep (nothing scans on a GET), a scan fault is said as a
  failure and surfaces no endpoints, and the bind refuses any endpoint that is not
  loopback / RFC-1918 before it ever reaches the credential-free seed path.

The credential-free config.json contract itself is pinned in
`test_seed_local_model.py::test_no_credential_is_written_anywhere` — the bind route
delegates to that exact `bind_local_model`, so it inherits the guarantee rather than
re-deriving it here.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import local_model_detect as lmd
from personalclaw import seed_local_model as slm
from personalclaw.dashboard.handlers.local_model import register_local_model_routes
from personalclaw.seed_local_model import ADDED, BOUND, SKIPPED_NO_SERVER, BindResult


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


# ── detection: which model it proposes ────────────────────────────────────────
#
# A machine running a chat model, a vision model and an embedding model, as Ollama itself
# describes them. The vision model is the one pulled LAST, so recency alone proposes it; and
# neither model's name says what the server's record does — that one calls tools and the other
# does not. The chat model the agent should talk to is the one that calls the tools every turn
# offers it.
_SERVED = {
    "qwen2.5vl:7b": (["completion", "vision"], "2026-09-20T00:00:00Z", "qwen25vl"),
    "gemma4:12b": (["completion", "vision", "tools"], "2026-09-01T00:00:00Z", "gemma4"),
    "qwen3-embedding:0.6b": (["embedding"], "2026-08-01T00:00:00Z", "qwen3"),
}


class _ServedHandler(BaseHTTPRequestHandler):
    """A loopback stand-in for an Ollama server: its model list and its record of each model."""

    def _json(self, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self._json(
            {
                "models": [
                    {"name": n, "model": n, "modified_at": at, "details": {"families": [fam]}}
                    for n, (_caps, at, fam) in _SERVED.items()
                ]
            }
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/show":
            self.send_error(404)
            return
        asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        self._json({"capabilities": _SERVED[asked["name"]][0]})

    def log_message(self, *args: object) -> None:
        """Quiet under -q."""


@pytest.fixture
def served_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ServedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def ollama_app_loaded(monkeypatch):
    """The Ollama app as the gateway loads it: its type and its catalog in the registry."""
    from personalclaw.apps.native_contract import NATIVE_DIR
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(llm_registry, "_default_registry", llm_registry.ProviderRegistry())
    spec = importlib.util.spec_from_file_location(
        "pc_test_ollama_proposal", NATIVE_DIR / "ollama-models" / "provider.py"
    )
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_it_proposes_the_chat_model_that_calls_tools_not_the_newer_vision_model(
    served_endpoint, ollama_app_loaded
):
    """🔴 Red before: the newest chat-tagged model won, and that was the vision model, because
    the pick read only the model ids, which say neither model reads images nor which one calls
    tools. The provider app's own listing says both."""
    found = lmd.detect_localhost(served_endpoint)
    assert found is not None
    assert found.model == "gemma4:12b"


def test_without_the_apps_description_the_ids_decide_as_before(served_endpoint, monkeypatch):
    """No loaded app describes the models (a CLI run before the gateway loads its apps): the pick
    falls back to what the ids say, and still never proposes the embedding model."""
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(llm_registry, "_default_registry", llm_registry.ProviderRegistry())
    found = lmd.detect_localhost(served_endpoint)
    assert found is not None
    assert found.model == "qwen2.5vl:7b"


def test_an_endpoint_already_set_up_says_which_instance_it_is(isolated, monkeypatch):
    """A step offering to add a discovered endpoint must be able to say it is added — after a
    reload too — rather than offer it again."""
    monkeypatch.setattr(
        slm,
        "endpoint_models",
        lambda endpoint, **_: slm._from_tags(  # noqa: SLF001
            [{"model": "gemma4:12b", "details": {"families": ["gemma4"]}}]
        ),
    )
    (isolated / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {"name": "bedrock", "type": "bedrock", "options": {"region": "us-west-2"}},
                    {
                        "name": "Local Ollama",
                        "type": "ollama",
                        "options": {"endpoint": "http://localhost:11434/"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    found = lmd.detect_localhost("http://localhost:11434")
    assert found is not None and found.provider == "Local Ollama"
    assert found.to_dict() == {
        "endpoint": "http://localhost:11434",
        "model": "gemma4:12b",
        "provider": "Local Ollama",
    }
    elsewhere = lmd.detect_localhost("http://127.0.0.1:11434")
    assert elsewhere is not None and elsewhere.provider == ""
    assert "provider" not in elsewhere.to_dict()


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
async def test_a_scan_that_fails_says_so_and_guesses_nothing(monkeypatch, isolated):
    """A sweep that did not finish is not an empty network. `{"endpoints": []}` told the step "no
    model server on your network" — the one thing a failed sweep cannot know — and its audit row
    said `ok`. It still offers no partial guess, and the fault's own text stays in the log."""

    def boom(**_):
        raise RuntimeError("interface enumeration blew up")

    monkeypatch.setattr(lmd, "scan_local_network", boom)
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post("/api/onboarding/local-model/scan")
        body = await resp.json()
    assert resp.status == 500
    assert body == {
        "error": {
            "code": "local_model_scan_failed",
            "message": (
                "The network scan could not finish, so it cannot say whether a model server is "
                "on your network. The gateway log says why."
            ),
        }
    }
    rows = [
        json.loads(line)
        for line in (isolated / "security_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    (row,) = [r for r in rows if r.get("operation") == "onboarding.local_model.scan"]
    assert row["outcome"] == "error"


@pytest.mark.asyncio
async def test_bind_refuses_a_public_endpoint(monkeypatch):
    called = {"bind": 0}
    monkeypatch.setattr(
        slm, "bind_local_model", lambda **_: called.__setitem__("bind", called["bind"] + 1)
    )
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://8.8.8.8:11434", "bind_chat": True},
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
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://localhost:11434", "bind_chat": True},
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
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://192.168.1.50:11434", "bind_chat": True},
        )
        assert resp.status == 200
    assert seen["endpoint"] == "http://192.168.1.50:11434"


@pytest.mark.asyncio
async def test_bind_makes_chat_resolvable_without_a_restart(monkeypatch):
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    class FakeModelProvider:
        def complete(self, *args, **kwargs):
            return "ok"

        def stream(self, *args, **kwargs):
            yield "ok"

    registry = llm_registry.ProviderRegistry()
    monkeypatch.setattr(llm_registry, "_default_registry", registry)
    registry.register_type(
        ProviderCapability(
            type=slm.PROVIDER_TYPE,
            capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        ),
        lambda **_: FakeModelProvider(),
    )

    model = "llama3.2:3b"

    def bind_and_persist(*, endpoint):
        slm._write_provider_entry(  # noqa: SLF001 — reproduce the successful bind's writes
            endpoint=endpoint, model=model, embedding_model=""
        )
        slm._write_active_models(model=model, embedding_model="")  # noqa: SLF001
        return BindResult(
            status=BOUND,
            detail="bound",
            endpoint=endpoint,
            model=model,
            provider_name=slm.PROVIDER_ENTRY_NAME,
        )

    monkeypatch.setattr(slm, "bind_local_model", bind_and_persist)
    assert registry.list_entries() == []

    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://localhost:11434", "bind_chat": True},
        )
        assert resp.status == 200

    try:
        provider = resolve_provider_for_use_case("chat", _force_model_axis=True)
    except ProviderResolutionError as exc:
        code = exc.agent_error.code if exc.agent_error else "uncoded"
        pytest.fail(f"successful bind left chat unresolved in this process: {code}")
    assert isinstance(provider, FakeModelProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize(("embedding_model", "reindexes"), [("nomic-embed-text", 1), ("", 0)])
async def test_a_bind_that_binds_embedding_takes_the_reindex_path(
    monkeypatch, embedding_model, reindexes
):
    """🔴 Red before: when the endpoint served an embedding model the one-click bind bound
    Embedding to it and started no re-index, so what was written before stayed read by keyword.
    A bind of chat alone changes no embedding model, and starts nothing."""
    from personalclaw.dashboard.handlers import embedding_reindex
    from personalclaw.llm import registry as llm_registry

    monkeypatch.setattr(llm_registry, "_default_registry", llm_registry.ProviderRegistry())
    scheduled: list[object] = []
    monkeypatch.setattr(embedding_reindex, "schedule_reindex_for_binding", scheduled.append)

    def bind_and_persist(*, endpoint):
        slm._write_provider_entry(  # noqa: SLF001 — reproduce the successful bind's writes
            endpoint=endpoint, model="llama3.2:3b", embedding_model=embedding_model
        )
        slm._write_active_models(  # noqa: SLF001
            model="llama3.2:3b", embedding_model=embedding_model
        )
        return BindResult(
            status=BOUND,
            detail="bound",
            endpoint=endpoint,
            model="llama3.2:3b",
            provider_name=slm.PROVIDER_ENTRY_NAME,
        )

    monkeypatch.setattr(slm, "bind_local_model", bind_and_persist)
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://localhost:11434", "bind_chat": True},
        )
        assert resp.status == 200

    assert len(scheduled) == reindexes


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
            "/api/onboarding/local-model/bind",
            json={"endpoint": "http://localhost:11434", "bind_chat": True},
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
            await c.post(
                "/api/onboarding/local-model/bind", json={"endpoint": "", "bind_chat": True}
            )
        ).status == 400


@pytest.mark.asyncio
async def test_a_bind_that_does_not_say_whether_it_is_the_chat_model_is_refused(monkeypatch):
    """🔴 Red before: the route had one answer — make it the chat model — so a step adding a
    second provider replaced the chat model the user had just chosen."""
    called: list[str] = []
    monkeypatch.setattr(slm, "bind_local_model", lambda **_: called.append("bind"))
    monkeypatch.setattr(slm, "add_local_model", lambda **_: called.append("add"))
    async with TestClient(TestServer(_app())) as c:
        for body in (
            {"endpoint": "http://localhost:11434"},
            {"endpoint": "http://localhost:11434", "bind_chat": "no"},
        ):
            resp = await c.post("/api/onboarding/local-model/bind", json=body)
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "invalid_body"
    assert called == []


@pytest.mark.asyncio
async def test_adding_it_beside_a_chosen_provider_appends_it_and_rebinds_nothing(
    isolated, served_endpoint, ollama_app_loaded, monkeypatch
):
    """🔴 Red before: Essentials could only add a local model AS the chat model. Added beside a
    cloud provider already chosen, it is one more instance, and chat stays what she picked."""
    monkeypatch.setattr(slm, "_installed_provider_app", lambda: True)
    (isolated / "config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {"name": "bedrock", "type": "bedrock", "options": {"region": "us-west-2"}}
                ]
            }
        ),
        encoding="utf-8",
    )
    chosen = {"chat": ["bedrock:global.anthropic.claude-sonnet-5-5"]}
    (isolated / "active_models.json").write_text(json.dumps(chosen), encoding="utf-8")

    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/onboarding/local-model/bind",
            json={"endpoint": served_endpoint, "bind_chat": False},
        )
        assert resp.status == 200, await resp.text()
        body = await resp.json()
        assert body == {
            "ok": True,
            "status": ADDED,
            "model": "gemma4:12b",
            "provider": "Local Ollama",
        }
        # Said as added from then on: a reload shows it set up, not on offer.
        assert lmd.detect_localhost(served_endpoint).provider == "Local Ollama"

    providers = json.loads((isolated / "config.json").read_text(encoding="utf-8"))["providers"]
    assert [p["name"] for p in providers] == ["bedrock", "Local Ollama"]
    assert providers[1]["options"]["endpoint"] == served_endpoint
    assert providers[1]["model"] == "gemma4:12b"
    assert json.loads((isolated / "active_models.json").read_text(encoding="utf-8")) == chosen
