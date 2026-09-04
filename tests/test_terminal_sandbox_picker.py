"""Tests for the terminal sandbox-providers endpoint (EXECUTION-ISOLATION EI-4 §1.3(3))."""

from __future__ import annotations

import json

import pytest

from personalclaw.dashboard.handlers.terminal import api_sandbox_providers
from personalclaw.sandbox_providers import register_provider, unregister_provider
from personalclaw.sandbox_providers.lima import create_provider


class _Req:
    """A minimal stand-in for aiohttp's Request — the handler only reads ``.get('user')``."""

    def __init__(self, user: str | None = "tester") -> None:
        self._user = user

    def get(self, key: str, default: object = None) -> object:
        return self._user if key == "user" else default


async def _providers(req: _Req) -> list[dict]:
    resp = await api_sandbox_providers(req)  # type: ignore[arg-type]
    return json.loads(resp.text)["providers"]


@pytest.mark.asyncio
async def test_requires_auth():
    resp = await api_sandbox_providers(_Req(user=None))  # type: ignore[arg-type]
    assert resp.status == 401


@pytest.mark.asyncio
async def test_lists_host_first_with_availability():
    providers = await _providers(_Req())
    assert providers, "at least the host tier must be present"
    assert providers[0]["name"] == "none"  # host tier is always first
    for p in providers:
        assert set(p) == {"name", "display_name", "available"}
        assert isinstance(p["available"], bool)
        assert p["display_name"]  # never blank
    # The host tier is always available.
    assert next(p for p in providers if p["name"] == "none")["available"] is True


@pytest.mark.asyncio
async def test_enabled_lima_tier_appears_greyed_when_down(monkeypatch):
    """A registered but non-Running lima tier surfaces as available=False (greyed-with-reason)."""
    monkeypatch.setattr(
        "personalclaw.sandbox_providers.lima._cached_probe",
        lambda instance, *, refresh: (False, "instance stopped"),
    )
    register_provider(create_provider())
    try:
        providers = await _providers(_Req())
        lima = next((p for p in providers if p["name"] == "lima"), None)
        assert lima is not None
        assert lima["available"] is False
    finally:
        unregister_provider("lima")
