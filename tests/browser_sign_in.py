"""A browser's sign-in, the way a test presents it: a bound session's cookie, and its pair.

Every sign-in a browser's cookie holds is bound to its pair (``dashboard/sign_in_pair.py``), and a
request on it carries the pair in ``X-PersonalClaw-Pair``, as the dashboard's own page sends it.
A test that stands in for the page sends both; one that sends the cookie alone is asserting the
refusal.
"""

from __future__ import annotations

from typing import Any

from personalclaw.dashboard import token_auth
from personalclaw.dashboard.session_store import ISSUER_LOGIN
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER, pair_for


def browser_token(
    user: str = "owner", ttl: int = 600, *, issuer: str = ISSUER_LOGIN, device: Any = None
) -> str:
    """A browser's bound sign-in through *issuer*'s door."""
    return token_auth.mint_session(user, ttl, issuer=issuer, device=device, bound=True).token


def pair_of(token: str) -> str:
    """The pair the page that holds *token*'s session keeps."""
    return pair_for(token_auth._secret(), token_auth.token_nonce(token))


def browser_headers(token: str, *, port: int = 10000) -> dict[str, str]:
    """What the page holding *token* sends with every request: the cookie and the pair."""
    return {"Cookie": f"pc_token_{port}={token}", PAIR_HEADER: pair_of(token)}


async def shell(request: Any) -> Any:
    """The dashboard's shell as ``handlers.index`` serves it, for a test gateway's ``/``: a
    ``?token=`` document carries the new sign-in's pair on its first-in-head scrub script."""
    from aiohttp import web

    from personalclaw.dashboard.owner_token_url import inject_scrub

    html = "<html><head></head><body>the dashboard</body></html>"
    if request.query.get("token"):
        html = inject_scrub(html, pair=str(request.get("sign_in_pair") or ""))
    return web.Response(text=html, content_type="text/html")


def handed_pair(headers: Any, content_type: str, body: bytes) -> str:
    """The pair an answer hands the page, wherever the sign-in that answered puts it: the
    exchange's header, the shell's scrub script, or a sign-in's JSON. ``""`` when none."""
    import json
    import re

    handed = str(headers.get(PAIR_HEADER, "") or "")
    if not handed and content_type == "text/html":
        found = re.search(r'data-pair="([A-Za-z0-9_-]{43})"', body.decode("utf-8", "replace"))
        handed = found.group(1) if found else ""
    if not handed and content_type == "application/json" and body:
        try:
            answer = json.loads(body)
        except ValueError:
            answer = None
        handed = str(answer.get("pair") or "") if isinstance(answer, dict) else ""
    return handed
