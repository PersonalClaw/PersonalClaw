"""The gateway version travels in the /api/system static payload.

The dashboard no longer carries a version pill; the shell's SystemWidget card
reads the version straight from /api/system, so the static info block must
expose it.
"""

import personalclaw


def test_static_system_info_includes_gateway_version() -> None:
    from personalclaw.dashboard import handlers_system

    # Reset the module-level cache so the field is computed fresh (other tests
    # may have populated it already).
    handlers_system._STATIC_SYSTEM_INFO = None
    info = handlers_system._get_static_system_info()

    assert info["version"] == personalclaw.__version__
