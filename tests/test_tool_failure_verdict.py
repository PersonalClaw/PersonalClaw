"""A HANDLED tool failure must reach the wire AND the audit log as a failure (#3487).

``InProcessMcpToolProvider.invoke`` used to answer ``ToolResult(success=True, …)`` for
every call that did not RAISE. Every handled failure in the ``mcp_*`` modules — an unknown
slug, a missing required argument, a service error code — is a normal ``str`` return by
design, so it arrived at ``POST /api/tools/invoke`` as ``200 {ok: true, error: ""}`` and at
the hash-chained Security Event Log as ``outcome: "completed"``.

The audit half is the reason this is a defect rather than a cosmetic label. A tamper-evident
ledger whose whole question is *"what destructive tool ran"* recorded four refused
destructive operations as completed, in the direction that manufactures false positives: an
operator reading it cannot tell "the delete happened" from "the tool refused".

The fix is a structured failure channel — :class:`personalclaw.tool_providers.base.ToolFailure`
— NOT a predicate over the returned prose. The tests below are written so a prose sniff
cannot pass them: :func:`test_a_handled_failure_whose_text_does_not_say_error_is_still_a_failure`
uses ``artifact_delete``'s not-found message, which carries no ``Error`` prefix at all, and
:func:`test_tool_output_that_merely_describes_an_error_is_still_a_success` returns legitimate
output that reads like a failure.
"""

from __future__ import annotations

import json

import pytest

import personalclaw.dashboard.handlers.tools as tools_mod


@pytest.fixture
def sel_rows(tmp_path, monkeypatch):
    """A REAL :class:`SecurityEventLog` at a tmp dir, plus a reader for its rows off disk.

    The claim is about the persisted, hash-chained ledger, so the assertions read the file
    rather than a recording double: a double would prove the route called a logger, not
    what an operator finds in ``security_events.jsonl``.
    """
    from personalclaw.sel import SecurityEventLog

    monkeypatch.setattr(SecurityEventLog, "_instance", None)
    monkeypatch.setattr(SecurityEventLog, "_initialized", False)
    log_dir = tmp_path / "sel"
    log_dir.mkdir()
    SecurityEventLog(log_dir)

    def rows(source: str = "tool_invoke") -> list[dict]:
        path = log_dir / "security_events.jsonl"
        if not path.exists():
            return []
        all_rows = [
            json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        return [r for r in all_rows if r.get("source") == source]

    return rows


class _InvokeRequest:
    """Minimal stand-in for ``api_tool_invoke`` (mirrors ``test_tools_handler``'s)."""

    def __init__(self, body: dict) -> None:
        self._body = body
        self.headers: dict[str, str] = {}

    async def json(self):
        return self._body

    def get(self, key, default=None):
        return default


def _allow_everything(monkeypatch) -> None:
    """No tool is disabled — this file is about the verdict, not the toggle gate."""
    monkeypatch.setattr("personalclaw.tool_providers.tool_prefs.load_disabled", lambda: set())
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled_providers", lambda: set()
    )


def _install_real_bridge(monkeypatch, module: str, provider_name: str):
    """Register the REAL ``InProcessMcpToolProvider`` over ``module``.

    The bridge is the subject, so it must not be faked: a stub provider returning
    ``ToolResult(success=False)`` would pass on ``origin/main``.
    """
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    prov = InProcessMcpToolProvider(module=module, provider_name=provider_name, display="x")
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.get_provider",
        lambda name: prov if name == provider_name else None,
    )
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [prov])
    return prov


async def _invoke(body: dict):
    resp = await tools_mod.api_tool_invoke(_InvokeRequest(body))
    return resp, json.loads(resp.body.decode())


# ── The headline claim: the SEL row for a refused destructive call ────────────


@pytest.mark.asyncio
async def test_a_refused_destructive_call_is_not_audited_as_completed(monkeypatch, sel_rows):
    """The measured defect, at the ledger. ``artifact_delete`` with no ``slug`` is refused
    by argument validation — nothing was deleted — and the SEL recorded ``completed``.

    This is the assertion the issue is about. A wire-only assertion would leave the audit
    half unproven, and the audit half is the one a security review reads.
    """
    _allow_everything(monkeypatch)
    _install_real_bridge(monkeypatch, "personalclaw.mcp_artifacts", "personalclaw-artifacts")

    resp, payload = await _invoke(
        {"tool": "artifact_delete", "arguments": {}, "confirm_risk": "destructive"}
    )

    assert resp.status == 200, payload
    rows = sel_rows()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["operation"] == "artifact_delete", row
    assert row["metadata"]["risk"] == "destructive", row
    assert row["outcome"] != "completed", (
        "the SEL recorded a refused DESTRUCTIVE call as completed — "
        f"outcome={row['outcome']!r}, wire={payload!r}"
    )
    assert row["outcome"] == "error", row


@pytest.mark.asyncio
async def test_the_wire_envelope_reports_a_handled_failure(monkeypatch, sel_rows):
    """``200 {ok: true, error: ""}`` over text that begins ``Error`` is the wire half."""
    _allow_everything(monkeypatch)
    _install_real_bridge(monkeypatch, "personalclaw.mcp_artifacts", "personalclaw-artifacts")

    _resp, payload = await _invoke(
        {"tool": "artifact_delete", "arguments": {}, "confirm_risk": "destructive"}
    )

    assert payload["ok"] is False, payload
    assert payload["error"], f"a failure with an empty error field: {payload!r}"
    assert "slug" in payload["error"], payload


@pytest.mark.asyncio
async def test_the_wire_and_the_sel_read_ONE_verdict(monkeypatch, sel_rows):
    """Two derivations of one fact is how they came to disagree.

    Both consumers must key off the same structured signal, so their answers cannot drift
    apart again: ``ok`` false ⇔ the audited outcome is not ``completed``. This holds on
    ``origin/main`` too (both read ``result.success``, and both were wrong together) — it is
    the invariant the fix must not break, not the defect.
    """
    _allow_everything(monkeypatch)
    _install_real_bridge(monkeypatch, "personalclaw.mcp_artifacts", "personalclaw-artifacts")

    seen = 0
    for args in ({}, {"slug": "pc3487-no-such-artifact"}):
        _resp, payload = await _invoke(
            {"tool": "artifact_delete", "arguments": args, "confirm_risk": "destructive"}
        )
        rows = sel_rows()
        assert len(rows) == seen + 1, rows
        row = rows[seen]
        seen += 1
        audited_ok = row["outcome"] == "completed"
        assert audited_ok is bool(payload["ok"]), (
            f"the wire said ok={payload['ok']!r} and the ledger said "
            f"outcome={row['outcome']!r} for args={args!r}"
        )
    assert seen == 2


@pytest.mark.asyncio
async def test_a_handled_failure_whose_text_does_not_say_error_is_still_a_failure(
    monkeypatch, sel_rows
):
    """``artifact_delete`` on a missing slug answers ``Artifact not found: …``.

    It carries NO ``Error`` prefix, which is exactly why a leading-``Error`` sniff was
    rejected: it would have fixed three of the issue's four rows and still called this
    refused destructive delete a success.
    """
    _allow_everything(monkeypatch)
    _install_real_bridge(monkeypatch, "personalclaw.mcp_artifacts", "personalclaw-artifacts")

    _resp, payload = await _invoke(
        {
            "tool": "artifact_delete",
            "arguments": {"slug": "pc3487-no-such-artifact"},
            "confirm_risk": "destructive",
        }
    )

    assert payload["ok"] is False, payload
    assert "not found" in (payload["error"] or "").lower(), payload
    rows = sel_rows()
    assert rows and rows[0]["outcome"] == "error", rows


# ── The other direction: a success must stay a success ────────────────────────


@pytest.mark.asyncio
async def test_a_real_success_is_still_reported_as_a_success(monkeypatch, sel_rows):
    """The vacuity floor. A fix that reported every call as a failure would satisfy every
    assertion above, so the passing arm has to be in the same file.

    ``artifact_list`` over an empty home answers ``No artifacts found.`` — prose that
    *describes* an absence and is a genuine success.
    """
    _allow_everything(monkeypatch)
    _install_real_bridge(monkeypatch, "personalclaw.mcp_artifacts", "personalclaw-artifacts")

    _resp, payload = await _invoke({"tool": "artifact_list", "arguments": {}})

    assert payload["ok"] is True, payload
    assert payload["error"] == "", payload
    rows = sel_rows()
    assert rows and rows[0]["outcome"] == "completed", rows


# ── The bridge itself, without the route ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bridge_maps_a_tool_failure_to_an_unsuccessful_result():
    """``ToolFailure`` is the structured channel: the bridge reads the TYPE, not the text."""
    import sys
    import types

    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.tool_providers.base import ToolFailure

    mod = types.ModuleType("pc3487_failing_tool_module")
    mod._list_tools = lambda: []  # type: ignore[attr-defined]
    mod._call_tool = lambda name, args: ToolFailure(  # type: ignore[attr-defined]
        "Error: it did not happen", reason="it did not happen"
    )
    sys.modules[mod.__name__] = mod
    try:
        prov = InProcessMcpToolProvider(module=mod.__name__, provider_name="p", display="P")
        result = await prov.invoke("whatever", {})
    finally:
        del sys.modules[mod.__name__]

    assert result.success is False
    # ``reason`` is the prefix-free message, so ``format_tool_result`` renders exactly one
    # ``Error: `` — the convention has ONE owner instead of one per call site.
    assert result.error == "it did not happen"


@pytest.mark.asyncio
async def test_tool_output_that_merely_describes_an_error_is_still_a_success():
    """The case a prose predicate gets wrong, pinned as a test.

    A ``grep`` for the word ``Error`` in a log file legitimately returns lines beginning
    ``Error``. Those are OUTPUT, and the call succeeded.
    """
    import sys
    import types

    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    mod = types.ModuleType("pc3487_honest_tool_module")
    mod._list_tools = lambda: []  # type: ignore[attr-defined]
    mod._call_tool = lambda name, args: (  # type: ignore[attr-defined]
        "Error: connection refused\nError: timeout\n2 matches"
    )
    sys.modules[mod.__name__] = mod
    try:
        prov = InProcessMcpToolProvider(module=mod.__name__, provider_name="p", display="P")
        result = await prov.invoke("grep_logs", {})
    finally:
        del sys.modules[mod.__name__]

    assert result.success is True
    assert result.output.startswith("Error: connection refused")


# ── The population floor: no handler may go back to prose-only failures ──────


def _prose_failure_returns(source: str) -> list[int]:
    """Line numbers of ``return`` statements whose literal text starts with ``Error``.

    Scanned with ``ast``, not a text regex, for two measured reasons. BRIEF §15: a rail that
    greps raw source matches the COMMENT documenting the fix — including this file's own
    module docstring — so the commit that closes a defect is the commit the rail reds. An AST
    walk cannot see a comment at all, so the rail measures the program rather than the
    explanation of it, by construction rather than by remembering to strip.

    The second reason is coverage: seven of these sites are parenthesised implicit
    concatenations spanning four lines, where the ``return`` line is just ``return (``. A
    ``return\\s+"Error`` regex reads ZERO on all seven — the exact shape of false green §12
    describes.
    """
    import ast

    def leading_literal(node) -> str | None:
        """The literal prefix ``node`` renders, or ``None`` if it is not a string at all."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):  # an f-string
            for part in node.values:
                return part.value if isinstance(part, ast.Constant) else ""
            return ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return leading_literal(node.left)
        if isinstance(node, ast.IfExp):  # ``a if cond else b`` — check the taken branch
            return leading_literal(node.body)
        return None

    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        literal = leading_literal(node.value)
        if literal is not None and literal.startswith("Error"):
            hits.append(node.lineno)
    return hits


def test_no_mcp_handler_states_a_failure_in_prose_only():
    """A handler that ``return``s a prose failure re-opens #3487 for its whole module.

    The population floor for the fix: the bridge can only report a failure the handler
    declared, so a new handler falling back to ``return "Error: …"`` silently reinstates
    ``200 {ok: true}`` and an audited ``completed`` for its own tool.
    """
    import pathlib

    root = pathlib.Path(tools_mod.__file__).resolve().parents[2]
    files = sorted(root.glob("mcp_*.py")) + [root / "computer_use" / "tools.py"]
    assert len(files) >= 9, f"the scan found only {len(files)} modules under {root}"

    # The vacuity control, in the same test: the scan MUST see a synthetic offender, or a
    # green below would only mean it can see nothing (§15's rule about a selector that
    # cannot fire being indistinguishable from a clean tree).
    control = _prose_failure_returns(
        'def h(a):\n    if a:\n        return (\n            "Error: nope "\n            "twice"\n'
        '        )\n    return "fine"\n'
    )
    assert control == [3], f"the scan cannot see its own offender: {control!r}"

    offenders: list[str] = []
    for path in files:
        for lineno in _prose_failure_returns(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], (
        "these handlers state a failure in prose only, so the bridge reports them as "
        "successes (#3487) — return tool_failure(...) instead:\n  " + "\n  ".join(offenders)
    )
