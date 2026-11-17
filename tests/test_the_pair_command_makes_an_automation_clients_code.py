"""`personalclaw pair --scope … --days … [--label NAME]` makes an automation client's pairing code
(scoped-pairing design §5, ruling 4).

It asks this home's running gateway, through `pair/start`, signed in with the home's local secret,
which is owner presence. It prints only the code, so `| pbcopy` copies it; `--json` adds the
link and what the code holds. The channel pairing it shares a name with (`personalclaw pair
<provider>`) is unchanged, and the two can't be asked at once.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from personalclaw import cli_commands, home_gateway


class _Gateway:
    """This home's gateway as the command meets it: a sign-in, then one POST."""

    def __init__(self, status: int = 200, answer: dict[str, Any] | None = None) -> None:
        self.status = status
        self.answer = answer or {
            "code": "ABCD-EFGH",
            "pairing_url": "http://127.0.0.1:10000/pair?code=ABCD-EFGH",
            "expires_at": 1800000000.0,
            "expires_in": 300,
            "scope": ["loops", "status"],
            "lifetime_days": 7,
        }
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def sign_in(self, ttl: str, *, timeout: float = 5.0) -> dict[str, Any]:
        return {"token": "a-short-sign-in", "ttl": ttl}

    def post(self, path: str, body: dict[str, Any], **kw: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append((path, body, kw))
        return self.status, self.answer


def _args(**kw: Any) -> argparse.Namespace:
    base = {"provider": None, "scope": None, "days": None, "label": "", "as_json": False}
    base.update(kw)
    return argparse.Namespace(port=None, explain=False, **base)


@pytest.fixture()
def gateway(monkeypatch) -> _Gateway:
    gw = _Gateway()
    monkeypatch.setattr(home_gateway, "reach", lambda port=None, **_kw: gw)
    return gw


def test_it_asks_pair_start_with_the_scope_days_and_label(gateway, capsys) -> None:
    cli_commands._pair(_args(scope="status,loops", days=7, label="Runner"))
    out = capsys.readouterr().out
    assert out == "ABCD-EFGH\n", "only the code, so `| pbcopy` copies it"
    path, body, kw = gateway.calls[-1]
    assert path == "/api/devices/pair/start"
    assert body == {"scope": ["status", "loops"], "days": 7, "label": "Runner"}
    assert kw.get("secret_header") == "X-Local-Secret", "the local secret is owner presence"
    assert kw.get("bearer") == "a-short-sign-in"


def test_json_adds_the_link_and_what_the_code_holds(gateway, capsys) -> None:
    cli_commands._pair(_args(scope="loops", as_json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["code"] == "ABCD-EFGH" and data["pairing_url"].endswith("code=ABCD-EFGH")
    assert data["scope"] == ["loops", "status"] and data["lifetime_days"] == 7
    assert "days" not in gateway.calls[-1][1], "no days asked: the gateway's 30-day default"


def test_a_refusal_is_said_and_exits_1(monkeypatch, capsys) -> None:
    refused = _Gateway(
        status=400,
        answer={"error": {"code": "device_pair_scope_invalid", "message": "Not a scope."}},
    )
    monkeypatch.setattr(home_gateway, "reach", lambda port=None, **_kw: refused)
    with pytest.raises(SystemExit) as exited:
        cli_commands._pair(_args(scope="approvals"))
    assert exited.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Not a scope." in captured.err


def test_no_gateway_running_is_said(monkeypatch, capsys) -> None:
    def gone(port=None, **_kw):
        raise home_gateway.NoGatewayRunning("No gateway is running for this home.")

    monkeypatch.setattr(home_gateway, "reach", gone)
    with pytest.raises(SystemExit) as exited:
        cli_commands._pair(_args(scope="loops"))
    assert exited.value.code == 1
    assert "No gateway is running" in capsys.readouterr().err


@pytest.mark.parametrize(
    "kw",
    [
        {"provider": "telegram", "scope": "loops"},
        {"provider": "telegram", "days": 7},
        {"provider": "telegram", "label": "Runner"},
        {"provider": "telegram", "as_json": True},
        {},
        {"days": 7},
        {"label": "Runner"},
    ],
)
def test_a_channel_and_a_scope_are_not_asked_at_once(kw, gateway) -> None:
    with pytest.raises(SystemExit) as exited:
        cli_commands._pair(_args(**kw))
    assert exited.value.code == 2
    assert gateway.calls == []


def test_the_channel_pairing_is_unchanged(monkeypatch, capsys, gateway) -> None:
    import personalclaw.channel_trust as ct

    monkeypatch.setattr(ct, "create_pairing_code", lambda provider: "12345678")
    monkeypatch.setattr(ct, "trust_policies", lambda provider: {"dm": "pairing"})
    cli_commands._pair(_args(provider="telegram"))
    assert capsys.readouterr().out == "12345678\n"
    assert gateway.calls == []


def test_the_parser_takes_the_scope_flags() -> None:
    from personalclaw.cli import build_parser

    args = build_parser().parse_args(["pair", "--scope", "loops,status", "--days", "7", "--json"])
    assert (args.provider, args.scope, args.days, args.as_json) == (None, "loops,status", 7, True)
    assert build_parser().parse_args(["pair", "telegram"]).provider == "telegram"


def test_the_gateway_client_sends_the_sign_in_and_the_secret_together(
    tmp_path, monkeypatch
) -> None:
    """The route signs the command in by its bearer and takes the local secret as presence: the
    two travel in one request, to the gateway ``reach`` proved, and the token in no URL."""
    (tmp_path / ".local_secret").write_text("this-homes-secret", encoding="utf-8")
    sent: list[tuple[str, dict[str, str]]] = []

    class _Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self) -> bytes:
            return b'{"code": "ABCD-EFGH"}'

    def open_loopback(request, *, timeout):
        sent.append((request.full_url, {k.lower(): v for k, v in request.header_items()}))
        return _Answer()

    monkeypatch.setattr(home_gateway, "open_loopback", open_loopback)
    gw = home_gateway.HomeGateway(home=tmp_path, port=4321, pid=0)
    status, answer = gw.post(
        "/api/devices/pair/start",
        {"scope": ["loops"]},
        secret_header="X-Local-Secret",
        bearer="t0k",
    )
    assert (status, answer) == (200, {"code": "ABCD-EFGH"})
    url, headers = sent[-1]
    assert url == "http://127.0.0.1:4321/api/devices/pair/start" and "t0k" not in url
    assert headers["authorization"] == "Bearer t0k"
    assert headers["x-local-secret"] == "this-homes-secret"
    gw.post("/api/logout", {}, secret_header="X-Local-Secret")
    assert "authorization" not in sent[-1][1], "no bearer asked, none sent"
