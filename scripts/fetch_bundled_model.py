#!/usr/bin/env python3
"""Pre-fetch the default chat model into a PersonalClaw home, without a UI.

**This is a THIN CLI, not a second implementation.** It loads
``apps/native/bundled-chat/provider.py`` — the module a running gateway loads — and calls the
same ``download_weight()`` a click in the dashboard calls. Nothing about the URL, the digest,
the size ceiling, the licence check, the lock or the atomic replace lives here; a copy of any of
that would be a second thing that can disagree with the one CI enforces.

**What it is for.** The model is normally fetched on first use, from the chat screen or
Settings → Models, because that is where a user can be told what it costs and can cancel. Three
cases want it without a browser:

* **An offline container or image.** Run this against the home the image will ship with, and
  every container starts with the model already present and never reaches for the network. That
  is the documented one-line answer to "can the image work offline" — the project ships no
  weight in the image itself (see the app's ``bundled-model-signoff.txt``), and this is how
  anyone who needs that builds it without a second artifact pipeline.
* **A fleet.** Warm one home, copy it.
* **A test rig.** ``scripts/ou14_zero_config_drive.py`` uses it to reach a first chat turn.

Usage::

    PERSONALCLAW_HOME=/path/to/home python scripts/fetch_bundled_model.py
    PERSONALCLAW_HOME=/path/to/home python scripts/fetch_bundled_model.py --check

``--check`` verifies without touching the network and exits non-zero when the weight is absent
or does not match the record. Exit 0 means the home holds the signed-off bytes.

🔴 It writes into ``$PERSONALCLAW_HOME``, so it refuses to run without that set: silently
warming ``~/.personalclaw`` because an env var was missing is not a thing a script should do to
somebody's real home.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_APP = _SRC / "personalclaw" / "apps" / "native" / "bundled-chat"


def _log(message: str) -> None:
    print(f"[fetch-bundled-model] {message}", flush=True)


def _fail(message: str) -> None:
    print(f"[fetch-bundled-model] FAIL: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _app_module():
    """Load the app's provider module exactly as ``providers/loader.py`` does."""
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from personalclaw.apps.native_contract import load_bundle_module

    return load_bundle_module(_APP, "bundled-chat", "provider")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only; never touch the network (exit 1 when absent or mismatched)",
    )
    args = parser.parse_args()

    if not os.environ.get("PERSONALCLAW_HOME"):
        _fail(
            "PERSONALCLAW_HOME is not set. This writes a 138 MiB file into the home it is "
            "pointed at, so it will not guess one — export PERSONALCLAW_HOME first."
        )

    app = _app_module()
    declaration = app._declaration()
    if declaration is None:
        _fail(
            "no default chat model is signed off in this tree "
            f"({app.DECLARATION_PATH}) — there is nothing to fetch."
        )
    _log(f"signed off : {declaration.model_id} under {declaration.licence}")
    _log(f"target     : {app.weight_path()}")
    _log(f"expected   : {declaration.size_bytes} bytes")

    present = app.installed_weight()
    if present is not None:
        verdict = app.verify_download(present, declaration)
        if verdict.ok:
            _log(f"already present and verified — {verdict.detail}")
            return 0
        if args.check:
            _fail(verdict.detail)
        _log(f"present but not the signed-off bytes ({verdict.outcome}) — re-fetching")

    if args.check:
        _fail(
            f"{app.weight_path()} is not there. --check never fetches; run without it to "
            "download the signed-off weight."
        )

    last = [0]

    def progress(done: int, total: int) -> None:
        # One line per ~10%, so a CI log is readable and a human sees it move.
        step = max(total // 10, 1)
        if done - last[0] >= step or done == total:
            last[0] = done
            pct = (done * 100 // total) if total else 0
            _log(f"  {pct:3d}%  {done} / {total} bytes")

    try:
        path = asyncio.run(app.download_weight(progress=progress))
    except app.DownloadFailed as exc:
        _fail(f"[{exc.outcome}] {exc}")
        return 1
    _log(f"OK — verified and installed at {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
