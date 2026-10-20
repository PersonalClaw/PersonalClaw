"""A provider-instance "test connection" failure must answer with the ONE wire error
envelope (``{"error": {"code", "message"}}``) at a real 4xx/5xx status — never an
HTTP 200 carrying a raw Python exception string.

Regression for the defect where ``handle_test_instance`` (and a model-endpoint probe it
used to carry, since removed with model instances in this store)
returned ``{"ok": false, "message": str(exc)[:200]}`` with no ``status=`` (so HTTP 200):
the frontend's shared error funnel only fires on ``!response.ok``, so a failed test was
a 200 it never saw, and the user read a truncated ``ConnectionRefusedError(...)`` during
first-run model/MCP setup. The fix routes every failure through
``personalclaw.http_errors.json_error``; these tests pin the envelope, the status class,
and that the raw exception text never reaches the user.
"""

from __future__ import annotations

import json

from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.providers.instance_routes import _probe_failure


def _body(resp) -> dict:
    """The decoded JSON body of a ``web.Response`` (json_response sets ``.body``)."""
    assert resp.body is not None
    return json.loads(resp.body)


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
