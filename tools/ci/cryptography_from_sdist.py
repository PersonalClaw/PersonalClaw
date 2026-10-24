"""cryptography for the Intel Mac app, built from the sdist ``uv.lock`` pins and linked statically.

cryptography ships no Intel-Mac wheel past 48.0.1, so the release's x64 ``desktop-mac`` leg builds
the locked release itself, explicitly rather than by ``uv sync`` falling back to the sdist:

- ``build`` reads cryptography's sdist from ``uv.lock`` (its URL, sha256 and size), downloads it
  and refuses it unless both match, then builds one wheel from it with ``uv build``. It refuses to
  build unless ``OPENSSL_STATIC=1`` and ``OPENSSL_DIR`` names an OpenSSL no older than the floor
  ``tools/ci/supply-chain.toml`` sets (``[cryptography_from_source] openssl_minimum``), so the
  wheel carries that OpenSSL inside it and needs no dylib an Intel Mac may not have. It writes
  a record: the package and version, the sdist's and the wheel's sha256, ``rustc --version``,
  and the OpenSSL it was built against (``OPENSSL_VERSION_TEXT`` from its ``opensslv.h``).
- ``installed`` reads the cryptography a Python has installed, and refuses it unless it is the
  record's version, its Rust extension is byte for byte the one in the wheel built, and the
  OpenSSL it reports is the one it was built against. It adds that to the record.

The record goes into the job's build record (``tools/ci/build_identity.py record
--built-from-source``), and from there into the release manifest. Both verbs exit 2 on anything
they cannot rely on, naming it. Standard library only, with no relative imports: a job runs it
as ``python3 -I tools/ci/cryptography_from_sdist.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

PACKAGE = "cryptography"
#: The supply-chain policy, which names the oldest OpenSSL the build may link.
POLICY = Path(__file__).with_name("supply-chain.toml")
#: The Rust extension a cryptography wheel carries, where OpenSSL is linked.
RUST_EXTENSION = re.compile(r"^cryptography/hazmat/bindings/_rust\.[^/]*\.so$")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_VERSION_TEXT = re.compile(r'#\s*define\s+OPENSSL_VERSION_TEXT\s+"([^"]+)"')
_OPENSSL_MAJOR = re.compile(r"^OpenSSL (\d+)\.")
_OPENSSL_RELEASE = re.compile(r"^OpenSSL (\d+)\.(\d+)\.(\d+)\b")
_RELEASE = re.compile(r"\d+\.\d+\.\d+")


class Refused(ValueError):
    """Something this cannot rely on, named."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def locked_sdist(lock_text: str, package: str = PACKAGE) -> dict[str, Any]:
    """The one locked release of *package* and its sdist: ``{version, url, hash, size}``."""
    lock = tomllib.loads(lock_text)
    entries = [p for p in lock.get("package", []) if p.get("name") == package]
    if len(entries) != 1:
        raise Refused(f"uv.lock holds {len(entries)} releases of {package}, not one")
    entry = entries[0]
    sdist = entry.get("sdist") or {}
    if not isinstance(sdist.get("url"), str) or not _SHA256.fullmatch(str(sdist.get("hash"))):
        raise Refused(f"uv.lock pins no sdist with a sha256 for {package} {entry.get('version')}")
    if type(sdist.get("size")) is not int:
        raise Refused(f"uv.lock pins no size for {package}'s sdist")
    return {"version": str(entry["version"]), **{k: sdist[k] for k in ("url", "hash", "size")}}


def openssl_text(openssl_dir: Path) -> str:
    """``OPENSSL_VERSION_TEXT`` from the OpenSSL at *openssl_dir*, which must be 3 or newer."""
    header = openssl_dir / "include" / "openssl" / "opensslv.h"
    try:
        found = _VERSION_TEXT.search(header.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refused(f"OPENSSL_DIR {openssl_dir} has no readable {header.name}: {exc}") from None
    if not found:
        raise Refused(f"{header} defines no OPENSSL_VERSION_TEXT")
    major = _OPENSSL_MAJOR.match(found.group(1))
    if not major or int(major.group(1)) < 3:
        raise Refused(f"OPENSSL_DIR {openssl_dir} is {found.group(1)!r}, not OpenSSL 3 or newer")
    return found.group(1)


def openssl_floor(policy_text: str) -> tuple[int, int, int]:
    """The oldest OpenSSL the build may link, as ``supply-chain.toml`` names it."""
    table = tomllib.loads(policy_text).get("cryptography_from_source")
    floor = table.get("openssl_minimum") if isinstance(table, dict) else None
    if not isinstance(floor, str) or not _RELEASE.fullmatch(floor):
        raise Refused(
            f"{POLICY.name} names no openssl_minimum X.Y.Z under [cryptography_from_source]"
        )
    major, minor, patch = (int(part) for part in floor.split("."))
    return major, minor, patch


def _rust_extension(wheel: zipfile.ZipFile) -> bytes:
    names = [n for n in wheel.namelist() if RUST_EXTENSION.match(n)]
    if len(names) != 1:
        raise Refused(f"the wheel holds {len(names)} Rust extensions, not one: {names}")
    return wheel.read(names[0])


def build(
    *,
    lock_text: str,
    policy_text: str,
    out: Path,
    environ: Mapping[str, str],
    fetch: Callable[[str], bytes],
    run: Callable[[Sequence[str]], str],
    python: str,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Build the locked cryptography from its sdist into *out*, and return its record."""
    if environ.get("OPENSSL_STATIC") != "1":
        raise Refused("OPENSSL_STATIC is not 1: the wheel would link an OpenSSL dylib")
    if not environ.get("OPENSSL_DIR"):
        raise Refused("no OPENSSL_DIR: name the OpenSSL to link statically")
    openssl = openssl_text(Path(environ["OPENSSL_DIR"]))
    floor = openssl_floor(policy_text)
    release = _OPENSSL_RELEASE.match(openssl)
    if not release or tuple(int(part) for part in release.groups()) < floor:
        raise Refused(
            f"OPENSSL_DIR {environ['OPENSSL_DIR']} is {openssl!r}, older than the "
            f"{'.'.join(map(str, floor))} {POLICY.name} requires"
        )
    locked = locked_sdist(lock_text)
    name = f"{PACKAGE} {locked['version']}"
    data = fetch(locked["url"])
    if len(data) != locked["size"] or _sha256(data) != locked["hash"]:
        raise Refused(
            f"{name}'s sdist from {locked['url']} is {len(data)} bytes, {_sha256(data)}: "
            f"uv.lock pins {locked['size']} bytes, {locked['hash']}"
        )
    out.mkdir(parents=True, exist_ok=True)
    sdist = out / locked["url"].rsplit("/", 1)[1]
    sdist.write_bytes(data)
    rust = run(["rustc", "--version"]).strip()
    log(f"building {name} from its locked sdist ({locked['hash']}), against {openssl}, with {rust}")
    run(["uv", "build", "--wheel", "--python", python, "--out-dir", str(out), str(sdist)])
    wheels = sorted(out.glob(f"{PACKAGE}-{locked['version']}-*.whl"))
    if len(wheels) != 1:
        raise Refused(f"uv build left {len(wheels)} {name} wheels in {out}, not one")
    with zipfile.ZipFile(wheels[0]) as wheel:
        _rust_extension(wheel)
    log(f"built {wheels[0].name}")
    return {
        "package": PACKAGE,
        "version": locked["version"],
        "sdist": locked["hash"],
        "wheel": wheels[0].name,
        "wheel_sha256": _sha256(wheels[0].read_bytes()),
        "rust": rust,
        "openssl": openssl,
    }


#: What ``installed`` asks the Python that installed the wheel: the version, the path of the Rust
#: extension, and the OpenSSL it reports.
_PROBE = (
    "import json, cryptography, cryptography.hazmat.bindings._rust as r; "
    "from cryptography.hazmat.backends.openssl.backend import backend as b; "
    "print(json.dumps({'version': cryptography.__version__, 'rust': r.__file__, "
    "'openssl': b.openssl_version_text()}))"
)


def installed(
    record: Mapping[str, Any], *, wheel: Path, run: Callable[[Sequence[str]], str], python: str
) -> dict[str, Any]:
    """*record*, borne out by the cryptography *python* has installed."""
    try:
        found = json.loads(run([python, "-I", "-c", _PROBE]))
    except ValueError as exc:
        raise Refused(f"{python} did not report its cryptography: {exc}") from None
    name = f"{PACKAGE} {record['version']}"
    if found.get("version") != record["version"]:
        raise Refused(f"{python} has {PACKAGE} {found.get('version')} installed, not {name}")
    with zipfile.ZipFile(wheel) as built:
        expected = _rust_extension(built)
    if Path(found["rust"]).read_bytes() != expected:
        raise Refused(f"the installed {found['rust']} is not the one in {wheel.name}")
    if found.get("openssl") != record["openssl"]:
        raise Refused(
            f"the installed {name} reports {found.get('openssl')!r}, not the "
            f"{record['openssl']!r} it was built against"
        )
    return {**record, "installed_openssl": found["openssl"]}


# ── the command ────────────────────────────────────────────────────────────────────────────────


def _fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise Refused(f"the sdist's URL is not https: {url}")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
        return response.read()


def _run(argv: Sequence[str]) -> str:
    done = subprocess.run(list(argv), capture_output=True, text=True)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-20:]
        raise Refused(f"{' '.join(argv[:3])} exited {done.returncode}: " + "\n".join(tail))
    return done.stdout


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refused(message)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = _Parser(prog="cryptography_from_sdist.py", add_help=False)
    parser.add_argument("verb", choices=("build", "installed"))
    parser.add_argument("--python", required=True)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--out", type=Path, default=Path("wheelhouse"))
    parser.add_argument("--policy", type=Path, default=POLICY)
    try:
        args = parser.parse_args(argv)
        if args.verb == "build":
            record = build(
                lock_text=args.lock.read_text(encoding="utf-8"),
                policy_text=args.policy.read_text(encoding="utf-8"),
                out=args.out,
                environ=os.environ,
                fetch=_fetch,
                run=_run,
                python=args.python,
            )
        else:
            record = json.loads(args.record.read_text(encoding="utf-8"))
            record = installed(
                record, wheel=args.out / record["wheel"], run=_run, python=args.python
            )
            print(f"installed {PACKAGE} {record['version']}, carrying {record['openssl']}")
        args.record.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return 0
    except (Refused, OSError, KeyError) as exc:
        print(f"cryptography_from_sdist.py: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
