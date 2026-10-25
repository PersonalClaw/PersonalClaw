"""Whether this Linux host lets Chromium's own sandbox run, for the catalogue's Electron jobs off
GitHub (``run``, ``make check``, ``make gate``).

Chromium sandboxes its renderers in user namespaces. Ubuntu 24.04's AppArmor rule
(``kernel.apparmor_restrict_unprivileged_userns = 1``) withholds them from an unconfined program,
so Electron falls back to its setuid sandbox helper, ``chrome-sandbox`` beside its binary, and
aborts unless that helper is root's with mode 4755. On a GitHub-hosted runner
``tools/ci/sandbox_helper.sh`` gives it that. Off GitHub nothing does, and nothing can that holds
for a run: an Electron job's own ``npm ci`` removes ``node_modules``, ``install_electron.sh``
downloads a new helper, and desktop-smoke's electron-builder build makes another, each owned by
whoever runs the job. So where the rule withholds the namespaces, every Electron job off GitHub
aborts, whatever was done to a helper before it started, and :func:`problem` names the host as
the cause and the commands that lift the rule. It names the commands that give a helper root's
4755 too, for running that Electron by hand, and says they do not hold for a run.

Only the AppArmor rule is read. A kernel that withholds the namespaces another way (Debian's
``kernel.unprivileged_userns_clone = 0``, ``user.max_user_namespaces = 0``) is not, and its
Electron jobs run and fail as they always did. Nor is a local AppArmor profile that grants
Electron the namespaces with the rule still on: its jobs could run, and still read "cannot run
here". Neither is ever a false green, and the commands named still lift the rule. A rule that
cannot be read (a ``/proc`` that refuses it) reads as none, so the job runs and fails as before.
``PCLAW_USERNS_RULE_FILE`` names a file to read in the kernel's place, for a test that plants a
host: a planted ``0`` lets the job run, and on a host that withholds the namespaces it then fails
red as it did before this check, never green.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

#: The AppArmor rule, by its sysctl name and where the kernel shows it.
USERNS_RULE = "kernel.apparmor_restrict_unprivileged_userns"
USERNS_RULE_FILE = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
#: The file read in the kernel's place when set: a test's planted host.
RULE_FILE_ENV = "PCLAW_USERNS_RULE_FILE"

#: Where a setting is kept across reboots, as the fix names it.
KEEP_FILE = "/etc/sysctl.d/60-apparmor-userns.conf"

#: The toolchain's sandbox helper, under the repository root: what ``install_electron.sh`` leaves.
HELPER = Path("node_modules") / "electron" / "dist" / "chrome-sandbox"


def read_rule(path: Path | None = None) -> str | None:
    """The rule's value as the kernel shows it, or ``None`` on a kernel without it or where it
    cannot be read. *path* defaults to the kernel's file, or the one ``PCLAW_USERNS_RULE_FILE``
    names."""
    if path is None:
        named = os.environ.get(RULE_FILE_ENV)
        path = Path(named) if named else USERNS_RULE_FILE
    try:
        return path.read_text().strip()
    except OSError:
        return None


def fix() -> list[str]:
    """The commands that let this host run the Electron jobs: lift the rule now, and keep it
    lifted after a reboot."""
    return [
        f"sudo sysctl -w {USERNS_RULE}=0",
        f"echo '{USERNS_RULE} = 0' | sudo tee {KEEP_FILE}",
    ]


def by_hand(root: Path) -> list[str]:
    """The commands that give the toolchain's helper under *root* root's 4755, for running that
    Electron by hand: a job's own installs replace the helper, so they do not hold for a run."""
    helper = root / HELPER
    return [f"sudo chown root:root {helper}", f"sudo chmod 4755 {helper}"]


def problem(
    root: Path, *, platform: str = sys.platform, read: Callable[[], str | None] = read_rule
) -> str | None:
    """Why an Electron job cannot run on this host, with the commands that fix it; ``None`` when
    it can: a host that is not Linux, a kernel without the rule, or the rule off. No helper's
    owner or mode is read: with the rule off Chromium sandboxes itself, and no helper is used,
    and with it on a job's own installs replace whatever helper is there before it starts."""
    if platform != "linux":
        return None
    value = read()
    if value is None or value == "0":
        return None
    return (
        f"this host withholds the user namespaces Chromium's sandbox needs ({USERNS_RULE} ="
        f" {value}), so Electron needs its setuid helper root's with mode 4755, and the job's"
        " own installs make a new one that is not. To run it here, lift the rule: "
        + "; ".join(fix())
        + ". To run that Electron by hand instead: "
        + "; ".join(by_hand(root))
        + " (make gate's own installs replace that helper, so this does not hold for a run)"
    )
