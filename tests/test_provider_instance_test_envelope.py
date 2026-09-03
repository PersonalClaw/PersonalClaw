"""A provider-instance "test connection" failure must answer with the ONE wire error
envelope (``{"error": {"code", "message"}}``) at a real 4xx/5xx status — never an
HTTP 200 carrying a raw Python exception string.

Regression for the defect where ``handle_test_instance`` / ``_test_model_connectivity``
returned ``{"ok": false, "message": str(exc)[:200]}`` with no ``status=`` (so HTTP 200):
the frontend's shared error funnel only fires on ``!response.ok``, so a failed test was
a 200 it never saw, and the user read a truncated ``ConnectionRefusedError(...)`` during
first-run model/MCP setup. The fix routes every failure through
``personalclaw.http_errors.json_error``; these tests pin the envelope, the status class,
and that the raw exception text never reaches the user.
"""

from __future__ import annotations

import asyncio
import json
import socket

from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.providers.instance_routes import _probe_failure, _test_model_connectivity


def _body(resp) -> dict:
    """The decoded JSON body of a ``web.Response`` (json_response sets ``.body``)."""
    assert resp.body is not None
    return json.loads(resp.body)


def _closed_loopback_port() -> int:
    """A loopback port with nothing listening — a connect there is refused, fast."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _assert_wire_envelope(resp, *, code: str, status: int) -> str:
    """Assert the response is exactly the wire envelope and return its message."""
    assert resp.status == status, f"expected {status}, got {resp.status}"
    body = _body(resp)
    assert set(body) == {"error"}, body
    err = body["error"]
    assert isinstance(err, dict), err
    assert err["code"] == code, err
    assert isinstance(err["message"], str) and err["message"].strip(), err
    return err["message"]


def test_model_endpoint_connection_refused_is_a_502_envelope() -> None:
    """A real connect to a closed loopback port → 502 with the standard envelope."""
    resp = asyncio.run(_test_model_connectivity(f"http://127.0.0.1:{_closed_loopback_port()}"))
    message = _assert_wire_envelope(resp, code="provider_unreachable", status=502)
    # Human guidance, not a raw exception.
    raw = resp.body.decode()
    for leak in ("Traceback", "ConnectionRefusedError", "ClientConnectorError", "Errno"):
        assert leak not in raw, f"raw exception text leaked to the user: {leak!r} in {raw!r}"
    assert "refused" in message.lower()


def test_probe_failure_bad_config_is_400_and_leaks_nothing() -> None:
    resp = _probe_failure(ValueError("SENSITIVE-CONFIG-DETAIL"), context="unit")
    _assert_wire_envelope(resp, code="provider_config_invalid", status=400)
    assert "SENSITIVE-CONFIG-DETAIL" not in resp.body.decode()


def test_probe_failure_unexpected_error_is_502_and_leaks_nothing() -> None:
    resp = _probe_failure(RuntimeError("SENSITIVE-INTERNAL-DETAIL"), context="unit")
    _assert_wire_envelope(resp, code="provider_test_failed", status=502)
    assert "SENSITIVE-INTERNAL-DETAIL" not in resp.body.decode()


def test_probe_failure_classifies_connection_refused_as_unreachable() -> None:
    """aiohttp connector errors wrap the OS cause in ``.os_error`` — the classifier
    must unwrap it rather than fall through to the generic bucket."""

    class _FakeConnectorError(Exception):
        def __init__(self) -> None:
            super().__init__("connector wrapper")
            self.os_error = ConnectionRefusedError("refused underneath")

    resp = _probe_failure(_FakeConnectorError(), context="unit")
    _assert_wire_envelope(resp, code="provider_unreachable", status=502)
    assert "refused underneath" not in resp.body.decode()


def test_the_new_provider_codes_are_registered() -> None:
    """Every code the module emits is in the append-only wire registry."""
    for code in ("provider_unreachable", "provider_config_invalid", "provider_test_failed"):
        assert code in HTTP_ERROR_CODES, code
        assert HTTP_ERROR_CODES[code].strip()
