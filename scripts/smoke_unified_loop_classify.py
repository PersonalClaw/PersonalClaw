"""Real-model smoke check for the unified Loop classify brain (both kinds).

The unified loop engine's 160 unit tests all STUB the LLM, and its routes aren't
registered until the 2e cutover — so before this script the unified classify/
walkthrough had never touched a real model. Run this against a configured model
(e.g. ``PERSONALCLAW_HOME=~/.personalclaw``) to confirm each kind's classifier
produces a real, well-formed classification end-to-end — a repeatable pre-cutover
gate that the in-process unit suite can't provide.

    PERSONALCLAW_HOME=~/.personalclaw .venv/bin/python scripts/smoke_unified_loop_classify.py

Exits non-zero if any kind fails to classify (classified=False) or raises. Not a
pytest test: it requires a live provider, so it stays out of the unit gate.
"""

from __future__ import annotations

import asyncio
import sys


async def _check(kind: str, task: str) -> bool:
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.loop import kinds

    kinds.ensure_loaded()

    async def ask(prompt: str) -> str:
        return await one_shot_completion(prompt, use_case="background")

    try:
        r = await kinds.get(kind).classify(task, ask)
    except Exception as exc:  # noqa: BLE001 - smoke check reports, doesn't crash
        print(f"  [{kind}] FAILED — classify raised: {exc}")
        return False
    ok = bool(r.get("classified"))
    plan_n = len(r.get("plan", []))
    print(f"  [{kind}] classified={ok}  plan_rows={plan_n}  "
          f"rigor={r.get('intake_rigor')!r}  kind_config_keys={sorted(r.get('kind_config', {}))}")
    if not ok:
        print(f"  [{kind}] WARN — classifier returned classified=False (fell back to defaults)")
    return ok


async def main() -> int:
    from personalclaw.llm.registry import get_default_registry, sync_entries_from_config

    n = sync_entries_from_config()
    entries = [e.name for e in get_default_registry().list_entries()]
    if not entries:
        print("No provider entries registered — set PERSONALCLAW_HOME to a configured "
              "home (e.g. ~/.personalclaw) with at least one model provider.")
        return 2
    print(f"Providers: {entries} (synced {n})\n")

    results = await asyncio.gather(
        _check("code", "Fix the null pointer crash when a user submits an empty search query"),
        _check("goal", "Research the best caching strategy for our high-traffic API and recommend one"),
    )
    ok = all(results)
    print(f"\n{'PASS' if ok else 'FAIL'} — unified loop classify smoke ({sum(results)}/{len(results)} kinds)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
