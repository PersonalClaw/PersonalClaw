"""Every release ships two Mac dmgs, each one arch, each built and run on a runner of that arch.

The shell bundles a PyInstaller backend, which cannot be cross-compiled: its Python and every
native module in it are the build host's. So ``desktop-mac`` in ``release-artifacts.yml`` is one
leg per arch, Apple silicon on ``macos-26`` and Intel on ``macos-26-intel``, and each leg:

- builds the shell for its host's arch (``make desktop-dist`` names it to electron-builder);
- names its dmg for that arch (``desktop/package.json``'s ``dmg.artifactName``: electron-builder's
  default drops ``x64`` from the name);
- holds the dmg's shell binary and bundled backend to that one arch with ``lipo -archs``;
- runs both from the mounted dmg: the backend smoke, then the shell's own smoke on its first run,
  and nothing may still run from the mount once the app has quit;
- uploads the dmg and its build record under its arch's name.

cryptography ships no Intel-Mac wheel past 48.0.1, so the Intel leg builds the locked release from
its sdist with ``tools/ci/cryptography_from_sdist.py``, the runner's OpenSSL 3 linked statically,
checks the installed library is the one built, and names it in its build record. Both legs check
with ``tools/ci/macho_links.py`` that every Mach-O in the backend links only the system's
libraries and the bundle's own, that cryptography links no OpenSSL dylib, and that
``doctor --crypto`` works from the dmg with no library path of the runner's; a planted
Homebrew-linked library must fail the link check.

The manifest job stages every leg's dmg and checks one is there for each arch, and the GitHub
Release's download takes every leg's.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Callable

import yaml
from rails_repo import REPO

ARTIFACTS = REPO / ".github" / "workflows" / "release-artifacts.yml"
RELEASE = REPO / ".github" / "workflows" / "release.yml"
PACKAGE = REPO / "desktop" / "package.json"
MAKEFILE = REPO / "Makefile"

#: One leg per arch: electron-builder's name for it, the runner of that arch, and what
#: ``lipo -archs`` prints for a binary built for it alone.
LEGS = [
    {"arch": "arm64", "runner": "macos-26", "lipo": "arm64"},
    {"arch": "x64", "runner": "macos-26-intel", "lipo": "x86_64"},
]
ARTIFACT_NAME = "${productName}-${version}-${arch}.${ext}"
APP_BINARY = '"$APP/Contents/MacOS/PersonalClaw"'


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


def _uploads(job: dict) -> list[dict]:
    return [
        s.get("with") or {}
        for s in _steps(job)
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]


def mac_problems(artifacts: str, release: str, package: str, makefile: str) -> list[str]:
    jobs = yaml.safe_load(artifacts)["jobs"]
    job = jobs["desktop-mac"]
    out: list[str] = []
    legs = ((job.get("strategy") or {}).get("matrix") or {}).get("include")
    if legs != LEGS:
        out.append(f"desktop-mac builds {legs!r}, not one dmg per arch on a runner of that arch")
    if job.get("runs-on") != "${{ matrix.runner }}":
        out.append(f"desktop-mac runs on {job.get('runs-on')!r}, not each leg's own runner")
    arches = [leg["arch"] for leg in LEGS]

    # The shell is built for the host's arch, and the dmg is named for it.
    recipe = re.search(r"^desktop-dist:.*?(?=^\S|\Z)", makefile, re.M | re.S)
    if not recipe or "npm run dist -- --$(DESKTOP_MAC_ARCH)" not in recipe.group(0):
        out.append("make desktop-dist does not name the shell's arch to electron-builder")
    arch = re.search(r"^DESKTOP_MAC_ARCH\s*:?=\s*(.+)$", makefile, re.M)
    if not arch or "uname -m" not in arch.group(1):
        out.append("DESKTOP_MAC_ARCH is not the host's arch")
    named = ((json.loads(package).get("build") or {}).get("dmg") or {}).get("artifactName")
    if named != ARTIFACT_NAME:
        out.append(f"the dmg is named {named!r}, not {ARTIFACT_NAME!r}: x64 would lose its arch")

    # The smoke holds the dmg, the shell and the backend to the leg's arch, and runs both.
    smoke = next((s for s in _steps(job) if "hdiutil attach" in str(s.get("run"))), {})
    run, env = str(smoke.get("run", "")), smoke.get("env") or {}
    if env.get("ARCH") != "${{ matrix.arch }}" or env.get("LIPO") != "${{ matrix.lipo }}":
        out.append(f"the smoke is not given its leg's arch: {env!r}")
    if '[[ "${DMGS[0]}" != *-"$ARCH".dmg ]]' not in run or '"${#DMGS[@]}" -ne 1' not in run:
        out.append("the smoke does not hold desktop/dist to one dmg named for its arch")
    loop = re.search(r"for bin in (.+?); do\n(.*?)\n\s*done", run, re.S)
    checked = loop.group(1).split() if loop else []
    if APP_BINARY not in checked or '"$BACKEND"' not in checked:
        out.append(f"lipo does not check both the shell and the backend: {checked}")
    if not loop or 'got=$(lipo -archs "$bin")' not in loop.group(2):
        out.append("the smoke reads no binary's arch with lipo -archs")
    elif '[ "$got" != "$LIPO" ]' not in loop.group(2):
        out.append("the smoke does not hold each binary to exactly its leg's arch")
    shell = f'desktop/test/smoke/shellSmoke.js" {APP_BINARY} --first-run'
    if shell not in run:
        out.append("the smoke does not start the shell from the dmg")
    elif 'pgrep -fl "$MNT/"' not in run.split(shell, 1)[1]:
        out.append("nothing checks the app left no process running from the dmg")

    # The Intel leg builds cryptography from its locked sdist, statically, and records it.
    steps = _steps(job)

    def step_running(text: str) -> dict:
        return next((s for s in steps if text in str(s.get("run"))), {})

    sync = step_running("uv sync --locked")
    skip = "${{ matrix.arch == 'x64' && 'cryptography' || '' }}"
    if (sync.get("env") or {}).get("FROM_SOURCE") != skip or (
        '${FROM_SOURCE:+--no-install-package "$FROM_SOURCE"}' not in str(sync.get("run"))
    ):
        out.append("the Intel leg installs cryptography from uv.lock, which has no Intel-Mac wheel")
    source = step_running("cryptography_from_sdist.py build")
    built = str(source.get("run", ""))
    if source.get("if") != "matrix.arch == 'x64'":
        out.append("cryptography is not built from its sdist on the Intel leg alone")
    if 'export OPENSSL_STATIC=1 OPENSSL_DIR="$(brew --prefix openssl@3)"' not in built:
        out.append("cryptography is not linked statically to the runner's OpenSSL 3")
    if "cryptography_from_sdist.py installed" not in built.partition("uv pip install")[2]:
        out.append("nothing checks the installed cryptography is the one built")
    if 'macho_links.py" "$BUNDLE" --executable "$BACKEND"' not in run:
        out.append("the smoke does not check every Mach-O in the backend links only the system")
    if "grep -E 'lib(ssl|crypto)[.0-9]*\\.dylib'" not in run:
        out.append("nothing checks cryptography carries its OpenSSL inside it")
    unset = "env -u DYLD_LIBRARY_PATH -u DYLD_FALLBACK_LIBRARY_PATH -u DYLD_INSERT_LIBRARIES"
    if f'{unset} "$BACKEND" doctor --crypto' not in run:
        out.append(
            "cryptography is not exercised from the dmg with no library path of the runner's"
        )
    elif "built-from-source/cryptography.json" not in run.partition("doctor --crypto")[2]:
        out.append("the Intel smoke does not hold cryptography to the OpenSSL it was built against")
    plant = str(step_running('macho_links.py "$PLANT"').get("run", ""))
    if '"$(brew --prefix openssl@3)/lib/libssl.3.dylib"' not in plant or (
        "grep -q 'libcrypto.3.dylib, which is outside it'" not in plant
    ):
        out.append("the link check has no planted Homebrew-linked library it must refuse")
    record = step_running("build_identity.py record")
    named = "${{ matrix.arch == 'x64' && 'built-from-source/cryptography.json' || '' }}"
    if (record.get("env") or {}).get("FROM_SOURCE") != named or (
        '${FROM_SOURCE:+--built-from-source "$FROM_SOURCE"}' not in str(record.get("run"))
    ):
        out.append("the Intel build record does not name the cryptography it built from source")

    # Each leg's dmg and record go up under its arch's name, and every one is staged.
    names = [str(u.get("name")) for u in _uploads(job)]
    dmg = next((u for u in _uploads(job) if u.get("path") == "desktop/dist/*.dmg"), {})
    if dmg.get("name") != "desktop-mac-${{ matrix.arch }}":
        out.append(f"the dmg goes up as {dmg.get('name')!r}, not under its arch's name")
    if dmg.get("if-no-files-found") != "error":
        out.append("a leg that built no dmg would upload nothing and pass")
    if "build-record-desktop-mac-${{ matrix.arch }}" not in names:
        out.append(f"the build record goes up as {names}, not under its arch's name")
    runs = " ".join(str(s.get("run", "")) for s in _steps(job))
    if "--name desktop-mac-${{ matrix.arch }}" not in runs:
        out.append("the build record is not named for its arch")
    stage = " ".join(str(s.get("run", "")) for s in _steps(jobs["manifest"]))
    if '"$A"/desktop-mac-*/*.dmg' not in stage:
        out.append("the manifest does not stage every desktop-mac leg's dmg")
    listed = re.search(
        r'for arch in ([a-z0-9 ]+); do\s+ls staged/PersonalClaw-\*-"\$arch"\.dmg', stage
    )
    if not listed or listed.group(1).split() != arches:
        out.append(f"the manifest does not check a dmg is staged for each of {arches}")
    notes = yaml.safe_load(release)["jobs"]["notes"]
    patterns = [
        str((s.get("with") or {}).get("pattern"))
        for s in _steps(notes)
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    ]
    for leg in arches:
        if not any(fnmatch.fnmatch(f"desktop-mac-{leg}", p) for p in patterns):
            out.append(f"the GitHub Release does not download the {leg} dmg")
    return out


def _texts() -> tuple[str, str, str, str]:
    return tuple(  # type: ignore[return-value]
        path.read_text(encoding="utf-8") for path in (ARTIFACTS, RELEASE, PACKAGE, MAKEFILE)
    )


def test_each_mac_dmg_is_built_named_and_run_as_its_own_arch() -> None:
    assert mac_problems(*_texts()) == []


#: Which of _texts() a plant edits.
WORKFLOW, NOTES, PACKAGE_JSON, MAKE = range(4)


def _in(index: int, old: str, new: str) -> Callable[[list[str]], None]:
    def plant(texts: list[str]) -> None:
        assert texts[index].count(old) == 1, old
        texts[index] = texts[index].replace(old, new)

    return plant


PLANTS = [
    (
        _in(WORKFLOW, "          - { arch: x64, runner: macos-26-intel, lipo: x86_64 }\n", ""),
        "not one dmg per arch",
    ),
    (
        _in(
            WORKFLOW,
            "- { arch: x64, runner: macos-26-intel, lipo: x86_64 }",
            "- { arch: x64, runner: macos-26, lipo: x86_64 }",
        ),
        "not one dmg per arch",
    ),
    (_in(WORKFLOW, "runs-on: ${{ matrix.runner }}", "runs-on: macos-26"), "each leg's own runner"),
    (
        _in(MAKE, "npm run dist -- --$(DESKTOP_MAC_ARCH)", "npm run dist"),
        "does not name the shell's arch",
    ),
    (
        _in(MAKE, "$(if $(filter arm64,$(shell uname -m)),arm64,x64)", "arm64"),
        "not the host's arch",
    ),
    (
        _in(
            PACKAGE_JSON, '      "artifactName": "${productName}-${version}-${arch}.${ext}",\n', ""
        ),
        "lose its arch",
    ),
    (_in(WORKFLOW, "          LIPO: ${{ matrix.lipo }}\n", ""), "not given its leg's arch"),
    (
        _in(WORKFLOW, ' || [[ "${DMGS[0]}" != *-"$ARCH".dmg ]]', ""),
        "one dmg named for its arch",
    ),
    (
        _in(WORKFLOW, f'for bin in {APP_BINARY} "$BACKEND"; do', f"for bin in {APP_BINARY}; do"),
        "both the shell and the backend",
    ),
    (
        _in(WORKFLOW, 'if [ "$got" != "$LIPO" ]; then', 'if [ -z "$got" ]; then'),
        "exactly its leg's arch",
    ),
    (_in(WORKFLOW, f"{APP_BINARY} --first-run", f"{APP_BINARY} --help"), "start the shell"),
    (_in(WORKFLOW, 'if pgrep -fl "$MNT/"; then', "if false; then"), "no process running"),
    (
        _in(WORKFLOW, "name: desktop-mac-${{ matrix.arch }}\n", "name: desktop-mac\n"),
        "not under its arch's name",
    ),
    (
        _in(
            WORKFLOW,
            "path: desktop/dist/*.dmg\n          if-no-files-found: error\n",
            "path: desktop/dist/*.dmg\n",
        ),
        "upload nothing and pass",
    ),
    (
        _in(
            WORKFLOW,
            "name: build-record-desktop-mac-${{ matrix.arch }}",
            "name: build-record-desktop-mac",
        ),
        "build record goes up",
    ),
    (
        _in(WORKFLOW, '"$A"/desktop-mac-*/*.dmg', '"$A"/desktop-mac-arm64/*.dmg'),
        "stage every desktop-mac leg's dmg",
    ),
    (_in(WORKFLOW, "for arch in arm64 x64; do", "for arch in arm64; do"), "for each of"),
    (_in(NOTES, "pattern: desktop-*\n", "pattern: desktop-linux\n"), "does not download the arm64"),
    (
        _in(WORKFLOW, ' ${FROM_SOURCE:+--no-install-package "$FROM_SOURCE"}', ""),
        "no Intel-Mac wheel",
    ),
    (
        _in(WORKFLOW, "        if: matrix.arch == 'x64'\n        run: |\n", "        run: |\n"),
        "on the Intel leg alone",
    ),
    (_in(WORKFLOW, "export OPENSSL_STATIC=1 OPENSSL_DIR=", "export OPENSSL_DIR="), "statically"),
    (
        _in(
            WORKFLOW,
            "python3 -I tools/ci/cryptography_from_sdist.py installed",
            "echo tools/ci/cryptography_from_sdist.py",
        ),
        "is the one built",
    ),
    (
        _in(
            WORKFLOW,
            '          python3 -I "$GITHUB_WORKSPACE/tools/ci/macho_links.py"',
            "          :",
        ),
        "links only the system",
    ),
    (
        _in(WORKFLOW, "grep -E 'lib(ssl|crypto)[.0-9]*\\.dylib'", "grep -E 'libnothing'"),
        "inside it",
    ),
    (
        _in(WORKFLOW, ' -u DYLD_INSERT_LIBRARIES "$BACKEND" doctor', ' "$BACKEND" doctor'),
        "no library path",
    ),
    (
        _in(
            WORKFLOW,
            '"$GITHUB_WORKSPACE/built-from-source/cryptography.json")',
            '"$GITHUB_WORKSPACE/elsewhere.json")',
        ),
        "OpenSSL it was built against",
    ),
    (
        _in(
            WORKFLOW, "          grep -q 'libcrypto.3.dylib, which is outside it'", "          true"
        ),
        "planted Homebrew-linked library",
    ),
    (
        _in(WORKFLOW, '          ${FROM_SOURCE:+--built-from-source "$FROM_SOURCE"}\n', ""),
        "built from source",
    ),
]


def test_a_mac_leg_that_is_not_its_own_arch_is_caught(subtests: object) -> None:
    assert len(PLANTS) == 28, "a plant was dropped"
    for plant, says in PLANTS:
        with subtests.test(says=says):  # type: ignore[attr-defined]
            texts = list(_texts())
            plant(texts)
            problems = mac_problems(*texts)
            assert any(says in p for p in problems), problems
