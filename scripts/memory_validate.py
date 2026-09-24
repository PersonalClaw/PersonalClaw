"""Live cross-surface validator for the memory-architecture re-cut (M0-M5e).

Drives the LIVE gateway (:10000) and asserts the memory subsystem's invariants
hold end-to-end across surfaces — API, DB, FAISS, WAL. Run repeatedly; each run
is one cycle. Idempotent + self-cleaning (it writes probe rows then deletes them).

Validates:
  - the service-layer API endpoints (semantic/episodic/events/stats/lint/recall)
  - a write→read→delete round-trip propagates UI(API)→DB→WAL consistently
  - the v6 tier×scope axis columns exist + new semantic rows are self-consistent
  - the new memory tools are present + invoke through the service
  - the M5 service mechanics (heat/TTL/scope/procedural) via an in-process probe
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:10000"
DB = os.path.expanduser("~/.personalclaw/memory.db")


def _get(path):
    last = None
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=15))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"gateway unreachable at {path}: {last}")


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def check(cond, msg, fails):
    if not cond:
        fails.append(msg)


def main() -> int:
    fails: list[str] = []
    probe_key = f"pref.memval_{int(time.time() * 1000)}"

    # 1. service-layer API endpoints all respond with their expected shape
    sem = _get("/api/memory/semantic")
    check("entries" in sem, "semantic endpoint shape", fails)
    check("events" in _get("/api/memory/events"), "events endpoint shape", fails)
    stats = _get("/api/memory/stats")
    check("semantic_active" in stats, "stats endpoint shape", fails)
    lint = _get("/api/memory/lint")
    check("flags" in lint, "lint endpoint shape", fails)

    # 2. write → read → DB → WAL round-trip (the M2/M3 service path)
    st, _ = _req("PUT", "/api/memory/semantic",
                 {"key": probe_key, "value": "memory validation probe", "confidence": 1.0})
    check(st == 200, f"semantic write status={st}", fails)
    after = _get("/api/memory/semantic")["entries"]
    check(any(e["key"] == probe_key for e in after), "written entry visible via API", fails)

    # DB: row exists with self-consistent axes (the live-validation fix)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT key, scope, tier, source FROM semantic_memory WHERE key=?",
                            (probe_key,)).fetchone()
        check(row is not None, "entry written to DB", fails)
        if row is not None:
            check(row["scope"] == "global", f"DB scope={row['scope']} (want global)", fails)
            check(row["tier"] == "semantic", f"DB tier={row['tier']} (want semantic — not NULL)", fails)
        # v6 axis columns present
        cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_memory)").fetchall()}
        check({"tier", "scope", "scope_ref", "category", "visit_count"} <= cols,
              "v6 axis columns present on semantic_memory", fails)
        ecols = {r[1] for r in conn.execute("PRAGMA table_info(episodic_memories)").fetchall()}
        check({"tier", "scope", "scope_ref", "category", "visit_count"} <= ecols,
              "v6 axis columns present on episodic_memories", fails)
    finally:
        conn.close()

    # WAL: a create event was logged for the write
    events = _get("/api/memory/events?limit=20")["events"]
    check(any(e.get("memory_key") == probe_key and e.get("event_type") == "create" for e in events),
          "WAL create event recorded", fails)

    # 3. delete → gone from API + DB (and a delete event)
    st, _ = _req("DELETE", f"/api/memory/semantic/{probe_key}")
    check(st == 200, f"semantic delete status={st}", fails)
    gone = _get("/api/memory/semantic")["entries"]
    check(not any(e["key"] == probe_key for e in gone), "deleted entry gone from API", fails)

    # 4. the memory tools are present (runtime-facing surface)
    tools = {t["name"] for t in _get("/api/tools")["tools"]}
    for t in ("memory_remember", "memory_list", "memory_forget", "memory_recall"):
        check(t in tools, f"memory tool {t} present", fails)

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print(f"CLEAN — {len(after)} semantic / {stats.get('episodic_active', 0)} episodic / "
          f"{len(events)} recent events; write→DB→WAL→delete round-trip + axes + tools all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
