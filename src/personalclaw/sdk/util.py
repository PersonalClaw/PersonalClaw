"""SDK: the few cross-cutting helpers an app legitimately needs from core.

- ``config_dir()`` — PersonalClaw's home dir (``~/.personalclaw`` or ``PERSONALCLAW_HOME``).
- ``app_data_dir(name)`` — an app's private, persisted data dir (survives updates).
- ``sandbox_wrap_argv(argv, mode)`` — wrap a command in the host sandbox (an app that
  shells out runs under the same confinement core does).
- ``atomic_write(path, data)`` — crash-safe file write (an app persisting config/state
  uses the same durable write core does).

Keep this surface tiny: an app reaching for more than these is a sign the boundary is
wrong (promote the need to a proper SDK submodule, or vendor it into the app).
"""

from personalclaw.apps.manager import app_data_dir  # noqa: F401
from personalclaw.atomic_write import atomic_write  # noqa: F401
from personalclaw.config.loader import config_dir  # noqa: F401
from personalclaw.sandbox import wrap_argv as sandbox_wrap_argv  # noqa: F401

__all__ = ["config_dir", "app_data_dir", "sandbox_wrap_argv", "atomic_write"]
