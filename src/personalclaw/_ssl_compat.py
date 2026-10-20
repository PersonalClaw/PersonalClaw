"""Ensure the system CA bundle is visible to OpenSSL on RPM-based distros.

Must run before any library (aiohttp, slack_sdk, requests) caches its
default SSL context, otherwise every HTTPS call fails with
CERTIFICATE_VERIFY_FAILED on systems where mise-installed Python looks
for certs at ``/etc/ssl/`` but the distro stores them at
``/etc/pki/tls/`` (Fedora, RHEL, CentOS, openSUSE, …).
"""

import os
from pathlib import Path

_CA_CANDIDATES = (
    "/etc/pki/tls/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt",
)


def _ensure_ssl_certs() -> None:
    """Point OpenSSL at the system CA bundle before any library caches it.

    Sets both ``SSL_CERT_FILE`` (used by OpenSSL / aiohttp) and
    ``REQUESTS_CA_BUNDLE`` (used by the ``requests`` library / slack_sdk).
    """
    if os.environ.get("SSL_CERT_FILE"):
        return

    import ssl

    defaults = ssl.get_default_verify_paths()
    if defaults.cafile and Path(defaults.cafile).exists():
        return

    for candidate in _CA_CANDIDATES:
        if Path(candidate).exists():
            os.environ["SSL_CERT_FILE"] = candidate
            os.environ.setdefault("REQUESTS_CA_BUNDLE", candidate)
            return
