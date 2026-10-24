"""`personalclaw doctor --crypto`: the cryptography library this install carries works.

No Intel-Mac wheel of cryptography exists past 48.0.1, so the Intel Mac app builds the locked
release from its sdist, statically linked to the build runner's OpenSSL. A build like that can
package cleanly and still fail the first time it runs, so the release's smoke runs this check
from inside the mounted dmg: a Fernet round trip, an X.509 certificate made, written and parsed
back, and the versions of cryptography and of the OpenSSL it carries.
"""

from __future__ import annotations

import sys

import pytest

from personalclaw import cli, cli_doctor


def test_it_round_trips_parses_and_names_what_it_carries(capsys) -> None:
    import cryptography
    from cryptography.hazmat.backends.openssl.backend import backend

    cli_doctor._doctor_crypto()
    lines = dict(line.split("\t", 1) for line in capsys.readouterr().out.splitlines())
    assert lines == {
        "cryptography": cryptography.__version__,
        "openssl": backend.openssl_version_text(),
        "fernet": "ok",
        "x509": "ok",
    }


def test_a_round_trip_that_comes_back_wrong_fails_the_check(monkeypatch, capsys) -> None:
    from cryptography import fernet

    monkeypatch.setattr(fernet.Fernet, "decrypt", lambda self, token: b"something else")
    with pytest.raises(SystemExit) as exited:
        cli_doctor._doctor_crypto()
    assert exited.value.code == 1
    assert "fernet\tFAILED: the round trip came back changed" in capsys.readouterr().out


def test_a_library_that_cannot_load_fails_the_check(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "cryptography.fernet", None)
    with pytest.raises(SystemExit) as exited:
        cli_doctor._doctor_crypto()
    assert exited.value.code == 1
    # Nothing can be checked, so this is a refusal and it goes to stderr. The dmg smoke captures
    # stdout and stops on the exit status, so a line on stdout would never reach its log.
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.startswith("cryptography\tFAILED: ")


def test_the_flag_reaches_the_check(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli, "_doctor_crypto", lambda: seen.append("crypto"))
    monkeypatch.setattr(cli, "_doctor_paths", lambda: seen.append("paths"))
    monkeypatch.setattr(cli, "_doctor", lambda *, start_agent_clis: seen.append("doctor"))
    monkeypatch.setattr(sys, "argv", ["personalclaw", "doctor", "--crypto"])
    cli.main()
    assert seen == ["crypto"]
