"""UT7 cross-surface consistency validator (unified tool-provider universe).

Hits the gateway of a scratch home you name (found from the record that gateway keeps in its
home) and asserts the invariants the unification must hold. Run repeatedly (each run = one
cycle); exits non-zero on any violation, printing the specific failure. Idempotent +
side-effect-free (it toggles then restores). The default home or no home is refused:

    .venv/bin/python scripts/ut7_validate.py --home /tmp/pc-tools
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import named_home  # noqa: E402


def check(cond, msg, fails):
    if not cond:
        fails.append(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a scratch home's tool universe.")
    named_home.add_home_argument(parser)
    args = parser.parse_args(argv)
    gateway = named_home.scratch_gateway(args.home)
    _get = gateway.get

    def _post(path, body):
        return gateway.call("POST", path, body)

    fails: list[str] = []

    tools = _get("/api/tools")["tools"]
    providers = _get("/api/providers")["providers"]
    apps = _get("/api/apps")["apps"]

    # 1. every tool appears EXACTLY once
    names = [t["name"] for t in tools]
    dupes = sorted({n for n in names if names.count(n) > 1})
    check(not dupes, f"duplicate tools in /api/tools: {dupes}", fails)

    # 2. no monolithic 'builtin' provider remains
    check(
        not any(t["provider"] == "builtin" for t in tools),
        "a tool is still under the monolithic 'builtin' provider",
        fails,
    )

    # 3. the split entity providers each own their slice
    by_prov: dict[str, set] = {}
    for t in tools:
        by_prov.setdefault(t["provider"], set()).add(t["name"])
    check(
        "read_file" in by_prov.get("personalclaw-filesystem", set())
        and "bash" in by_prov.get("personalclaw-filesystem", set()),
        "filesystem/shell not under personalclaw-filesystem",
        fails,
    )
    check(
        by_prov.get("personalclaw-knowledge-tools")
        and all(n.startswith("knowledge_") for n in by_prov["personalclaw-knowledge-tools"]),
        "knowledge provider slice wrong",
        fails,
    )

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
    check(
        fs_prov and fs_prov.get("platform"),
        "platform provider missing/unflagged on /api/providers",
        fails,
    )
    check(
        fs_app and fs_app.get("platform"), "platform provider missing/unflagged on /api/apps", fails
    )

    # 7. locked tools never report disabled; platform provider can't be disabled
    for t in tools:
        if t.get("locked"):
            check(not t.get("disabled"), f"locked tool {t['name']} reports disabled", fails)
    status, body = _post(
        "/api/tools/provider-toggle", {"provider": "personalclaw-filesystem", "enabled": False}
    )
    check(
        status == 409 and not body.get("ok"),
        f"platform provider disable not refused (status={status})",
        fails,
    )

    # 8. per-tool + per-provider disable round-trips through /api/tools (one source)
    status, _ = _post(
        "/api/tools/provider-toggle", {"provider": "personalclaw-knowledge-tools", "enabled": False}
    )
    after = _get("/api/tools")["tools"]
    kn = [t for t in after if t["provider"] == "personalclaw-knowledge-tools"]
    check(
        kn and all(t.get("disabled") and t.get("providerDisabled") for t in kn),
        "provider-disable not reflected in /api/tools",
        fails,
    )
    # restore
    _post(
        "/api/tools/provider-toggle", {"provider": "personalclaw-knowledge-tools", "enabled": True}
    )
    after2 = _get("/api/tools")["tools"]
    kn2 = [t for t in after2 if t["provider"] == "personalclaw-knowledge-tools"]
    check(
        kn2 and not any(t.get("disabled") for t in kn2), "provider re-enable didn't restore", fails
    )

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print(f"CLEAN — {len(tools)} tools, {len(tool_provs)} tool providers, all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
