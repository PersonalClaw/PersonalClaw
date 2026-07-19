"""Cross-surface live validator for this session's changes.

Hits the LIVE gateway and asserts the behavioral invariants the session's work
must hold, across DISTINCT surfaces (not just the unified-tool-universe ones in
ut7_validate.py):

  - projects-category tool redefinition (project_run_* present, loop tools gone)
  - per-entity provider split (no monolithic 'builtin')
  - MCP per-provider reconnect (probe/{name}) + warm reachability
  - app list is fast + free of garbage dirs (the perf regression we fixed)
  - tool-disable round-trips through the one registry

Run repeatedly (each run = one cycle); exits non-zero on any violation. Idempotent
+ side-effect-safe (toggles are restored). Retries transient slow-startup GETs so
it tests invariants, not latency.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:10000"


def _get(path):
    last = None
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=15))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"gateway unreachable at {path} after retries: {last}")


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
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

    tools = _get("/api/tools")["tools"]
    by_prov: dict[str, set] = {}
    for t in tools:
        by_prov.setdefault(t["provider"], set()).add(t["name"])
    names = {t["name"] for t in tools}

    # 1. projects category redefinition
    proj = by_prov.get("personalclaw-project-tools", set())
    check(proj == {"project_run_create", "project_run_start", "project_run_status", "project_run_list"},
          f"project-tools provider slice wrong: {sorted(proj)}", fails)
    for stale in ("code_project_create", "goal_loop_create", "sdlc_status", "loop_create",
                  "project_create", "project_list"):
        check(stale not in proj, f"stale loop tool {stale!r} back in project provider", fails)

    # 2. no monolithic builtin; removed shell tools gone
    check("builtin" not in by_prov, "monolithic 'builtin' provider reappeared", fails)
    for gone in ("git", "run_tests", "diagnostics"):
        check(gone not in names, f"removed shell-wrapper tool {gone!r} reappeared", fails)

    # 3. MCP per-provider reconnect: probe/{name} works for each configured server
    mcp = _get("/api/mcp")
    servers = mcp.get("servers", mcp) if isinstance(mcp, dict) else mcp
    srv_names = [s.get("name") for s in servers if isinstance(s, dict)]
    check(srv_names, "no MCP servers listed", fails)
    for n in srv_names:
        st, _ = _req("POST", f"/api/mcp/probe/{n}")
        check(st in (200, 202), f"probe-one [{n}] failed: status={st}", fails)

    # 4. app list is fast + free of garbage (the /api/apps perf regression)
    t0 = time.time()
    apps = _get("/api/apps")["apps"]
    dt = time.time() - t0
    check(dt < 2.0, f"/api/apps too slow ({dt:.2f}s) — apps-dir pollution may be back", fails)
    real = [a for a in apps if not a.get("platform") and a.get("origin") != "bundled"]
    check(len(apps) < 200, f"/api/apps returned {len(apps)} entries — garbage dirs?", fails)

    # 5. tool-disable round-trips through the one registry
    st, _ = _req("POST", "/api/tools/provider-toggle",
                 {"provider": "personalclaw-knowledge-tools", "enabled": False})
    after = _get("/api/tools")["tools"]
    kn = [t for t in after if t["provider"] == "personalclaw-knowledge-tools"]
    check(kn and all(t.get("disabled") for t in kn), "provider-disable not reflected", fails)
    _req("POST", "/api/tools/provider-toggle",
         {"provider": "personalclaw-knowledge-tools", "enabled": True})
    after2 = _get("/api/tools")["tools"]
    kn2 = [t for t in after2 if t["provider"] == "personalclaw-knowledge-tools"]
    check(kn2 and not any(t.get("disabled") for t in kn2), "provider re-enable didn't restore", fails)

    # 6. platform provider can't be disabled
    st, _ = _req("POST", "/api/tools/provider-toggle",
                 {"provider": "personalclaw-filesystem", "enabled": False})
    check(st == 409, f"platform provider disable not refused (status={st})", fails)

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print(f"CLEAN — {len(tools)} tools / {len(by_prov)} providers / "
          f"{len(srv_names)} MCP servers / {len(apps)} apps ({dt*1000:.0f}ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
