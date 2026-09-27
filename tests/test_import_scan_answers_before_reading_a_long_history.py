"""The onboarding import at the scale its users have: a months-long history (ledger row 240).

**Measured on origin/main**, on a synthetic history shaped like a real power user's (12,005
conversation files in 5.3 GB — a third of the 15 GB + 2.5 GB one that prompted this — built from
the importers' own record shapes, never from a real history): ``GET /api/onboarding/import``
took 41 s, the step sat on a spinner the whole time, the gateway held 1.1 GB, and "Import 12161
items" was refused outright by a 10,000-item cap. Importing the 10,000 the route allowed took
246 s in one request with no progress, and a visit after it took 82 s. Three causes, each
measured:

* the scan READ EVERY TRANSCRIPT IN FULL, and held every conversation's messages, before it
  answered;
* each file cost 0.2 ms of resolving the same protected locations again (``is_sensitive_path``);
* two quadratic writes: the import ledger was rewritten whole for every conversation, and the
  session index found a session's row by scanning every row.

These rails are counts, not stopwatches, wherever a count can say it: how many lines the scan
parses, how many times it resolves the protected locations, how many ledger writes an import
makes, how many SQLite steps one index update takes. Where time is the claim — the first answer
does not grow with the history's size — it is a RATIO within one test run (the same files, ten
times the bytes), so the machine's speed cancels out, with slack for noise.

Each builds its history in ``tmp_path``; ``HOME``, ``CLAUDE_CONFIG_DIR``, ``CODEX_HOME`` and
``PERSONALCLAW_HOME`` all point there, so nothing reads a developer's real history.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import tracemalloc
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

#: The CI-sized history: as many transcript files as a few weeks of daily use, most of them small.
FILES = 1500
#: The reading pass's thread (:class:`~personalclaw.onboarding_import.activity.ReadingPass`).
READING_THREAD = "onboarding-import-reading"
#: The conversation text a reply carries — what a scan that held conversations would hold.
REPLY = "The flaky test waits on a timer it never cancels, so the next test sees it fire. " * 200


def _transcript(n: int, tool_lines: int) -> str:
    """One Claude Code transcript: a prompt, a long reply, then tool output (never imported)."""
    base = {"cwd": f"/Users/ada/src/app{n % 25}", "sessionId": f"s{n}"}
    lines = [
        {
            **base,
            "type": "user",
            "timestamp": "2026-08-01T10:00:00.000Z",
            "message": {"role": "user", "content": f"Why is build {n} slow?"},
        },
        {
            **base,
            "type": "assistant",
            "timestamp": "2026-08-01T10:00:05.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": REPLY}]},
        },
    ]
    tool = {
        **base,
        "type": "user",
        "timestamp": "2026-08-01T10:00:06.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 900}],
        },
    }
    return "".join(json.dumps(line) + "\n" for line in [*lines, *[tool] * tool_lines])


def _history(root: Path, *, tool_lines: int) -> Path:
    """``FILES`` transcripts across 25 projects, under ``root`` as ``$CLAUDE_CONFIG_DIR``."""
    for n in range(FILES):
        folder = root / "projects" / f"-Users-ada-src-app{n % 25}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{n:08d}-0000-4000-8000-000000000000.jsonl").write_text(
            _transcript(n, tool_lines), encoding="utf-8"
        )
    return root


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh home and a machine whose only agent tool is the Claude Code history built here."""
    from personalclaw import session_search
    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.sources import claude_code
    from personalclaw.onboarding_import.sources.common import READINGS

    home = tmp_path / "pclaw-home"
    home.mkdir()
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    assert config_dir() == home, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    assert claude_code.resolve_root() == tmp_path / "claude", "CLAUDE_CONFIG_DIR did not bind"
    READINGS.clear()
    session_search.reset_for_tests()
    yield tmp_path
    READINGS.clear()
    session_search.reset_for_tests()


def _client() -> TestClient:
    from personalclaw.dashboard.handlers.onboarding_import import (
        register_onboarding_import_routes,
    )

    app = web.Application()
    register_onboarding_import_routes(app)
    return TestClient(TestServer(app))


class _Counts:
    """How often the scan parses a line and resolves the protected locations — the answer's own
    work: the reading pass the answer starts parses on its own thread, and is not counted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from personalclaw import security

        self.lines = 0
        self.protected = 0
        loads, protected_forms = json.loads, security._protected_forms

        def counting_loads(*args, **kwargs):
            if threading.current_thread().name != READING_THREAD:
                self.lines += 1
            return loads(*args, **kwargs)

        def counting_protected(*args, **kwargs):
            self.protected += 1
            return protected_forms(*args, **kwargs)

        monkeypatch.setattr(json, "loads", counting_loads)
        monkeypatch.setattr(security, "_protected_forms", counting_protected)

    def reset(self) -> None:
        self.lines = self.protected = 0


async def _first_answer(client: TestClient) -> tuple[dict, float]:
    started = time.monotonic()
    resp = await client.get("/api/onboarding/import")
    assert resp.status == 200
    return await resp.json(), time.monotonic() - started


def _conversations(body: dict) -> list[dict]:
    claude = next(s for s in body["sources"] if s["source"] == "claude_code")
    return [i for i in claude["items"] if i["category"] == "conversations"]


async def _stop_reading(client: TestClient) -> None:
    """Stop the background reading the GET started, so a test's measurement is its own."""
    from personalclaw.dashboard.handlers.onboarding_import import ACTIVITY

    await asyncio.to_thread(client.app[ACTIVITY].reading.stop, wait=True)


# ── the first answer ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_answer_does_not_grow_with_how_much_history_there_is(machine, monkeypatch):
    """The same 1,500 transcripts, then the same 1,500 with ten times the tool output in each:
    the first answer lists every one both times, parses one line of each (its prompt) both
    times, and takes about as long — it does not read what it does not show.

    On origin/main it parsed every line of every file (22k, then 181k lines), and the second
    answer took ~5x the first. The time rail is a ratio within this run, with slack for noise."""
    counts = _Counts(monkeypatch)
    _history(machine / "claude", tool_lines=12)
    async with _client() as client:
        small, small_seconds = await _first_answer(client)
        small_lines = counts.lines
        await _stop_reading(client)

    import shutil

    from personalclaw.onboarding_import.sources.common import READINGS

    shutil.rmtree(machine / "claude")
    READINGS.clear()
    _history(machine / "claude", tool_lines=120)
    counts.reset()
    async with _client() as client:
        large, large_seconds = await _first_answer(client)
        large_lines = counts.lines
        await _stop_reading(client)

    assert len(_conversations(small)) == len(_conversations(large)) == FILES
    assert small_lines <= FILES + 50, f"{small_lines} lines parsed for {FILES} transcripts"
    assert large_lines <= FILES + 50, f"{large_lines} lines parsed for {FILES} transcripts"
    assert large_seconds < 2 * small_seconds + 1.0, (small_seconds, large_seconds)


@pytest.mark.asyncio
async def test_the_first_answer_says_what_it_has_not_read_yet(machine):
    """Each conversation is named from the start of its file, and says it is not read in full
    rather than giving a count; the answer says how many are read, of how many."""
    _history(machine / "claude", tool_lines=12)
    async with _client() as client:
        body, _seconds = await _first_answer(client)
        await _stop_reading(client)
    conversations = _conversations(body)
    assert {c["title"] for c in conversations} == {f"Why is build {n} slow?" for n in range(FILES)}
    assert all(c["provisional"] for c in conversations)
    assert {c["note"] for c in conversations} == {
        "Not read in full yet. Tool calls come over by name; their output does not."
    }
    claude = next(s for s in body["sources"] if s["source"] == "claude_code")
    assert claude["reading"] == {"read": 0, "of": FILES}


@pytest.mark.asyncio
async def test_the_protected_locations_are_resolved_once_per_scan_not_once_per_file(
    machine, monkeypatch
):
    """Every file the scan opens passes the credential floor. Resolving the platform's protected
    locations for each one was 0.2 ms a file — 2.4 s of a 12,000-file history (measured). Resolved
    once per walk, each file still has its own path resolved."""
    counts = _Counts(monkeypatch)
    _history(machine / "claude", tool_lines=2)
    async with _client() as client:
        await _first_answer(client)
        await _stop_reading(client)
    assert counts.protected < 500, f"{counts.protected} resolutions for {FILES} files"


def _long_claude_transcript(n: int, lines: int) -> str:
    """A Claude Code transcript of ``lines`` short tool results after its prompt."""
    base = {"cwd": "/Users/ada/src/app", "sessionId": f"long{n}"}
    prompt = {
        **base,
        "type": "user",
        "timestamp": "2026-08-01T10:00:00.000Z",
        "message": {"role": "user", "content": f"Long session {n}"},
    }
    tool = {
        **base,
        "type": "user",
        "timestamp": "2026-08-01T10:00:06.000Z",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }
    return json.dumps(prompt) + "\n" + (json.dumps(tool) + "\n") * lines


@pytest.mark.asyncio
async def test_a_scan_answered_while_the_history_is_read_does_not_share_the_interpreter_with_it(
    machine, monkeypatch
):
    """The reading pass parses on its own thread, in the same interpreter as a scan answered
    meanwhile — and beside it, that scan took 7.9 s instead of 0.8 s (measured over 12,005 files).
    So a scan pauses the pass: it waits before each file and every few thousand lines of a long
    one, and parses at most one such stretch while the scan is answered.

    Here the scan lasts half a second and each transcript is three stretches long: left running
    beside it, the pass would parse tens of thousands of lines in that time."""
    from personalclaw.dashboard.handlers import onboarding_import as handler
    from personalclaw.onboarding_import.sources.common import GIVE_WAY_LINES

    folder = machine / "claude" / "projects" / "-Users-ada-src-app"
    folder.mkdir(parents=True)
    for n in range(12):
        (folder / f"{n:08d}-0000-4000-8000-000000000000.jsonl").write_text(
            _long_claude_transcript(n, 3 * GIVE_WAY_LINES), encoding="utf-8"
        )
    beside = {"scanning": False, "lines": 0, "pass_lines": 0}
    loads = json.loads

    def counting_loads(*args, **kwargs):
        if threading.current_thread().name == READING_THREAD:
            beside["pass_lines"] += 1
            if beside["scanning"]:
                beside["lines"] += 1
        return loads(*args, **kwargs)

    monkeypatch.setattr(json, "loads", counting_loads)
    real_scan = handler._scan_with_plans
    at_start: list[dict] = []

    def a_long_scan():
        at_start.append(activity.reading.to_dict())
        beside["scanning"] = True
        try:
            answer = real_scan()
            time.sleep(0.5)
            return answer
        finally:
            beside["scanning"] = False

    monkeypatch.setattr(handler, "_scan_with_plans", a_long_scan)
    async with _client() as client:
        activity = client.app[handler.ACTIVITY]
        await _first_answer(client)
        for _ in range(1000):
            if activity.reading.to_dict()["read"] >= 1:
                break
            await asyncio.sleep(0.005)
        await _first_answer(client)
        await _stop_reading(client)

    assert at_start[1]["running"] and at_start[1]["read"] < at_start[1]["of"], (
        "the pass was still reading when the second scan came",
        at_start,
    )
    assert beside["pass_lines"] > GIVE_WAY_LINES, "the pass read in this test"
    assert beside["lines"] <= GIVE_WAY_LINES, beside


def test_a_reader_gives_way_before_a_file_and_every_few_thousand_lines_of_it(machine):
    """How a reading steps aside inside one long file: its reader calls the pass's wait before the
    first line and every ``GIVE_WAY_LINES`` lines after it — both tools' readers, and a scan that
    is not a reading pass never waits."""
    from personalclaw.onboarding_import.sources import claude_code, codex
    from personalclaw.onboarding_import.sources.common import GIVE_WAY_LINES, READINGS, giving_way

    lines = 2 * GIVE_WAY_LINES + 10
    transcript = machine / "claude-session.jsonl"
    transcript.write_text(_long_claude_transcript(0, lines - 1), encoding="utf-8")
    session = "01a0aaaa-0000-7000-8000-000000000001"
    meta = {"type": "session_meta", "payload": {"id": session, "cwd": "/w"}}
    typed = {"type": "event_msg", "payload": {"type": "user_message", "message": "Why?"}}
    reply = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Because."}],
        },
    }
    rollout = machine / f"rollout-2026-09-01T10-00-00-{session}.jsonl"
    rollout.write_text(
        "".join(json.dumps(line) + "\n" for line in [meta, typed] + [reply] * (lines - 2)),
        encoding="utf-8",
    )

    for read_in_full, path in (
        (claude_code.read_in_full, transcript),
        (codex.read_in_full, rollout),
    ):
        waits: list[int] = []
        with giving_way(lambda: waits.append(1)):
            read_in_full(path)
        assert len(waits) == 3, (path.name, len(waits))
        READINGS.clear()
        read_in_full(path)
        assert len(waits) == 3, "outside a reading pass, nothing waits"


def test_a_scan_holds_no_conversation_and_the_reading_pass_one_at_a_time(machine):
    """The listing is titles and counts; a conversation's messages are read when it is imported.
    On origin/main every conversation's messages were held by the scan at once (1.1 GB for the
    measured history). Measured here with ``tracemalloc``: the look, and the reading pass that
    reads every file in full after it, each peak far below the conversation text the history
    holds (1,500 replies of 16 KB, 24 MB)."""
    from personalclaw.onboarding_import import read_unread, scan_all, unread

    _history(machine / "claude", tool_lines=12)
    held = FILES * len(REPLY)
    tracemalloc.start()
    try:
        results = scan_all(look=True)
        look_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.reset_peak()
        assert read_unread(results) == FILES
        read_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    items = [i for r in results for i in r.items]
    assert len(items) == FILES and all(not i.payload for i in items), "no messages in a scan"
    assert look_peak < held / 3, (look_peak, held)
    assert read_peak < held / 3, (read_peak, held)
    assert unread(scan_all(look=True)) == 0, "what the pass read is what the next scan answers from"


def test_a_looked_then_read_listing_is_the_listing_a_full_read_makes(machine):
    """Parity: after the reading pass, the listing a looking scan gives is — field for field —
    the one reading every file first gives, which is what the step showed before this change."""
    from personalclaw.onboarding_import import read_unread, scan_all
    from personalclaw.onboarding_import.sources.common import READINGS

    _history(machine / "claude", tool_lines=3)
    looked = scan_all(look=True)
    assert any(i.provisional for r in looked for i in r.items)
    read_unread(looked)
    after = [r.to_dict() for r in scan_all(look=True)]
    READINGS.clear()
    full = [r.to_dict() for r in scan_all()]
    assert after == full
    assert all(not i["provisional"] for s in after for i in s["items"])


# ── the import ────────────────────────────────────────────────────────────────


def test_an_import_writes_no_ledger_entry_per_conversation_and_its_transcripts_stay_ours(
    machine, monkeypatch
):
    """The ledger was rewritten whole for every conversation imported — 8 s of a 30 s import of
    3,000, growing with each one. A transcript says itself where it came from, so it needs no
    entry: and a crash between writing one and recording it can no longer leave ours reading as
    someone else's. Here the ledger is deleted after the import, as a crash would lose it."""
    from personalclaw import atomic_write
    from personalclaw.onboarding_import import ImportCategory, plans, run_import, scan_all
    from personalclaw.onboarding_import.writers import state_path

    _history(machine / "claude", tool_lines=2)
    ledger_writes = []
    real = atomic_write._atomic_write

    def spying(path, *args, **kwargs):
        if Path(path).name == "import_state.json":
            ledger_writes.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(atomic_write, "_atomic_write", spying)
    results = scan_all(look=True)
    picks = [
        i.fingerprint
        for r in results
        for i in r.items
        if i.category is ImportCategory.CONVERSATIONS
    ][:200]
    report = run_import(results, fingerprints=picks)
    assert report.counts()["imported"] == 200
    assert ledger_writes == []
    state_path().unlink(missing_ok=True)
    planned = plans(scan_all(look=True))
    assert {planned[fp].state.value for fp in picks} == {"existing"}


def test_an_import_puts_the_setup_before_the_history_and_a_stop_names_the_rest(machine):
    """Every kind of item comes over before any conversation, so an import stopped part-way has
    brought the setup over; what the stop left is named, not dropped."""
    from personalclaw.onboarding_import import ImportCategory, run_import, scan_all

    root = _history(machine / "claude", tool_lines=1)
    (root / "CLAUDE.md").write_text("# Rules\n\n- Run the linter.\n", encoding="utf-8")
    results = scan_all(look=True)
    written: list[ImportCategory] = []
    report = run_import(
        results,
        on_result=lambda item, _result: written.append(item.category),
        stop_before=lambda item: item.category is ImportCategory.CONVERSATIONS and len(written) > 5,
    )
    assert written[0] is ImportCategory.INSTRUCTIONS
    assert written.count(ImportCategory.CONVERSATIONS) == 5
    assert len(report.not_reached) == FILES - 5


def test_the_session_index_costs_the_same_however_many_sessions_it_holds(machine):
    """Each imported conversation is indexed for search as it lands. The index found a session's
    row by its key — an UNINDEXED column of an FTS5 table — so every update read every row: 1.6 ms
    at 1,000 sessions, 17 ms at 6,000 (measured), half of an import's time. Counted here in SQLite
    VM steps rather than seconds: an update at 1,000 sessions costs what one at 50 does."""
    from personalclaw import session_search

    conn = session_search._connect()
    assert conn is not None

    def steps_to_update(key: str) -> int:
        steps = 0

        def tick() -> int:
            nonlocal steps
            steps += 1
            return 0

        conn.set_progress_handler(tick, 100)
        try:
            assert session_search.index_session(key, "title", "body text " * 200)
        finally:
            conn.set_progress_handler(None, 0)
        return steps

    for n in range(50):
        session_search.index_session(f"s{n}", "t", "words " * 100)
    early = steps_to_update("s10")
    for n in range(50, 1000):
        session_search.index_session(f"s{n}", "t", "words " * 100)
    late = steps_to_update("s10")
    assert late < early * 2 + 20, (early, late)


def test_after_an_import_the_history_is_listed_and_kept_searchable_without_rereading_it(
    machine, monkeypatch
):
    """What the gateway does with an imported history afterwards. The chat list counted each
    imported chat's messages by reading its whole file, since the import did not record the count
    the dashboard's own save records. And the search index's pass — at start and every five
    minutes — re-read every transcript to see whether it had changed: 7.4 s and 883 MB for 12,005
    imported conversations (measured), every five minutes. Now the count is recorded, and the pass
    compares each file's time and size with those it indexed."""
    from personalclaw import history, session_search
    from personalclaw.onboarding_import import ImportCategory, run_import, scan_all

    _history(machine / "claude", tool_lines=2)
    results = scan_all(look=True)
    picks = [
        i.fingerprint
        for r in results
        for i in r.items
        if i.category is ImportCategory.CONVERSATIONS
    ][:300]
    assert run_import(results, fingerprints=picks).counts()["imported"] == 300

    counted: list[int] = []
    reread: list[str] = []
    count_lines, read_messages = history._count_message_lines, history.ConversationLog.read_messages

    def counting(lines):
        counted.append(1)
        return count_lines(lines)

    def reading(self, key, *args, **kwargs):
        reread.append(key)
        return read_messages(self, key, *args, **kwargs)

    monkeypatch.setattr(history, "_count_message_lines", counting)
    monkeypatch.setattr(history.ConversationLog, "read_messages", reading)
    listed = [
        s
        for s in history.ConversationLog().list_sessions()
        if s["key"].startswith("dashboard_claude-code-")
    ]
    assert len(listed) == 300 and {s["messages"] for s in listed} == {2}
    assert counted == [], "the list counted lines instead of reading the recorded count"
    assert session_search.reindex_all(limit=200) == 0
    assert reread == [], f"the index pass re-read {len(reread)} unchanged transcripts"

    # The pass still sees a change, and reads only what changed.
    changed = history.ConversationLog()
    changed.append(listed[0]["key"], "user", "and the zebra migration?")
    assert session_search.reindex_all(limit=200) == 1
    assert set(reread) == {listed[0]["key"]}
    assert [r["key"] for r in session_search.search_sessions("zebra")] == [listed[0]["key"]]


@pytest.mark.asyncio
async def test_a_history_imports_as_a_job_within_a_generous_ceiling(machine):
    """End to end over the route: every conversation of a 1,500-file history lands, as a job
    whose progress counts up to the pick. A ceiling, not a benchmark: it catches an import that
    has gone quadratic again (origin/main: 40 items a second at 10,000, falling as it grew)."""
    from personalclaw.history import ConversationLog

    _history(machine / "claude", tool_lines=2)
    async with _client() as client:
        body, _seconds = await _first_answer(client)
        picks = [c["fingerprint"] for c in _conversations(body)]
        started = time.monotonic()
        resp = await client.post("/api/onboarding/import", json={"fingerprints": picks})
        assert resp.status == 202
        seen_progress = set()
        for _ in range(6000):
            job = (await (await client.get("/api/onboarding/import/job")).json())["job"]
            seen_progress.add(job["done"])
            if job["status"] != "running":
                break
            await asyncio.sleep(0.01)
        seconds = time.monotonic() - started
    assert job["status"] == "done", job
    assert job["counts"]["imported"] == job["done"] == job["total"] == FILES
    assert len(seen_progress) > 2, "the job reports its progress as it goes"
    assert seconds < 120, f"{FILES} conversations took {seconds:.0f} s"
    keys = {s["key"] for s in ConversationLog().list_sessions()}
    assert len([k for k in keys if k.startswith("dashboard_claude-code-")]) == FILES
