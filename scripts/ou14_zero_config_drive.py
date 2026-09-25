#!/usr/bin/env python3
"""OU-14 clause 4 — drive the zero-config first chat turn on a throwaway home.

**The promise under test.** A fresh PersonalClaw install with NO provider bound, NO API key and
NO Ollama reachable should reach a working first chat turn against a bundled default model —
not the calm ``NoModelSetupState`` that OU-12 makes legible, and not ``ERR_MODEL_UNRESOLVED``.

**Why this is a script and not a gateway drive.** An earlier note recorded this clause as
"undrivable by construction on this rig — it needs a fresh unbound dev-home, which is a rig
act". That was wrong, and the correction is the whole shape of this file: a throwaway
``PERSONALCLAW_HOME`` plus ONE in-process call to
``resolve_provider_for_use_case("chat")`` drives it in seconds. No gateway, no port, no provider
binding, no network, and nothing written to any real home. The resolver is the exact seam the
chat surface streams from (``tests/test_no_provider_first_run_rail.py`` surface 1 drives the
same call), so resolving here IS the first turn reaching a model.

**What the exit code means — read this before wiring it into anything.** The exit code reports
CONSISTENCY between what the repository declares and what the rig observes, not whether the
promise is kept:

* exit 0 — the observation agrees with ``docs/architecture/bundled-model-signoff.txt``. With no
  model signed off, that means chat correctly does NOT resolve and the promise is honestly
  reported UNMET. With one signed off, it means chat resolves.
* exit 1 — a CONTRADICTION. A bundle is signed off and chat still does not resolve (the weight
  ships but nothing can run it), or nothing is signed off yet chat resolved from a bundled
  provider (something is bundled that no record declares).

So this gate is meaningful today, is not a rubber stamp, and flips meaning the moment the owner
fills the record in. The ``zero_config_first_chat:`` line, not the exit code, is the promise.

Usage:
    python scripts/ou14_zero_config_drive.py [--home DIR] [--json]

    --home DIR  drive against DIR instead of a fresh temporary directory. The directory is
                created if absent. Refuses the user's real home outright.
    --json      emit the observation as JSON instead of the human block.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The use case the dashboard's chat surface resolves. One call, one answer.
_USE_CASE = "chat"


def _refuse_real_home(home: Path) -> None:
    """A drive that writes to ``~/.personalclaw`` is not a drive, it is an accident."""
    real = Path.home() / ".personalclaw"
    if home.resolve() == real.resolve():
        raise SystemExit(
            f"refusing to drive against the real home {real}. This probe exists to observe a "
            "FRESH unbound home; pointing it at a real one would both invalidate the "
            "observation and touch a user's state."
        )


def observe(home: Path) -> dict[str, object]:
    """Drive one in-process chat resolution on *home* and report what happened.

    ``PERSONALCLAW_HOME`` is set BEFORE ``personalclaw`` is imported, because several core
    modules capture a home-derived path into a module-level constant at import time; setting it
    afterwards would drive a different directory than the one reported.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PERSONALCLAW_HOME"] = str(home)
    src = _REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from personalclaw import bundled_model
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    declaration = bundled_model.repo_declaration(_REPO_ROOT)

    before = sorted(p.name for p in home.iterdir())
    resolved: str | None = None
    error_code: str | None = None
    error_text: str | None = None
    try:
        provider = resolve_provider_for_use_case(_USE_CASE)
        resolved = type(provider).__name__
    except ProviderResolutionError as exc:
        agent_error = getattr(exc, "agent_error", None)
        error_code = getattr(agent_error, "code", None)
        error_text = str(exc)
    after = sorted(p.name for p in home.iterdir())

    # The credential half of the clause: reaching a bundled model must cost NO secret. `.env` is
    # the credential store's file (config/credentials.py), and a provider entry carrying a
    # `credential` key in config.json is the other way one gets recorded.
    config_path = home / "config.json"
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    credential_written = (home / ".env").exists() or '"credential"' in config_text

    return {
        "home": str(home),
        "bundle_declared": declaration is not None,
        "bundle_model_id": declaration.model_id if declaration else None,
        "bundle_licence": declaration.licence if declaration else None,
        "home_entries_before": before,
        "home_entries_after": after,
        "chat_resolved": resolved is not None,
        "chat_provider": resolved,
        "error_code": error_code,
        "error_text": error_text,
        "credential_written": credential_written,
    }


def verdict(observation: dict[str, object]) -> tuple[bool, str, str]:
    """``(consistent, promise, explanation)`` for *observation*.

    ``promise`` is the OU-14 clause-4 reading — MET or UNMET. ``consistent`` is whether the
    observation agrees with the repository's own declaration, which is what the exit code
    reports; see the module docstring for why the two are separate.
    """
    declared = bool(observation["bundle_declared"])
    resolved = bool(observation["chat_resolved"])
    credential = bool(observation["credential_written"])
    model = observation["bundle_model_id"]
    provider = observation["chat_provider"]
    code = observation["error_code"]

    if declared and resolved and not credential:
        return (
            True,
            "MET",
            (
                f"a bundled model is signed off ({model}) and chat resolved to {provider} "
                "on an unbound home with no credential written"
            ),
        )
    if declared and resolved and credential:
        return (
            False,
            "UNMET",
            (
                "chat resolved, but a credential was written to the throwaway home — "
                "the zero-config path must cost no secret (the bundled provider emits "
                "no `credential` key)"
            ),
        )
    if declared and not resolved:
        return (
            False,
            "UNMET",
            (
                f"a bundled model is signed off ({model}) but chat still raised {code} — "
                "the weight is declared and nothing resolves it. A signed-off bundle with no "
                "runnable provider is the contradiction this drive exists to catch."
            ),
        )
    if not declared and resolved:
        return (
            False,
            "MET",
            (
                f"chat resolved to {provider} on an unbound home, but NOTHING is signed off "
                "in the bundled-model record — something is resolving that no licence record "
                "declares. Record it, or find out what is bound."
            ),
        )
    return (
        True,
        "UNMET",
        (
            f"no model is signed off, and chat correctly raised {code} on an unbound home — "
            "the calm OU-12 setup state, which is exactly the state OU-14 exists to eliminate. "
            "This is the honest reading of an empty sign-off record, not a rig limitation: the "
            "bundle CHOICE and its licence sign-off are owner-gated."
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive OU-14's zero-config first chat turn.")
    ap.add_argument("--home", help="drive against this dir instead of a fresh temp dir")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the human block")
    args = ap.parse_args()

    scratch: Path | None = None
    if args.home:
        home = Path(args.home).expanduser()
    else:
        scratch = Path(tempfile.mkdtemp(prefix="ou14_zero_config_"))
        home = scratch / "home"
    _refuse_real_home(home)
    try:
        observation = observe(home)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    consistent, promise, explanation = verdict(observation)
    if args.json:
        print(
            json.dumps(
                {**observation, "consistent": consistent, "promise": promise, "why": explanation},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("── OU-14 zero-config first-chat drive ──────────────────────────────────")
        print(f"home (throwaway)          : {observation['home']}")
        print(f"bundle signed off         : {observation['bundle_declared']}")
        print(f"chat resolved             : {observation['chat_resolved']}")
        print(f"chat provider             : {observation['chat_provider']}")
        print(f"error code                : {observation['error_code']}")
        print(f"credential written        : {observation['credential_written']}")
        print(f"home entries after        : {observation['home_entries_after']}")
        print(f"zero_config_first_chat    : {promise}")
        print(f"declaration consistent    : {consistent}")
        print(f"why                       : {explanation}")
        if observation["error_text"]:
            print(f"error text                : {observation['error_text']}")
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
