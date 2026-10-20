"""personalclaw-client — async Python client for the PersonalClaw Gateway.

Usage::

    from personalclaw_client import PersonalClawClient

    async with PersonalClawClient(app_name="my-app") as mc:
        ok = await mc.ping()
        status = await mc.get_status()
        await mc.send_message("session-1", "hello")
"""
from personalclaw_client.client import PersonalClawClient
from personalclaw_client.errors import PersonalClawError, ErrorCode

__all__ = ["PersonalClawClient", "PersonalClawError", "ErrorCode"]
