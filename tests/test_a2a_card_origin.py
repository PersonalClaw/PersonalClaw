"""Rails for #2620: the A2A agent card advertised a hard-coded ``127.0.0.1:10000``.

``_card_base_url()`` fell back to that literal whenever ``external_access.public_url``
was undeclared — which is the DEFAULT config, not an edge case. A card is the document a
peer persists to learn where to reach this instance, so a gateway bound to any other port
published a card naming a *different* instance: same consequence as #2539's child-process
leak, but handed to a third party, so the wrong address propagates off the machine.

Two halves are pinned here.

**The address is the one this gateway actually bound**, read through the single owner
#2539 established (``gateway_base.resolve_port()``) rather than through a fourth way to
learn the port. Asserted from the SERVED card over HTTP, not from the helper's return
value — the issue asked for exactly that, because a helper can be right while the handler
that assembles the card ignores it.

**An unresolvable address is a refusal, not a guess.** Per ARCC SAX-04 Outcome 5,
*"failing open for security-critical operations"* is a named pitfall; advertising a guessed
address to a peer is failing open. This module already had the precedent and the vocabulary
for it — a catalog that cannot be read answers 503 rather than serving an empty card, on
the stated principle that "an EMPTY card and a BROKEN card must not look alike". An
unresolvable origin now answers 503 the same way, with its own code so an operator can
tell "declare a public_url" from "the catalog is broken".

:class:`TestNoSiteGuessesThePortFromALiteral` closes the blind spot the issue predicted.
``test_gateway_base_owner.py``'s rail scans for reads of the port SYMBOLS
(``_DEFAULT_PORT``, ``DASHBOARD_PORT``, ``parse_dashboard_url``), so it was structurally
incapable of seeing this site: the defect was a bare integer in an f-string, which reads
no symbol at all. A rail that cannot see the shape of the bug it is meant to catch is the
gap, so the literal is now scanned for too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import a2a

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The literal #2620 removed. Scanned for as a string so this rail's own docstring and
#: comments cannot satisfy it (comments never reach the AST).
_GUESSED_PORT = "10000"


# ── the served card names the socket this gateway bound ───────────────────────


async def _card(monkeypatch, **enable_kwargs):
    """``(status, body)`` of ``GET /a2a/agent-card`` with a valid bearer token.

    A working catalog is installed on purpose. The handler checks the catalog BEFORE the
    origin, so without a registered provider every case here would answer
    ``a2a_catalog_unavailable`` and the origin assertions would be vacuously "green" on
    the wrong refusal — which is how a rail ends up proving nothing.
    """
    from personalclaw.inbound import auth
    from tests.test_inbound_a2a import _def, _enable, _install

    _enable(monkeypatch, **enable_kwargs)
    token = "t" * 48
    monkeypatch.setenv(auth.token_env_key(a2a.SURFACE), token)
    _install(monkeypatch, [_def("triage")])

    app = web.Application()
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get(a2a.ROUTE_CARD, headers={"Authorization": f"Bearer {token}"})
        return resp.status, await resp.json()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_served_card_names_the_port_this_gateway_bound(monkeypatch) -> None:
    """THE defect. A gateway on 10771 must not publish a card naming 10000.

    ``10771`` is deliberately not the default, and the assertion is a negative on the
    guessed literal as well as a positive on the real one: a fix that happened to
    resolve 10000 correctly would otherwise pass.
    """
    from personalclaw import gateway_base

    monkeypatch.setenv(gateway_base.PORT_ENV, "10771")
    status, body = await _card(monkeypatch)
    assert status == 200, body
    assert body["url"] == "http://127.0.0.1:10771/a2a"
    assert _GUESSED_PORT not in body["url"]


@pytest.mark.asyncio
async def test_a_declared_public_url_still_wins(monkeypatch) -> None:
    """The declared-origin path was never the defect and must be untouched.

    An operator's ``public_url`` outranks the bound socket: behind a tunnel the address a
    peer can reach is the public one, and the loopback port is useless to them.
    """
    from personalclaw import gateway_base

    monkeypatch.setenv(gateway_base.PORT_ENV, "10771")
    status, body = await _card(monkeypatch, public_url="https://peer.example.test/")
    assert status == 200, body
    assert body["url"] == "https://peer.example.test/a2a"


@pytest.mark.asyncio
async def test_an_unresolvable_origin_refuses_instead_of_guessing(monkeypatch) -> None:
    """Fail closed: no resolvable address ⇒ no card at all.

    Exercised end to end through the route, because the point of the refusal is what a
    PEER receives. A 200 here — with any url — is the failing-open shape.
    """
    from personalclaw import gateway_base

    monkeypatch.delenv(gateway_base.PORT_ENV, raising=False)
    monkeypatch.setattr(gateway_base, "live_port", lambda: None)
    monkeypatch.setattr(gateway_base, "_configured_port", lambda: None)

    status, body = await _card(monkeypatch)
    assert status == 503, body
    assert body["error"]["code"] == "a2a_origin_unresolved"
    # The refusal must not leak the guess it declined to make.
    assert _GUESSED_PORT not in str(body)


@pytest.mark.asyncio
async def test_the_refusal_names_what_the_operator_should_do(monkeypatch) -> None:
    """A bare "unavailable" sends the operator reading code. #2539's own standard."""
    from personalclaw import gateway_base

    monkeypatch.delenv(gateway_base.PORT_ENV, raising=False)
    monkeypatch.setattr(gateway_base, "live_port", lambda: None)
    monkeypatch.setattr(gateway_base, "_configured_port", lambda: None)

    _status, body = await _card(monkeypatch)
    message = body["error"]["message"]
    assert "public_url" in message
    assert "card" in message.lower()


def test_the_new_code_is_in_the_append_only_registry() -> None:
    """A wire code with no registry row is a code no client can look up."""
    from personalclaw.http_errors import HTTP_ERROR_CODES

    assert "a2a_origin_unresolved" in HTTP_ERROR_CODES
    assert HTTP_ERROR_CODES["a2a_origin_unresolved"].strip()
    # Distinct from the catalog refusal: the two need different operator actions, which
    # is the reason this file argues for a second code rather than reusing the first.
    assert HTTP_ERROR_CODES["a2a_origin_unresolved"] != HTTP_ERROR_CODES["a2a_catalog_unavailable"]


# ── the rail #2539 could not have caught this with ────────────────────────────


def _literal_port_sites(root: Path) -> set[tuple[str, int]]:
    """``(module, lineno)`` of every non-docstring string literal naming ``:10000``.

    The AST carries no comments, so prose about the old bug cannot satisfy this. Module,
    class and function docstrings ARE ``Constant`` nodes, so they are collected and
    excluded explicitly — otherwise every module that explains #2539 would read as an
    offender and the rail would have to be deleted to go green.
    """
    found: set[tuple[str, int]] = set()
    needle = f":{_GUESSED_PORT}"
    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = (getattr(node, "body", None) or [None])[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and needle in node.value
                and id(node) not in docstrings
            ):
                found.add((rel, node.lineno))
    return found


class TestNoSiteGuessesThePortFromALiteral:
    """``test_gateway_base_owner.py``'s rail scans SYMBOL reads; #2620 was a LITERAL.

    That is the enumerated-rail blind spot the issue asked to have checked: the existing
    rail was green throughout, and correctly so — the offending line read no resolver
    name at all. This closes the shape rather than the instance.
    """

    def test_no_module_hard_codes_the_gateway_port_in_a_url(self) -> None:
        offenders = sorted(_literal_port_sites(SRC))
        assert not offenders, (
            "these string literals name the gateway's default port directly instead of "
            "asking personalclaw.gateway_base; on a multi-instance host the address they "
            f"publish is a DIFFERENT instance (#2539/#2620): {offenders}"
        )

    def test_the_scan_reds_on_a_planted_literal(self, tmp_path) -> None:
        """Detection direction. A rail that cannot fail is not a rail."""
        (tmp_path / "sneaky.py").write_text(
            '"""A docstring mentioning http://127.0.0.1:10000 must NOT count."""\n'
            "def card():\n"
            '    return "http://127.0.0.1:10000/a2a"\n',
            encoding="utf-8",
        )
        planted = _literal_port_sites(tmp_path)
        assert planted == {("sneaky.py", 3)}, planted

    def test_the_scan_is_not_vacuous(self) -> None:
        """The corpus is real and the docstring exclusion is doing work, not everything.

        Counted by a different mechanism than the AST walk — a plain textual count — so a
        walker that silently stopped parsing cannot satisfy both. The textual count is
        deliberately much higher than the AST count: most occurrences are the comments and
        docstrings that explain #2539, which is exactly what the exclusion removes.
        """
        pattern = re.compile(rf":{_GUESSED_PORT}\b")
        text_hits = sum(
            len(pattern.findall(py.read_text(encoding="utf-8"))) for py in SRC.rglob("*.py")
        )
        assert text_hits >= 5, f"only {text_hits} textual occurrences — the scan lost its corpus"
        assert len(_literal_port_sites(SRC)) == 0
