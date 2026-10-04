"""Real-model smoke check for the unified Loop classify brain (both kinds).

The unified loop engine's 160 unit tests all STUB the LLM, and its routes aren't
registered until the 2e cutover — so before this script the unified classify/
walkthrough had never touched a real model. Run this against a configured model
to confirm each kind's classifier produces a real, well-formed classification
end-to-end — a repeatable pre-cutover gate that the in-process unit suite can't
provide.

It calls a model and records what that costs in the home it runs on, so it runs only on a
scratch home you name, with a model bound into it; the default home (the install's own) or no
home at all is refused. A local Ollama is the model a scratch home can take with no
credential: seed one with it bound, stop that gateway once it says it is up, then run this:

    PERSONALCLAW_HOME=/tmp/pc-classify personalclaw gateway --seed empty --seed-replace \
        --seed-local-model --no-open
    .venv/bin/python scripts/smoke_unified_loop_classify.py --home /tmp/pc-classify

Exits non-zero if any kind fails to classify (classified=False) or raises. Not a
pytest test: it requires a live provider, so it stays out of the unit gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import named_home  # noqa: E402


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
    print(
        f"  [{kind}] classified={ok}  plan_rows={plan_n}  "
        f"rigor={r.get('intake_rigor')!r}  kind_config_keys={sorted(r.get('kind_config', {}))}"
    )
    if not ok:
        print(f"  [{kind}] WARN — classifier returned classified=False (fell back to defaults)")
    return ok


async def main(home: Path) -> int:
    from personalclaw.llm.registry import get_default_registry, sync_entries_from_config

    n = sync_entries_from_config()
    entries = [e.name for e in get_default_registry().list_entries()]
    if not entries:
        print(
            f"No provider entries registered in {home}: bind a model into this scratch home "
            "first (this script's header shows one way, with a local Ollama)."
        )
        return 2
    print(f"Providers: {entries} (synced {n})\n")

    results = await asyncio.gather(
        _check("code", "Fix the null pointer crash when a user submits an empty search query"),
        _check(
            "goal", "Research the best caching strategy for our high-traffic API and recommend one"
        ),
    )
    ok = all(results)
    print(
        f"\n{'PASS' if ok else 'FAIL'} — unified loop classify smoke"
        f" ({sum(results)}/{len(results)} kinds)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="Smoke the loop classifier on a real model.")
    named_home.add_home_argument(_parser)
    sys.exit(asyncio.run(main(named_home.scratch_home(_parser.parse_args().home))))
