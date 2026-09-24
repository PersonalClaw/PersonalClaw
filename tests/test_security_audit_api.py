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
        # The cursor is an opaque `<byte offset>.<event_id>` anchor, not a bare id. The offset is
        # what makes the next page an O(page) read from where this one stopped instead of a
        # re-scan from the tail — the change that made the whole log reachable (#593). The id
        # half is what lets the server prove the offset still points at the record the client
        # was handed, so a cursor stale from a prune fails closed.
        offset, _, anchor_id = page1["next_cursor"].partition(".")
        assert offset.isdigit() and int(offset) > 0, page1["next_cursor"]
        assert anchor_id == first[-1], "the anchor names the last row of the page"

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


@pytest.mark.parametrize(
    "cursor",
    [
        "nosuchevent",  # a bare id — the pre-#593 token shape, and any junk
        "999999999.e0000",  # an offset past the end of the file
        "7.e0000",  # an offset that is not a line start (mid-record)
        "0.notthisone",  # a real line start, but naming a different record
    ],
)
@pytest.mark.asyncio
async def test_expired_cursor_is_refused_not_restarted(cursor):
    """An anchor that no longer holds fails CLOSED. Restarting from the newest record would
    silently re-serve the entire trail as if it were a fresh page — and BOTH halves of the token
    have to be checked, or a stale offset resumes at whatever record now happens to live there."""
    _write(3)
    async with _client() as client:
        status, body = await _get(client, f"/api/security/audit?cursor={cursor}")
    assert status == 400, body
    assert body["error"]["code"] == "invalid_cursor"
    assert "events" not in body


@pytest.mark.asyncio
async def test_a_cursor_stale_from_a_prune_is_refused(home):
    """The concrete case the id half exists for. ``prune()`` rewrites the file, so every byte
    offset shifts; the anchor still points at a line start, just not the client's record."""
    _write(30)
    async with _client() as client:
        _, page1 = await _get(client, "/api/security/audit?limit=5")
        cursor = page1["next_cursor"]
        # Drop the oldest 10 records the way a prune does — same file, every offset moved.
        path = home / "security_events.jsonl"
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        path.write_text("\n".join(lines[10:]) + "\n")
        status, body = await _get(client, f"/api/security/audit?limit=5&cursor={cursor}")
    assert status == 400, body
    assert body["error"]["code"] == "invalid_cursor"


# ── Reachability: the budget is not a wall (issue #593) ──────────────────────


@pytest.mark.asyncio
async def test_the_whole_log_is_reachable_past_the_scan_budget(monkeypatch):
    """THE #593 rail. Every record must be reachable by paging, however long the log is.

    This used to be false by construction: the scan bound was ``scan_cap=_MAX_ENTRIES`` applied to
    the WHOLE log, so records older than the newest 50,000 were unreachable at any page depth.
    Measured on a real 63,653-entry log: 13,653 rows (21.4%) could not be read at all, and the
    walk to that wall cost 525s across 250 pages because each page re-read the entire tail.

    The budget is shrunk here so the test crosses it in milliseconds; the shape is identical.
    """
    monkeypatch.setattr("personalclaw.sel._AUDIT_PAGE_SCAN_BUDGET", 7)
    written = _write(60)

    seen: list[str] = []
    cursor = ""
    async with _client() as client:
        for _request in range(200):  # a ceiling, so a non-advancing cursor fails loudly
            url = f"/api/security/audit?limit=5{f'&cursor={cursor}' if cursor else ''}"
            status, page = await _get(client, url)
            assert status == 200, page
            seen.extend(e["event_id"] for e in page["events"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        else:
            pytest.fail(f"paging did not terminate; reached {len(seen)} of {len(written)}")

    assert len(seen) == len(set(seen)), "a row was served twice"
    assert seen == list(reversed(written)), "the pages must tile the log exactly, newest first"


@pytest.mark.asyncio
async def test_a_budget_stop_hands_back_a_usable_anchor(monkeypatch):
    """A page that runs out of budget before filling up says so AND says where it stopped.

    ``truncated`` without a cursor is the old defect restated: "there is more, and you cannot
    have it". The pairing is the contract — a sparse filter deep in a long log is exactly the
    query that hits this, and it must make progress rather than report an empty log.
    """
    monkeypatch.setattr("personalclaw.sel._AUDIT_PAGE_SCAN_BUDGET", 5)
    _write(40, prefix="noise", outcome="ok")
    rare = _write(1, prefix="rare", outcome="denied")[0]

    async with _client() as client:
        _, page = await _get(client, "/api/security/audit?limit=5&outcome=denied")
        assert page["count"] == 1 and page["events"][0]["event_id"] == rare, "the newest match"
        assert page["truncated"] is True, "40 older records remain and the budget bit"
        assert page["next_cursor"], "truncated must always be paired with somewhere to go"

        # And the walk terminates on the START of the log, not on the budget.
        total, cursor, requests = page["count"], page["next_cursor"], 1
        while cursor and requests < 200:
            _, page = await _get(
                client, f"/api/security/audit?limit=5&outcome=denied&cursor={cursor}"
            )
            total += page["count"]
            cursor = page["next_cursor"]
            requests += 1
        assert not cursor, "paging did not terminate"
        assert page["truncated"] is False, "the last page reached the log's start, not a budget"
    assert total == 1, "exactly the one matching record, found across budgeted pages"


@pytest.mark.asyncio
async def test_the_first_page_reads_only_what_it_serves():
    """The other half of #593's cost: page 1 used to read the newest 50,000 lines before choosing
    a single row (2.1s on a 63,653-entry log). ``scanned`` is the observable — it must be about
    the page size, not about the log size."""
    _write(400)
    async with _client() as client:
        _, page = await _get(client, "/api/security/audit?limit=10")
    assert page["count"] == 10
    assert page["scanned"] <= 12, f"read {page['scanned']} lines to serve 10 rows"


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


#: One over the window. Every other verify test here sits at 3-4 entries, so the cap has
#: never actually BITTEN in this file — and "the cap bit" is the whole state issue #536 was
#: filed about. Deliberately the smallest chain that crosses the line: the two tests below
#: cost ~0.8s each to write, and a bigger one buys no additional proof.
_OVER_WINDOW = _VERIFY_WINDOW + 7


@pytest.mark.asyncio
async def test_verify_on_a_chain_longer_than_the_window_reports_a_partial_pass(home):
    """The state the bug report was filed from: 62,907 entries, 5,000 checked.

    Reproduced at the boundary rather than at 62,907. Both facts the consumer needs must be
    on the wire: `checked == window` (the cap bit, so entries were left out) and, from
    `?full=1`, that an exhaustive pass exists and reports a DIFFERENT, larger count. The
    second half is the vacuity floor — a handler hard-coded to say "partial" would pass the
    first three assertions and fail here.
    """
    _write(_OVER_WINDOW)
    on_disk = sum(
        1 for ln in (home / "security_events.jsonl").read_text().splitlines() if ln.strip()
    )
    assert on_disk == _OVER_WINDOW

    async with _client() as client:
        _, partial = await _get(client, "/api/security/audit/verify")
        _, whole = await _get(client, "/api/security/audit/verify?full=1")

    assert partial["checked"] == _VERIFY_WINDOW
    assert partial["window"] == _VERIFY_WINDOW
    assert partial["windowed"] is True
    # The cap BIT — this is the comparison `capped()` makes in the panel, and the reason the
    # verdict may not be worded as a statement about the chain.
    assert partial["checked"] >= partial["window"]
    assert partial["checked"] < on_disk

    assert whole["checked"] == on_disk
    assert whole["window"] is None
    assert whole["windowed"] is False


@pytest.mark.asyncio
async def test_a_tamper_below_the_window_is_invisible_to_the_default_check(home):
    """WHY the verdict may not claim the chain: the default check cannot see this tamper.

    The oldest record is altered in place. The windowed pass returns a clean `ok: true`
    having never read it; only `?full=1` finds it. So "Chain intact" from the default call
    is not a weaker claim than the truth — on this log it is the OPPOSITE of it, which is
    what makes stating the examined scope a correctness fix and not a wording preference.
    """
    _write(_OVER_WINDOW)
    victim = _tamper(home, 0)

    async with _client() as client:
        _, partial = await _get(client, "/api/security/audit/verify")
        _, whole = await _get(client, "/api/security/audit/verify?full=1")

    assert partial["ok"] is True, "the windowed pass cannot see below its own window"
    assert partial["tampered"] == 0
    assert partial["checked"] == _VERIFY_WINDOW

    assert whole["ok"] is False, f"the exhaustive pass must find the altered {victim}"
    assert whole["tampered"] == 1
    assert whole["checked"] == _OVER_WINDOW


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
async def test_the_pills_can_select_the_bulk_of_the_log():
    """Issue #535. Measured on a live 1,040-entry log, ``outcome=ok`` was 1,021 rows (98.2%) and
    matched NEITHER shipped pill, so the two filters together reached 6 rows — 0.6%. An operator
    could narrow to what went wrong and never to what happened, and on an audit surface "no
    matching events" reads as "nothing happened".
    """
    _write(50, prefix="ok", outcome="ok")
    _write(3, prefix="d", outcome="denied")
    _write(2, prefix="f", outcome="hook_error")

    async with _client() as client:
        _, page = await _get(client, "/api/security/audit?limit=1")
        families = {f["key"]: f for f in page["outcome_families"]}
        assert "ok" in families, "the success vocabulary must be OFFERED, not merely classified"

        counts = {}
        for key, family in families.items():
            joined = ",".join(family["values"])
            _, hit = await _get(client, f"/api/security/audit?limit=200&outcome={joined}")
            counts[key] = hit["count"]

    assert counts["ok"] == 50, counts
    assert counts["denied"] == 3, counts
    assert counts["failed"] == 2, "hook_error is a failure — the token match keeps the prefix"
    assert sum(counts.values()) == 55, f"every row is selectable by some pill: {counts}"


@pytest.mark.asyncio
async def test_every_family_ships_a_tone_and_every_row_is_stamped_with_one():
    """The tone and the pill are ONE decision, made server-side.

    The panel used to hold its own ``outcome -> colour`` map and it had drifted: ``not_found`` is a
    member of the ``failed`` family and had no entry, so a record the Failed pill calls a failure
    rendered in neutral grey. Asserted per row, because that is the surface an operator reads.
    """
    _write(1, prefix="nf", outcome="not_found")
    _write(1, prefix="okk", outcome="ok")
    _write(1, prefix="nc", outcome="needs_confirm")
    _write(1, prefix="unk", outcome="halted_on_budget")

    async with _client() as client:
        _, page = await _get(client, "/api/security/audit?limit=10")

    for family in page["outcome_families"]:
        assert family["tone"], f"{family['key']} shipped no tone"
    tone = {e["outcome"]: e["outcome_tone"] for e in page["events"]}
    assert tone == {
        "not_found": "danger",  # was neutral grey while the Failed pill called it a failure
        "ok": "success",
        "needs_confirm": "warning",
        # Unclassified stays neutral. Guessing here is how the log would assert a verdict
        # nobody decided.
        "halted_on_budget": "neutral",
    }, tone


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


# ── The reserved auth param is not a filter (issue 2927) ─────────────────────


@pytest.mark.parametrize(
    "query",
    ["token=TOK", "token=TOK&outcome=denied&limit=5", "outcome=denied&token=TOK"],
)
@pytest.mark.asyncio
async def test_the_query_token_auth_param_is_not_an_unknown_filter(query):
    """`?token=` is the gateway's query-token credential. The auth middleware reads it and
    deliberately does NOT strip it, so it reaches every handler — and this was the one route
    that diffed the WHOLE query string against a filter allowlist. The result was a catch-22
    no client could get out of: without the token, auth answered 403; with it, this handler
    answered 400 `unknown_filter`. There was no way for a query-token client to read the audit
    trail at all, including a deep-link to the audit URL with the startup token before the
    cookie is set.
    """
    _write(3, outcome="denied")
    async with _client() as client:
        status, body = await _get(client, f"/api/security/audit?{query}")
    assert status == 200, body
    assert "events" in body


@pytest.mark.asyncio
async def test_the_token_param_never_reaches_the_sel_query_as_a_filter():
    """Subtracted, NOT added to the filter allowlist. `token` is a credential, and a credential
    that leaked into the SEL query would be matched against a record field — which is both a
    silently-empty page and a credential in a query path."""
    from personalclaw.dashboard.handlers import security_audit as H
    from personalclaw.dashboard.token_auth import RESERVED_QUERY_PARAMS

    assert "token" in RESERVED_QUERY_PARAMS
    assert "token" not in H._QUERY_PARAMS
    assert "token" not in H._FILTER_PARAMS

    _write(4, outcome="denied")
    async with _client() as client:
        _, with_token = await _get(client, "/api/security/audit?token=TOK&outcome=denied")
        _, without = await _get(client, "/api/security/audit?outcome=denied")
    assert with_token["count"] == without["count"] == 4, (with_token, without)


@pytest.mark.asyncio
async def test_a_typod_filter_is_still_refused_alongside_the_token():
    """The fail-closed check must survive the exemption — subtracting one reserved credential
    must not become "ignore anything unrecognised", which is the behaviour the check exists to
    prevent."""
    _write(3)
    async with _client() as client:
        status, body = await _get(client, "/api/security/audit?token=TOK&caler=cron")
    assert status == 400, body
    assert body["error"]["code"] == "unknown_filter"
    assert "caler" in body["error"]["message"]
    assert "token" not in body["error"]["message"], (
        "the refusal must not name the credential — it is not the caller's mistake, and echoing "
        "the param name next to a value is how a token ends up in a log"
    )


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
