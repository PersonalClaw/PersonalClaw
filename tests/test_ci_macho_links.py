"""Every Mach-O in the Mac app's backend links only the system's libraries and the bundle's own.

``tools/ci/macho_links.py`` runs on each ``desktop-mac`` leg, on the backend inside the mounted
dmg. An Intel Mac may have no Homebrew, so a library linked from a build runner's
``/usr/local/opt`` or ``/opt/homebrew`` is refused, and so is an ``@rpath`` link that resolves to no
file inside the bundle. ``otool`` is stood in for here with the output it prints.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import macho_links as ml

MACHO_64 = b"\xcf\xfa\xed\xfe" + b"\x00" * 60
FAT = b"\xca\xfe\xba\xbe\x00\x00\x00\x02" + b"\x00" * 56
JAVA_CLASS = b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\x00" * 56


def _lib(path: str, *, compat: str = "1.0.0") -> str:
    return f"\t{path} (compatibility version {compat}, current version 1.0.0)\n"


class _Otool:
    """``otool -L``, ``-D`` and ``-l`` for each file, as written into the table."""

    def __init__(self, table: dict[str, dict[str, list[str]]]) -> None:
        self.table = table

    def __call__(self, flag: str, path: Path) -> str:
        entry = self.table.get(path.name, {})
        head = f"{path}:\n"
        if flag == "-L":
            return head + "".join(_lib(d) for d in entry.get("links", []))
        if flag == "-D":
            return head + "".join(f"{i}\n" for i in entry.get("id", []))
        return head + "".join(
            f"Load command {n}\n          cmd LC_RPATH\n      cmdsize 32\n"
            f"         path {r} (offset 12)\n"
            for n, r in enumerate(entry.get("rpaths", []))
        )


def _bundle(tmp_path: Path) -> Path:
    """A PyInstaller-shaped folder: the executable, `_internal/` with a dylib and an extension."""
    root = tmp_path / "personalclaw-backend"
    (root / "_internal" / "cryptography").mkdir(parents=True)
    for rel in (
        "personalclaw-backend",
        "_internal/libssl.3.dylib",
        "_internal/cryptography/_rust.so",
    ):
        (root / rel).write_bytes(MACHO_64)
    (root / "_internal" / "base_library.zip").write_bytes(b"PK\x03\x04")
    return root


GOOD = {
    "personalclaw-backend": {
        "links": ["/usr/lib/libSystem.B.dylib", "@rpath/libssl.3.dylib"],
        "rpaths": ["@loader_path/_internal"],
    },
    "libssl.3.dylib": {
        "id": ["@rpath/libssl.3.dylib"],
        "links": ["@rpath/libssl.3.dylib", "/usr/lib/libz.1.dylib"],
    },
    "_rust.so": {
        "id": ["@rpath/cryptography.hazmat.bindings._rust.abi3.so"],
        "links": [
            "@rpath/cryptography.hazmat.bindings._rust.abi3.so",
            "@loader_path/../libssl.3.dylib",
            "/System/Library/Frameworks/Security.framework/Versions/A/Security",
        ],
    },
}


def _problems(root: Path, table: dict) -> tuple[int, list[str]]:
    return ml.problems(root, root / "personalclaw-backend", _Otool(table))


def test_a_bundle_that_links_only_the_system_and_itself_passes(tmp_path: Path) -> None:
    assert _problems(_bundle(tmp_path), GOOD) == (3, [])


@pytest.mark.parametrize(
    ("file", "links", "says"),
    [
        (
            "_rust.so",
            ["/usr/local/opt/openssl@3/lib/libcrypto.3.dylib"],
            "_internal/cryptography/_rust.so links "
            "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib, which is outside it",
        ),
        (
            "libssl.3.dylib",
            ["/opt/homebrew/Cellar/openssl@3/3.6.3/lib/libcrypto.3.dylib"],
            "which is outside it",
        ),
        (
            "personalclaw-backend",
            ["@rpath/libgone.dylib"],
            "libgone.dylib, which resolves to no file",
        ),
        (
            "_rust.so",
            ["@loader_path/../../../outside.dylib"],
            "resolves to no file inside the bundle",
        ),
        ("personalclaw-backend", ["@executable_path/missing.dylib"], "resolves to no file"),
    ],
)
def test_a_link_outside_the_bundle_is_refused(
    tmp_path: Path, file: str, links: list, says: str
) -> None:
    root = _bundle(tmp_path)
    (tmp_path / "outside.dylib").write_bytes(MACHO_64)
    table = {k: dict(v) for k, v in GOOD.items()}
    table[file]["links"] = [*table[file]["links"], *links]
    count, found = _problems(root, table)
    assert count == 3 and len(found) == 1 and says in found[0], found


def test_an_rpath_link_resolves_through_the_files_own_rpaths(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    table = {k: dict(v) for k, v in GOOD.items()}
    table["personalclaw-backend"]["rpaths"] = ["@loader_path/elsewhere"]
    _, found = _problems(root, table)
    assert found == [
        "personalclaw-backend links @rpath/libssl.3.dylib, "
        "which resolves to no file inside the bundle"
    ]


def test_only_mach_o_files_are_read_and_a_fat_one_is_one() -> None:
    assert ml.is_macho(MACHO_64) and ml.is_macho(FAT)
    assert not ml.is_macho(JAVA_CLASS), "a Java class file shares a fat file's magic"
    assert not ml.is_macho(b"PK\x03\x04") and not ml.is_macho(b"#!/bin/sh\n")


def test_a_fat_files_every_architecture_is_read_and_its_own_name_skipped() -> None:
    otool_l = (
        "lib (architecture x86_64):\n"
        + _lib("@rpath/lib")
        + _lib("/usr/local/lib/x.dylib")
        + "lib (architecture arm64):\n"
        + _lib("@rpath/lib")
        + _lib("/usr/lib/libSystem.B.dylib")
    )
    own = ml.install_names(
        "lib (architecture x86_64):\n@rpath/lib\nlib (architecture arm64):\n@rpath/lib\n"
    )
    assert own == {"@rpath/lib"}
    assert ml.dependencies(otool_l, own) == ["/usr/lib/libSystem.B.dylib", "/usr/local/lib/x.dylib"]


def test_a_folder_with_no_mach_o_is_not_a_bundle_checked(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("no binaries here")
    with pytest.raises(ml.Unreadable, match="holds no Mach-O file"):
        ml.problems(tmp_path, tmp_path / "x", _Otool({}))


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("otool"), reason="needs otool")
def test_on_this_mac_a_homebrew_linked_library_is_refused(tmp_path: Path) -> None:
    brew = shutil.which("brew")
    prefix = (
        subprocess.run(
            [brew, "--prefix", "openssl@3"], capture_output=True, text=True
        ).stdout.strip()
        if brew
        else ""
    )
    libssl = Path(prefix) / "lib" / "libssl.3.dylib"
    if not libssl.is_file():
        pytest.skip("no Homebrew openssl@3 to plant")
    planted = tmp_path / "bundle"
    planted.mkdir()
    shutil.copy2(libssl, planted / "libssl.3.dylib")
    assert ml.main([str(planted), "--executable", str(planted / "libssl.3.dylib")]) == 1
