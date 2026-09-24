"""The refusal a platform PersonalClaw *intends* to support gives before its driver exists
(`DCU-6`, §3.6).

**Why this is not the refusal :mod:`~personalclaw.computer_use.driver_host` already had.** When
``resolve_driver`` finds nothing importable it answers ``ERR_COMPUTER_USE_DRIVER_UNAVAILABLE``,
and that code's meaning is *this build has no driver for this platform at all* — the honest
answer for a platform outside ``DRIVER_MODULES`` entirely (FreeBSD, an unknown
``platform.system()``), and the answer for a mapped platform whose driver has no handler for the
requested operation.
Windows and Linux are a different situation and flattening the two is the dishonesty this atom
exists to remove: PersonalClaw fully intends to drive those desktops, the accessibility API each
will use is already chosen (UIA / AT-SPI), and *no action the operator takes on that machine
changes the answer today*. So they get their own registered code, their own WHY naming the API
that is missing, and a FIX that names the one thing that does work — macOS — instead of
"nothing to configure", which reads as a shrug.

**One refusal, two platform modules.** :mod:`~personalclaw.computer_use.windows_driver` and
:mod:`~personalclaw.computer_use.linux_driver` exist because ``DRIVER_MODULES`` resolves a driver
by importing the module named for the platform, and because each is the file a future
implementer fills in. What they must NOT each own is the wording: two copies of a WHAT/WHY/FIX
drift the moment one is edited, and the structural-duplication ratchet counts that family for
exactly this reason. So the sentence lives here once and is parameterised by the two facts that
genuinely differ — the platform's name and the accessibility API a real driver there will use.

**Nothing in this module or its two callers touches the OS.** They import cleanly on every
platform, deliberately: ``resolve_driver`` runs *inside the gateway's own process*, so a driver
module that raised at import time on the wrong OS would turn "this machine has no desktop
capability" into "this machine has no gateway". ``macos_ffi``'s no-framework-at-import rail
makes the same promise for the driver that does call an OS; here it is free, and
``test_computer_use_unsupported_platforms.py`` pins it so a future real driver cannot quietly
put a ``import comtypes`` at the top of ``windows_driver`` and take the gateway down on macOS.
"""

from __future__ import annotations

from typing import Any

from personalclaw.computer_use.types import DriverError

#: A platform this build means to support, whose driver is not written yet. Distinct from
#: ``ERR_COMPUTER_USE_DRIVER_UNAVAILABLE`` (no driver at all / no handler for this operation) and
#: from ``ERR_COMPUTER_USE_DRIVER_FAILED`` (a driver ran and something broke) because the three
#: are acted on differently: this one is fixed by running on macOS or waiting for the platform's
#: driver, and by nothing the operator can do on the machine in front of them. ``service``'s
#: ``_CHILD_CODES`` honours it — like every other member it is a REFUSAL, so a child naming it
#: can only cause a refusal and never an approval.
ERR_PLATFORM_UNSUPPORTED = "ERR_COMPUTER_USE_PLATFORM_UNSUPPORTED"


def refusal(platform: str, accessibility_api: str, op: str) -> dict[str, Any]:
    """The typed answer every operation on *platform* gives, as the wire envelope.

    A :class:`~personalclaw.computer_use.types.DriverError` rather than a dict literal built
    here: the ``{"error": {"code": …}}`` shape has one serialiser in this codebase's driver
    layer and this is not a fourteenth place to re-derive it.
    """
    return DriverError(
        ERR_PLATFORM_UNSUPPORTED,
        f"desktop computer use has no {platform} driver yet, so {op} did not run",
        why=(
            "PersonalClaw drives a desktop through the OS accessibility layer, and the "
            f"{platform} implementation ({accessibility_api}) is not in this build — there is "
            "nothing here to read a window's element tree with, and nothing to post input "
            "through. Nothing was clicked, typed, scrolled or changed."
        ),
        fix=(
            "This is a missing implementation, not a permission or a setting, so no "
            f"configuration on this {platform} machine turns it on — do not go looking for one. "
            "macOS (the AX accessibility API) is the only desktop driver implemented today, so "
            "run the gateway on macOS if the agent needs to drive a desktop application. Every "
            f"other PersonalClaw capability works normally on {platform}."
        ),
    ).to_dict()
