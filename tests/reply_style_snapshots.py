"""Rewrite the reply-style snapshots in ``tests/fixtures/reply_style/`` from the current tree.

    .venv/bin/python tests/reply_style_snapshots.py

Run it only when a change to what a turn sends is the point of the change, and say why in the
commit. ``natural-voice-on-main.json`` is not rewritten: it is what natural voice sent before reply
styles existed, the reference ``plain`` is compared with.

The home is a new temporary folder, never the owner's.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def _main() -> None:
    from reply_style_shared import CASES, SNAPSHOTS, case_name, record

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for style, theme in CASES:
        with tempfile.TemporaryDirectory() as folder:
            os.environ["PERSONALCLAW_HOME"] = folder
            requests = await record(Path(folder), style, theme)
        out = SNAPSHOTS / f"{case_name(style, theme)}.json"
        out.write_text(json.dumps(requests, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out.name}")


if __name__ == "__main__":
    asyncio.run(_main())
