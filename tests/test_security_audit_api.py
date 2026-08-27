"""SECURITY-HARDENING SH-8 — the SEL audit surface.

Rails for the four properties this surface can silently get wrong:

* **Pagination stability.** The log is append-only and read newest-first, so an
  offset scheme duplicates rows after a concurrent append and SKIPS rows after a
  prune. Skipping is the one that matters: the surface would omit events while
  looking complete. Proven by appending BETWEEN two page fetches.
* **Authorization.** The audit trail spans every actor on the instance, so an
  app-scoped token is refused categorically — with a 403, never a 200 carrying an
  empty list (which reads as "your agent did nothing").
* **Credential safety.** Records carry truncated real tool arguments. A planted
  secret must not survive into the read surface, and the per-record integrity
  verdict must still be computed on the RAW line — redacting first would rewrite
  the payload the HMAC covers and report every record as tampered.
* **Fail-closed filtering.** A malformed filter is refused, not ignored. An
  ignored filter returns the whole log while looking like it narrowed it.

Isolation: ``PERSONALCLAW_HOME`` is redirected per test, and ``conftest``'s autouse
``_reset_sel_singleton`` guarantees the handler's ``sel()`` binds to it rather than
inheriting an earlier test's directory. ``test_real_home_untouched`` asserts the
outcome directly — SEL events are exactly the state that leaks into a real home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.security_audit import register_security_audit_routes
from personalclaw.sel import _VERIFY_WINDOW, SecurityEvent, sel

SECRET = "sk-ant-api03-PLANTEDoTTERsecretVALUE0123456789abcdefXYZ"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


def _client(app_name: str = "") -> TestClient:
    app = web.Application()
    if app_name:
        # Mirrors what the auth middleware stamps for an app-scoped token.
        @web.middleware
        async def stamp_app(request, handler):
            request["app"] = app_name
            return await handler(request)

        app.middlewares.append(stamp_app)
    register_security_audit_routes(app)
    return TestClient(TestServer(app))


def _write(n: int, *, prefix: str = "e", **overrides) -> list[str]:
    """Append ``n`` events through the real writer; return their ids, oldest first."""
    ids = []
    for i in range(n):
        ev = SecurityEvent(
            event_id=f"{prefix}{i:04d}",
            timestamp=f"2026-08-{(i % 27) + 1:02d}T12:00:{i % 60:02d}+00:00",
            event_type="tool_invocation",
            caller_identity=overrides.get("caller_identity", "dashboard:abc"),
            agent="personalclaw",
            source="dashboard",
            operation=overrides.get("operation", "execute_bash"),
            outcome=overrides.get("outcome", "completed"),
            downstream_service=overrides.get("downstream_service", ""),
            resources=overrides.get("resources", f"cmd {i}"),
        )
        sel().log(ev)
        ids.append(ev.event_id)
    return ids


async def _get(client: TestClient, url: str) -> tuple[int, dict]:
    resp = await client.get(url)
    return resp.status, await resp.json()


# ── Pagination stability ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_pages_are_disjoint_under_concurrent_appends():
    """THE rail. Page 1, then append, then page 2 — no row may repeat or vanish.

    A naive ``offset`` fails here: the 5 appends shift the newest-first list by 5, so
    ``[5:10]`` re-serves exactly the 5 rows page 1 already showed.
    """
    written = _write(10)
    async with _client() as client:
        status, page1 = await _get(client, "/api/security/audit?limit=5")
        assert status == 200
        first = [e["event_id"] for e in page1["events"]]
        assert first == list(reversed(written))[:5], "page 1 is the 5 newest, newest first"
        assert page1["next_cursor"] == first[-1]

        # A concurrent writer lands 5 NEW events between the two page fetches.
        _write(5, prefix="late")

        status, page2 = await _get(
            client, f"/api/security/audit?limit=5&cursor={page1['next_cursor']}"
        )
        assert status == 200
        second = [e["event_id"] for e in page2["events"]]

    assert not set(first) & set(second), f"pages overlap: {sorted(set(first) & set(second))}"
    assert second == list(reversed(written))[5:10], "page 2 is the next 5 older, none skipped"
    assert first + second == list(reversed(written)), "the two pages tile the original run exactly"


@pytest.mark.asyncio
async def test_next_cursor_empty_at_end_of_log():
    """A cursor is only handed out when it leads somewhere — no empty trailing page."""
    _write(3)
    async with _client() as client:
        _, page = await _get(client, "/api/security/audit?limit=5")
    assert page["count"] == 3
    assert page["next_cursor"] == ""


@pytest.mark.asyncio
async def test_expired_cursor_is_refused_not_restarted():
    """An anchor no longer in the log fails CLOSED. Restarting from the newest record
    would silently re-serve the entire trail as if it were a fresh page."""
    _write(3)
    async with _client() as client:
        status, body = await _get(client, "/api/security/audit?cursor=nosuchevent")
    assert status == 400
    assert body["error"]["code"] == "invalid_cursor"
    assert "events" not in body


# ── Authorization ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/security/audit", "/api/security/audit/verify"])
@pytest.mark.asyncio
async def test_app_token_is_refused_with_403_not_empty_results(path):
    """An app gets nothing — and is TOLD nothing, rather than handed a 200 with an
    empty list, which on an audit surface reads as "no events exist"."""
    _write(3)
    async with _client(app_name="growth") as client:
        status, body = await _get(client, path)
    assert status == 403
    assert body["error"]["code"] == "audit_owner_only"
    assert "events" not in body


@pytest.mark.asyncio
async def test_owner_request_is_allowed():
    """The counterpart: the 403 above is about the app identity, not a broken route."""
    _write(3)
    async with _client() as client:
        status, body = await _get(client, "/api/security/audit")
    assert status == 200
    assert body["count"] == 3


@pytest.mark.asyncio
async def test_app_refusal_is_itself_audited(home):
    """The refusal is logged. A denied read of the audit trail is exactly the event an
    audit trail exists to record."""
    async with _client(app_name="growth") as client:
        await _get(client, "/api/security/audit")
    lines = (home / "security_events.jsonl").read_text().splitlines()
    denials = [json.loads(ln) for ln in lines if ln.strip()]
    assert any(
        d["outcome"] == "denied" and d["caller_identity"] == "app:growth" for d in denials
    ), f"no denial recorded, got {[d.get('operation') for d in denials]}"


# ── Credential safety ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planted_secret_does_not_reach_the_read_surface():
    _write(1, resources=f"curl -H 'authorization: Bearer {SECRET}' https://x.test")
    async with _client() as client:
        resp = await client.get("/api/security/audit")
        raw = await resp.text()
        body = json.loads(raw)
    assert SECRET not in raw, "the plaintext secret survived into the audit response"
    assert "REDACTED" in body["events"][0]["resources"]


@pytest.mark.asyncio
async def test_integrity_is_computed_before_redaction():
    """The ordering landmine: redaction rewrites the very bytes the HMAC covers, so a
    record containing a secret must still verify. Redact-then-verify would mark every
    such record tampered — an audit surface crying wolf on its own honest records."""
    _write(1, resources=f"export TOKEN={SECRET}")
    async with _client() as client:
        _, body = await _get(client, "/api/security/audit")
    row = body["events"][0]
    assert "REDACTED" in row["resources"], "precondition: this row really was redacted"
    assert row["integrity_ok"] is True


@pytest.mark.asyncio
async def test_chain_hashes_survive_redaction():
    """``entry_hash``/``prev_hash`` pass through untouched, so an exported record stays
    verifiable by anyone holding the key."""
    _write(2)
    async with _client() as client:
        _, body = await _get(client, "/api/security/audit")
    for row in body["events"]:
        assert len(row["entry_hash"]) == 64 and int(row["entry_hash"], 16) >= 0


# ── Tamper evidence ──────────────────────────────────────────────────────────


def _tamper(home: Path, index: int) -> str:
    """Alter one record in place without re-signing it. Returns its event_id."""
    path = home / "security_events.jsonl"
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    rec = json.loads(lines[index])
    rec["resources"] = "ALTERED AFTER THE FACT"
    lines[index] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    return rec["event_id"]


@pytest.mark.asyncio
async def test_tampered_record_is_flagged_per_row(home):
    """The deliberately-broken chain link the page must show."""
    _write(4)
    victim = _tamper(home, 1)
    async with _client() as client:
        _, body = await _get(client, "/api/security/audit")
    by_id = {e["event_id"]: e for e in body["events"]}
    assert by_id[victim]["integrity_ok"] is False
    assert all(
        e["integrity_ok"] is True for e in body["events"] if e["event_id"] != victim
    ), "only the altered record may be flagged — a blanket false is not tamper evidence"


@pytest.mark.asyncio
async def test_verify_reports_checked_and_ok(home):
    _write(4)
    async with _client() as client:
        _, clean = await _get(client, "/api/security/audit/verify")
        assert clean == {
            "checked": 4,
            "ok": True,
            "valid": 4,
            "tampered": 0,
            "windowed": True,
            # WHICH cap was applied. `windowed` says one was set; a consumer cannot tell
            # "I stopped at 5000" from "5000 is the whole log" without the size — and the
            # dashboard was rendering the count as if it were the whole chain. Asserted as
            # the exact envelope on purpose: a field added to a tamper-evidence response
            # should have to be declared here.
            "window": _VERIFY_WINDOW,
        }

        _tamper(home, 2)
        _, dirty = await _get(client, "/api/security/audit/verify")
    assert dirty["ok"] is False
    assert dirty["checked"] == 4 and dirty["tampered"] == 1


@pytest.mark.asyncio
async def test_verify_on_empty_log_is_ok():
    """0 of 0 records tampered. An empty log is clean, not broken."""
    async with _client() as client:
        _, body = await _get(client, "/api/security/audit/verify")
    assert body == {
        "checked": 0,
        "ok": True,
        "valid": 0,
        "tampered": 0,
        "windowed": True,
        "window": _VERIFY_WINDOW,
    }


@pytest.mark.asyncio
async def test_verify_says_which_cap_it_applied(home):
    """`window` is what lets a consumer tell a truncated pass from a complete one.

    The dashboard used to render `checked` alone, so a capped verification read as
    "Chain intact — 5000 events verified" on a tamper-evidence surface. `windowed`
    cannot fix that by itself: it reports that a cap was SET, and on a short log
    (43 entries, cap 5000) it is true while nothing was left out — so trusting it
    alone understates a complete answer as badly as the count overstated a partial
    one. The size is the missing fact, and it is cheap: a total would cost the O(n)
    walk this window exists to avoid.
    """
    _write(3)
    async with _client() as client:
        _, capped = await _get(client, "/api/security/audit/verify")
        _, whole = await _get(client, "/api/security/audit/verify?full=1")

    # A cap was set but never bit: 3 < 5000, so a consumer can prove the answer is complete.
    assert capped["windowed"] is True
    assert capped["window"] == _VERIFY_WINDOW
    assert capped["checked"] == 3
    assert capped["checked"] < capped["window"]

    # An exhaustive pass reports NO cap at all, rather than a cap of infinity.
    assert whole["windowed"] is False
    assert whole["window"] is None
    assert whole["checked"] == 3


# ── Filters: they work, and they fail closed ─────────────────────────────────


@pytest.mark.asyncio
async def test_each_filter_narrows():
    _write(2, prefix="a", operation="execute_bash", outcome="completed", caller_identity="cron:x")
    _write(
        2,
        prefix="b",
        operation="fetch_url",
        outcome="denied",
        caller_identity="dashboard:y",
        downstream_service="brave-search",
    )
    async with _client() as client:
        for query, expected in (
            ("operation=fetch_url", 2),
            ("outcome=denied", 2),
            ("caller=cron", 2),
            ("downstream_service=brave", 2),
            ("operation=fetch_url&outcome=completed", 0),  # AND, not OR
        ):
            status, body = await _get(client, f"/api/security/audit?{query}")
            assert status == 200, body
            assert body["count"] == expected, f"{query} -> {body['count']}, want {expected}"


@pytest.mark.asyncio
async def test_time_bounds_are_inclusive_of_the_named_day():
    """A date-only ``until`` must include events ON that day. Comparing against bare
    "YYYY-MM-DD" would mean midnight and exclude the whole day — a false "no events"."""
    _write(5)  # timestamps 2026-08-01 .. 2026-08-05
    async with _client() as client:
        _, body = await _get(client, "/api/security/audit?until=2026-08-02")
        assert body["count"] == 2, [e["timestamp"] for e in body["events"]]
        _, body = await _get(client, "/api/security/audit?since=2026-08-04")
        assert body["count"] == 2


@pytest.mark.parametrize(
    "query,code",
    [
        ("caler=cron", "unknown_filter"),  # a typo must not silently widen the result
        ("limit=abc", "invalid_limit"),
        ("limit=0", "invalid_limit"),
        ("limit=99999", "invalid_limit"),
        ("since=last-tuesday", "invalid_time_filter"),
        ("until=08%2F16%2F2026", "invalid_time_filter"),
    ],
)
@pytest.mark.asyncio
async def test_malformed_request_is_refused_not_ignored(query, code):
    _write(3)
    async with _client() as client:
        status, body = await _get(client, f"/api/security/audit?{query}")
    assert status == 400, body
    assert body["error"]["code"] == code
    assert "events" not in body, "a refused request must not also return data"


# ── Isolation ────────────────────────────────────────────────────────────────


def test_real_home_untouched(home):
    """SEL events are the classic real-home leak. Assert the outcome, not the fixture."""
    _write(2)
    assert (home / "security_events.jsonl").exists(), "precondition: events went to the tmp home"
    real = Path.home() / ".personalclaw" / "security_events.jsonl"
    before = real.stat().st_mtime if real.exists() else None
    _write(2, prefix="more")
    after = real.stat().st_mtime if real.exists() else None
    assert before == after, "the real home's SEL log was written during this test"
