#!/usr/bin/env python3
"""The canonical distribution build, and the wheel contract verifier (plan 34 T1.5, contract C4).

Proves a built PersonalClaw wheel is a self-contained, installable, servable
artifact — the guarantee every install channel (pip/uv/pipx/container) rides on.
It asserts, against a real wheel installed into a scratch venv:

  1. the wheel carries the built SPA (``personalclaw/static/dist/index.html``);
  2. it installs into a fresh venv from the wheel alone (no source tree, no npm);
  3. ``personalclaw gateway --test-mode`` boots and emits its READY line;
  4. ``GET /api/healthz`` → 200 JSON (auth-exempt liveness);
  5. ``GET /`` → 200 HTML (the SPA shell, served from the packaged assets), and
  6. every bundled app/extension the wheel ships actually ENABLED — see
     :func:`extension_failures` for why assertion 6 exists (#2758);
  7. the INSTALLED package carries the default chat model's sign-off record — the one the
     installed bundled-chat app reads — under a permitted licence, and the wheel carries no
     model weight and is still small; see :func:`_assert_installed_bundled_model` (OU-14), and
  8. the booted install OFFERS that model's download on a fresh home (``chat_download_offer``
     on ``GET /api/onboarding``); see :func:`_assert_download_offer`, and
  9. it SERVES the licence notices Settings → Updates links, the fonts' and the bundled npm
     packages', as plain text; see :func:`_assert_notices_served`.

Everything is read from the ARTIFACT, never from this repository. Assertion 7 used to import
``personalclaw.bundled_model`` from ``src/`` and read the record from ``docs/`` — so it passed
for an image whose installed package had no record at all (2026-09-25), because the repository
always has one. Assertions 7 and 8 share ``scripts/installed_bundled_model_probe.py`` with the
container-image gate, so the wheel and the image are asked the same question the same way.

Before any of that, the wheel's CONTENTS are inspected against this checkout
(:func:`inspect_wheel`): every package file, dashboard file and bundled-app file the source
declares, nothing it does not, metadata that matches ``pyproject.toml``, and a ``RECORD``
whose digests are true. Booting a gateway proves the wheel works; it cannot prove the wheel
is complete, because a missing file only fails when something reaches for it.

Exit 0 = contract met.

Usage:
    python scripts/verify_wheel.py [--wheel dist/personalclaw-*.whl] [--keep]
    python scripts/verify_wheel.py --build [--keep]      # what `make build` runs

    --wheel PATH  inspect and verify this wheel (default: newest dist/*.whl). What
                  ``release.yml`` runs on the wheel ``uv build`` just produced.
    --build       the canonical distribution build (:func:`_canonical_distribution_build`):
                  clean every staging tree, install the root npm workspace from its
                  lockfile, build and freshness-stamp the SPA, check every asset and npm
                  package it ships has its licence notice, build and inspect the sdist
                  and the wheel, rebuild the wheel FROM the sdist and require it to be
                  byte-identical, then install and serve it.
    --keep        keep the scratch venvs/homes for debugging.

The script deliberately uses only the stdlib (+ the wheel it installs) so it can
run on a bare CI runner without extra deps.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import glob
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
import venv
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Iterable, NoReturn

_SPA_MARKER = "personalclaw/static/dist/index.html"
_READY_PREFIX = "PERSONALCLAW_READY:"
_BOOT_TIMEOUT_S = 90.0
#: 1980-01-01, the earliest timestamp a ZIP entry can carry — the reproducible-build default.
_REPRODUCIBLE_EPOCH = "315532800"
_PACKAGE = "personalclaw"
_SOURCE_PACKAGE = Path("src") / _PACKAGE

#: Log lines that mean "the wheel shipped an app it cannot load". Each one is emitted by
#: ``personalclaw/providers/registry.py``; ``tests/test_verify_wheel_contract.py`` pins every
#: marker to the source line that emits it, so rewording the log reds that rail instead of
#: silently disarming assertion 6 here.
_EXTENSION_FAILURE_MARKERS: tuple[str, ...] = (
    "Failed to enable extension",  # registry._enable_one — handler.create() raised
    "No type handler for provider type",  # registry._enable_one — unknown provider type
    "Cannot enable unknown extension",  # registry.enable — manifest names a missing entry
)

#: The POSITIVE line, and the reason the gateway is booted verbose. Without it assertion 6 can
#: only say "no bad news", which is what an extension loader that never ran also says.
_EXTENSION_SUCCESS_MARKER = "Enabled extension"

#: Where a bundled app lives inside the wheel — used only to report the two counts side by side.
_BUNDLED_APP_PREFIX = "personalclaw/apps/native/"

#: The probe the INSTALLED artifact runs to report on its own bundled-model record. Shared with
#: ``tools/docker_single_container_smoke.py``, so the wheel gate and the image gate ask one
#: question in one dialect.
_BUNDLED_MODEL_PROBE = Path(__file__).resolve().with_name("installed_bundled_model_probe.py")

#: How long the probe may take: it imports the installed package and loads one app.
_PROBE_TIMEOUT_S = 120.0


def _log(msg: str) -> None:
    print(f"[verify_wheel] {msg}", flush=True)


def _fail(msg: str) -> NoReturn:
    print(f"[verify_wheel] FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def _find_wheel(explicit: str | None) -> Path:
    """The wheel to verify, as an ABSOLUTE path.

    🔴 Absolute because the path leaves this process. pip installs it, and the bundled-model
    probe opens it with the scratch home as its working directory, which is not this one.
    ``release.yml`` passes ``--wheel "dist/*.whl"``, relative to the checkout, and a relative
    path handed across that boundary names a file that is not there. Measured: the probe died
    with ``FileNotFoundError: 'dist/personalclaw-0.2.0-py3-none-any.whl'``, and every drive of
    the gate before it had passed an absolute path. ``resolve()`` rather than ``absolute()``
    because a wheel is a file whose NAME pip parses: a ``dist/latest.whl`` symlink would be
    refused as an invalid wheel filename, and its target's real name is the one pip needs.
    """
    if explicit:
        matches = sorted(glob.glob(explicit))
        if not matches:
            _fail(f"no wheel matched {explicit!r}")
        return Path(matches[-1]).resolve()
    matches = sorted(glob.glob("dist/*.whl"))
    if not matches:
        _fail("no wheel in dist/ — run `make build` (or pass --wheel)")
    return Path(matches[-1]).resolve()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


#: Every generated tree a distribution build can consume, relative to the checkout.
_STALE_OUTPUTS = (
    "build",
    "dist",
    "src/personalclaw.egg-info",
    "src/personalclaw/static/dist",
    "web/dist",
)


def _clean_distribution_outputs(root: Path) -> None:
    """Remove every generated tree a build could re-use, so the artifacts hold only this tree.

    🪤 A setuptools build DOES NOT CLEAR ``build/``, and re-uses whatever it finds there.
    MEASURED 2026-09-07 (#2758) in a tree that ``git status`` reported clean: the wheel carried
    three ``personalclaw/apps/native/`` app directories that existed neither in git nor on disk
    — ``run-workflow-action``, ``personalclaw-schedule-tools``, ``native-workflows`` — left
    behind in ``build/lib/`` by an earlier build. Two of them named factory functions that no
    longer exist anywhere in the package, so the gateway logged two ERROR tracebacks while this
    very script printed PASS. So anything ever DELETED from ``src/personalclaw/**`` could
    reappear in a locally built wheel, and a container image, a ``pip install ./dist/*.whl`` or
    a hand-cut release would inherit it.

    ``dist/`` goes because :func:`_find_wheel` picks ``sorted(glob(...))[-1]``, which is
    LEXICOGRAPHIC, not newest-by-mtime: a leftover wheel with a higher version string would be
    verified in place of the one just built. ``egg-info`` carries a ``SOURCES.txt`` that names
    deleted files. ``web/dist`` and the ``static/dist`` link to it go because the SPA is rebuilt
    from the lockfile below; a stale bundle is the other half of what #2758 was. Removing the
    dev symlink costs nothing: the gateway re-links it on start (``ensure_dev_dist_symlink``).
    Only under ``--build`` — a bare ``--wheel`` invocation (what ``release.yml`` runs) touches
    nothing.
    """
    for relative in _STALE_OUTPUTS:
        stale = root / relative
        if stale.exists() or stale.is_symlink():
            _log(f"removing stale build output {relative}")
            _remove_path(stale)


def _build_environment() -> dict[str, str]:
    """The environment every build step runs in: the reproducible epoch, and CI mode.

    ``SOURCE_DATE_EPOCH`` is what setuptools stamps into every ZIP entry, so two builds of one
    tree are byte-identical only when it is fixed; an operator's own value is kept, but it must
    be a date a ZIP entry can hold. ``CI=1`` keeps the root package's ``postinstall`` from
    installing git hooks into the checkout being built.
    """
    env = dict(os.environ)
    epoch = env.setdefault("SOURCE_DATE_EPOCH", _REPRODUCIBLE_EPOCH)
    if not epoch.isdigit() or int(epoch) < int(_REPRODUCIBLE_EPOCH):
        _fail(
            "SOURCE_DATE_EPOCH must be an integer at or after 315532800 "
            "(1980-01-01, the earliest time a ZIP entry can carry)"
        )
    env.setdefault("CI", "1")
    return env


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    _log(f"running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _one_artifact(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        _fail(f"expected exactly one {pattern!r} in {directory}, found {len(matches)}")
    return matches[0]


def artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_evidence(path: Path) -> str:
    return f"{path.name}: sha256={artifact_sha256(path)} size={path.stat().st_size}"


def normalize_sdist(sdist: Path, *, epoch: int) -> None:
    """Rewrite the sdist's archive metadata so identical trees give identical bytes.

    Setuptools keeps each file's own mtime and the builder's uid/gid/names in the tar
    members, and writes the current time into the gzip header. The CONTENTS of two builds
    agree and the digests still differ, which is what makes a published sdist unverifiable.
    Members are sorted, owned by 0:0 with no names, stamped *epoch*, and the gzip header
    carries *epoch* and no filename.
    """
    temporary = sdist.with_name(f".{sdist.name}.normalizing")
    try:
        with tarfile.open(sdist, "r:gz") as source, temporary.open("wb") as output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=output, compresslevel=9, mtime=epoch
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as out:
                    for original in sorted(source.getmembers(), key=lambda member: member.name):
                        member = copy.copy(original)
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.mtime = epoch
                        member.pax_headers = {}
                        body = source.extractfile(original) if original.isfile() else None
                        out.addfile(member, body)
        os.replace(temporary, sdist)
    finally:
        temporary.unlink(missing_ok=True)


def _load_package_manifest(root: Path):
    """``scripts/backend_bundle_manifest.py``, the one reading of the package-data globs."""
    manifest_path = root / "scripts" / "backend_bundle_manifest.py"
    spec = importlib.util.spec_from_file_location("_distribution_package_manifest", manifest_path)
    if spec is None or spec.loader is None:
        _fail(f"cannot load the package-data contract from {manifest_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_payload(root: Path) -> dict[str, set[str]]:
    """What the source tree says the artifacts must carry, derived — never listed by hand.

    Every ``.py``/``.pyi`` under ``src/personalclaw``, every file a ``package-data`` glob
    matches (through the same reader the frozen bundle's spec uses), and every file in
    ``web/dist``. Each set comes in both spellings: the source path the sdist holds and the
    member path the wheel holds.
    """
    package_root = root / _SOURCE_PACKAGE
    python_sources = {
        path.relative_to(root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".pyi"}
    }
    manifest = _load_package_manifest(root)
    package_data_sources = {source for source, _destination in manifest.package_data_datas(root)}
    package_sources = python_sources | package_data_sources
    package_members = {Path(source).relative_to("src").as_posix() for source in package_sources}

    web_root = root / "web" / "dist"
    web_sources = {
        path.relative_to(root).as_posix() for path in web_root.rglob("*") if path.is_file()
    }
    web_members = {
        (Path(_PACKAGE) / "static" / "dist" / Path(source).relative_to("web/dist")).as_posix()
        for source in web_sources
    }

    native_root = root / _SOURCE_PACKAGE / "apps" / "native"
    native_sources = {
        path.relative_to(root).as_posix()
        for path in native_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    native_members = {Path(source).relative_to("src").as_posix() for source in native_sources}
    return {
        "package_sources": package_sources,
        "package_members": package_members,
        "web_sources": web_sources,
        "web_members": web_members,
        "native_sources": native_sources,
        "native_members": native_members,
    }


def _dependency_name(specifier: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", specifier)
    if match is None:
        _fail(f"cannot read a dependency name from {specifier!r}")
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _specifier_set(value: str) -> frozenset[str]:
    """PEP 440 clauses as a set: a backend may emit ``<3.14,>=3.12`` for ``>=3.12,<3.14``."""
    return frozenset(clause.strip() for clause in value.split(",") if clause.strip())


def _assert_metadata(text: str, *, root: Path, artifact: str) -> None:
    """The artifact's core metadata says what ``pyproject.toml`` says."""
    metadata = Parser().parsestr(text)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected = {
        "Name": project["name"],
        "Version": project["version"],
        "License-Expression": project["license"],
    }
    mismatches = {
        key: (value, metadata.get(key))
        for key, value in expected.items()
        if metadata.get(key) != value
    }
    if mismatches:
        _fail(f"{artifact} metadata differs from pyproject.toml (expected, actual): {mismatches}")

    expected_python = _specifier_set(project["requires-python"])
    actual_python = _specifier_set(metadata.get("Requires-Python", ""))
    if expected_python != actual_python:
        _fail(
            f"{artifact} Requires-Python differs: "
            f"expected={sorted(expected_python)}, actual={sorted(actual_python)}"
        )

    expected_extras = {name.replace("_", "-") for name in project.get("optional-dependencies", {})}
    actual_extras = {name.replace("_", "-") for name in metadata.get_all("Provides-Extra") or []}
    if expected_extras != actual_extras:
        _fail(
            f"{artifact} extras differ: missing={sorted(expected_extras - actual_extras)}, "
            f"extra={sorted(actual_extras - expected_extras)}"
        )

    declared = list(project.get("dependencies", []))
    for requirements in project.get("optional-dependencies", {}).values():
        declared.extend(requirements)
    expected_dependencies = {_dependency_name(requirement) for requirement in declared}
    actual_dependencies = {
        _dependency_name(requirement) for requirement in metadata.get_all("Requires-Dist") or []
    }
    if expected_dependencies != actual_dependencies:
        _fail(
            f"{artifact} dependencies differ: "
            f"missing={sorted(expected_dependencies - actual_dependencies)}, "
            f"extra={sorted(actual_dependencies - expected_dependencies)}"
        )

    expected_urls = {str(key): str(value) for key, value in project.get("urls", {}).items()}
    actual_urls = {}
    for value in metadata.get_all("Project-URL") or []:
        label, separator, url = value.partition(",")
        if separator:
            actual_urls[label.strip()] = url.strip()
    if expected_urls != actual_urls:
        _fail(f"{artifact} project URLs differ: expected={expected_urls}, actual={actual_urls}")


def _assert_wheel_record(archive: zipfile.ZipFile, members: set[str], record: str) -> None:
    """``RECORD`` names every member exactly once, with a digest and size that are true."""
    rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
    entries = {row[0]: row[1:] for row in rows}
    if set(entries) != members:
        _fail(
            "wheel RECORD does not enumerate the archive exactly: "
            f"missing={sorted(members - set(entries))[:20]}, "
            f"extra={sorted(set(entries) - members)[:20]}"
        )
    for name in sorted(members - {record}):
        digest_text, size_text = entries[name]
        if not digest_text.startswith("sha256=") or not size_text:
            _fail(f"wheel RECORD has no sha256/size for {name}")
        body = archive.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        if digest_text != f"sha256={encoded}" or int(size_text) != len(body):
            _fail(f"wheel RECORD digest or size is wrong for {name}")
    if entries[record] != ["", ""]:
        _fail("wheel RECORD must leave its own digest and size empty")


def _payload_diff(label: str, expected: set[str], actual: set[str]) -> None:
    if expected != actual:
        _fail(
            f"{label}: missing={sorted(expected - actual)[:30]}, "
            f"extra={sorted(actual - expected)[:30]}"
        )


def inspect_wheel(wheel: Path, *, root: Path | None = None) -> None:
    """The wheel's contents are exactly what this checkout declares, and its metadata agrees."""
    root = root or _repo_root()
    payload = _source_payload(root)
    with zipfile.ZipFile(wheel) as archive:
        members = {name for name in archive.namelist() if not name.endswith("/")}
        metadata_members = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata_members) != 1:
            _fail(f"{wheel.name} must contain one dist-info/METADATA, found {metadata_members}")
        dist_info = metadata_members[0].removesuffix("METADATA")
        required = {f"{dist_info}{name}" for name in ("WHEEL", "RECORD", "entry_points.txt")}
        required.add(f"{dist_info}licenses/LICENSE")
        if required - members:
            _fail(f"{wheel.name} is missing wheel metadata: {sorted(required - members)}")

        package = {name for name in members if name.startswith(f"{_PACKAGE}/")}
        _payload_diff(
            f"{wheel.name} package payload differs from the source",
            payload["package_members"] | payload["web_members"],
            package,
        )
        _payload_diff(
            f"{wheel.name} dashboard differs from web/dist",
            payload["web_members"],
            {name for name in members if name.startswith(f"{_PACKAGE}/static/dist/")},
        )
        _payload_diff(
            f"{wheel.name} bundled apps differ from the source",
            payload["native_members"],
            {name for name in members if name.startswith(_BUNDLED_APP_PREFIX)},
        )

        _assert_metadata(
            archive.read(metadata_members[0]).decode("utf-8"), root=root, artifact=wheel.name
        )
        entry_points = archive.read(f"{dist_info}entry_points.txt").decode("utf-8")
        if "personalclaw = personalclaw.cli:main" not in entry_points:
            _fail(f"{wheel.name} does not expose the personalclaw console entry point")
        wheel_metadata = Parser().parsestr(archive.read(f"{dist_info}WHEEL").decode("utf-8"))
        if wheel_metadata.get("Root-Is-Purelib") != "true":
            _fail(f"{wheel.name} is unexpectedly not a pure-Python wheel")
        _assert_wheel_record(archive, members, f"{dist_info}RECORD")

    _log(
        f"OK: wheel payload complete — {len(payload['package_members'])} package file(s), "
        f"{len(payload['web_members'])} dashboard file(s), "
        f"{len(payload['native_members'])} bundled-app file(s)"
    )
    _log(_artifact_evidence(wheel))


def _sdist_members(sdist: Path) -> dict[str, bytes]:
    """The sdist's files, keyed by their path under its one top-level directory."""
    with tarfile.open(sdist, "r:*") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        roots = {Path(member.name).parts[0] for member in files if Path(member.name).parts}
        if len(roots) != 1:
            _fail(f"{sdist.name} must contain one top-level directory, found {sorted(roots)}")
        members: dict[str, bytes] = {}
        for member in files:
            relative = Path(*Path(member.name).parts[1:]).as_posix()
            stream = archive.extractfile(member)
            if stream is None:
                _fail(f"cannot read {member.name} from {sdist.name}")
            members[relative] = stream.read()
    return members


def inspect_sdist(sdist: Path, *, root: Path | None = None) -> None:
    """The sdist carries everything a wheel is rebuilt from, and its metadata agrees."""
    root = root or _repo_root()
    payload = _source_payload(root)
    bodies = _sdist_members(sdist)
    members = set(bodies)
    required = {"LICENSE", "MANIFEST.in", "README.md", "pyproject.toml", "setup.py", "PKG-INFO"}
    missing = (payload["package_sources"] | payload["web_sources"] | required) - members
    if missing:
        _fail(f"{sdist.name} omitted required source files: {sorted(missing)[:30]}")
    _payload_diff(
        f"{sdist.name} package sources differ from the source",
        payload["package_sources"],
        {name for name in members if name.startswith(f"{_SOURCE_PACKAGE.as_posix()}/")},
    )
    _payload_diff(
        f"{sdist.name} dashboard differs from web/dist",
        payload["web_sources"],
        {name for name in members if name.startswith("web/dist/")},
    )
    _payload_diff(
        f"{sdist.name} bundled apps differ from the source",
        payload["native_sources"],
        {name for name in members if name.startswith(f"{_SOURCE_PACKAGE.as_posix()}/apps/native/")},
    )
    _assert_metadata(bodies["PKG-INFO"].decode("utf-8"), root=root, artifact=sdist.name)
    _log(
        f"OK: sdist payload complete — {len(payload['package_sources'])} package source "
        f"file(s), {len(payload['web_sources'])} dashboard file(s), "
        f"{len(payload['native_sources'])} bundled-app file(s)"
    )
    _log(_artifact_evidence(sdist))


def _extract_sdist(sdist: Path, destination: Path) -> Path:
    with tarfile.open(sdist, "r:*") as archive:
        roots = {Path(member.name).parts[0] for member in archive.getmembers() if member.name}
        if len(roots) != 1:
            _fail(f"{sdist.name} must contain one top-level directory, found {sorted(roots)}")
        archive.extractall(destination, filter="data")
    extracted = destination / next(iter(roots))
    if not extracted.is_dir():
        _fail(f"{sdist.name} did not extract to {extracted}")
    return extracted


def bundled_model_probe():
    """The shared probe's JUDGES, imported by path — stdlib-only, it imports no ``personalclaw``.

    By path because this script runs on a bare runner. Only the judges run here; the probe's
    ``probe()`` runs inside the artifact (:func:`_installed_bundled_model_report`).
    """
    spec = importlib.util.spec_from_file_location(
        "_installed_bundled_model_probe", _BUNDLED_MODEL_PROBE
    )
    if spec is None or spec.loader is None:
        _fail(f"cannot load the bundled-model probe at {_BUNDLED_MODEL_PROBE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installed_bundled_model_report(py: Path, home: Path, wheel: Path) -> dict:
    """Run the probe with the scratch venv's OWN interpreter, so the installed package answers.

    ``-I`` (isolated) ignores ``PYTHONPATH`` and the script's directory, so nothing from a source
    tree can stand in for the install. ``PERSONALCLAW_HOME`` is the scratch home, because loading
    the app resolves a home and it must never be the real one.
    """
    # Every path is made absolute HERE, because this is the call that changes the working
    # directory: a relative one would be read against the scratch home and name nothing. With
    # `absolute()`, never `resolve()`: a venv's `bin/python` is a symlink to the base interpreter,
    # and following it would run a Python that does not have the wheel installed.
    py, home, wheel = py.absolute(), home.absolute(), wheel.absolute()
    env = dict(os.environ)
    env["PERSONALCLAW_HOME"] = str(home)
    proc = subprocess.run(
        [str(py), "-I", str(_BUNDLED_MODEL_PROBE), str(wheel)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
        timeout=_PROBE_TIMEOUT_S,
    )
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        _fail(
            f"the bundled-model probe did not run inside the installed wheel (rc={proc.returncode})"
            f": {proc.stderr.strip()[-2000:]}"
        )
    try:
        return json.loads(lines[-1])
    except ValueError:
        _fail(f"the bundled-model probe printed no report: {proc.stdout.strip()[-2000:]!r}")


def _assert_installed_bundled_model(py: Path, home: Path, wheel: Path) -> None:
    """Assertion 7 (OU-14): the INSTALLED package can say what it would download.

    🔴 This read the record from the REPOSITORY until 2026-09-25, through a copy of
    ``personalclaw.bundled_model`` imported from ``src/``. The repository always has the record,
    so the gate passed while the container image — whose installed package had none, because the
    record was a symlink into ``docs/`` and the image copies only ``src/`` — offered no download
    anywhere. Now the scratch venv's interpreter reports on the record the installed
    bundled-chat app reads, judged by :func:`record_failures`.

    The record is still load-bearing although the weight is not in the wheel: it carries the
    licence, the source pin and the digest the first-run fetch depends on. The same probe runs
    the INSTALLED wheel gate — the wheel carries NO weight-shaped member and stays small — which
    is the opposite of what this asserted on 2026-09-23, when the weight briefly shipped.
    """
    report = _installed_bundled_model_report(py, home, wheel)
    judges = bundled_model_probe()
    record = report.get("record", {})
    _log(f"bundled model: the installed app reads {record.get('path')}")
    failures = judges.record_failures(report, wheel_gate_required=True)
    if failures:
        detail = "\n".join(f"  {line}" for line in failures)
        _fail(f"the installed wheel cannot offer its default chat model:\n{detail}")
    declaration = report["declaration"]
    _log(
        f"bundled model: {declaration['model_id']} signed off under {declaration['licence']} "
        "(read from the installed package, not the repository)"
    )
    _log(f"bundled model: {report['wheel_gate']['summary']}")


def _assert_download_offer(base: str) -> None:
    """Assertion 8: the booted install OFFERS the default model's download on a fresh home.

    The user-facing half of assertion 7, and the one the image defect was actually measured on:
    every response 200, ``chat_download_offer`` null, and so no download offer in onboarding, on
    the chat screen or in Settings → Models. Judged by the shared probe's
    :func:`offer_failure`, so this and the image gate cannot drift into two readings of it.
    """
    status, _ctype, body = _http_get(f"{base}/api/onboarding", limit=None)
    if status != 200:
        _fail(f"/api/onboarding returned {status} (want 200)")
    try:
        payload = json.loads(body)
    except ValueError:
        _fail(f"/api/onboarding did not answer JSON: {body[:300]!r}")
    failure = bundled_model_probe().offer_failure(payload)
    if failure:
        _fail(failure)
    offer = payload["chat_download_offer"]
    _log(
        f"OK: /api/onboarding offers {offer.get('model')} ({offer['bytes']} bytes) on a fresh home"
    )


#: The licence notices the installed wheel serves, and how each one's text opens.
_NOTICE_TITLES = {
    "/THIRD_PARTY_NOTICES.txt": "Third-party notices for the PersonalClaw dashboard\n",
    "/THIRD_PARTY_NOTICES_NPM.txt": "Third-party notices for the PersonalClaw dashboard's code\n",
}


def _assert_notices_served(base: str) -> None:
    """Assertion 9: the booted install serves both licence notices as plain text.

    What a user opens from Settings → Updates → Licences. Both files are in the wheel's
    ``static/dist`` (:func:`inspect_wheel` sees to that); this asks the INSTALLED gateway for them,
    because an unrouted file is answered with the dashboard's own HTML and still returns 200.
    """
    for path, title in _NOTICE_TITLES.items():
        status, ctype, body = _http_get(f"{base}{path}")
        if status != 200:
            _fail(f"{path} returned {status} (want 200 text/plain)")
        if not ctype.lower().startswith("text/plain") or not body.startswith(title):
            _fail(f"{path} did not serve its notices (content-type={ctype!r}): {body[:120]!r}")
    _log(f"OK: {' and '.join(_NOTICE_TITLES)} → 200 text/plain (the licence notices)")


def _assert_spa_in_wheel(wheel: Path) -> None:
    names = zipfile.ZipFile(wheel).namelist()
    if not any(n.endswith(_SPA_MARKER) for n in names):
        _fail(
            f"wheel {wheel.name} does not carry the SPA ({_SPA_MARKER}). "
            "Build distributions with `make build`, which builds the dashboard from the "
            "lockfile before setup.py's BuildWithWeb stages web/dist into the package."
        )
    _log(f"OK: wheel carries the SPA — {wheel.name}")


def bundled_app_count(wheel: Path) -> int:
    """How many app directories the wheel carries under ``personalclaw/apps/native/``.

    Reported beside the enabled count purely so the release log says what was on offer as well
    as what loaded — assertion 6 does not require the two to be EQUAL, because a bundled app
    that declares no ``provider`` legitimately never becomes an extension.
    """
    names = zipfile.ZipFile(wheel).namelist()
    dirs = {
        n[len(_BUNDLED_APP_PREFIX) :].split("/", 1)[0]
        for n in names
        if n.startswith(_BUNDLED_APP_PREFIX) and "/" in n[len(_BUNDLED_APP_PREFIX) :]
    }
    return len(dirs)


def _make_venv(root: Path) -> Path:
    """Create a venv with pip; return the python executable path."""
    venv.EnvBuilder(with_pip=True, clear=True).create(str(root))
    py = (
        root
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    if not py.exists():
        _fail(f"venv python not found at {py}")
    return py


def _pip_install_wheel(py: Path, wheel: Path) -> None:
    _log("installing the wheel into the scratch venv (from the wheel alone)…")
    subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        check=True,
    )
    subprocess.run(
        [str(py), "-m", "pip", "install", str(wheel), "--quiet"],
        check=True,
    )


def _assert_no_node() -> None:
    """The wheel must serve its own SPA with NO Node toolchain present."""
    if shutil.which("npm") or shutil.which("node"):
        _log(
            "WARNING: node/npm present on PATH — the contract is that assets ship "
            "in the wheel; the test still holds but does not *prove* Node-absence."
        )
    else:
        _log("OK: no node/npm on PATH — asset-serving proves the wheel is self-contained")


def extension_failures(lines: Iterable[str]) -> list[str]:
    """The gateway lines that say a bundled app FAILED TO LOAD, in order.

    Assertion 6, and the reason it exists: this script already booted a real gateway and
    already read its output — that is how #2758's two ERROR tracebacks were visible in a run
    that printed ``PASS``. The evidence was in hand and nothing asserted on it, which is the
    same shape as a check that passes while the thing it exists to prove never happened.

    A user installing such a wheel gets silently missing capabilities plus error rows in the
    app Store, and nothing in the release path says so. Extension loading is best-effort BY
    DESIGN in the gateway (one broken app must not take the process down), so a failure is
    logged and the boot succeeds — which means the log is the only place the failure exists.
    """
    return [line for line in lines if any(m in line for m in _EXTENSION_FAILURE_MARKERS)]


def extensions_enabled(lines: Iterable[str]) -> list[str]:
    """The gateway lines that say a bundled app DID load — the anti-vacuity half."""
    return [line for line in lines if _EXTENSION_SUCCESS_MARKER in line]


def _assert_every_bundled_app_enabled(transcript: list[str], bundled_apps: int) -> None:
    """Assertion 6: the registry ran, and it reported no failure.

    BOTH halves, because either alone is satisfiable by nothing happening. Measured on the
    0.1.3 wheel: 182 gateway lines, 30 ``Enabled extension`` lines against 30 bundled app
    directories, one WARNING (the expected ``AUTH_MODE=none`` notice) and zero failures. At the
    default log level only 2 lines are emitted and NONE of them mention an extension, which is
    why the gateway is booted ``--verbose`` — a check whose evidence window is empty passes for
    the same reason a broken one does.
    """
    failures = extension_failures(transcript)
    if failures:
        detail = "\n".join(f"  {line}" for line in failures[:20])
        _fail(
            f"the wheel boots but {len(failures)} bundled extension line(s) report a FAILURE "
            f"to load:\n{detail}\n"
            "The wheel ships an app the package cannot enable. If the app directory is not in "
            "git, a stale build/ tree was packaged — rebuild with --build (which clears it). "
            "If it IS in git, the app's manifest names a factory the package no longer defines."
        )
    enabled = extensions_enabled(transcript)
    if not enabled:
        _fail(
            f"the gateway never reported enabling a single extension, so this assertion "
            f"measured NOTHING — a wheel with broken apps would look identical. The wheel "
            f"carries {bundled_apps} bundled app director(ies) under {_BUNDLED_APP_PREFIX}. "
            f"Read {len(transcript)} line(s); check that the gateway is still booted with the "
            f"top-level --verbose flag (the registry logs enables at INFO)."
        )
    _log(
        f"OK: {len(enabled)} extension(s) enabled, 0 failed "
        f"({bundled_apps} bundled app dir(s) in the wheel, {len(transcript)} gateway line(s))"
    )


def _read_ready_line(proc: "subprocess.Popen[str]", deadline: float, transcript: list[str]) -> dict:
    """Block until the gateway prints its PERSONALCLAW_READY line (or timeout).

    Every line read is appended to *transcript*, including the pre-READY startup chatter —
    which is exactly where the extension failures of #2758 appear, since the registry enables
    the bundled apps during boot.
    """
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                _fail(f"gateway exited early (rc={proc.returncode}) before READY")
            continue
        line = line.rstrip("\n")
        if line.startswith(_READY_PREFIX):
            return json.loads(line[len(_READY_PREFIX) :])
        # Surface startup chatter for debugging without failing on it — but KEEP it, so
        # assertion 6 can judge it (see `extension_failures`).
        transcript.append(line)
        _log(f"gateway> {line}")
    _fail("timed out waiting for the gateway READY line")


def _drain(proc: "subprocess.Popen[str]", transcript: list[str]) -> None:
    """Keep reading the gateway's output after READY, into *transcript*.

    Without this the pipe would fill (blocking the gateway) and, more importantly, any
    extension that is enabled lazily — after the READY line rather than during boot — would
    report its failure into a stream nobody read. Assertion 6 must not depend on WHEN the
    registry happens to enable an app.
    """
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            transcript.append(line)
            _log(f"gateway> {line}")
    except (ValueError, OSError):  # pipe closed under us by terminate()
        return


def _http_get(url: str, timeout: float = 10.0, limit: int | None = 4096) -> tuple[int, str, str]:
    """GET *url*. *limit* caps the body read; ``None`` reads it whole (a JSON body must be)."""
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read() if limit is None else resp.read(limit)
            body = raw.decode("utf-8", "replace")
            return resp.status, resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:  # non-2xx
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", ""
    except Exception as exc:  # noqa: BLE001
        _fail(f"GET {url} raised {type(exc).__name__}: {exc}")


def _boot_and_probe(py: Path, home: Path, bundled_apps: int = 0) -> None:
    env = dict(os.environ)
    env["PERSONALCLAW_HOME"] = str(home)
    # Loopback-only, no-auth so `/` (the SPA shell) is served without a token —
    # a localhost smoke test; effective_bind() pins NONE mode to 127.0.0.1.
    env["PERSONALCLAW_AUTH_MODE"] = "none"
    env.pop("PYTHONWARNINGS", None)

    # 🪤 `--verbose` is a TOP-LEVEL flag and belongs BEFORE the subcommand — `gateway
    # --test-mode --verbose` exits 2 with "unrecognized arguments". It is here because the
    # registry logs each enable at INFO and the default level is WARNING: measured on the 0.1.3
    # wheel, the default boot emits 2 lines and mentions no extension at all, so assertion 6
    # would have had an EMPTY evidence window. Verbose gives it 182 lines and 30 enables.
    _log("booting `personalclaw --verbose gateway --test-mode`…")
    proc = subprocess.Popen(
        [str(py), "-m", "personalclaw", "--verbose", "gateway", "--test-mode"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    transcript: list[str] = []
    drain: threading.Thread | None = None
    try:
        ready = _read_ready_line(proc, time.time() + _BOOT_TIMEOUT_S, transcript)
        drain = threading.Thread(target=_drain, args=(proc, transcript), daemon=True)
        drain.start()
        port = int(ready["port"])
        base = f"http://127.0.0.1:{port}"
        _log(f"gateway READY on {base} (pid={ready.get('pid')})")

        # 4. /api/healthz — auth-exempt liveness, 200 JSON with the version.
        status, ctype, body = _http_get(f"{base}/api/healthz")
        if status != 200:
            _fail(f"/api/healthz returned {status} (want 200)")
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            payload = {}
        if payload.get("status") != "ok":
            _fail(f"/api/healthz body not ok: {body!r}")
        _log(f"OK: /api/healthz → 200 {payload}")

        # 5. / — the SPA shell, 200 HTML served from the packaged static/dist.
        status, ctype, body = _http_get(f"{base}/")
        if status != 200:
            _fail(f"/ returned {status} (want 200 HTML)")
        if "text/html" not in ctype.lower() and "<!doctype html" not in body.lower():
            _fail(f"/ did not return HTML (content-type={ctype!r})")
        _log("OK: / → 200 HTML (SPA shell served from the wheel's static/dist)")

        # 8. /api/onboarding — the install offers its default chat model's download.
        _assert_download_offer(base)

        # 9. The licence notices Settings → Updates links, served from the wheel's static/dist.
        _assert_notices_served(base)
    finally:
        _log("stopping gateway…")
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if drain is not None:
            drain.join(timeout=5)

    # 6. Assertion SIX, deliberately after the shutdown so the transcript is complete: the
    #    gateway served / and /api/healthz, so the two assertions above are satisfied — and a
    #    wheel whose bundled apps failed to load satisfies them too. #2758.
    _assert_every_bundled_app_enabled(transcript, bundled_apps)


def _verify_wheel_runtime(wheel: Path, *, keep: bool) -> None:
    """Assertions 1-8: install the wheel alone into a scratch venv, then boot and probe it."""
    _log(f"verifying {wheel}")
    _assert_spa_in_wheel(wheel)
    _assert_no_node()

    # Absolute like the wheel, and for the same reason: the venv interpreter and the home are
    # handed to child processes, and a relative TMPDIR would make both depend on this one's cwd.
    scratch = Path(tempfile.mkdtemp(prefix="pc_verify_wheel_")).absolute()
    venv_dir = scratch / "venv"
    home_dir = scratch / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    try:
        py = _make_venv(venv_dir)
        _pip_install_wheel(py, wheel)
        _assert_installed_bundled_model(py, home_dir, wheel)
        _boot_and_probe(py, home_dir, bundled_app_count(wheel))
    finally:
        if keep:
            _log(f"kept scratch dir: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    _log(
        "PASS: wheel contract met (SPA packaged, installs from the wheel alone, the installed "
        "package carries a permitted bundled-model record and no weight, gateway serves / + "
        "/api/healthz, every bundled app enabled, the install offers its default model, and "
        "it serves its licence notices)."
    )


def _canonical_distribution_build(*, keep: bool) -> None:
    """``make build``: the one way to produce a distribution that can be checked.

    Clean, then the SPA from the lockfile (``npm ci``, never ``npm install``), stamped and
    proved fresh against the checked-out sources; then the sdist (normalized, see
    :func:`normalize_sdist`) and the wheel from the tree, both inspected; then a second wheel
    built FROM the sdist, which must be byte-identical to the first — the proof that the
    published sdist reproduces the published wheel. Only then is the wheel installed and
    served: identical bytes behave identically, so booting the one is booting the other.
    """
    root = _repo_root()
    env = _build_environment()
    npm = env.get("NPM", "npm")
    uv = env.get("UV", "uv")

    _clean_distribution_outputs(root)
    _run([npm, "ci"], cwd=root, env=env)
    _run([npm, "run", "build"], cwd=root, env=env)
    _run([sys.executable, "scripts/spa_dist_freshness.py", "stamp"], cwd=root, env=env)
    _run(
        [sys.executable, "scripts/spa_dist_freshness.py", "check", "--require-stamp"],
        cwd=root,
        env=env,
    )
    # Nothing ships without its licence notice: every tracked asset, every font and binary the
    # SPA build emitted, and every npm package whose code it bundled (THIRD_PARTY_NOTICES_NPM.txt).
    _run([sys.executable, "scripts/check_asset_licenses.py", "--built-web"], cwd=root, env=env)

    dist = root / "dist"
    _run([uv, "build", "--sdist", "--out-dir", str(dist)], cwd=root, env=env)
    sdist = _one_artifact(dist, "*.tar.gz")
    normalize_sdist(sdist, epoch=int(env["SOURCE_DATE_EPOCH"]))
    _run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=root, env=env)
    wheel = _one_artifact(dist, "*.whl")
    inspect_sdist(sdist, root=root)
    inspect_wheel(wheel, root=root)

    scratch = Path(tempfile.mkdtemp(prefix="pc_verify_sdist_")).absolute()
    try:
        extracted = _extract_sdist(sdist, scratch / "source")
        rebuilt_dir = scratch / "wheel"
        _run(
            [uv, "build", "--wheel", "--out-dir", str(rebuilt_dir), str(extracted)],
            cwd=root,
            env=env,
        )
        rebuilt = _one_artifact(rebuilt_dir, "*.whl")
        direct_digest, rebuilt_digest = artifact_sha256(wheel), artifact_sha256(rebuilt)
        if direct_digest != rebuilt_digest:
            _fail(
                "the wheel rebuilt from the sdist is not byte-identical to the wheel built from "
                f"the tree: tree={direct_digest}, from-sdist={rebuilt_digest}"
            )
        _log(f"OK: the wheel rebuilt from the sdist is byte-identical — sha256={direct_digest}")
    finally:
        if keep:
            _log(f"kept sdist rebuild scratch dir: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    _verify_wheel_runtime(wheel, keep=keep)
    _log("PASS: canonical distribution build")
    _log(_artifact_evidence(sdist))
    _log(_artifact_evidence(wheel))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build (--build) or verify (--wheel) a PersonalClaw distribution (C4)."
    )
    ap.add_argument("--wheel", help="wheel path or glob (default: newest dist/*.whl)")
    ap.add_argument(
        "--build",
        action="store_true",
        help="run the canonical build: clean, locked SPA, sdist + wheel, inspection, "
        "byte-identical rebuild from the sdist, install and serve",
    )
    ap.add_argument("--keep", action="store_true", help="keep scratch venvs/homes")
    args = ap.parse_args()

    if args.build:
        if args.wheel:
            ap.error("--wheel cannot be combined with --build")
        _canonical_distribution_build(keep=args.keep)
        return 0

    wheel = _find_wheel(args.wheel)
    inspect_wheel(wheel)
    _verify_wheel_runtime(wheel, keep=args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
