"""Tests for _ssl_compat SSL certificate bootstrap."""

import contextlib
import os
from unittest.mock import patch

import pytest

from personalclaw._ssl_compat import _CA_CANDIDATES, _ensure_ssl_certs

_CA_ENV_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


@contextlib.contextmanager
def _preserved_ca_env():
    """Restore the CA env vars on exit, whether or not they started set.

    `monkeypatch.delenv(name, raising=False)` records NO undo entry when the key is
    already absent, so a value the code under test writes with a direct
    `os.environ[name] = ...` survives teardown. These tests assert that
    `_ensure_ssl_certs()` DID write the env, so every one of them leaks a bundle
    path holding the literal text "fake cert bundle" into the rest of the worker —
    and the next test to build an httpx client dies with
    `X509: NO_CERTIFICATE_OR_CRL_FOUND` (issue 3341). Snapshot-and-restore does not
    depend on the starting state, so it closes the case `delenv` cannot.
    """
    saved = {key: os.environ.get(key) for key in _CA_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestEnsureSslCerts:
    """Tests for _ensure_ssl_certs()."""

    @pytest.fixture(autouse=True)
    def _no_ca_env_leak(self):
        with _preserved_ca_env():
            yield

    def test_noop_when_ssl_cert_file_already_set(self, monkeypatch):
        """Should return immediately if SSL_CERT_FILE is already set."""
        monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        _ensure_ssl_certs()

        assert os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"
        assert os.environ.get("REQUESTS_CA_BUNDLE") is None

    def test_noop_when_default_cafile_exists(self, monkeypatch, tmp_path):
        """Should return if ssl.get_default_verify_paths().cafile exists."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        ca_file = tmp_path / "system-ca.pem"
        ca_file.write_text("fake cert bundle")

        mock_paths = type("P", (), {"cafile": str(ca_file), "capath": None})()
        with patch("ssl.get_default_verify_paths", return_value=mock_paths):
            _ensure_ssl_certs()

        assert os.environ.get("SSL_CERT_FILE") is None

    def test_sets_env_from_first_existing_candidate(self, monkeypatch, tmp_path):
        """Should set SSL_CERT_FILE and REQUESTS_CA_BUNDLE from the first candidate found."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        # Simulate: cafile is None (no default bundle)
        mock_paths = type("P", (), {"cafile": None, "capath": None})()

        # Make the second candidate exist
        fake_bundle = tmp_path / "ca-bundle.crt"
        fake_bundle.write_text("fake cert bundle")

        candidates = (
            "/nonexistent/cert.pem",
            str(fake_bundle),
            "/also/nonexistent.crt",
        )

        with (
            patch("ssl.get_default_verify_paths", return_value=mock_paths),
            patch("personalclaw._ssl_compat._CA_CANDIDATES", candidates),
        ):
            _ensure_ssl_certs()

        assert os.environ["SSL_CERT_FILE"] == str(fake_bundle)
        assert os.environ["REQUESTS_CA_BUNDLE"] == str(fake_bundle)

    def test_does_not_overwrite_existing_requests_ca_bundle(self, monkeypatch, tmp_path):
        """REQUESTS_CA_BUNDLE should not be overwritten if already set."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/existing/bundle.crt")

        mock_paths = type("P", (), {"cafile": None, "capath": None})()

        fake_bundle = tmp_path / "cert.pem"
        fake_bundle.write_text("fake cert bundle")
        candidates = (str(fake_bundle),)

        with (
            patch("ssl.get_default_verify_paths", return_value=mock_paths),
            patch("personalclaw._ssl_compat._CA_CANDIDATES", candidates),
        ):
            _ensure_ssl_certs()

        assert os.environ["SSL_CERT_FILE"] == str(fake_bundle)
        assert os.environ["REQUESTS_CA_BUNDLE"] == "/existing/bundle.crt"

    def test_no_env_set_when_no_candidate_exists(self, monkeypatch):
        """Should leave env vars unset if no candidate file exists."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        mock_paths = type("P", (), {"cafile": None, "capath": None})()
        candidates = ("/nonexistent/a.pem", "/nonexistent/b.crt")

        with (
            patch("ssl.get_default_verify_paths", return_value=mock_paths),
            patch("personalclaw._ssl_compat._CA_CANDIDATES", candidates),
        ):
            _ensure_ssl_certs()

        assert os.environ.get("SSL_CERT_FILE") is None
        assert os.environ.get("REQUESTS_CA_BUNDLE") is None

    def test_cafile_missing_on_disk_falls_through(self, monkeypatch, tmp_path):
        """If cafile is set but the file doesn't exist, should fall through to candidates."""
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        # cafile points to a nonexistent path
        mock_paths = type("P", (), {"cafile": "/ghost/cert.pem", "capath": None})()

        fake_bundle = tmp_path / "ca-bundle.crt"
        fake_bundle.write_text("fake cert bundle")
        candidates = (str(fake_bundle),)

        with (
            patch("ssl.get_default_verify_paths", return_value=mock_paths),
            patch("personalclaw._ssl_compat._CA_CANDIDATES", candidates),
        ):
            _ensure_ssl_certs()

        assert os.environ["SSL_CERT_FILE"] == str(fake_bundle)

    def test_candidates_match_expected_paths(self):
        """Verify the candidate list covers common Linux cert paths."""
        assert "/etc/pki/tls/cert.pem" in _CA_CANDIDATES
        assert "/etc/pki/tls/certs/ca-bundle.crt" in _CA_CANDIDATES
        assert "/etc/ssl/certs/ca-certificates.crt" in _CA_CANDIDATES

    def test_cli_invokes_ensure_ssl_certs(self):
        """Reloading cli.py must trigger _ensure_ssl_certs()."""
        import importlib
        from unittest.mock import MagicMock

        mock_fn = MagicMock()
        with patch("personalclaw._ssl_compat._ensure_ssl_certs", mock_fn):
            import personalclaw.cli

            importlib.reload(personalclaw.cli)
        mock_fn.assert_called()


class TestPreservedCaEnv:
    """The guard that keeps this file from poisoning the rest of its xdist worker.

    Asserted on the helper directly rather than as an ordered pair of tests, so the
    coverage holds under any xdist distribution rather than only when both halves
    land on the same worker.
    """

    def test_restores_a_key_that_started_unset(self, monkeypatch):
        """The leaking case: a direct write over an absent key must not survive."""
        for key in _CA_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

        with _preserved_ca_env():
            for key in _CA_ENV_KEYS:
                os.environ[key] = "/tmp/fake-bundle.crt"

        for key in _CA_ENV_KEYS:
            assert key not in os.environ

    def test_restores_a_key_that_started_set(self, monkeypatch):
        """The ambient-environment case: the original value comes back, not the write."""
        for key in _CA_ENV_KEYS:
            monkeypatch.setenv(key, f"/original/{key}.pem")

        with _preserved_ca_env():
            for key in _CA_ENV_KEYS:
                os.environ[key] = "/tmp/fake-bundle.crt"

        for key in _CA_ENV_KEYS:
            assert os.environ[key] == f"/original/{key}.pem"

    def test_restores_even_when_the_body_raises(self, monkeypatch):
        """A failing assertion inside a test must not skip the restore."""
        for key in _CA_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(RuntimeError):
            with _preserved_ca_env():
                os.environ["SSL_CERT_FILE"] = "/tmp/fake-bundle.crt"
                raise RuntimeError("boom")

        assert "SSL_CERT_FILE" not in os.environ
