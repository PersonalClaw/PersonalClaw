#!/usr/bin/env python3
"""Ask an INSTALLED PersonalClaw whether it can offer its default chat model.

**Why this exists (2026-09-25, a release blocker).** The published gateway image could neither
offer nor fetch the bundled default model: its sign-off record — which carries the source pin,
the digest and the size the first-run download needs — was a symlink into ``docs/``, and the
image copies only ``src/``. ``scripts/verify_wheel.py`` did not catch it because it read the
record out of the REPOSITORY, which always has ``docs/``. A gate that consults the source tree
reports on the source tree; the question is what the ARTIFACT carries. So the record check now
runs inside the artifact, with the artifact's own interpreter, against the artifact's own copy
of everything.

Two halves in one file, on purpose — one dialect for two gates:

* **Run BY the artifact** — ``<artifact python> installed_bundled_model_probe.py [WHEEL]``: the
  wheel's scratch venv (``verify_wheel.py``) or ``docker exec … python -c <this file>``
  (``tools/docker_single_container_smoke.py``). It imports only the INSTALLED package, loads the
  bundled-chat app exactly as the gateway does, reads the record THAT app reads, and prints ONE
  JSON line. Given a wheel path it also runs the installed wheel gate
  (``personalclaw.bundled_model.gate_wheel``: no weight-shaped member, under the size ceiling).
* **Imported BY the gates** — :func:`record_failures` turns that report into sentences, and
  :func:`offer_failure` judges a ``GET /api/onboarding`` body. Nothing at import time touches
  ``personalclaw``, so a bare runner with nothing installed can judge an artifact without
  consulting the repository's copy of anything.

The record's own rules (the licence allowlist, the partial-record refusal, the digest shape) are
NOT restated here: the probe hands the text to the installed parser and reports its verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: The app whose record this is — the name the gateway loads it under.
APP = "bundled-chat"


def probe(wheel: str | None = None) -> dict[str, Any]:
    """Report on the record the installed bundled-chat app reads. Runs INSIDE the artifact."""
    from personalclaw import bundled_model as rail
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    app = load_bundle_module(NATIVE_DIR / APP, APP, "provider")
    path = Path(app.DECLARATION_PATH)
    report: dict[str, Any] = {
        "package": str(Path(rail.__file__).resolve().parent),
        "record": {"path": str(path), "is_symlink": path.is_symlink(), "is_file": path.is_file()},
        "declaration": None,
        "declaration_error": "",
        "licence": None,
    }
    if path.is_file():
        try:
            declaration = rail.parse_declaration(path.read_text(encoding="utf-8"))
        except rail.BundleDeclarationError as exc:
            report["declaration_error"] = str(exc)
        else:
            if declaration is not None:
                decision = rail.licence_decision(declaration.licence)
                report["declaration"] = {
                    "model_id": declaration.model_id,
                    "licence": declaration.licence,
                    "size_bytes": declaration.size_bytes,
                }
                report["licence"] = {"permitted": decision.permitted, "reason": decision.reason}
    if wheel:
        report["wheel_gate"] = _gate_wheel(rail, wheel)
    return report


def _gate_wheel(rail: Any, wheel: str) -> dict[str, Any]:
    """The installed wheel gate's verdict on *wheel*, resolved against THIS process's directory.

    A path that is not a file from here is a named refusal, never a traceback. This probe runs
    in the scratch home, not the caller's directory, so a RELATIVE path from the caller names
    nothing here: ``verify_wheel.py`` once handed it ``dist/personalclaw-0.2.0-py3-none-any.whl``
    and ``gate_wheel`` died with ``FileNotFoundError`` before the record half of the report was
    written. The caller now sends an absolute path; this keeps a regression legible.
    """
    target = Path(wheel).resolve()
    if not target.is_file():
        return {
            "ok": False,
            "summary": f"no wheel at {target}",
            "refusals": [
                f"the wheel gate was handed {wheel!r}, which is not a file from this probe's "
                f"working directory ({Path.cwd()}). Hand it an absolute path: a relative one "
                "means the caller's directory, and the probe runs in the artifact's own."
            ],
        }
    gate = rail.gate_wheel(target)
    return {"ok": gate.ok, "summary": gate.summary, "refusals": list(gate.refusals)}


def record_failures(report: dict[str, Any], *, wheel_gate_required: bool = False) -> list[str]:
    """Everything wrong with the installed record, one sentence each. Empty means it is sound.

    Each sentence says what a USER of that artifact would meet, because that is the reason the
    gate exists: an install without a readable, permitted record shows no download offer
    anywhere, and its first chat names a download that nothing on screen offers.

    ``wheel_gate_required`` is for the wheel gate, which hands the probe a wheel: a report with
    no wheel verdict in it means the weight/size check never ran, and that must not read as a
    pass.
    """
    record = report.get("record") or {}
    where = record.get("path", "?")
    if record.get("is_symlink"):
        return [
            f"the installed record {where} is a SYMLINK. An install carries only the package, so "
            "a link resolves in the tree it was built from and nowhere else — make it a real file"
        ]
    if not record.get("is_file"):
        return [
            f"the installed package carries no sign-off record at {where}, so this artifact can "
            "neither offer nor fetch its default chat model — the package was built without it"
        ]
    if report.get("declaration_error"):
        return [f"the installed sign-off record is unreadable: {report['declaration_error']}"]
    declaration = report.get("declaration")
    if not declaration:
        return [
            f"the installed record {where} signs off no model, so the first-run fetch has "
            "nothing to fetch and a fresh install reaches no chat at all"
        ]
    failures: list[str] = []
    licence = report.get("licence") or {}
    if not licence.get("permitted"):
        failures.append(f"the signed-off licence is not permitted: {licence.get('reason', '?')}")
    gate = report.get("wheel_gate")
    if gate is None:
        if wheel_gate_required:
            failures.append(
                "the probe ran no wheel gate, so the weight/size check measured nothing"
            )
    elif not gate.get("ok"):
        failures.extend(f"wheel gate refusal: {refusal}" for refusal in gate.get("refusals", []))
        failures.append(f"the bundled-model wheel gate refused this wheel ({gate.get('summary')})")
    return failures


def offer_failure(payload: Any) -> str | None:
    """Why a ``GET /api/onboarding`` body does NOT offer the default model, or ``None`` if it does.

    A fresh home has no model bound and no weight downloaded, so a working install MUST offer the
    download here — it is the only thing onboarding, the chat screen and Settings → Models render
    it from. ``null`` is the measured image defect: every response 200, no offer anywhere.
    """
    if not isinstance(payload, dict):
        return f"/api/onboarding did not answer a JSON object: {payload!r}"
    offer = payload.get("chat_download_offer")
    if offer is None:
        return (
            "/api/onboarding reports chat_download_offer = null on a fresh home "
            f"(needs_model={payload.get('needs_model')!r}), so no surface offers the default "
            "chat model's download and the first chat has nothing to answer it"
        )
    if (
        not isinstance(offer, dict)
        or not isinstance(offer.get("bytes"), int)
        or offer["bytes"] <= 0
    ):
        return f"chat_download_offer does not state a positive size in bytes: {offer!r}"
    return None


def main(argv: list[str]) -> int:
    print(json.dumps(probe(argv[1] if len(argv) > 1 else None), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
