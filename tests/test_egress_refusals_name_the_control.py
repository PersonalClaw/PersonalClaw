"""A refusal the owner can lift names the control that lifts it — never a config key.

Measured on the container image, adding a model provider whose server listens on the container's
own loopback: its Test answered

    Egress policy blocked http://127.0.0.1:18907/v1/models (host '127.0.0.1' resolves to a
    non-public address …) — allow-list the host under security.egress.allow_hosts, or set
    security.egress.allow_private for a LAN/localhost endpoint.

Two config keys, one of them the switch that opens every private address at once. What the owner
actually uses is one host in Allowed hosts, under Settings → Security → Network egress — which is
what the Store's own refusal already said. The guard's hints, shown on a web fetch's tool card,
spoke the same config dialect. One place now names the control, and every one of these sentences
comes from it.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from personalclaw.net.guard import EGRESS_SETTINGS, allow_host_step, evaluate
from personalclaw.net.policy import EgressPolicy

_CONFIG_DIALECT = re.compile(r"security\.egress|allow_hosts|allow_private")
_SECURITY_PANEL = Path(__file__).resolve().parents[1] / "web/src/pages/settings/SecurityPanel.tsx"


@pytest.mark.asyncio
async def test_a_blocked_model_endpoint_names_allowed_hosts_and_the_host():
    from personalclaw.llm.catalog import ModelDiscoveryError, openai_compatible_discover_models

    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_discover_models("http://127.0.0.1:18907/v1", "fv-fake-key")
    said = str(caught.value)
    assert "add 127.0.0.1 to Allowed hosts in Settings → Security → Network egress" in said, said
    assert "this computer (127.0.0.1)" in said, said
    assert not _CONFIG_DIALECT.search(said), said
    # The narrow step, not the switch that opens every private address — an allow-list keeps
    # the rest of the network unreachable.
    assert "private networks" not in said, said


@pytest.mark.asyncio
async def test_a_name_that_does_not_resolve_is_not_told_to_allow_list_it(monkeypatch):
    """The allow-list cannot help a host with no address, so the sentence does not offer it."""
    from personalclaw.llm.catalog import ModelDiscoveryError, openai_compatible_discover_models
    from personalclaw.net import guard

    def nowhere(host: str) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(guard, "_resolve", nowhere)
    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_discover_models("http://models.invalid/v1", "fv-fake-key")
    said = str(caught.value)
    assert "models.invalid" in said and "could not be found" in said, said
    assert "Allowed hosts" not in said, said
    assert not _CONFIG_DIALECT.search(said), said


def test_the_guards_hint_for_a_private_address_names_the_control():
    decision = evaluate(
        "http://nas.example/feed", EgressPolicy(name="test"), resolver=lambda h: ["10.0.0.4"]
    )
    assert not decision.allow and decision.category == "private"
    hints = " ".join(decision.recovery_hints)
    assert allow_host_step("nas.example") in hints, hints
    assert not _CONFIG_DIALECT.search(hints), hints


def test_the_guards_hint_for_a_host_off_an_exclusive_list_names_the_control():
    decision = evaluate(
        "https://api.example.com/v1",
        EgressPolicy(name="listed", allow_only=True),
        resolver=lambda h: ["93.184.216.34"],
    )
    assert not decision.allow and decision.category == "not_listed"
    hints = " ".join(decision.recovery_hints)
    assert allow_host_step("api.example.com") in hints, hints
    assert not _CONFIG_DIALECT.search(hints), hints


def test_the_named_control_is_the_one_the_security_page_renders():
    """The sentence names a page, so the page is what it is held to: a rename there reds here."""
    assert EGRESS_SETTINGS == "Settings → Security → Network egress"
    panel = _SECURITY_PANEL.read_text(encoding="utf-8")
    assert '<Section title="Network egress"' in panel
    assert '<HostList label="Allowed hosts"' in panel
    assert "Allowed hosts in Settings → Security → Network egress" in allow_host_step("h.example")
