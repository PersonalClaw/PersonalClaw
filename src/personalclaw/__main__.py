"""Entry point for ``python -m personalclaw``, and the desktop app's frozen bundle.

In the frozen bundle this is the entry script of its executable. The bundle first gives back the
environment it was started with, then asks whether its command line runs one of the package's own
child modules (``_frozen_child``): those run as ``python -m`` would run them, before anything below
and without the CLI.

``_ensure_ssl_certs()`` MUST run before ``from personalclaw.cli import main``
because that import triggers ``aiohttp`` (via ``dashboard.origin`` →
``dashboard.__init__`` → ``dashboard.server`` → ``from aiohttp import web``).

aiohttp caches its default SSL context at import time
(``aiohttp.connector._SSL_CONTEXT_VERIFIED``).  On some systems the
cafile may be missing, so the cached context ends up with zero CA
certs and every HTTPS connection fails with CERTIFICATE_VERIFY_FAILED.
"""

import sys

from personalclaw.self_update import is_frozen

if __name__ == "__main__" and is_frozen():
    from personalclaw import _frozen_child

    _frozen_child.restore_environment()
    _child = _frozen_child.child_module(sys.argv)
    if _child:
        _frozen_child.run(_child)

from personalclaw._ssl_compat import _ensure_ssl_certs  # noqa: E402

_ensure_ssl_certs()

if __name__ == "__main__":
    from personalclaw.cli import main  # noqa: E402

    main()
