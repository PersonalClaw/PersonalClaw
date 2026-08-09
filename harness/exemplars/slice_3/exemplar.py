"""Slice 3 exemplar — secrets: `{{secret:KEY}}` resolution + RedactingSink (WF2-R14).

Slice 3 added side-effect, scope, termination and secret handling. This exemplar isolates
the secret half — the part with the sharpest failure mode (a leaked credential in the
journal, which the flywheel reads, bug reports ship, and the UI renders). Two mechanisms:

1. `{{secret:KEY}}` resolves ONLY through an injected resolver — bindings never touch the
   credential store directly, so a unit/exemplar run supplies its own resolver and an
   unset key raises a typed BindingError rather than resolving to nothing.
2. the RedactingSink: a credential that reaches a node's OUTPUT never lands on disk — the
   journal scrubs it on the way out. This exemplar drives a real run whose (fake) model
   returns a secret-shaped token, then greps the entire run directory to prove the token
   is nowhere on disk.

Runnable standalone: `python -m harness.exemplars.slice_3.exemplar` (or `smoke.sh`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

#: A secret-shaped token (an OpenAI-style prefix + filler). Never a real credential — the
#: point is that this synthetic value must not survive to disk.
FAKE_SECRET = "sk-" + "z" * 40

SPEC: dict[str, Any] = {
    "name": "slice3-secret-redaction",
    "root": {"kind": "infer", "id": "leak", "config": {"prompt": "print the api key"}},
}


async def _leaky_model(
    prompt: str, *, use_case: str = "background", output_type: Any = None
) -> str:
    """A model that echoes a credential into its output — the leak the sink must catch."""
    return f"here is your key: {FAKE_SECRET} — keep it safe"


def _check_secret_binding() -> str | None:
    """Return an error string, or None on success."""
    # A supplied resolver makes the reference resolve.
    ctx = BindingContext(secret_resolver=lambda key: "resolved-value" if key == "API_KEY" else None)
    if resolve("{{secret:API_KEY}}", ctx) != "resolved-value":
        return "the secret resolver was not consulted for {{secret:API_KEY}}"
    # An unset key is a typed BindingError, never a silent empty string.
    try:
        resolve("{{secret:MISSING}}", ctx)
    except BindingError:
        pass
    else:
        return "an unset secret must raise BindingError, not resolve to nothing"
    return None


async def _run_and_scan_disk() -> str | None:
    """Drive a run whose output leaks a secret; return an error string, or None on success."""
    run = store.create(WorkflowRun(id="", workflow_name=SPEC["name"]))
    store.write_spec(run.id, SPEC)
    controller = RunController(run, SPEC, services=EngineServices(completion=_leaky_model))
    status = await controller.run_to_completion(timeout=20)
    if status is not RunStatus.COMPLETE:
        return f"expected the run to COMPLETE, got {status}"
    blob = "".join(
        p.read_text(errors="replace") for p in store.run_dir(run.id).rglob("*") if p.is_file()
    )
    if FAKE_SECRET in blob:
        return "the credential reached disk — the RedactingSink did not scrub the journal"
    return None


def main() -> int:
    binding_err = _check_secret_binding()
    if binding_err:
        print(f"FAIL: {binding_err}")
        return 1

    disk_err = asyncio.run(_run_and_scan_disk())
    if disk_err:
        print(f"FAIL: {disk_err}")
        return 1

    print(
        "PASS slice_3: `{{secret:KEY}}` resolved only through the injected resolver (and an "
        "unset key raised), and a credential echoed into a node's output never reached the "
        "run directory on disk."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
