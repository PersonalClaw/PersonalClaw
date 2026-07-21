"""OIDC / OAuth2 JWT verification for AuthMode.OAUTH2.

The ``cryptography`` package is imported lazily inside :meth:`OidcVerifier.__init__`
so that it is never loaded unless the operator has explicitly set
``AuthMode.OAUTH2``.

Verification steps:
1. Fetch JWKS from ``{issuer}/.well-known/jwks.json`` (cached for 1 h).
2. Decode the JWT header to find the signing key by ``kid``.
3. Verify the RS256 (or ES256) signature.
4. Assert ``exp > now``, ``iss == issuer``, ``aud`` contains ``audience``.

This module is only imported from ``dashboard.token_auth.auth_middleware``
when the mode is ``OAUTH2``; it is never imported at the top level of any
other module.
"""

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_JWKS_TTL_SECS = 3600  # refresh JWKS at most once per hour


class OidcVerificationError(Exception):
    """Raised when JWT verification fails for any reason."""


class OidcVerifier:
    """Verifies RS256/ES256 JWTs issued by a standard OIDC provider.

    ``cryptography`` is imported inside ``__init__`` so the package is
    never loaded unless this class is instantiated.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        client_id: str | None = None,
    ) -> None:
        # Lazy import — only loaded when AuthMode.OAUTH2 is active.
        try:
            from cryptography.hazmat.primitives.asymmetric.ec import (  # noqa: F401
                ECDSA,
                EllipticCurvePublicKey,
            )
            from cryptography.hazmat.primitives.asymmetric.rsa import (  # noqa: F401
                RSAPublicKey,
            )
            from cryptography.hazmat.primitives.hashes import SHA256  # noqa: F401
            from cryptography.hazmat.primitives.serialization import (  # noqa: F401
                Encoding,
                PublicFormat,
            )
            from cryptography.x509 import load_der_x509_certificate  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'cryptography' package is required for AuthMode.OAUTH2. "
                "Install it with: pip install cryptography"
            ) from exc

        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client_id = client_id
        self._jwks_uri = f"{self._issuer}/.well-known/jwks.json"
        self._jwks_cache: dict[str, Any] = {}
        self._jwks_fetched_at: float = 0.0

    # ── JWKS ──────────────────────────────────────────────────────────

    def _fetch_jwks(self) -> dict[str, Any]:
        now = time.time()
        if self._jwks_cache and (now - self._jwks_fetched_at) < _JWKS_TTL_SECS:
            return self._jwks_cache
        try:
            with urllib.request.urlopen(self._jwks_uri, timeout=10) as resp:
                raw = resp.read()
            self._jwks_cache = json.loads(raw)
            self._jwks_fetched_at = now
            logger.debug("OidcVerifier: refreshed JWKS from %s", self._jwks_uri)
        except Exception as exc:
            if self._jwks_cache:
                logger.warning("OidcVerifier: JWKS refresh failed (%s); using cached keys", exc)
            else:
                raise OidcVerificationError(
                    f"Failed to fetch JWKS from {self._jwks_uri}: {exc}"
                ) from exc
        return self._jwks_cache

    def _find_key(self, kid: str | None, alg: str) -> Any:
        from cryptography.x509 import load_der_x509_certificate

        jwks = self._fetch_jwks()
        keys = jwks.get("keys", [])
        for jwk in keys:
            if kid and jwk.get("kid") != kid:
                continue
            if jwk.get("alg") and jwk["alg"] != alg:
                continue
            use = jwk.get("use", "sig")
            if use != "sig":
                continue
            kty = jwk.get("kty", "")
            if kty == "RSA":
                return self._rsa_public_key(jwk)
            if kty == "EC":
                return self._ec_public_key(jwk)
            # x5c fallback
            x5c = jwk.get("x5c", [])
            if x5c:
                der = _b64_decode_url_safe(x5c[0])
                cert = load_der_x509_certificate(der)
                return cert.public_key()
        raise OidcVerificationError(f"No matching key found in JWKS for kid={kid!r}, alg={alg!r}")

    @staticmethod
    def _rsa_public_key(jwk: dict[str, Any]) -> Any:
        import base64

        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

        def _b64_int(s: str) -> int:
            padding = 4 - len(s) % 4
            data = base64.urlsafe_b64decode(s + "=" * (padding % 4))
            return int.from_bytes(data, "big")

        n = _b64_int(jwk["n"])
        e = _b64_int(jwk["e"])
        return RSAPublicNumbers(e, n).public_key()

    @staticmethod
    def _ec_public_key(jwk: dict[str, Any]) -> Any:
        import base64

        from cryptography.hazmat.primitives.asymmetric.ec import (
            SECP256R1,
            SECP384R1,
            SECP521R1,
            EllipticCurvePublicNumbers,
        )

        _CURVES = {"P-256": SECP256R1(), "P-384": SECP384R1(), "P-521": SECP521R1()}
        crv = jwk.get("crv", "P-256")
        curve = _CURVES.get(crv)
        if curve is None:
            raise OidcVerificationError(f"Unsupported EC curve: {crv!r}")

        def _b64_int(s: str) -> int:
            padding = 4 - len(s) % 4
            data = base64.urlsafe_b64decode(s + "=" * (padding % 4))
            return int.from_bytes(data, "big")

        x = _b64_int(jwk["x"])
        y = _b64_int(jwk["y"])
        return EllipticCurvePublicNumbers(x, y, curve).public_key()

    # ── JWT decode ────────────────────────────────────────────────────

    def verify(self, token: str) -> dict[str, Any]:
        """Verify *token* and return its claims dict.

        Raises :class:`OidcVerificationError` on any failure.  Never
        returns partial claims — caller only receives the dict on success.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise OidcVerificationError("Malformed JWT: expected 3 parts")
        header_b64, payload_b64, sig_b64 = parts
        try:
            header = json.loads(_b64_decode_url_safe(header_b64))
            payload = json.loads(_b64_decode_url_safe(payload_b64))
        except Exception as exc:
            raise OidcVerificationError(f"Failed to decode JWT parts: {exc}") from exc

        alg = header.get("alg", "RS256")
        kid = header.get("kid")
        if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512"):
            raise OidcVerificationError(f"Unsupported JWT algorithm: {alg!r}")

        public_key = self._find_key(kid, alg)
        message = f"{header_b64}.{payload_b64}".encode()
        signature = _b64_decode_url_safe(sig_b64)
        self._verify_signature(public_key, alg, message, signature)

        # Claims validation
        now = time.time()
        if payload.get("exp", 0) < now:
            raise OidcVerificationError("JWT has expired")
        nbf = payload.get("nbf")
        if nbf is not None and nbf > now + 30:
            raise OidcVerificationError("JWT not yet valid (nbf)")
        iss = payload.get("iss", "")
        if iss.rstrip("/") != self._issuer:
            raise OidcVerificationError(
                f"JWT issuer mismatch: expected {self._issuer!r}, got {iss!r}"
            )
        aud = payload.get("aud", [])
        if isinstance(aud, str):
            aud = [aud]
        if self._audience not in aud:
            raise OidcVerificationError(f"JWT audience mismatch: {self._audience!r} not in {aud!r}")
        return payload

    @staticmethod
    def _verify_signature(public_key: Any, alg: str, message: bytes, sig: bytes) -> None:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

        _HASH_MAP = {
            "RS256": hashes.SHA256(),
            "RS384": hashes.SHA384(),
            "RS512": hashes.SHA512(),
            "ES256": hashes.SHA256(),
            "ES384": hashes.SHA384(),
            "ES512": hashes.SHA512(),
        }
        hash_alg = _HASH_MAP[alg]
        try:
            if isinstance(public_key, RSAPublicKey):
                public_key.verify(sig, message, asym_padding.PKCS1v15(), hash_alg)
            else:
                public_key.verify(sig, message, ECDSA(hash_alg))
        except InvalidSignature as exc:
            raise OidcVerificationError("JWT signature verification failed") from exc


def _b64_decode_url_safe(s: str) -> bytes:
    import base64

    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))
