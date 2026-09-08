"""Config package."""

from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AppConfig, env_path, resolve_agent_config_path


def config_dir():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


def config_path():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


__all__ = ["AppConfig", "config_dir", "config_path", "env_path", "resolve_agent_config_path"]
