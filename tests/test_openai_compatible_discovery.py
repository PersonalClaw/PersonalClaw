"""#955 — the openai-compatible provider discovers the models its endpoint lists,
and says so out loud when it cannot.

Two defects, one file, both driven against a REAL ``http.server`` on loopback through the
REAL ``net.fetch`` egress chokepoint — no mocked HTTP client, so the URL under test is the
URL a server actually receives:

1. **The base-URL join.** ``if not base.endswith("/v1"): base += "/v1"`` appended a second
   version segment to every OpenAI-compatible base that spells its version any other way.
   ``https://api.z.ai/api/coding/paas/v4`` was fetched as ``…/paas/v4/v1/models`` → 404 →
   zero models, for an endpoint serving a textbook OpenAI list.
2. **The swallowed failure.** Blocked, unreachable, 401, 404 and non-JSON all returned
   ``[]`` — the same value as "this endpoint serves no models" — at every log level. The
   user's only symptom was an empty picker.

Every case asserts the path the server was ASKED for as well as the models returned, so a
test cannot pass without a request having happened (the vacuity floor for the URL half),
and every payload carries case-unique ids, so an implementation that hardcodes a model or
returns a constant list fails somewhere (the floor for the parse half).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from personalclaw.llm.catalog import (
    ModelDiscoveryError,
    openai_compatible_discover_models,
    openai_compatible_list_models,
    openai_compatible_models_url,
)


def _run(coro):
    return asyncio.run(coro)


# ── A real loopback server ────────────────────────────────────────────────────


class _Endpoint:
    """A real HTTP server that serves ONE models path and 404s everything else.

    404-on-anything-else is load-bearing: it is how a wrong URL is punished here exactly
    as MiniMax/z.ai punish it in production, instead of a permissive mock answering
    whatever path the code happens to build.
    """

    def __init__(self, models_path: str, body: str, *, status: int = 200) -> None:
        self.requested: list[str] = []
        outer = self

        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
                outer.requested.append(self.path)
                if self.path == models_path:
                    outer._write(self, status, body)
                else:
                    outer._write(self, 404, json.dumps({"error": {"message": "not found"}}))

            def log_message(self, *args: object) -> None:  # keep pytest output clean
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    @staticmethod
    def _write(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
        raw = body.encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def allow_loopback_egress(monkeypatch: pytest.MonkeyPatch):
    """Discovery against a loopback endpoint, as an operator who set
    ``security.egress.allow_private`` sees it.

    Only the POLICY is substituted, and only in the one direction an operator can already
    take themselves. ``net.fetch``, ``guard.evaluate``, host classification, IP pinning and
    the redirect re-check all still run for real — the point of driving a real socket.
    """
    from personalclaw.net import CONNECTOR

    monkeypatch.setattr(
        "personalclaw.sdk.net.egress_policy_for",
        lambda base: CONNECTOR.with_overrides(allow_private=True),
    )


# ── 1. The base-URL join ──────────────────────────────────────────────────────

# (case, the path the SERVER serves, the endpoint an operator configures)
# Every row is a base shape a real OpenAI-compatible vendor ships. The `served path` and
# the `configured endpoint` are independent inputs: the code passes only by deriving the
# former from the latter.
_BASE_SHAPES = [
    # The two that worked before #955 — the regression floor.
    ("openai-style /v1", "/v1/models", "/v1"),
    ("kimi-style /coding/v1", "/coding/v1/models", "/coding/v1"),
    # A bare host: the property `endswith("/v1")` existed to protect. Serving ONLY
    # /v1/models is what makes an implementation that stopped appending /v1 fail here.
    ("bare host, no version", "/v1/models", ""),
    # #955's two failures. Serving the version-correct path means an implementation that
    # appends /v1 unconditionally fails here.
    ("z.ai-style /api/coding/paas/v4", "/api/coding/paas/v4/models", "/api/coding/paas/v4"),
    ("gemini-shim-style /v1beta/openai", "/v1beta/openai/models", "/v1beta/openai"),
    # A trailing slash is an operator typo, not a different endpoint.
    ("trailing slash", "/v1/models", "/v1/"),
]


@pytest.mark.parametrize(
    ("case", "served_path", "suffix"), _BASE_SHAPES, ids=[r[0] for r in _BASE_SHAPES]
)
def test_every_openai_compatible_base_shape_discovers_its_models(
    case: str, served_path: str, suffix: str, allow_loopback_egress: None
) -> None:
    # A case-unique id: a hardcoded or constant model list cannot satisfy all six rows.
    model_id = f"Model-For-{case.replace(' ', '-').replace('/', '-')}"
    payload = json.dumps(
        {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "vendor"}]}
    )
    srv = _Endpoint(served_path, payload)
    try:
        got = _run(openai_compatible_discover_models(f"{srv.base}{suffix}", "sk-test"))
        assert [m.id for m in got] == [model_id], f"{case}: wrong models discovered"
        # The vacuity floor for the URL half: the server must have been asked the path it
        # serves. Without this a test could pass on an empty list from a request never made.
        assert srv.requested == [
            served_path
        ], f"{case}: asked for {srv.requested}, want {served_path}"
    finally:
        srv.close()


def test_the_models_url_is_derived_not_guessed() -> None:
    """The pure URL derivation, so the rule is readable without a socket."""
    assert openai_compatible_models_url("https://h/v1") == "https://h/v1/models"
    assert openai_compatible_models_url("https://h/api/coding/paas/v4") == (
        "https://h/api/coding/paas/v4/models"
    )
    assert (
        openai_compatible_models_url("https://h/v1beta/openai") == "https://h/v1beta/openai/models"
    )
    # No version segment anywhere → /v1 is supplied (the property being preserved).
    assert openai_compatible_models_url("https://h") == "https://h/v1/models"
    assert openai_compatible_models_url("https://h/openai") == "https://h/openai/v1/models"
    # A segment that merely STARTS with "v" is not a version — a self-hosted vLLM mount
    # point still gets its /v1, which is the failure mode of a looser regex.
    assert openai_compatible_models_url("https://h/vllm") == "https://h/vllm/v1/models"
    # An empty endpoint falls back to the caller's default base, unchanged.
    assert openai_compatible_models_url("", default_base="https://d/v1") == "https://d/v1/models"


# ── 2. "0 models" and "I could not reach it" are different answers ────────────

# (case, status, body, the fragments the user-facing sentence must carry)
_FAILURE_SHAPES = [
    ("401 rejected key", 401, '{"error":{"message":"invalid api key"}}', ("401", "API key")),
    ("403 forbidden", 403, '{"error":{"message":"forbidden"}}', ("403", "API key")),
    ("500 upstream", 500, '{"error":{"message":"boom"}}', ("500",)),
    ("200 but not JSON", 200, "<html>sign in</html>", ("not JSON", "endpoint")),
    (
        "200 but not a model list",
        200,
        '{"object":"list","models":[{"id":"x"}]}',
        ("OpenAI-shaped",),
    ),
    (
        "200 but no ids",
        200,
        '{"object":"list","data":[{"object":"model"},{"object":"model"}]}',
        ("2 entries", "id"),
    ),
]


@pytest.mark.parametrize(
    ("case", "status", "body", "fragments"), _FAILURE_SHAPES, ids=[r[0] for r in _FAILURE_SHAPES]
)
def test_a_discovery_failure_says_what_happened_and_what_to_do(
    case: str, status: int, body: str, fragments: tuple[str, ...], allow_loopback_egress: None
) -> None:
    srv = _Endpoint("/v1/models", body, status=status)
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            _run(openai_compatible_discover_models(f"{srv.base}/v1", "sk-test"))
    finally:
        srv.close()
    msg = str(caught.value)
    # Vacuity floor: an implementation that raised a bare/empty error, or one generic
    # sentence for all six, fails — each row names its own cause and a next action.
    for fragment in fragments:
        assert fragment in msg, f"{case}: {fragment!r} missing from {msg!r}"
    assert caught.value.url.endswith("/v1/models"), f"{case}: the URL tried must be named"
    assert "sk-test" not in msg, f"{case}: the credential must never appear in the message"
    # A traceback is not the convention; a raw exception class name is not an instruction.
    assert "Traceback" not in msg


def test_a_wrong_base_url_names_the_404_and_the_base_url(allow_loopback_egress: None) -> None:
    """#955's z.ai symptom, as it looked BEFORE the join was fixed: a 404 must read as a
    404 with the URL that produced it, not as an empty catalog."""
    srv = _Endpoint("/v1/models", '{"object":"list","data":[{"id":"served"}]}')
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            _run(openai_compatible_discover_models(f"{srv.base}/api/v9/nope", "sk-test"))
    finally:
        srv.close()
    assert caught.value.status == 404
    assert "404" in str(caught.value) and "/api/v9/nope/models" in str(caught.value)


def test_an_unreachable_endpoint_is_not_an_empty_catalog(allow_loopback_egress: None) -> None:
    # Port 1 on loopback: nothing listens, so this exercises the transport-failure arm
    # without a network call leaving the machine.
    with pytest.raises(ModelDiscoveryError) as caught:
        _run(openai_compatible_discover_models("http://127.0.0.1:1/v1", "sk-test"))
    assert "Could not reach" in str(caught.value)
    assert caught.value.status is None, "no HTTP status was ever received"


def test_a_blocked_host_says_how_to_allow_list_it() -> None:
    """No egress fixture here on purpose: the default public-only posture must refuse a
    loopback endpoint with an instruction, not with an empty list."""
    with pytest.raises(ModelDiscoveryError) as caught:
        _run(openai_compatible_discover_models("http://127.0.0.1:9/v1", "sk-test"))
    msg = str(caught.value)
    assert "Egress policy blocked" in msg
    assert "allow_private" in msg or "allow_hosts" in msg


def test_an_unconfigured_provider_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(ModelDiscoveryError) as caught:
        _run(openai_compatible_discover_models(None, None))
    assert "endpoint" in str(caught.value)


# ── An endpoint that genuinely lists nothing is NOT a failure ─────────────────


def test_an_empty_but_valid_list_is_an_honest_zero(allow_loopback_egress: None) -> None:
    """The one case that must stay ``[]``: the endpoint answered, correctly, with no
    models. Vacuity floor for the whole failure half — an implementation that raised on
    everything, or that returned a non-empty list unconditionally, fails here."""
    srv = _Endpoint("/v1/models", '{"object":"list","data":[]}')
    try:
        assert _run(openai_compatible_discover_models(f"{srv.base}/v1", "sk-test")) == []
        assert srv.requested == ["/v1/models"]
    finally:
        srv.close()


def test_unknown_fields_and_extra_envelope_keys_are_ignored_not_fatal(
    allow_loopback_egress: None,
) -> None:
    """A compliant server may carry vendor extras (MiniMax's ``base_resp``, per-model
    ``created``/``permission``/anything). None of them may cost a model."""
    srv = _Endpoint(
        "/v1/models",
        json.dumps(
            {
                "object": "list",
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "data": [
                    {
                        "id": "MiniMax-M2.5",
                        "object": "model",
                        "created": 1770948000,
                        "owned_by": "minimax",
                        "permission": [],
                        "some_future_field": {"nested": True},
                    },
                    {"id": "MiniMax-M3", "object": "model"},
                ],
            }
        ),
    )
    try:
        got = _run(openai_compatible_discover_models(f"{srv.base}/v1", "sk-test"))
    finally:
        srv.close()
    assert [m.id for m in got] == ["MiniMax-M2.5", "MiniMax-M3"]
    assert got[0].extra == {"owned_by": "minimax"}
    assert got[1].extra == {}, "an absent owned_by must not invent one"
    assert "chat" in got[0].capabilities


# ── The fail-soft wrapper still degrades, but no longer in silence ────────────


def test_the_fail_soft_wrapper_returns_empty_and_logs_at_warning(
    allow_loopback_egress: None, caplog: pytest.LogCaptureFixture
) -> None:
    """``openai_compatible_list_models`` keeps its ``[]``-on-failure contract for the
    callers that need it (``OpenAIProvider.start``'s default-model resolution), but the
    reason is now on the record at WARNING — the specific thing #955 could not find at any
    log level."""
    srv = _Endpoint("/v1/models", '{"error":{"message":"nope"}}', status=401)
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.llm.catalog"):
            assert _run(openai_compatible_list_models(f"{srv.base}/v1", "sk-test")) == []
    finally:
        srv.close()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a discovery that yielded nothing must be visible at WARNING"
    logged = warnings[0].getMessage()
    # Vacuity floor: a bare "discovery failed" line would satisfy "logged something" while
    # leaving the user exactly as stuck. The status and the URL are the actionable part.
    assert "401" in logged and "/v1/models" in logged


def test_the_fail_soft_wrapper_logs_nothing_on_success(
    allow_loopback_egress: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The floor under the previous test: WARNING means something went wrong. A healthy
    discovery that also warned would train the user to ignore the line."""
    srv = _Endpoint("/v1/models", '{"object":"list","data":[{"id":"fine"}]}')
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.llm.catalog"):
            assert [m.id for m in _run(openai_compatible_list_models(f"{srv.base}/v1", "k"))] == [
                "fine"
            ]
    finally:
        srv.close()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


# ── The provider row: BrandedCatalog, the openai-compatible app's catalog ─────


def _byo_catalog(endpoint: str, *, default_model: str = "", fallback: tuple = ()):
    """The catalog the ``openai-compatible`` app registers — a bring-your-own endpoint whose
    spec declares NO curated fallback models, which is the exact instance shape #955 was
    filed against (``PersonalClawApps/openai-compatible/provider.py``)."""
    from personalclaw.sdk.provider_helpers import BrandedCatalog, BrandedProviderSpec

    spec = BrandedProviderSpec(
        type="openai_compatible",
        protocol="openai",
        default_base_url="",
        api_key_env="",  # no env fallback, so the test's key is the only credential
        fallback_models=fallback,
    )
    return BrandedCatalog(spec, endpoint=endpoint, api_key="sk-test", default_model=default_model)


def test_a_byo_provider_with_nothing_to_fall_back_on_raises_instead_of_listing_zero(
    allow_loopback_egress: None,
) -> None:
    """The #955 headline. ``/api/model-providers/{name}/models``,
    ``/api/models/available`` and ``/api/models/chat`` each already relay a RAISED
    discovery failure onto the provider row and swallow a returned ``[]``, so for the
    instance with no fallback the failure has to travel as an exception to be visible at
    all."""
    srv = _Endpoint("/v1/models", '{"error":{"message":"nope"}}', status=401)
    try:
        with pytest.raises(ModelDiscoveryError) as caught:
            _run(_byo_catalog(f"{srv.base}/v1").list_models())
    finally:
        srv.close()
    assert "401" in str(caught.value)


def test_a_curated_or_configured_fallback_still_wins_over_raising(
    allow_loopback_egress: None, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty picker is worse than a stale one: when the instance HAS something to offer
    (a configured Default Model — #955's own workaround — or a branded app's curated list)
    discovery failure degrades to it. What changes is that the reason is logged, not that
    the user loses the list."""
    srv = _Endpoint("/v1/models", '{"error":{"message":"nope"}}', status=401)
    try:
        with caplog.at_level(logging.WARNING):
            got = _run(_byo_catalog(f"{srv.base}/v1", default_model="MiniMax-M2.5").list_models())
    finally:
        srv.close()
    assert [m.id for m in got] == ["MiniMax-M2.5"]
    logged = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "401" in logged, "degrading to the fallback must still record WHY"


def test_a_reachable_endpoint_beats_the_configured_default_model(
    allow_loopback_egress: None,
) -> None:
    """The floor under the previous test: the fallback is a degradation, not a short
    circuit. Live discovery, when it works, is what the picker shows."""
    srv = _Endpoint("/v1/models", '{"object":"list","data":[{"id":"live-a"},{"id":"live-b"}]}')
    try:
        got = _run(_byo_catalog(f"{srv.base}/v1", default_model="MiniMax-M2.5").list_models())
    finally:
        srv.close()
    assert [m.id for m in got] == ["live-a", "live-b"]


def test_the_zai_base_shape_reaches_the_provider_row_end_to_end(
    allow_loopback_egress: None,
) -> None:
    """#955's z.ai row, driven through the app's own catalog rather than the raw helper:
    eight models on the wire, eight models on the provider row."""
    ids = [f"glm-4.{n}" for n in range(8)]
    srv = _Endpoint(
        "/api/coding/paas/v4/models",
        json.dumps({"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}),
    )
    try:
        got = _run(_byo_catalog(f"{srv.base}/api/coding/paas/v4").list_models())
    finally:
        srv.close()
    assert [m.id for m in got] == ids
    assert len(got) == 8


@pytest.mark.parametrize(
    ("case", "status", "body", "fragment"),
    [
        ("401", 401, '{"error":{"message":"bad key"}}', "401"),
        ("404 wrong base", 404, '{"error":{"message":"nope"}}', "404"),
        ("non-JSON", 200, "<html>login</html>", "not JSON"),
    ],
)
def test_test_connection_names_the_cause_instead_of_one_generic_sentence(
    case: str, status: int, body: str, fragment: str, allow_loopback_egress: None
) -> None:
    """Settings → "Test connection" printed "No models available (check key/endpoint)" for
    every one of these. Vacuity floor: each row demands its OWN fragment, so a single
    reworded generic sentence fails."""
    srv = _Endpoint("/v1/models", body, status=status)
    try:
        res = _run(_byo_catalog(f"{srv.base}/v1").test_connection())
    finally:
        srv.close()
    assert res.ok is False, case
    assert fragment in res.detail, f"{case}: want {fragment!r} in {res.detail!r}"


def test_test_connection_reports_a_reachable_endpoint_that_lists_nothing_honestly(
    allow_loopback_egress: None,
) -> None:
    srv = _Endpoint("/v1/models", '{"object":"list","data":[]}')
    try:
        res = _run(_byo_catalog(f"{srv.base}/v1").test_connection())
    finally:
        srv.close()
    assert res.ok is False
    assert "listed no models" in res.detail and "Default Model" in res.detail


def test_test_connection_does_not_credit_a_fallback_it_never_reached(
    allow_loopback_egress: None,
) -> None:
    """A connectivity probe that counted the curated list would answer "connected, 2
    models" for an endpoint it never reached — which is how #955's instances came to read
    ``Configured`` / ``credential_status: ok`` while contributing nothing."""
    srv = _Endpoint("/v1/models", '{"error":{"message":"bad key"}}', status=401)
    fallback = ({"id": "curated-a"}, {"id": "curated-b"})
    try:
        res = _run(_byo_catalog(f"{srv.base}/v1", fallback=fallback).test_connection())
    finally:
        srv.close()
    assert res.ok is False
    assert res.model_count is None
    assert "401" in res.detail


def test_test_connection_still_succeeds_on_a_healthy_endpoint(
    allow_loopback_egress: None,
) -> None:
    """The floor under every failure row above: a working endpoint must still pass."""
    srv = _Endpoint("/v1/models", '{"object":"list","data":[{"id":"a"},{"id":"b"}]}')
    try:
        res = _run(_byo_catalog(f"{srv.base}/v1").test_connection())
    finally:
        srv.close()
    assert res.ok is True
    assert res.model_count == 2
