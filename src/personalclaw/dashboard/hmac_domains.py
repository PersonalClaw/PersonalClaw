"""Every label a key is derived under from the session signing key, in one list.

Three things are keyed off the one secret that signs sessions (``token_auth._secret``): a
browser's pair (``sign_in_pair``), a signed URL (``signed_urls``) and an automation client's
session token (``token_auth``, a scoped pairing). Each derives its own key as
``HMAC(secret, label + ...)``, so a value made for one can never verify as another. That holds
only while no label is a prefix of another, which this list lets a test check; a new derivation
adds its label here, not inline.
"""

from __future__ import annotations

#: A browser sign-in's pair: ``HMAC(secret, PAIR + nonce)``.
PAIR = b"pair\0"
#: A signed URL's per-session key: ``HMAC(secret, URL + nonce)``.
URL = b"url\0"
#: The key a scoped session's token is signed with: ``HMAC(secret, SCOPE)``.
SCOPE = b"scope\0v1"

HMAC_DOMAINS: tuple[bytes, ...] = (PAIR, URL, SCOPE)
