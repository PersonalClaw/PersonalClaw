"""What the gateway answers for a request its router has no route for.

The innermost middleware of ``start_dashboard``'s stack is one of these two. :func:`api_fallback`
answers the router's two refusals on ``/api/*`` in the one wire envelope; it is all a gateway
started without the dashboard's pages (``personalclaw gateway --headless``) runs.
:func:`spa_fallback` is the same, plus the web app's own part: ``index.html`` for any other page a
browser asks for, since the client-side router owns those paths.
"""

from aiohttp import web

from personalclaw.dashboard import handlers


@web.middleware  # type: ignore[misc]
async def api_fallback(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    """The router's refusals on ``/api/*``, in the one wire envelope; any other path's as is."""
    try:
        return await handler(request)  # type: ignore[operator]
    except web.HTTPNotFound:
        # An unmatched /api/* route must answer in the one wire envelope — a JSON
        # client that mistypes or hits a removed route cannot parse aiohttp's
        # text/plain default, and so cannot tell "route gone" from "server broke".
        # Handlers that ANSWER 404 (rather than raising) are untouched here.
        if request.path.startswith("/api/"):
            from personalclaw.http_errors import json_error

            return json_error("not_found", status=404)
        raise
    except web.HTTPMethodNotAllowed as exc:
        # A wrong method on a REAL /api/* route raises HTTPMethodNotAllowed, which
        # otherwise sails past the 404 branch and answers the very text/plain default
        # that branch exists to prevent. Normalize it to the same wire envelope. The
        # `Allow` header the router set (the methods that WOULD work) is preserved so
        # a client can still discover them.
        if request.path.startswith("/api/"):
            from personalclaw.http_errors import json_error

            allow = exc.headers.get("Allow")
            return json_error(
                "method_not_allowed",
                status=405,
                headers={"Allow": allow} if allow else None,
            )
        raise


@web.middleware  # type: ignore[misc]
async def spa_fallback(
    request: web.Request,
    handler: object,
) -> web.StreamResponse:
    """:func:`api_fallback`, and ``index.html`` for the client-side React Router's paths."""
    try:
        return await api_fallback(request, handler)
    except web.HTTPNotFound:
        # `/icons/` is excluded for the PWA: a manifest icon that resolves to
        # index.html is an invalid icon, and the only symptom is an install
        # prompt that never appears. A 404 is diagnosable; HTML is not.
        if request.method == "GET" and not request.path.startswith(
            ("/assets/", "/icons/", "/sprites/", "/vendor/")
        ):
            return await handlers.index(request)
        raise
