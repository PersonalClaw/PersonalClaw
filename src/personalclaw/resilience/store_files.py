"""The Doctor's two checks of the store files a write would be refused over.

A store file that is there and cannot be read is never written over (``record_files``): every
write to it is refused until it can be read, and a copy of it as it was is kept beside it. These
checks name such a file before a refused write is the first anyone hears of it: the automations
file in its own row (``automations.store``), which says what its being unreadable stops, in the
Triggers page's own words; every other one this gateway found, in another
(``durability.store_files``). Registered by :mod:`~personalclaw.resilience.doctor`, which owns the
probe registry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the Doctor imports this module to register the checks below
    from personalclaw.resilience.doctor import DoctorContext, Probe, ProbeResult


async def probe_trigger_store(ctx: DoctorContext) -> ProbeResult:
    """automations — can the automations file be read?

    A ``triggers.json`` that is there and cannot be read is never written over, and nothing in it
    is listed, armed or fired until it can be read. This row says which file, what that stops,
    where the copy of it is kept and what to do (``triggers.store.unreadable_said``); and, once it
    reads again, that a copy kept from when it could not is still there to remove. It reads the
    file and lists what is beside it; the one thing it may write is the copy of a file it finds
    unreadable, which every read of one keeps.
    """
    from personalclaw import record_files
    from personalclaw.resilience.doctor import ProbeResult
    from personalclaw.triggers.store import TriggerStore, unreadable, unreadable_said

    store = TriggerStore(base_dir=ctx.home)
    found = await asyncio.to_thread(unreadable, store)
    copies = [p.name for p in await asyncio.to_thread(record_files.kept_copies, store.path)]
    ev: dict[str, Any] = {"file": store.path.name, "kept": copies}
    if found is not None:
        said = unreadable_said(found)
        return ProbeResult(
            ok=False,
            detail=said["said"],
            evidence=ev,
            remedy=f"No automatic fix — what the file holds is yours to judge. {said['remedy']}",
        )
    if copies:
        return ProbeResult(
            ok=True,
            detail=(
                "The automations file can be read. A copy of it from when it could not be is "
                f"still kept beside it as {copies[-1]}; remove it when you no longer need it."
            ),
            evidence=ev,
        )
    return ProbeResult(ok=True, detail="The automations file can be read.", evidence=ev)


async def probe_unreadable_store_files(ctx: DoctorContext) -> ProbeResult:
    """durability — is every store file this gateway read readable?

    The files this process found it could not read, and that are still as they were then
    (``record_files.unreadable_files``), each with why and where its copy is. The automations file
    is left to its own row. Read-only.
    """
    from personalclaw import record_files
    from personalclaw.home_paths import from_home
    from personalclaw.resilience.doctor import ProbeResult
    from personalclaw.triggers.store import STORE_FILENAME

    home = ctx.home.resolve()
    automations = home / STORE_FILENAME

    def _here() -> list[record_files.Unreadable]:
        out = []
        for found in record_files.unreadable_files():
            where = found.path.resolve()
            if where.is_relative_to(home) and where != automations:
                out.append(found)
        return out

    found = await asyncio.to_thread(_here)
    if not found:
        return ProbeResult(ok=True, detail="Every store file this gateway read could be read.")
    named = [
        f"{from_home(f.path)} ({f.why}"
        + (f"; its copy is {f.kept.name})" if f.kept is not None else ")")
        for f in found
    ]
    count = len(found)
    return ProbeResult(
        ok=False,
        detail=(
            f"{count} store file{'s' if count != 1 else ''} could not be read, so nothing is "
            f"written to {'them' if count != 1 else 'it'} until {'they' if count != 1 else 'it'} "
            "can be: " + "; ".join(named) + "."
        ),
        evidence={
            "files": [
                {"file": from_home(f.path), "why": f.why, "kept": f.kept.name if f.kept else ""}
                for f in found
            ]
        },
        remedy=(
            "No automatic fix — what each holds is yours to judge. Repair each file, or remove it "
            "to start it over; the copy kept beside it keeps what it held."
        ),
    )


def store_file_probes() -> list[Probe]:
    """The two checks, as the Doctor registers them."""
    from personalclaw.resilience.doctor import Probe, Tier

    return [
        Probe(
            "automations.store",
            "automations",
            Tier.CAPABILITY,
            probe_trigger_store,
            "The automations file can be read",
        ),
        Probe(
            "durability.store_files",
            "durability",
            Tier.CAPABILITY,
            probe_unreadable_store_files,
            "Store files that could not be read",
        ),
    ]
