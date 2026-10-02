"""WS5c — the web_fetch pipeline + tool: provenance gate → egress (net.fetch) →
shared extractor → token-budgeted pagination.

net.fetch is mocked (the egress layer is tested in test_net_*); the focus here is the
pipeline wiring, the provenance gate, pagination, and the web_fetch tool contract.
"""

from __future__ import annotations

import pytest

from personalclaw.net.client import EgressBlocked, FetchResponse
from personalclaw.net.guard import GuardDecision
from personalclaw.web import fetch as wf
from personalclaw.web.fetch import (
    FROM_TOOL,
    FROM_USER,
    record_seen_urls,
    record_user_message_urls,
    url_provenance,
    web_fetch,
)


def _web_tool_provider_cls():
    """Load WebToolProvider from the web-tools APP (it moved out of core). Mirrors how
    the app loader imports an installed app's provider module."""
    import importlib.util
    import sys
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[2] / "apps" / "web-tools"
    if not app_dir.is_dir():  # standalone core clone — the web-tools app isn't present
        pytest.skip("web-tools app dir not present (standalone clone)")
    uniq = "_pclaw_app_web_tools__provider"
    if uniq in sys.modules:
        return sys.modules[uniq].WebToolProvider
    spec = importlib.util.spec_from_file_location(uniq, app_dir / "provider.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[uniq] = mod
    added = str(app_dir) not in sys.path
    if added:
        sys.path.insert(0, str(app_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(app_dir))
    return mod.WebToolProvider


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Fresh per-session provenance each test.
    monkeypatch.setattr(wf, "_seen_by_session", {})
    yield


def _resp(body, ctype="text/html", url="https://example.com/post"):
    return FetchResponse(url=url, status=200, headers={"Content-Type": ctype}, body=body.encode())


def _patch_net(monkeypatch, resp=None, exc=None):
    async def _fake(url, **kw):
        if exc:
            raise exc
        return resp if resp is not None else _resp("<html><body><p>x</p></body></html>")

    monkeypatch.setattr(wf, "net_fetch", _fake)


# ── provenance gate ─────────────────────────────────────────────────────────


def test_record_and_check_provenance():
    record_seen_urls("s1", ["https://a.com/x", "https://b.com/y#frag"])
    assert url_provenance("s1", "https://a.com/x") == FROM_TOOL
    assert url_provenance("s1", "https://b.com/y") == FROM_TOOL  # fragment canonicalized
    assert url_provenance("s1", "https://a.com/x/") == FROM_TOOL  # trailing slash canonicalized
    assert url_provenance("s1", "HTTPS://A.com/x") == FROM_TOOL  # scheme and host case
    assert url_provenance("s1", "https://c.com/z") == ""


def test_a_link_the_user_gave_stays_theirs_when_a_tool_returns_it_again():
    record_user_message_urls("s1", "read https://a.example.com/notes please")
    record_seen_urls("s1", ["https://a.example.com/notes"])
    assert url_provenance("s1", "https://a.example.com/notes") == FROM_USER


def test_a_long_search_never_pushes_out_a_link_the_user_gave(monkeypatch):
    monkeypatch.setattr(wf, "_MAX_SEEN_PER_SESSION", 3)
    record_user_message_urls("s1", "start from https://a.example.com/start")
    record_seen_urls("s1", [f"https://b.example.com/{n}" for n in range(10)])
    assert url_provenance("s1", "https://a.example.com/start") == FROM_USER
    assert url_provenance("s1", "https://b.example.com/9") == FROM_TOOL
    assert url_provenance("s1", "https://b.example.com/0") == ""
    assert len(wf._seen_by_session["s1"]) == 3


def test_only_the_users_own_words_in_a_message_grant_a_link():
    from personalclaw.security import fence_untrusted

    record_user_message_urls(
        "s1",
        "mine: https://a.example.com/mine. "
        + fence_untrusted("theirs: https://b.example.com/theirs", source="channel:telegram:x")
        + " and (https://a.example.com/also), not ftp://a.example.com/file",
    )
    assert url_provenance("s1", "https://a.example.com/mine") == FROM_USER
    assert url_provenance("s1", "https://a.example.com/also") == FROM_USER
    assert url_provenance("s1", "https://b.example.com/theirs") == ""
    assert url_provenance("s1", "ftp://a.example.com/file") == ""


def test_an_unclosed_fence_keeps_the_rest_of_the_text():
    from personalclaw.security import UNTRUSTED_OPEN

    record_user_message_urls(
        "s1", f"https://a.example.com/before {UNTRUSTED_OPEN} https://b.example.com/x"
    )
    assert url_provenance("s1", "https://a.example.com/before") == FROM_USER
    assert url_provenance("s1", "https://b.example.com/x") == ""


@pytest.mark.asyncio
async def test_fetch_blocked_without_provenance(monkeypatch):
    _patch_net(monkeypatch)
    out = await web_fetch("https://evil.com/secret", session_key="s1")
    assert out.ok is False
    assert "their own message" in out.error and "web_search or web_fetch" in out.error
    assert out.recovery_hints


@pytest.mark.asyncio
async def test_fetch_allowed_with_provenance(monkeypatch):
    _patch_net(
        monkeypatch,
        _resp(
            "<html><body><article><p>Real body content here that is long enough.</p></article></body></html>"  # noqa: E501
        ),
    )
    record_seen_urls("s1", ["https://example.com/post"])
    out = await web_fetch("https://example.com/post", session_key="s1")
    assert out.ok is True
    assert "Real body content" in out.content


@pytest.mark.asyncio
async def test_no_session_skips_provenance(monkeypatch):
    # Context-less caller (no session_key) isn't falsely blocked — egress still applies.
    _patch_net(monkeypatch, _resp("<html><body><p>hello world body</p></body></html>"))
    out = await web_fetch("https://example.com/x", session_key="")
    assert out.ok is True


@pytest.mark.asyncio
async def test_explicit_opt_out_of_provenance(monkeypatch):
    _patch_net(monkeypatch, _resp("<html><body><p>hi</p></body></html>"))
    out = await web_fetch("https://example.com/x", session_key="s1", require_provenance=False)
    assert out.ok is True


# ── egress wiring ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_egress_block_maps_to_recovery(monkeypatch):
    blocked = GuardDecision(
        allow=False,
        url="https://x.com",
        reason="resolves to private",
        risk_level="destructive",
        recovery_hints=["fetch a public URL"],
    )
    _patch_net(monkeypatch, exc=EgressBlocked(blocked))
    out = await web_fetch("https://x.com", session_key="", require_provenance=False)
    assert out.ok is False
    assert out.risk_level == "destructive"
    assert "fetch a public URL" in out.recovery_hints


@pytest.mark.asyncio
async def test_non_http_rejected(monkeypatch):
    _patch_net(monkeypatch)
    out = await web_fetch("ftp://x.com/file", require_provenance=False)
    assert out.ok is False and "http" in out.error


@pytest.mark.asyncio
async def test_operator_egress_config_layered_onto_policy(monkeypatch):
    # The Security panel's deny/allow-host config must bind the agent's primary
    # fetch surface: web_fetch layers security.egress onto the caller's profile
    # (it used to pass the pristine STRICT profile straight through, so a UI
    # "Denied host" was ignored by agent web_fetch — only webhook/connector
    # surfaces honored it).
    seen = {}

    async def _fake(url, **kw):
        seen["policy"] = kw.get("policy")
        return _resp("<html><body><p>hello world body</p></body></html>")

    monkeypatch.setattr(wf, "net_fetch", _fake)
    monkeypatch.setattr(
        wf,
        "egress_policy_for",
        lambda base: base.with_overrides(deny_hosts=("denied.example",)),
    )
    out = await web_fetch("https://example.com/x", require_provenance=False)
    assert out.ok is True
    assert seen["policy"].deny_hosts == ("denied.example",)


# ── extraction + non-HTML ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_html_kept_as_text(monkeypatch):
    _patch_net(monkeypatch, _resp("plain text body", ctype="text/plain"))
    out = await web_fetch("https://x.com/f.txt", require_provenance=False)
    assert out.ok is True
    assert out.extractor == "raw"
    assert "plain text body" in out.content


#: A page carrying an event handler, which only a real sanitizer removes.
_UNCLEAN_PAGE = '<html><body><p>Body.</p><img src=x onerror="alert(1)"></body></html>'


@pytest.mark.asyncio
async def test_a_page_nothing_can_sanitize_is_an_ordinary_failed_fetch(monkeypatch):
    """Proves a missing sanitizer reaches the caller as a failed fetch, not an exception: with
    nh3 missing, web_fetch returns ok=False in the refusal's words with a recovery hint, carries
    none of the page, and does not record the page as one the session has seen."""
    from personalclaw.web import extract

    monkeypatch.setattr(extract, "_nh3", None)
    _patch_net(monkeypatch, _resp(_UNCLEAN_PAGE, url="https://example.com/page"))

    out = await web_fetch("https://example.com/page", session_key="s3", require_provenance=False)

    assert out.ok is False
    assert out.error.startswith("HTML cannot be sanitized: nh3"), out.error
    assert out.recovery_hints == ["Reinstall PersonalClaw, then fetch the page again."]
    assert (out.content, out.title, out.char_count) == ("", "", 0)
    assert url_provenance("s3", "https://example.com/page") == ""


@pytest.mark.asyncio
async def test_web_extract_passes_the_refusal_on_and_asks_no_model(monkeypatch):
    """Proves web_extract, which fetches through web_fetch, returns the same failed result and
    never hands a page nothing sanitized to the extraction model."""
    from personalclaw import llm_helpers
    from personalclaw.web import extract

    monkeypatch.setattr(extract, "_nh3", None)
    _patch_net(monkeypatch, _resp(_UNCLEAN_PAGE))
    asked: list[str] = []

    async def _model(prompt, **_kw):
        asked.append(prompt)
        return "{}"

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _model)

    out = await wf.web_extract("https://example.com/post", "the title", require_provenance=False)

    assert out.ok is False
    assert out.error.startswith("HTML cannot be sanitized: nh3"), out.error
    assert asked == []


# ── pagination ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_truncates_and_returns_next_index(monkeypatch):
    # 200 chars of plain text, budget max_tokens=10 → 40-char window.
    _patch_net(monkeypatch, _resp("A" * 200, ctype="text/plain"))
    out = await web_fetch("https://x.com/big", require_provenance=False, max_tokens=10)
    assert out.truncated is True
    assert out.char_count == 40
    assert out.next_index == 40
    assert out.total_chars == 200


@pytest.mark.asyncio
async def test_pagination_resume_from_start_index(monkeypatch):
    _patch_net(monkeypatch, _resp("0123456789" * 20, ctype="text/plain"))  # 200 chars
    out = await web_fetch(
        "https://x.com/big", require_provenance=False, max_tokens=10, start_index=40
    )
    assert out.char_count == 40
    assert out.next_index == 80


@pytest.mark.asyncio
async def test_fetched_url_becomes_provenanced(monkeypatch):
    _patch_net(
        monkeypatch,
        _resp("<html><body><p>body text here</p></body></html>", url="https://example.com/page"),
    )
    await web_fetch("https://example.com/page", session_key="s2", require_provenance=False)
    # the fetched page itself is now the session's, so it can be paged or fetched again
    assert url_provenance("s2", "https://example.com/page") == FROM_TOOL


# ── tool wiring ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_lists_web_fetch():
    WebToolProvider = _web_tool_provider_cls()
    tools = {t.name for t in await WebToolProvider().list_tools()}
    assert "web_fetch" in tools  # full set ({web_search, web_fetch, web_extract}) pinned elsewhere


@pytest.mark.asyncio
async def test_tool_web_fetch_truncation_hint(monkeypatch):
    # The tool clamps max_tokens to a 500-token floor (→ 2000-char window), so the
    # body must exceed 2000 chars to truncate. citation URL is the fetched resp.url.
    _patch_net(monkeypatch, _resp("Z" * 5000, ctype="text/plain", url="https://x.com/big"))
    WebToolProvider = _web_tool_provider_cls()
    res = await WebToolProvider().invoke(
        "web_fetch", {"url": "https://x.com/big", "max_tokens": 10}
    )
    assert res.success is True
    assert res.truncated is True
    assert res.metadata["next_index"] == 2000
    assert any("start_index=2000" in h for h in res.recovery_hints)
    # §5 fetch-derived citation: url + the [start, end) char span of this window.
    assert res.metadata["citations"] == [
        {"url": "https://x.com/big", "start_char": 0, "end_char": 2000}
    ]


@pytest.mark.asyncio
async def test_outcome_carries_char_range(monkeypatch):
    # The pipeline records the [start_char, end_char) span of the returned window so a
    # quote can be attributed to a precise offset (and the range tracks pagination).
    _patch_net(monkeypatch, _resp("A" * 200, ctype="text/plain"))
    out = await web_fetch(
        "https://x.com/big", require_provenance=False, max_tokens=10, start_index=40
    )
    assert out.start_char == 40
    assert out.end_char == 80  # 40 + 10*4-char budget
