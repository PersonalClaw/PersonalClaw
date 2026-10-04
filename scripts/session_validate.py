"""Cross-surface live validator for this session's changes.

Hits the gateway of a scratch home you name and asserts the behavioral invariants the
session's work must hold, across DISTINCT surfaces (not just the unified-tool-universe ones in
ut7_validate.py):

  - projects-category tool redefinition (project_run_* present, loop tools gone)
  - per-entity provider split (no monolithic 'builtin')
  - MCP per-provider reconnect (probe/{name}) + warm reachability
  - app list is fast + free of garbage dirs (the perf regression we fixed)
  - tool-disable round-trips through the one registry

Run repeatedly (each run = one cycle); exits non-zero on any violation. Idempotent
+ side-effect-safe (toggles are restored). Retries transient slow-startup GETs so
it tests invariants, not latency.

It toggles providers, so it runs only against a scratch home: the gateway is found from the
record it keeps in the home you name, and the default home or no home is refused:

    .venv/bin/python scripts/session_validate.py --home /tmp/pc-session
"""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a scratch home's tool surfaces.")
    named_home.add_home_argument(parser)
    args = parser.parse_args(argv)
    gateway = named_home.scratch_gateway(args.home)
    _get, _req = gateway.get, gateway.call

    fails: list[str] = []

    tools = _get("/api/tools")["tools"]
    by_prov: dict[str, set] = {}
    for t in tools:
        by_prov.setdefault(t["provider"], set()).add(t["name"])
    names = {t["name"] for t in tools}

    # 1. projects category redefinition
    proj = by_prov.get("personalclaw-project-tools", set())
    check(
        proj
        == {"project_run_create", "project_run_start", "project_run_status", "project_run_list"},
        f"project-tools provider slice wrong: {sorted(proj)}",
        fails,
    )
    for stale in (
        "code_project_create",
        "goal_loop_create",
        "sdlc_status",
        "loop_create",
        "project_create",
        "project_list",
    ):
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
    check(len(apps) < 200, f"/api/apps returned {len(apps)} entries — garbage dirs?", fails)

    # 5. tool-disable round-trips through the one registry
    st, _ = _req(
        "POST",
        "/api/tools/provider-toggle",
        {"provider": "personalclaw-knowledge-tools", "enabled": False},
    )
    after = _get("/api/tools")["tools"]
    kn = [t for t in after if t["provider"] == "personalclaw-knowledge-tools"]
    check(kn and all(t.get("disabled") for t in kn), "provider-disable not reflected", fails)
    _req(
        "POST",
        "/api/tools/provider-toggle",
        {"provider": "personalclaw-knowledge-tools", "enabled": True},
    )
    after2 = _get("/api/tools")["tools"]
    kn2 = [t for t in after2 if t["provider"] == "personalclaw-knowledge-tools"]
    check(
        kn2 and not any(t.get("disabled") for t in kn2), "provider re-enable didn't restore", fails
    )

    # 6. platform provider can't be disabled
    st, _ = _req(
        "POST",
        "/api/tools/provider-toggle",
        {"provider": "personalclaw-filesystem", "enabled": False},
    )
    check(st == 409, f"platform provider disable not refused (status={st})", fails)

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print(
        f"CLEAN — {len(tools)} tools / {len(by_prov)} providers / "
        f"{len(srv_names)} MCP servers / {len(apps)} apps ({dt*1000:.0f}ms)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
