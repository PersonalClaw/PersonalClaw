"""Localhost web preview for a run's dev server.

When a code run's workspace is running a dev server, the cockpit should be able to open it —
the "see what my code loop built" case. This module answers one question: **which listening
TCP ports belong to processes working inside this run's workspace?**

Three deliberate properties, each of which is the answer to a way this feature is usually
built wrong:

* **Computed on read, never persisted.** The obvious design registers ``preview_urls`` on the
  run record, but a stored port list is exactly the stale-link defect — a dev server dies, the
  record keeps the port, and the cockpit offers an "Open Preview" that loads nothing (or, worse,
  loads whatever process took the port next). The scan is cheap and the truth is on the host,
  so the record does not cache it. Removal on teardown then costs no code: a torn-down
  workspace has no processes under it, so it reports no ports.
* **A port is only offered if ``localhost`` can actually reach it.** A listener bound to a LAN
  address is a real listener and a dead ``localhost`` link, so only wildcard/loopback binds are
  reported (:data:`_LOOPBACK_ADDRS`).
* **"Nothing found" and "could not look" are different answers.** Every result carries a
  ``reason`` when the list is empty, because rendering an empty preview section on a host with
  no ``lsof`` would tell the user their server is not running when the truth is that nothing
  looked.

Scope guard: this is not a tunnel and not a share. It reports ``http://localhost:<port>``
on the same machine, and there is no authentication layer because there is no remote party.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.process_facts import run_probe, working_dirs

logger = logging.getLogger(__name__)

#: Bind addresses a ``localhost`` URL can actually reach. A listener on a specific LAN
#: address is deliberately NOT previewable — the link would resolve and then fail.
_LOOPBACK_ADDRS: frozenset[str] = frozenset(
    {"*", "127.0.0.1", "0.0.0.0", "::", "::1", "[::]", "[::1]", "localhost"}  # noqa: S104
)

#: Ceiling on reported ports. A runaway process tree should not turn one drawer into a
#: thousand links.
_MAX_PORTS = 24


@dataclass
class PreviewPort:
    """One previewable dev server: a port, the process behind it, and the URL to open."""

    port: int
    pid: int = 0
    command: str = ""
    address: str = ""

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "url": self.url,
            "pid": self.pid,
            "command": self.command,
            "address": self.address,
        }


@dataclass
class PreviewScan:
    """The result of one scan. ``reason`` explains an empty ``ports`` list, always."""

    ports: list[PreviewPort] = field(default_factory=list)
    root: str = ""
    scanned: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ports": [p.to_dict() for p in self.ports],
            "root": self.root,
            "scanned": self.scanned,
            "reason": self.reason,
        }


def parse_lsof_listeners(out: str) -> list[tuple[int, str, int]]:
    """Parse ``lsof -nP -iTCP -sTCP:LISTEN -FpPn`` into ``(pid, address, port)`` triples.

    The ``-F`` field format emits a ``p<pid>`` line that OWNS every following ``n<addr>:<port>``
    line until the next ``p``. Parsed as a pure function so the format is testable without a
    live socket.
    """
    found: list[tuple[int, str, int]] = []
    pid = 0
    for line in out.splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(rest)
            except ValueError:
                pid = 0
            continue
        if tag != "n" or pid <= 0:
            continue
        addr, sep, port_text = rest.rpartition(":")
        if not sep:
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        if 0 < port < 65536:
            found.append((pid, addr, port))
    return found


def parse_ss_listeners(out: str) -> list[tuple[int, str, int]]:
    """Parse ``ss -lntpH`` into ``(pid, address, port)`` triples (the Linux tier).

    A row's local address is field 3 and the process block looks like
    ``users:(("node",pid=4242,fd=24))``. Kept a pure function for the same reason as the
    ``lsof`` parser: this box is Darwin, so the format is asserted from a fixture rather
    than left unverified.
    """
    found: list[tuple[int, str, int]] = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        addr, sep, port_text = fields[3].rpartition(":")
        if not sep:
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        if not 0 < port < 65536:
            continue
        for chunk in line.split("pid=")[1:]:
            digits = ""
            for ch in chunk:
                if not ch.isdigit():
                    break
                digits += ch
            if digits:
                found.append((int(digits), addr, port))
    return found


def _listeners() -> tuple[list[tuple[int, str, int]], str]:
    """Every listening TCP socket with its owning pid. Returns (triples, reason-if-empty)."""
    if shutil.which("lsof"):
        out = run_probe(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-FpPn"])
        return parse_lsof_listeners(out), ""
    if shutil.which("ss"):
        return parse_ss_listeners(run_probe(["ss", "-lntpH"])), ""
    # Honest degradation: the caller must be able to tell this apart from "found nothing".
    return [], "no port scanner available on this host (install lsof)"


def _command(pid: int) -> str:
    """A short command label for *pid* — never its full argv.

    Truncated and stripped of arguments deliberately: a dev-server command line routinely
    carries tokens and paths, and this string is rendered in the cockpit and copied into bug
    reports.
    """
    out = run_probe(["ps", "-o", "comm=", "-p", str(pid)]).strip()
    return Path(out.splitlines()[0]).name[:64] if out else ""


def _within(path: str, root: Path) -> bool:
    """Whether *path* resolves inside *root*. Resolved, so a symlink cannot smuggle a match."""
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def discover_ports(root: Path | str) -> PreviewScan:
    """Listening, localhost-reachable ports owned by processes working inside *root*.

    The scan is two probes, not one per process: one for every listening socket, then one
    batched cwd lookup for just the pids that turned up. A host with fifty listeners costs
    two subprocess calls.
    """
    scan = PreviewScan(root=str(root))
    try:
        base = Path(root).resolve()
    except OSError:
        scan.reason = "the run's workspace path could not be resolved"
        return scan
    if not base.is_dir():
        scan.reason = "the run's workspace is gone, so nothing can be running in it"
        return scan

    triples, reason = _listeners()
    if reason:
        scan.reason = reason
        return scan
    scan.scanned = True
    reachable = [t for t in triples if t[1] in _LOOPBACK_ADDRS]
    if not reachable:
        scan.reason = "no dev server is listening in this run's workspace"
        return scan

    own = os.getpid()
    pids = sorted({pid for pid, _addr, _port in reachable if pid != own})
    cwds = working_dirs(pids)
    seen: set[int] = set()
    for pid, addr, port in sorted(reachable, key=lambda t: t[2]):
        if pid == own or port in seen or not _within(cwds.get(pid, ""), base):
            continue
        seen.add(port)
        scan.ports.append(PreviewPort(port=port, pid=pid, command=_command(pid), address=addr))
        if len(scan.ports) >= _MAX_PORTS:
            break
    if not scan.ports:
        scan.reason = "no dev server is listening in this run's workspace"
    return scan


def preview_scan(run: Any) -> PreviewScan:
    """The preview scan for one run record.

    Reads the workspace path through :func:`~personalclaw.workflows.provisioning.workspace_state`
    — the one reader for that block — so this never becomes a second spelling of
    ``worktree_path``. An inline run has no isolated workspace and honestly says so rather
    than scanning the whole host.
    """
    from personalclaw.workflows import provisioning

    try:
        state = provisioning.workspace_state(run)
    except Exception:  # noqa: BLE001 — a preview is an aid; it must not break the panel
        logger.debug("web_preview: workspace_state failed", exc_info=True)
        return PreviewScan(reason="this run's workspace could not be read")
    path = str(state.get("path", "") or "")
    if not path:
        return PreviewScan(
            reason="this run has no isolated workspace, so there is nothing to preview"
        )
    try:
        return discover_ports(path)
    except Exception:  # noqa: BLE001
        logger.debug("web_preview: scan failed for %s", path, exc_info=True)
        return PreviewScan(root=path, reason="the port scan failed on this host")
