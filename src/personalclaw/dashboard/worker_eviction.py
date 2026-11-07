"""A service worker this gateway didn't serve removes itself.

The worker it's answered with deletes this origin's caches and unregisters. It has no fetch
handler, so it serves nothing while it does.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

#: The one path this gateway serves its own worker at (``handlers.service_worker``).
WORKER_PATH = "/sw.js"

SELF_REMOVING_WORKER = """\
// This address serves no PersonalClaw worker: remove this one, and everything it kept.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.map(function (key) { return caches.delete(key); }));
      })
      .then(function () { return self.registration.unregister(); })
  );
});
"""


def is_worker_script_fetch(request: Any) -> bool:
    """Whether *request* is the browser fetching a service worker's script, to register it or to
    check it for an update."""
    headers = getattr(request, "headers", None) or {}
    return (
        str(headers.get("Service-Worker") or "").lower() == "script"
        or str(headers.get("Sec-Fetch-Dest") or "").lower() == "serviceworker"
    )


def serves_no_worker(request: Any) -> bool:
    """Whether *request* fetches a worker script at a path where this gateway has none."""
    return request.path != WORKER_PATH and is_worker_script_fetch(request)


def self_removing_worker() -> web.Response:
    """The worker that removes itself and every cache this origin kept."""
    return web.Response(
        text=SELF_REMOVING_WORKER,
        content_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )
