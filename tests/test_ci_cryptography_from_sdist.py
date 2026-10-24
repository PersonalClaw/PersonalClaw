"""The Intel Mac app's cryptography is built from the sdist ``uv.lock`` pins, linked statically.

``tools/ci/cryptography_from_sdist.py`` downloads the locked sdist and refuses it unless its sha256
and size are the lock's, refuses to build unless OpenSSL is linked statically from an OpenSSL 3,
and then refuses an installed cryptography that is not the version, the Rust extension and the
OpenSSL it built. The downloads and the build are stood in for here: what is held is each refusal.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.ci import cryptography_from_sdist as cs

SDIST = b"the sdist's bytes"
URL = "https://files.example/cryptography-50.0.0.tar.gz"
RUST = b"a statically linked extension"
OPENSSL = "OpenSSL 3.6.3 9 Jun 2026"
POLICY = '[cryptography_from_source]\nopenssl_minimum = "3.6.3"\n'


def _lock(**sdist: object) -> str:
    pinned = {"url": URL, "hash": "sha256:" + hashlib.sha256(SDIST).hexdigest(), "size": len(SDIST)}
    pinned.update(sdist)
    fields = ", ".join(f"{k} = {json.dumps(v)}" for k, v in pinned.items())
    return (
        '[[package]]\nname = "cryptography"\nversion = "50.0.0"\n'
        f"sdist = {{ {fields} }}\n\n"
        '[[package]]\nname = "cffi"\nversion = "2.0.0"\n'
    )


def _openssl(tmp_path: Path, text: str = OPENSSL) -> Path:
    header = tmp_path / "openssl" / "include" / "openssl" / "opensslv.h"
    header.parent.mkdir(parents=True)
    header.write_text(f'# define OPENSSL_VERSION_TEXT "{text}"\n')
    return tmp_path / "openssl"


def _wheel(path: Path, rust: bytes = RUST, extensions: int = 1) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        for n in range(extensions):
            suffix = "abi3" if n == 0 else f"cp3{n}"
            wheel.writestr(f"cryptography/hazmat/bindings/_rust.{suffix}.so", rust)
        wheel.writestr("cryptography/__init__.py", b"")


class _Runner:
    def __init__(self, out: Path, wheels: int = 1) -> None:
        self.out, self.wheels, self.argv = out, wheels, []

    def __call__(self, argv: Sequence[str]) -> str:
        self.argv.append(list(argv))
        if argv[0] == "rustc":
            return "rustc 1.98.0 (abc 2026-09-01)\n"
        for n in range(self.wheels):
            _wheel(self.out / f"cryptography-50.0.0-cp31{n}-abi3-macosx_10_9_x86_64.whl")
        return ""


def _build(
    tmp_path: Path,
    *,
    lock: str | None = None,
    environ: dict | None = None,
    policy: str = POLICY,
    **kw,
):
    out = tmp_path / "wheelhouse"
    runner = kw.pop("runner", None) or _Runner(out)
    env = {"OPENSSL_STATIC": "1", "OPENSSL_DIR": str(_openssl(tmp_path))}
    record = cs.build(
        lock_text=_lock() if lock is None else lock,
        policy_text=policy,
        out=out,
        environ=env if environ is None else environ,
        fetch=kw.pop("fetch", lambda url: SDIST),
        run=runner,
        python=".venv/bin/python",
        log=lambda line: None,
    )
    return record, runner, out


def test_it_builds_one_wheel_from_the_locked_sdist_and_records_how(tmp_path: Path) -> None:
    record, runner, out = _build(tmp_path)
    wheel = out / record["wheel"]
    assert record == {
        "package": "cryptography",
        "version": "50.0.0",
        "sdist": "sha256:" + hashlib.sha256(SDIST).hexdigest(),
        "wheel": "cryptography-50.0.0-cp310-abi3-macosx_10_9_x86_64.whl",
        "wheel_sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "rust": "rustc 1.98.0 (abc 2026-09-01)",
        "openssl": OPENSSL,
    }
    build = runner.argv[-1]
    assert build[:3] == ["uv", "build", "--wheel"] and build[-1] == str(
        out / "cryptography-50.0.0.tar.gz"
    )
    assert (out / "cryptography-50.0.0.tar.gz").read_bytes() == SDIST


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"fetch": lambda url: SDIST + b"x"}, "uv.lock pins"),
        ({"lock": _lock(hash="sha256:" + "0" * 64)}, "uv.lock pins"),
        ({"lock": _lock(size=1)}, "uv.lock pins"),
        ({"lock": _lock(hash="md5:abc")}, "pins no sdist with a sha256"),
        ({"lock": _lock().replace('name = "cryptography"', 'name = "x"')}, "0 releases"),
        ({"environ": {"OPENSSL_DIR": "/x"}}, "OPENSSL_STATIC is not 1"),
        ({"environ": {"OPENSSL_STATIC": "1"}}, "no OPENSSL_DIR"),
        ({"environ": {"OPENSSL_STATIC": "1", "OPENSSL_DIR": "/nowhere"}}, "no readable"),
    ],
)
def test_a_build_it_cannot_rely_on_is_refused(tmp_path: Path, change: dict, says: str) -> None:
    with pytest.raises(cs.Refused, match=says):
        _build(tmp_path, **change)


def test_an_openssl_below_the_supply_chain_floor_is_refused(tmp_path: Path) -> None:
    with pytest.raises(cs.Refused, match="older than the 3.6.4 supply-chain.toml requires"):
        _build(tmp_path, policy=POLICY.replace("3.6.3", "3.6.4"))


@pytest.mark.parametrize(
    "policy",
    ["", "[cryptography_from_source]\n", POLICY.replace('"3.6.3"', '"3.6"')],
    ids=["no table", "no floor", "a floor that is not X.Y.Z"],
)
def test_a_policy_that_names_no_floor_is_refused(tmp_path: Path, policy: str) -> None:
    with pytest.raises(cs.Refused, match="names no openssl_minimum"):
        _build(tmp_path, policy=policy)


def test_an_openssl_older_than_3_is_refused(tmp_path: Path) -> None:
    with pytest.raises(cs.Refused, match="not OpenSSL 3 or newer"):
        cs.openssl_text(_openssl(tmp_path, "OpenSSL 1.1.1w 11 Sep 2023"))


@pytest.mark.parametrize("wheels", [0, 2])
def test_a_build_that_leaves_other_than_one_wheel_is_refused(tmp_path: Path, wheels: int) -> None:
    with pytest.raises(cs.Refused, match=f"left {wheels} cryptography 50.0.0 wheels"):
        _build(tmp_path, runner=_Runner(tmp_path / "wheelhouse", wheels=wheels))


def _installed(tmp_path: Path, **found: object):
    record, _, out = _build(tmp_path)
    rust = tmp_path / "site" / "_rust.abi3.so"
    rust.parent.mkdir()
    rust.write_bytes(found.pop("bytes", RUST))
    reported = {"version": "50.0.0", "rust": str(rust), "openssl": OPENSSL, **found}
    return cs.installed(
        record,
        wheel=out / record["wheel"],
        run=lambda argv: json.dumps(reported),
        python=".venv/bin/python",
    )


def test_the_installed_library_is_the_one_built(tmp_path: Path) -> None:
    assert _installed(tmp_path)["installed_openssl"] == OPENSSL


@pytest.mark.parametrize(
    ("found", "says"),
    [
        ({"version": "48.0.1"}, "cryptography 48.0.1 installed, not cryptography 50.0.0"),
        ({"bytes": b"a wheel from somewhere else"}, "is not the one in"),
        ({"openssl": "OpenSSL 4.0.1 9 Jun 2026"}, "not the 'OpenSSL 3.6.3 9 Jun 2026' it was"),
    ],
)
def test_an_installed_library_that_is_not_the_one_built_is_refused(
    tmp_path: Path, found: dict, says: str
) -> None:
    with pytest.raises(cs.Refused, match=says):
        _installed(tmp_path, **found)


def test_the_command_exits_2_naming_what_it_refused(tmp_path: Path, capsys) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(_lock())
    argv = ["build", "--python", "py", "--record", str(tmp_path / "r.json"), "--lock", str(lock)]
    assert cs.main(argv) == 2
    assert "refused: OPENSSL_STATIC is not 1" in capsys.readouterr().err


def test_a_wheel_without_one_rust_extension_is_refused() -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as wheel:
        wheel.writestr("cryptography/__init__.py", b"")
    with zipfile.ZipFile(data) as wheel, pytest.raises(cs.Refused, match="0 Rust extensions"):
        cs._rust_extension(wheel)
