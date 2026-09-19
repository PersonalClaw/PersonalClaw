"""RUA-5 — the bypass-behind-a-proxy hazard is VISIBLE in doctor, and admission is untouched.

``PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1`` admits any request whose resolved client address is
private (``token_auth.py``'s middleware: ``is_private_network(_resolved_client_ip(request))``).
On a home LAN that means "someone in my house". Behind a reverse proxy it does not: the address
the middleware resolves is the *proxy's* (``127.0.0.1`` for a local tunnel daemon, ``172.18.x.x``
on a compose bridge), both private, so every request the proxy forwards from anywhere on the
internet is admitted with no token. The one shape that escapes it is the one whose proxy sets
``X-Real-IP`` — the single forwarded header the code reads (RUA-6, closed separately in #3048).

**This file is deliberately two halves, and the second is the more important one.**

* The hazard becomes *visible*: the ``remote.reachability`` probe fails, naming both
  ``BYPASS_LOCAL_NETWORKS`` and ``trusted_proxies``, when the bypass is armed together with a
  declared proxy or public URL — and is unchanged from today when it is not.
* Admission is *unchanged*. Whether that token-free ``200`` should become a ``403`` is the
  escalated owner fork this atom is explicitly scoped out of, so the four probe shapes are pinned
  at the status codes they return today. A diagnostic that quietly re-decided who gets in would be
  the worse bug: an operator relying on the bypass would lose access with a health row as the only
  explanation.

The four-row parametrization also pins the RUA-6 *direction* taken on ``main``. Honoring
``X-Forwarded-For`` in ``_resolved_client_ip`` — the other way RUA-6 could have been closed —
would flip the forged-public-address rows, because the forged address would then be believed.
That is a strictly better security outcome *and* an admission change, i.e. exactly the fork.
Those rows are therefore the tripwire on that boundary: if a later change makes them go red, it
has crossed into the fork and needs the owner, not a rebase.

**On the clause's probe command.** RUA-5's ``done_when`` reads the row as
``personalclaw doctor --json | jq -r '.checks[] | select(.name=="remote") | .status'`` == ``fail``.
That command is not runnable as written and never was: ``personalclaw doctor`` has no ``--json``
flag (verified — argparse rejects it), the report (``GET /api/doctor`` → :func:`run_doctor`) has no
``checks[]`` array (probe rows live under ``capabilities.<cap>.probes[]``), the row's identifier
key is ``id`` not ``name``, and its pass/fail key is the boolean ``ok``, not a ``status`` string.
The clause's *intent* is unambiguous, so it is asserted against the real surface: ``ok is False``
on the ``remote`` capability's row, reached BOTH through the probe body and through the assembled
report, so neither the row's identity nor its wiring into the report is taken on faith.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard import origin, token_auth
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext

BYPASS = "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"
PORT = 10000
PROXY_PEER = "127.0.0.1"  # a local cloudflared/nginx daemon — the realistic tunnel shape
PUBLIC_CLIENT = "8.8.8.8"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never read the real home: this test writes config.json and reads it back."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(BYPASS, raising=False)
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    # A plain local install unless a test says otherwise, so the probe's three pre-existing
    # outcomes stay out of the way of the one being asserted.
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "127.0.0.1")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: False)
    monkeypatch.setattr(origin, "tailscale_cli_present", lambda: False)
    return tmp_path


def _write_config(home, **dashboard) -> None:
    (home / "config.json").write_text(json.dumps({"dashboard": dashboard}), encoding="utf-8")


async def _probe() -> doctor.ProbeResult:
    return await doctor._probe_remote_reachability(DoctorContext(port=PORT))


# ── half one: the hazard is visible ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_bypass_plus_declared_trusted_proxies_fails_the_remote_row(_isolated, monkeypatch):
    """The headline combination. `detail` must carry BOTH literals the clause names: an operator
    reading the row has to be able to connect the variable they exported to the config they
    wrote."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"])
    monkeypatch.setenv(BYPASS, "1")

    res = await _probe()

    assert res.ok is False
    assert "BYPASS_LOCAL_NETWORKS" in res.detail
    assert "trusted_proxies" in res.detail
    assert res.evidence["bypass_local_networks"] is True
    assert res.evidence["trusted_proxies"] == ["127.0.0.1"]
    assert res.evidence["guide"] == "docs/guides/remote-access.md"


@pytest.mark.asyncio
async def test_bypass_plus_a_declared_public_url_also_fails_the_row(_isolated, monkeypatch):
    """`public_url` is the other way an operator says "traffic reaches me through a proxy", and
    it is the one the guide's own walkthrough tells them to set — so it must count."""
    _write_config(_isolated, public_url="https://pc.example.com")
    monkeypatch.setenv(BYPASS, "1")

    res = await _probe()

    assert res.ok is False
    assert "BYPASS_LOCAL_NETWORKS" in res.detail
    assert "trusted_proxies" in res.detail
    assert res.evidence["public_url_declared"] is True
    # The declared URL itself is not echoed into the diagnostic — the boolean is the whole
    # signal, and a health row is not the place to restate a hostname.
    assert "pc.example.com" not in res.detail
    assert "pc.example.com" not in json.dumps(res.evidence)


@pytest.mark.asyncio
async def test_the_failing_row_is_the_remote_row_of_the_single_capability_report(
    _isolated, monkeypatch
):
    """The clause reads a ROW out of a doctor report, not a ProbeResult. This is the
    `GET /api/doctor/remote` path — what a user hits when they open the Remote card — so "the
    probe returns not-ok" cannot pass while the surface they read shows something else. It also
    pins the real key names (`id`, `ok`, `capability`, `probes[]`) that the clause's
    `.checks[] | select(.name==…) | .status` path gets wrong."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"])
    monkeypatch.setenv(BYPASS, "1")

    result = await doctor.run_capability("remote", DoctorContext(port=PORT))

    assert result.get("unknown") is None, "the 'remote' capability must exist"
    rows = [p for p in result["probes"] if p["id"] == "remote.reachability"]
    assert len(rows) == 1, f"expected exactly one remote.reachability row, got {rows!r}"
    row = rows[0]
    assert row["capability"] == "remote"
    assert row["ok"] is False
    assert "BYPASS_LOCAL_NETWORKS" in row["detail"]
    assert "trusted_proxies" in row["detail"]
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_the_failing_row_does_not_make_the_full_report_call_the_gateway_down(
    _isolated, monkeypatch
):
    """The full-report shape, and the CAPABILITY-tier promise with it. Scoped to this one probe
    because `run_doctor` skips every tier-3 probe when the core ladder fails, and a unit test has
    no live gateway to satisfy tiers 0-2 — the `probes=` argument is the seam `run_doctor` exposes
    for exactly this. A security-shaped row must degrade the `remote` card and nothing else:
    `core_ok` stays True and no restart is suggested."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"])
    monkeypatch.setenv(BYPASS, "1")
    remote_probe = {p.id: p for p in doctor.all_probes()}["remote.reachability"]

    report = await doctor.run_doctor(DoctorContext(port=PORT), probes=[remote_probe])

    row = report["capabilities"]["remote"]["probes"][0]
    assert row["id"] == "remote.reachability"
    assert row["ok"] is False
    assert "BYPASS_LOCAL_NETWORKS" in row["detail"]
    assert "trusted_proxies" in row["detail"]
    assert report["capabilities"]["remote"]["ok"] is False
    assert report["core_ok"] is True, "a bypass warning is not a gateway failure"
    assert report["restart_suggested"] is False


@pytest.mark.asyncio
async def test_the_hazard_is_reported_even_on_a_tailnet(_isolated, monkeypatch):
    """A tailnet address does not make the bypass safe behind a proxy, so the hazard must not be
    masked by the pre-existing tailnet-is-ok outcome that follows it."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"])
    monkeypatch.setenv(BYPASS, "1")
    monkeypatch.setattr(origin, "tailnet_ip", lambda addresses=None: "100.101.102.103")

    res = await _probe()

    assert res.ok is False
    assert "BYPASS_LOCAL_NETWORKS" in res.detail


@pytest.mark.asyncio
async def test_the_auth_off_row_keeps_its_own_message(_isolated, monkeypatch):
    """Both conditions can hold at once. The auth-OFF failure is the broader one and it shipped
    first, so it keeps precedence — this atom only ADDS an outcome, it never rewords one an
    operator may already be acting on."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"])
    monkeypatch.setenv(BYPASS, "1")
    monkeypatch.setattr(origin, "resolve_bind_host", lambda auth_cfg=None: "0.0.0.0")
    monkeypatch.setattr(origin, "auth_is_off", lambda auth_cfg=None: True)

    res = await _probe()

    assert res.ok is False
    assert "auth OFF" in res.detail


# ── the row is unchanged when the combination does not hold (both directions) ─


@pytest.mark.asyncio
async def test_declared_proxies_alone_change_the_row_not_at_all(_isolated):
    """Direction one: config without the bypass. Running behind a proxy is the SUPPORTED
    deployment — flagging it on its own would train operators to ignore the row."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"], public_url="https://pc.example.com")
    with_config = await _probe()

    (_isolated / "config.json").write_text(json.dumps({"dashboard": {}}), encoding="utf-8")
    without_config = await _probe()

    assert with_config.ok is True
    assert (with_config.ok, with_config.detail) == (without_config.ok, without_config.detail)
    assert with_config.evidence == without_config.evidence
    assert "local-only" in with_config.detail
    assert "BYPASS_LOCAL_NETWORKS" not in with_config.detail


@pytest.mark.asyncio
async def test_the_bypass_alone_changes_the_row_not_at_all(_isolated, monkeypatch):
    """Direction two: the bypass armed without a declared proxy. That is the documented
    dev-convenience use on a trusted LAN, and it is not this hazard. Asserted field-for-field
    against the row as it reads with the variable unset — the clause's "unchanged from today" is a
    statement about the whole row, not just its ok flag."""
    _write_config(_isolated)
    baseline = await _probe()
    monkeypatch.setenv(BYPASS, "1")
    with_env = await _probe()

    assert with_env.ok is True
    assert (with_env.ok, with_env.detail) == (baseline.ok, baseline.detail)
    assert with_env.evidence == baseline.evidence


@pytest.mark.asyncio
async def test_an_unreadable_config_reports_no_hazard(_isolated, monkeypatch):
    """Mirrors the middleware rather than guessing. `exposure.*` degrades to ""/[] on a corrupt
    config, and so does the trust rule it feeds — so "no declared proxy" is what actually holds at
    runtime. Inventing an alarm here would report a hazard the middleware, in that same state,
    does not have."""
    (_isolated / "config.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv(BYPASS, "1")

    res = await _probe()

    assert res.ok is True
    assert "BYPASS_LOCAL_NETWORKS" not in res.detail


@pytest.mark.asyncio
async def test_the_probe_is_still_a_capability_probe():
    """Adding a security-shaped outcome must not promote this probe onto the core readiness
    ladder — a bypass warning is not a reason to call the gateway down."""
    probe = {p.id: p for p in doctor.all_probes()}["remote.reachability"]
    assert probe.tier is doctor.Tier.CAPABILITY
    assert probe.capability == "remote"


def test_the_probe_mirrors_the_middleware_instead_of_respelling_the_env_var():
    """The clause says to mirror the middleware, per `cli_doctor.py:344-349`'s own note (#2860). A
    row that re-spells `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` in `os.environ.get` is another copy of
    the rule that can drift from it, so the probe must reach the fact through `origin`'s mirror.
    The variable may still appear in the human-facing `detail` string — that is the operator's own
    variable name and the clause requires it there."""
    import inspect

    src = inspect.getsource(doctor._probe_remote_reachability)
    assert "local_network_bypass_enabled()" in src, (
        "the RUA-5 row must consult origin.local_network_bypass_enabled(), the mirror of the "
        "middleware's bypass short-circuit"
    )
    assert 'os.environ.get("PERSONALCLAW_BYPASS' not in src, (
        "the probe re-derives the bypass rule from the raw env var instead of mirroring the "
        "middleware — the drift cli_doctor.py:344-349 warns about (#2860)"
    )
    # And the mirror really is the middleware's rule, not a lookalike.
    assert origin.local_network_bypass_enabled.__module__ == "personalclaw.dashboard.origin"


# ── half two: NO admission decision changes ──────────────────────────────────

# The four probe shapes, with the status each returns TODAY on a 127.0.0.1 peer (a local tunnel
# daemon) under BYPASS_LOCAL_NETWORKS=1. The X-Real-IP row is the only 403, and it is the only row
# whose proxy sets the one header the code reads — which is the whole hazard in one table.
_SHAPES_UNDER_BYPASS = [
    ("no forwarded header at all", {}, 200),
    ("X-Real-IP: a public client", {"X-Real-IP": PUBLIC_CLIENT}, 403),
    ("X-Forwarded-For only", {"X-Forwarded-For": PUBLIC_CLIENT}, 200),
    (
        "X-Forwarded-For + X-Forwarded-Proto",
        {"X-Forwarded-For": PUBLIC_CLIENT, "X-Forwarded-Proto": "https"},
        200,
    ),
]


def _request(headers: dict[str, str]) -> web.Request:
    r = MagicMock(spec=web.Request)
    r.path = "/api/config"
    r.method = "GET"
    r.query = {}
    r.cookies = {}
    r.headers = dict(headers)
    r.remote = PROXY_PEER
    return r


async def _handler(_req):
    return web.Response(text="ok")


@pytest.mark.parametrize("label,headers,expected", _SHAPES_UNDER_BYPASS)
@pytest.mark.asyncio
async def test_api_config_status_is_unchanged_under_bypass(
    _isolated, monkeypatch, label: str, headers: dict[str, str], expected: int
):
    """Pinned at TODAY's behaviour, on purpose, including the three rows that are a fail-open.
    Closing those is the escalated owner fork; this atom only makes them legible, and this test is
    what proves it did not quietly do more than that."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"], public_url="https://pc.example.com")
    monkeypatch.setenv(BYPASS, "1")
    mw = token_auth.token_auth_middleware(port=PORT)

    resp = await mw(_request(headers), _handler)

    assert resp.status == expected, f"admission changed for {label}"


@pytest.mark.parametrize("label,headers,_expected", _SHAPES_UNDER_BYPASS)
@pytest.mark.asyncio
async def test_api_config_is_denied_for_all_four_shapes_without_the_bypass(
    _isolated, label: str, headers: dict[str, str], _expected: int
):
    """The bypass is opt-in: with the variable unset every shape is refused. This is the row that
    keeps the hazard's severity honestly bounded rather than overstated."""
    _write_config(_isolated, trusted_proxies=["127.0.0.1"], public_url="https://pc.example.com")
    mw = token_auth.token_auth_middleware(port=PORT)

    resp = await mw(_request(headers), _handler)

    assert resp.status == 403, f"a tokenless request was admitted without the bypass: {label}"
