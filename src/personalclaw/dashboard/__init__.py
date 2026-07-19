"""Web dashboard + gateway API — served at ``localhost:10000`` by default.

Uses ``aiohttp`` for HTTP serving, a multiplexed WebSocket (``/api/ws``) for
live real-time events, and Server-Sent Events for per-resource streams. Serves
static assets from the ``static/`` directory.

Key modules: ``server`` (route wiring + lifecycle), ``state``
(DashboardState / chat-session data), ``chat_*`` (multi-session chat endpoints
+ the background runner), and the ``handlers/`` subpackage (per-domain
endpoints).
"""

from personalclaw.dashboard.server import start_api_server, start_dashboard
from personalclaw.dashboard.state import DashboardState, _ChatSession, _fmt_duration

__all__ = ["start_api_server", "start_dashboard", "DashboardState", "_ChatSession", "_fmt_duration"]
