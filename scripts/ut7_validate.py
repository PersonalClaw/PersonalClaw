"""UT7 cross-surface consistency validator (unified tool-provider universe).

Hits the LIVE gateway and asserts the invariants the unification must hold. Run
repeatedly (each run = one cycle); exits non-zero on any violation, printing the
specific failure. Idempotent + side-effect-free (it toggles then restores).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:10000"


def _get(path):
    # Retry with backoff: right after a gateway restart the asyncio loop can be
    # briefly saturated by background warm/probe tasks, so a single 10s GET may
    # time out on a gateway that is in fact healthy. Retry before declaring a
    # consistency violation (this harness checks invariants, not latency).
    last = None
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=15))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"gateway unreachable at {path} after retries: {last}")


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def check(cond, msg, fails):
    if not cond:
        fails.append(msg)


def main() -> int:
    fails: list[str] = []

    tools = _get("/api/tools")["tools"]
    providers = _get("/api/providers")["providers"]
    apps = _get("/api/apps")["apps"]

    # 1. every tool appears EXACTLY once
    names = [t["name"] for t in tools]
    dupes = sorted({n for n in names if names.count(n) > 1})
    check(not dupes, f"duplicate tools in /api/tools: {dupes}", fails)

    # 2. no monolithic 'builtin' provider remains
    check(not any(t["provider"] == "builtin" for t in tools),
          "a tool is still under the monolithic 'builtin' provider", fails)

    # 3. the split entity providers each own their slice
    by_prov: dict[str, set] = {}
    for t in tools:
        by_prov.setdefault(t["provider"], set()).add(t["name"])
    check("read_file" in by_prov.get("personalclaw-filesystem", set()) and
          "bash" in by_prov.get("personalclaw-filesystem", set()),
          "filesystem/shell not under personalclaw-filesystem", fails)
    check(by_prov.get("personalclaw-knowledge-tools") and
          all(n.startswith("knowledge_") for n in by_prov["personalclaw-knowledge-tools"]),
          "knowledge provider slice wrong", fails)

    # 4. the removed shell-wrapper tools are gone
    for gone in ("git", "run_tests", "diagnostics"):
        check(gone not in names, f"removed tool {gone!r} reappeared", fails)

    # 5. every tool-type provider on Settings>Providers also appears in Store/Library
    tool_provs = {p["name"] for p in providers if (p.get("provider") or {}).get("type") == "tool"}
    app_names = {a["name"] for a in apps}
    missing = sorted(tool_provs - app_names)
    check(not missing, f"tool providers on Providers but missing from Library: {missing}", fails)

    # 6. the platform provider is present + flagged on BOTH surfaces, non-removable
    fs_prov = next((p for p in providers if p["name"] == "personalclaw-filesystem"), None)
    fs_app = next((a for a in apps if a["name"] == "personalclaw-filesystem"), None)
    check(fs_prov and fs_prov.get("platform"), "platform provider missing/unflagged on /api/providers", fails)
    check(fs_app and fs_app.get("platform"), "platform provider missing/unflagged on /api/apps", fails)

    # 7. locked tools never report disabled; platform provider can't be disabled
    for t in tools:
        if t.get("locked"):
            check(not t.get("disabled"), f"locked tool {t['name']} reports disabled", fails)
    status, body = _post("/api/tools/provider-toggle",
                         {"provider": "personalclaw-filesystem", "enabled": False})
    check(status == 409 and not body.get("ok"),
          f"platform provider disable not refused (status={status})", fails)

    # 8. per-tool + per-provider disable round-trips through /api/tools (one source)
    status, _ = _post("/api/tools/provider-toggle",
                      {"provider": "personalclaw-knowledge-tools", "enabled": False})
    after = _get("/api/tools")["tools"]
    kn = [t for t in after if t["provider"] == "personalclaw-knowledge-tools"]
    check(kn and all(t.get("disabled") and t.get("providerDisabled") for t in kn),
          "provider-disable not reflected in /api/tools", fails)
    # restore
    _post("/api/tools/provider-toggle", {"provider": "personalclaw-knowledge-tools", "enabled": True})
    after2 = _get("/api/tools")["tools"]
    kn2 = [t for t in after2 if t["provider"] == "personalclaw-knowledge-tools"]
    check(kn2 and not any(t.get("disabled") for t in kn2), "provider re-enable didn't restore", fails)

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print(f"CLEAN — {len(tools)} tools, {len(tool_provs)} tool providers, all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
