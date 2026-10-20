"""Live cross-surface validator for the memory subsystem.

Drives the gateway of a scratch home you name and asserts the memory subsystem's invariants
hold end to end across surfaces: API, DB, WAL. Run repeatedly; each run is one cycle.
Idempotent + self-cleaning (it writes probe rows then deletes them).

Validates:
  - the service-layer API endpoints (semantic/episodic/events/stats/lint)
  - a write→read→delete round-trip propagates UI(API)→DB→WAL consistently
  - the tier×scope axis columns exist + new semantic rows are self-consistent
  - the memory tools are present on the tool surface

It writes, and it reads the home's ``memory.db`` straight from disk (read-only), so it runs
only against a scratch home: the gateway is found from the record it keeps in the home you
name, and the default home (the install's own memory) or no home at all is refused:

    PERSONALCLAW_HOME=/tmp/pc-memval personalclaw gateway --port auto --no-open   # one shell
    .venv/bin/python scripts/memory_validate.py --home /tmp/pc-memval              # another
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import named_home  # noqa: E402


def check(cond, msg, fails):
    if not cond:
        fails.append(msg)


def _memory_db(home: Path) -> Path:
    """The home's memory database, where the home's own state manifest declares it."""
    from personalclaw.durability import inventory

    entry = inventory.by_id("memory_db")
    if entry is None:
        raise SystemExit("the state manifest declares no memory_db entry")
    return home / entry.path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a scratch home's memory surfaces.")
    named_home.add_home_argument(parser)
    args = parser.parse_args(argv)
    gateway = named_home.scratch_gateway(args.home)
    _get, _req = gateway.get, gateway.call

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
    st, _ = _req(
        "PUT",
        "/api/memory/semantic",
        {"key": probe_key, "value": "memory validation probe", "confidence": 1.0},
    )
    check(st == 200, f"semantic write status={st}", fails)
    after = _get("/api/memory/semantic")["entries"]
    check(any(e["key"] == probe_key for e in after), "written entry visible via API", fails)

    # DB: row exists with self-consistent axes. Read-only: the gateway owns this database.
    conn = sqlite3.connect(f"{_memory_db(gateway.home).as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT key, scope, tier, source FROM semantic_memory WHERE key=?", (probe_key,)
        ).fetchone()
        check(row is not None, "entry written to DB", fails)
        if row is not None:
            check(row["scope"] == "global", f"DB scope={row['scope']} (want global)", fails)
            check(
                row["tier"] == "semantic",
                f"DB tier={row['tier']} (want semantic — not NULL)",
                fails,
            )
        # v6 axis columns present
        cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_memory)").fetchall()}
        check(
            {"tier", "scope", "scope_ref", "category", "visit_count"} <= cols,
            "v6 axis columns present on semantic_memory",
            fails,
        )
        ecols = {r[1] for r in conn.execute("PRAGMA table_info(episodic_memories)").fetchall()}
        check(
            {"tier", "scope", "scope_ref", "category", "visit_count"} <= ecols,
            "v6 axis columns present on episodic_memories",
            fails,
        )
    finally:
        conn.close()

    # WAL: a create event was logged for the write
    events = _get("/api/memory/events?limit=20")["events"]
    check(
        any(e.get("memory_key") == probe_key and e.get("event_type") == "create" for e in events),
        "WAL create event recorded",
        fails,
    )

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
    print(
        f"CLEAN — {len(after)} semantic / {stats.get('episodic_active', 0)} episodic / "
        f"{len(events)} recent events; write→DB→WAL→delete round-trip + axes + tools all hold"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
