"""Which device a request's sign-in sentences speak to, and the command they name for it.

`personalclaw token` prints this computer's link, which another device cannot open; `--remote`
prints the link at `dashboard.url`, the address other devices open. The auth middleware decides
for each request (:func:`_speaks_to_another_device`) and every sign-in sentence of that request
names its command through :func:`sign_in_command`.
"""

from __future__ import annotations

from contextvars import ContextVar

from aiohttp import web

from personalclaw.dashboard.origin import is_loopback

#: Whether a sign-in sentence of this request names `personalclaw token --remote`: set for each
#: request by the auth middleware (:func:`_speaks_to_another_device`). `personalclaw token` prints
#: this computer's link, which another device cannot open; `--remote` prints the link at
#: `dashboard.url`, the address other devices open.
_FROM_ANOTHER_DEVICE: ContextVar[bool] = ContextVar("pc_sign_in_from_another_device", default=False)


def sign_in_command(*, html: bool = False) -> str:
    """The command a sign-in sentence names for the device it answers: backticked, or in a
    ``<code>`` element for a page."""
    words = "personalclaw token --remote" if _FROM_ANOTHER_DEVICE.get() else "personalclaw token"
    return f"<code>{words}</code>" if html else f"`{words}`"


def _speaks_to_another_device(request: web.Request, remote_origin: str) -> bool:
    """Whether *request*'s sign-in sentences name `personalclaw token --remote`: there is an
    address other devices open (*remote_origin*, ``dashboard.url``'s origin), and the request came
    from a device that is not this computer.

    That is a peer that is not loopback, or a loopback peer (a proxy on this computer) whose
    ``X-Real-IP`` names another address and is believed: the sign-in lockout's rule
    (``exposure.client_address`` without the private-address heuristic), which believes the
    header only from a peer listed in ``dashboard.trusted_proxies``. So no header from any other
    peer moves the answer, and a loopback request without ``X-Real-IP`` reads no config.
    """
    if not remote_origin:
        return False
    if not is_loopback(request.remote or ""):
        return True
    if not request.headers.get("X-Real-IP", "").strip():
        return False
    from personalclaw.dashboard.exposure import client_address

    return not is_loopback(client_address(request, private_peers=False))
