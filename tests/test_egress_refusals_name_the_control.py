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
async def test_a_denied_model_endpoint_names_denied_hosts_and_the_host():
    """A model provider's own endpoint is the owner's, so her server on this computer needs no
    entry in Allowed hosts; one she put on Denied hosts is refused in the words of that control."""
    import json

    from personalclaw.config.loader import config_dir
    from personalclaw.llm.catalog import ModelDiscoveryError, openai_compatible_discover_models

    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": {"deny_hosts": ["127.0.0.1"]}}}), encoding="utf-8"
    )
    with pytest.raises(ModelDiscoveryError) as caught:
        await openai_compatible_discover_models("http://127.0.0.1:18907/v1", "fv-fake-key")
    said = str(caught.value)
    assert f"127.0.0.1 is on Denied hosts in {EGRESS_SETTINGS}" in said, said
    assert not _CONFIG_DIALECT.search(said), said


def test_a_model_request_sent_on_to_this_computer_names_allowed_hosts_and_the_host():
    """A request a model provider's client is sent to that is not its configured endpoint (a
    redirect, an address its answer names) keeps to the public-only stance, and the refusal names
    the narrow step: this one host in Allowed hosts, not the switch that opens every private
    address."""
    from personalclaw.net.guard import egress_refusal
    from personalclaw.net.policy import provider_egress_policy

    url = "http://127.0.0.1:18907/v1/models"
    decision = evaluate(url, provider_egress_policy("https://models.example/v1"))
    said = egress_refusal(url, decision)
    assert not decision.allow
    assert "add 127.0.0.1 to Allowed hosts in Settings → Security → Network egress" in said, said
    assert "this computer (127.0.0.1)" in said, said
    assert not _CONFIG_DIALECT.search(said), said
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


def _unresolvable(host: str) -> list[str]:
    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")


@pytest.mark.parametrize(
    ("url", "policy", "resolver", "said"),
    [
        (
            "https://search.example.com/api",
            EgressPolicy(name="test", deny_hosts=("search.example.com",)),
            None,
            "https://search.example.com/api was not reached: search.example.com is on Denied "
            "hosts in Settings → Security → Network egress.",
        ),
        (
            "http://nas.example/api",
            EgressPolicy(name="test"),
            lambda h: ["10.0.0.4"],
            "PersonalClaw's network settings refused http://nas.example/api, which is on a "
            "private network (nas.example, which resolves to 10.0.0.4). If this endpoint is "
            "yours, add nas.example to Allowed hosts in Settings → Security → Network egress, "
            "then test again.",
        ),
        (
            "https://search.invalid/api",
            EgressPolicy(name="test"),
            _unresolvable,
            "search.invalid could not be found, so https://search.invalid/api was not reached "
            "— check the address, and this computer's network connection.",
        ),
    ],
    ids=["denied", "private", "unresolvable"],
)
def test_an_app_says_an_egress_refusal_in_the_guards_words(url, policy, resolver, said):
    """An app turns the guard's refusal into the sentence core's own refusals use, naming the
    control that lifts it (or none, when none does): ``personalclaw.sdk.net.egress_refusal``."""
    from personalclaw.sdk.net import egress_refusal

    kw = {"resolver": resolver} if resolver is not None else {}
    decision = evaluate(url, policy, **kw)
    assert not decision.allow
    assert egress_refusal(url, decision) == said
    assert not _CONFIG_DIALECT.search(said), said
