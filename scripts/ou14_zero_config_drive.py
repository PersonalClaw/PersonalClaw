#!/usr/bin/env python3
"""OU-14 clause 4 — drive the zero-config first chat turn on a throwaway home.

**The promise under test.** A fresh PersonalClaw install with NO provider bound, NO API key and
NO Ollama reachable should reach a working first chat turn against a bundled default model —
not the calm ``NoModelSetupState`` that OU-12 makes legible, and not ``ERR_MODEL_UNRESOLVED``.

**Why this is a script and not a gateway drive.** An earlier note recorded this clause as
"undrivable by construction on this rig — it needs a fresh unbound dev-home, which is a rig
act". That was wrong, and the correction is the whole shape of this file: a throwaway
``PERSONALCLAW_HOME``, the same provider bootstrap any non-gateway process runs, and one real
chat turn drive it in seconds. No gateway, no port, no provider binding, no network, and
nothing written to any real home.

**It COMPLETES a turn, it does not merely resolve one.** Resolving proves a provider was
found; OU-14's clause 4 asks for "a real turn rather than the calm setup-state", and a
resolution that then failed to generate would satisfy the first and not the second. So the
drive streams a real completion through the same ``NativeAgentRuntime`` the chat surface uses
and reports the reply text it got back. ``bootstrap_cli_providers()`` is what makes that
representative: a bare process registers no app-contributed provider until it runs, so a drive
without it would report "nothing resolves" on a home where the gateway resolves fine — the
error message blames a missing provider and the cause is an unbootstrapped process.

**What the exit code means — read this before wiring it into anything.** The exit code reports
CONSISTENCY between what the repository declares and what the rig observes, not whether the
promise is kept:

* exit 0 — the observation agrees with the bundled-chat app's ``bundled-model-signoff.txt``.
  With a model signed off AND its weight fetched into the tree, that means chat resolved and
  answered.
  With the record filled but no weight present (a source checkout that never ran
  ``scripts/fetch_bundled_model.py``), it means chat correctly does NOT resolve — that is the
  honest state of a dev tree, not a contradiction, and ``weight_present`` distinguishes it.
* exit 1 — a CONTRADICTION. The weight is installed and chat still does not resolve or does not
  answer, or nothing is signed off yet chat resolved from a bundled provider (something is
  bundled that no record declares).

The ``zero_config_first_chat:`` line, not the exit code, is the promise.

Usage:
    python scripts/ou14_zero_config_drive.py [--home DIR] [--json]

    --home DIR  drive against DIR instead of a fresh temporary directory. The directory is
                created if absent. Refuses the user's real home outright.
    --json      emit the observation as JSON instead of the human block.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
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


#: The prompt the drive sends. Short, factual and answerable by a very small model: the clause
#: under test is "a turn completed", not "the model is clever", and a prompt only a large model
#: could answer would make the rail fail for the wrong reason.
_PROMPT = "In one short sentence: what is the capital of France?"


def _first_turn(provider: object) -> str:
    """Stream one real turn through *provider* and return the assistant text.

    The same ``stream()`` the chat surface consumes, so what is measured here is what a user
    would see — not a private helper that happens to produce tokens.
    """
    import asyncio

    async def run() -> str:
        parts: list[str] = []
        await provider.start()  # type: ignore[attr-defined]
        try:
            async for event in provider.stream(_PROMPT):  # type: ignore[attr-defined]
                if event.kind == "text_chunk":
                    parts.append(event.text)
                elif event.kind == "complete":
                    break
        finally:
            await provider.shutdown()  # type: ignore[attr-defined]
        return "".join(parts).strip()

    return asyncio.run(run())


def observe(home: Path) -> dict[str, object]:
    """Drive one in-process chat turn on *home* and report what happened.

    ``PERSONALCLAW_HOME`` is set BEFORE ``personalclaw`` is imported, because several core
    modules capture a home-derived path into a module-level constant at import time; setting it
    afterwards would drive a different directory than the one reported.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PERSONALCLAW_HOME"] = str(home)
    src = _REPO_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
    from personalclaw.providers.loader import bootstrap_cli_providers
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError,
        resolve_provider_for_use_case,
    )

    # THE FETCH. The weight ships in neither git nor the wheel — it is downloaded once into the
    # home, which is the whole shape of this atom, so the drive downloads it exactly as a user
    # clicking the offer does: the same app module, the same `download_weight()`, the same
    # digest check. Skipping it and asserting on a pre-warmed home would leave the download path
    # — the part that is new and the part that can fail four ways — undriven.
    app = load_bundle_module(NATIVE_DIR / "bundled-chat", "bundled-chat", "provider")
    # The declaration comes from the APP, not from a second read of the repo. They resolve the
    # same record today, and asking twice is how a drive comes to assert against a record the
    # runtime is not using — measured: a planted repo-root record was invisible to the app,
    # whose own copy ships beside it in the wheel and takes precedence.
    declaration = app._declaration()
    offer_before = app.offer()
    fetch_error = ""
    fetch_seconds = 0.0
    frames = 0
    if offer_before is not None:
        started = time.monotonic()

        def _count(_done: int, _total: int) -> None:
            nonlocal frames
            frames += 1

        try:
            asyncio.run(app.download_weight(progress=_count))
        except app.DownloadFailed as exc:
            fetch_error = f"[{exc.outcome}] {exc}"
        fetch_seconds = round(time.monotonic() - started, 1)
    weight_present = app.installed_weight() is not None

    before = sorted(p.name for p in home.iterdir())
    # Register app-contributed providers exactly as a non-gateway process does.
    bootstrap_cli_providers()
    # The module was imported above, before the home had a weight, so its floor entry was not
    # registered. Re-evaluating is what a real download does too (the download route calls the
    # same function) — without it this would measure "you must restart after downloading".
    app.refresh_registration()
    resolved: str | None = None
    inner: str | None = None
    reply: str | None = None
    error_code: str | None = None
    error_text: str | None = None
    try:
        provider = resolve_provider_for_use_case(_USE_CASE)
        resolved = type(provider).__name__
        # The MODEL axis behind the agent runtime — the thing that actually generates. Named
        # separately because "the native runtime resolved" is true even when its inner model
        # is a cloud provider, and this clause is about the BUNDLED one.
        inner = type(resolve_provider_for_use_case(_USE_CASE, _force_model_axis=True)).__name__
        reply = _first_turn(provider)
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
        "offer_before_fetch": offer_before,
        "fetch_error": fetch_error,
        "fetch_seconds": fetch_seconds,
        "progress_frames": frames,
        "weight_present": weight_present,
        "home_entries_before": before,
        "home_entries_after": after,
        # The floor must cost no PERSISTED provider row: an in-memory entry vanishes with the
        # app, a config.json row would be an orphan the moment the bundle is removed.
        "providers_persisted": '"providers"' in config_text,
        "chat_resolved": resolved is not None,
        "chat_provider": resolved,
        "chat_model_provider": inner,
        "first_turn_reply": reply,
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
    present = bool(observation["weight_present"])
    resolved = bool(observation["chat_resolved"])
    credential = bool(observation["credential_written"])
    persisted = bool(observation["providers_persisted"])
    reply = str(observation["first_turn_reply"] or "")
    model = observation["bundle_model_id"]
    provider = observation["chat_provider"]
    inner = observation["chat_model_provider"]
    code = observation["error_code"]

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
    if not declared:
        return (
            True,
            "UNMET",
            (
                f"no model is signed off, and chat correctly raised {code} on an unbound home — "
                "the calm OU-12 setup state, which is exactly the state OU-14 exists to "
                "eliminate."
            ),
        )
    if not present:
        # The fetch did not produce the bytes. Consistent with chat not resolving, and honestly
        # UNMET — this is the state a machine with no network is in, and the reason is carried.
        return (
            resolved is False,
            "UNMET",
            (
                f"{model} is signed off but the download did not produce it, so chat raised "
                f"{code}. {observation['fetch_error'] or 'No fetch was attempted.'} A tree in "
                "this state that nevertheless RESOLVED chat would be a contradiction, which is "
                "why the consistency flag tracks it."
            ),
        )
    if not resolved:
        return (
            False,
            "UNMET",
            (
                f"the signed-off weight for {model} IS installed and chat still raised {code} — "
                "the weight ships and nothing runs it. A signed-off, present bundle with no "
                "runnable provider is the contradiction this drive exists to catch."
            ),
        )
    if not reply:
        return (
            False,
            "UNMET",
            (
                f"chat resolved to {provider} (model axis {inner}) but the first turn produced "
                "NO text. Resolution is not the clause: OU-14 asks for a real turn, and a "
                "provider that resolves and then says nothing is the calm setup state with "
                "extra steps."
            ),
        )
    if credential:
        return (
            False,
            "UNMET",
            (
                "the first turn completed, but a credential was written to the throwaway home — "
                "the zero-config path must cost no secret (the bundled provider emits no "
                "`credential` key)"
            ),
        )
    if persisted:
        return (
            False,
            "UNMET",
            (
                "the first turn completed, but a `providers` array was persisted to "
                "config.json. The floor is an IN-MEMORY entry on purpose: a persisted row "
                "outlives the bundle and becomes a stale pin naming an absent provider."
            ),
        )
    return (
        True,
        "MET",
        (
            f"{model} is signed off, its weight is installed, and a first chat turn COMPLETED "
            f"on an unbound home through {provider} (model axis {inner}) with no credential and "
            f"no persisted provider row. Reply: {reply[:160]!r}"
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
        print(f"bundle model id           : {observation['bundle_model_id']}")
        print(f"bundle licence            : {observation['bundle_licence']}")
        print(f"offer before fetch        : {observation['offer_before_fetch']}")
        print(f"fetch seconds             : {observation['fetch_seconds']}")
        print(f"progress frames           : {observation['progress_frames']}")
        print(f"fetch error               : {observation['fetch_error'] or '(none)'}")
        print(f"weight downloaded         : {observation['weight_present']}")
        print(f"chat resolved             : {observation['chat_resolved']}")
        print(f"chat provider             : {observation['chat_provider']}")
        print(f"chat model axis           : {observation['chat_model_provider']}")
        print(f"error code                : {observation['error_code']}")
        print(f"credential written        : {observation['credential_written']}")
        print(f"providers[] persisted     : {observation['providers_persisted']}")
        print(f"zero_config_first_chat    : {promise}")
        print(f"declaration consistent    : {consistent}")
        print(f"why                       : {explanation}")
        print(f"first turn reply          : {observation['first_turn_reply']}")
        if observation["error_text"]:
            print(f"error text                : {observation['error_text']}")
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
