"""Marker-scoped, reversible seeding of a CLI's own MCP config.

Prong B of ACP-AGENT-PARITY §2.1, for the CLIs that ignore protocol-passed
``mcpServers`` at ``session/new``. kiro is the measured case: the host already
generates a complete ``$PERSONALCLAW_HOME/agents/personalclaw.json`` whose
``mcpServers.personalclaw-core`` is correct, but kiro's only two agent-discovery
roots are ``<cwd>/.kiro/agents`` and ``~/.kiro/agents`` — it prints them itself in
``kiro-cli agent list`` — so the file is never read (`K6`, `G31`). Seeding makes
the file discoverable from a root kiro actually reads.

**The seeding contract** (plan `:139`), which every function here obeys:

1. *Marker-scoped.* We never edit a user file. We create exactly one path we own
   and we record it in a **receipt** under our own home
   (``$PERSONALCLAW_HOME/acp_seeds.json``). The receipt — not a guess about the
   file's contents — is what makes a seed identifiable as ours.
2. *Idempotent.* Seeding an already-correct seed is a no-op that reports
   ``already_seeded``; it does not rewrite, re-stat or re-audit anything.
3. *Never clobber user config outside our block.* A pre-existing path we did not
   write is left exactly as-is and the seed is refused
   (``skipped_user_owned``). Every other file in the CLI's config directory is
   untouched — we address one path by name.
4. *Reversible.* Unseeding removes exactly what the receipt says we wrote, and
   only while the on-disk state still matches it. A seed the user has since
   replaced is disowned, not deleted (``skipped_diverged``).
5. *Audited.* Every seed and unseed decision, including the refusals, emits a SEL
   event.

A symlink (not a copy) is the seeded artifact so the seed cannot go stale when the
generated config is refreshed at install time, and so "what did we write" has a
single verifiable answer: a link at a known path pointing at a known target.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: The marker recorded in every receipt entry. Present so a receipt hand-inspected
#: by an operator (or a future format change) is self-identifying.
SEED_MARKER = "personalclaw-managed"

#: Receipt file, under OUR home — never inside the CLI's config tree.
_RECEIPT_NAME = "acp_seeds.json"


def _receipt_path() -> Path:
    from personalclaw.config import config_dir

    return config_dir() / _RECEIPT_NAME


def _generated_agent_config() -> Path:
    """The host-generated agent config that already lists ``@personalclaw-core``."""
    from personalclaw.config import config_dir

    return config_dir() / "agents" / "personalclaw.json"


def _load_receipt() -> dict:
    path = _receipt_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_receipt(receipt: dict) -> None:
    path = _receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _audit(operation: str, outcome: str, resources: str, error: str = "") -> None:
    """SEL-audit a seed/unseed decision (contract item 5)."""
    try:
        from personalclaw.security import redact
        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="acp_config_seed",
                caller_identity="acp_config_seed",
                agent="personalclaw",
                source="cli",
                operation=operation,
                outcome=outcome,
                resources=redact(resources),
                error=error,
            )
        )
    except Exception:
        logger.debug("SEL audit for %s failed", operation, exc_info=True)


def _is_our_link(path: Path, target: str) -> bool:
    """Whether *path* is precisely the symlink we record as ours.

    ``os.readlink`` rather than ``resolve()``: a broken link (its target's home was
    removed) is still ours to clean up, and ``resolve()`` on a broken link would
    compare a path that does not exist.
    """
    try:
        return path.is_symlink() and os.readlink(path) == target
    except OSError:
        return False


def seed_agent_config(cli: str, agents_dir: str | Path) -> dict:
    """Make the generated ``personalclaw.json`` discoverable from *agents_dir*.

    ``agents_dir`` is the CLI's OWN agent-discovery root and is supplied by the
    agent app bundle, never guessed here: which directory a given CLI reads is
    vendor knowledge, and the provider boundary keeps vendor knowledge in the
    removable bundle. Core owns the mechanism (where our config lives, what a
    seed is, how it is recorded and reversed); the bundle owns the destination.

    Returns ``{"status": ..., "path": ..., "target": ...}`` where status is one of
    ``seeded`` / ``already_seeded`` / ``skipped_user_owned`` / ``skipped_no_source``
    / ``failed``.
    """
    provider = cli
    source = _generated_agent_config()
    dest_dir = Path(agents_dir)
    dest = dest_dir / "personalclaw.json"
    target = str(source)
    result = {"status": "failed", "path": str(dest), "target": target}

    if not source.is_file():
        # Nothing to point at yet: the agent config is generated at install time.
        result["status"] = "skipped_no_source"
        _audit("seed", "rejected", f"provider={provider} dest={dest}", "source config missing")
        return result

    if _is_our_link(dest, target):
        # Contract item 2: idempotent — do not rewrite, do not re-audit.
        result["status"] = "already_seeded"
        return result

    if dest.exists() or dest.is_symlink():
        # Contract item 3: a path we did not write is the user's. Never clobber it.
        result["status"] = "skipped_user_owned"
        _audit(
            "seed",
            "denied",
            f"provider={provider} dest={dest}",
            "destination exists and is not a personalclaw-managed link",
        )
        return result

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(source)
    except OSError as exc:
        _audit("seed", "failed", f"provider={provider} dest={dest}", str(exc))
        return result

    receipt = _load_receipt()
    receipt[provider] = {
        "marker": SEED_MARKER,
        "kind": "symlink",
        "path": str(dest),
        "target": target,
        "seeded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _write_receipt(receipt)
    _audit("seed", "completed", f"provider={provider} dest={dest} target={target}")
    result["status"] = "seeded"
    return result


def unseed_agent_config(cli: str, agents_dir: str | Path | None = None) -> dict:
    """Remove exactly what :func:`seed_agent_config` wrote for *cli*, and nothing else.

    Returns ``{"status": ...}`` where status is one of ``unseeded`` /
    ``not_seeded`` / ``skipped_diverged`` / ``failed``.
    """
    provider = cli
    receipt = _load_receipt()
    entry = receipt.get(provider)
    if not isinstance(entry, dict):
        return {"status": "not_seeded", "path": ""}

    # The receipt is authoritative about what we wrote. ``agents_dir`` only
    # overrides it when the receipt predates a relocation (tests, or a moved root).
    recorded = Path(str(entry.get("path") or ""))
    if agents_dir is not None and recorded.name:
        recorded = Path(agents_dir) / recorded.name
    target = str(entry.get("target") or "")
    result = {"status": "failed", "path": str(recorded)}

    if not _is_our_link(recorded, target):
        # Contract item 4: the user replaced it. Disown, do not delete.
        receipt.pop(provider, None)
        _write_receipt(receipt)
        result["status"] = "skipped_diverged"
        _audit(
            "unseed",
            "denied",
            f"provider={provider} dest={recorded}",
            "on-disk state no longer matches the receipt",
        )
        return result

    try:
        recorded.unlink()
    except OSError as exc:
        _audit("unseed", "failed", f"provider={provider} dest={recorded}", str(exc))
        return result

    receipt.pop(provider, None)
    _write_receipt(receipt)
    _audit("unseed", "completed", f"provider={provider} dest={recorded}")
    result["status"] = "unseeded"
    return result


def seed_status(cli: str, *, agents_dir: str | Path | None = None) -> str:
    """Report the live seed state for *cli* without changing anything."""
    entry = _load_receipt().get(cli)
    if not isinstance(entry, dict):
        return "not_seeded"
    recorded = Path(str(entry.get("path") or ""))
    if agents_dir is not None and recorded.name:
        recorded = Path(agents_dir) / recorded.name
    return "seeded" if _is_our_link(recorded, str(entry.get("target") or "")) else "diverged"
