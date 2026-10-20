"""Unbounded `>=` version claims — for a dependency OR for the interpreter — stay bounded.

A `>=` spec with no upper bound is not a preference, it is a bet that every future major
release keeps the symbols this repo imports. `mcp` lost that bet: `mcp 2.0.0` renamed
`mcp.client.streamable_http.streamablehttp_client`, which `personalclaw/mcp_client.py`
imports by name for the streamable-HTTP transport. The repo's own venv had `mcp 1.28.1`
and was green, so nothing here failed — and **CI installs from the lockfile, so CI could
not see it either**. Only a fresh install of what was then `personalclaw[mcp]` resolved 2.0.0
and broke, which is the one path a new user takes.

This rail asserts an upper bound on the dependencies whose modules are imported for a *named*
attribute, or whose next major changes a type this repo hands them, wherever pyproject declares
them. It deliberately does not police every dependency: a bound costs real maintenance (someone
must widen it), so it is spent where a major is known to break what the code relies on rather
than everywhere as a matter of style.

`requires-python` is the same defect with a bigger blast radius, and it collected too: an
open `>=3.12` promised every future interpreter while `full.yml` verified two, so 3.14
installed cleanly and broke the connector-pack parse fence outright — the fence denies
`importlib`, so neutering the pre-imported `importlib._bootstrap` replaces the import
machinery's own `_find_and_load` and EVERY import inside a pack script is refused,
legitimate ones included. Hence the second pair of rails below: the *declared* support
range and the classifier list are both derived from the matrix that actually runs the
suite, so widening one without widening the other is a red rather than a promise nothing
keeps.

The last rail is the other end of a bound: a floor a behaviour needs. An app can declare its own
floor, but an install through the extra that carries the same library resolves what the extra
says, and CI installs what the lock recorded for it, so both hold it too.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from dependency_bounds_shared import (
    _MUST_BE_BOUNDED,
    _MUST_KEEP_A_CEILING,
    _REPO_ROOT,
    _declarations,
)


def test_import_sensitive_dependencies_declare_an_upper_bound() -> None:
    unbounded: list[str] = []
    for dist, reason in _MUST_BE_BOUNDED.items():
        found = [
            (where, spec)
            for where, specs in _declarations().items()
            for spec in specs
            if spec.split(">=")[0].split("[")[0].strip() == dist
        ]
        assert found, f"pyproject.toml no longer declares {dist!r}"
        for where, spec in found:
            if "<" not in spec and "==" not in spec and "~=" not in spec:
                unbounded.append(f"{where}: {spec!r} — {reason}")
    assert not unbounded, (
        "these specs would let a FRESH install resolve a major release that breaks what this "
        "repo relies on, a symbol it imports or a type it passes (CI installs from the lockfile "
        "and will not catch it): " + "; ".join(unbounded)
    )


def test_the_streamable_http_symbol_this_bound_protects_is_still_imported_by_name() -> None:
    """Vacuity floor: if the import goes away, the bound above is arguing for nothing.

    Without this, deleting the import site would leave a bound nobody can justify — and
    the next person to widen it would have no way to tell whether the reason still held.
    """
    source = (_REPO_ROOT / "src" / "personalclaw" / "mcp_client.py").read_text()
    assert "from mcp.client.streamable_http import streamablehttp_client" in source, (
        "mcp_client.py no longer imports streamablehttp_client by name — re-derive whether "
        "the mcp<2 bound is still needed instead of carrying it on faith"
    )


def test_the_lockfile_agrees_with_the_declared_range() -> None:
    """`uv.lock` carries its own copy of `requires-python`, and CI installs from the lock.

    Every Python job runs `uv sync --locked`, which REFUSES to re-resolve: a lock whose
    range disagrees with `pyproject` fails the install outright rather than picking a
    different interpreter. So narrowing the range without re-running `uv lock` reds every
    Python job at once while the jobs that skip `uv sync` stay green — a whole-matrix
    failure whose message names the lockfile and not the bound that moved it.

    The rail above pins the declared range to the matrix; this one pins the *resolved*
    range to the declared one, which is the copy CI actually installs from.
    """
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        declared = str(tomllib.load(fh)["project"]["requires-python"])
    with (_REPO_ROOT / "uv.lock").open("rb") as fh:
        locked = str(tomllib.load(fh)["requires-python"])
    assert locked.replace(" ", "") == declared.replace(" ", ""), (
        f"uv.lock resolved for requires-python = {locked!r} but pyproject declares "
        f"{declared!r}. `uv sync --locked` will refuse the install in every Python job. "
        "Run `uv lock` and commit the result in the same change that moves the bound."
    )


#: A distribution whose floor a behaviour or a published advisory needs, wherever pyproject
#: declares it: the floor, a version just below it, and why.
_MUST_KEEP_A_FLOOR = {
    "sentence-transformers": (
        "5.6",
        "5.5.99",
        "5.6 is the oldest release checked for the local-only model card the sentence-transformers "
        "app hands a model it loads (SentenceTransformerModelCardData(local_files_only=True))",
    ),
    "urllib3": (
        "2.8.0",
        "2.7.99",
        "2.8.0 is the first release outside GHSA-8988-9cw3-xx77, GHSA-vxq7-64xx-v4gw and "
        "GHSA-gh4c-6fx4-qh6g, and an update keeps an installed urllib3 that every range admits",
    ),
}


def test_a_floor_a_behaviour_needs_holds_in_pyproject_and_in_the_lock() -> None:
    from packaging.requirements import Requirement

    with (_REPO_ROOT / "uv.lock").open("rb") as fh:
        [ours] = [p for p in tomllib.load(fh)["package"] if p["name"] == "personalclaw"]
    locked = {
        (entry["name"], entry.get("marker", "")): entry.get("specifier", "")
        for entry in ours["metadata"]["requires-dist"]
    }
    for dist, (floor, below, reason) in _MUST_KEEP_A_FLOOR.items():
        declared = [
            (where, Requirement(spec))
            for where, specs in _declarations().items()
            for spec in specs
            if Requirement(spec).name == dist
        ]
        assert declared, f"pyproject.toml no longer declares {dist!r}"
        for where, req in declared:
            assert req.specifier.contains(floor), f"{where}: {req} refuses {floor} itself"
            assert not req.specifier.contains(below), f"{where}: {req} admits {below}: {reason}"
            marker = "" if where == "dependencies" else f"extra == '{where}'"
            recorded = locked.get((dist, marker))
            assert recorded == str(req.specifier), (
                f"uv.lock records {dist}{recorded or ''} for {where} but pyproject declares "
                f"{req}: run `uv lock` and commit the result in the same change"
            )


def test_a_ceiling_a_behaviour_needs_sits_beside_what_needs_it_and_in_the_lock() -> None:
    """``pip install 'personalclaw[stt]'`` resolves what the extra says, and faster-whisper's own
    ``av>=11`` admits PyAV 19, so the extra carries the ceiling. The lock must record the same
    specifier, and the version it pins must be one the ceiling admits."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    with (_REPO_ROOT / "uv.lock").open("rb") as fh:
        lock = tomllib.load(fh)["package"]
    [ours] = [p for p in lock if p["name"] == "personalclaw"]
    recorded = {
        (entry["name"], entry.get("marker", "")): entry.get("specifier", "")
        for entry in ours["metadata"]["requires-dist"]
    }
    for dist, (needed_by, works, breaks, reason) in _MUST_KEEP_A_CEILING.items():
        needing = 0
        for where, specs in _declarations().items():
            reqs = [Requirement(spec) for spec in specs]
            if not any(canonicalize_name(r.name) == needed_by for r in reqs):
                continue
            needing += 1
            mine = [r for r in reqs if canonicalize_name(r.name) == dist]
            assert mine, f"{where} declares {needed_by} with no {dist} ceiling: {reason}"
            for req in mine:
                assert req.specifier.contains(works), f"{where}: {req} refuses {works}"
                assert not req.specifier.contains(
                    breaks
                ), f"{where}: {req} admits {breaks}: {reason}"
                marker = "" if where == "dependencies" else f"extra == '{where}'"
                assert recorded.get((dist, marker)) == str(req.specifier), (
                    f"uv.lock records {dist}{recorded.get((dist, marker)) or ''} for {where} but "
                    f"pyproject declares {req}: run `uv lock` and commit the result"
                )
                pinned = [p["version"] for p in lock if p["name"] == dist]
                assert pinned and all(
                    req.specifier.contains(v) for v in pinned
                ), f"uv.lock pins {dist} {pinned}, outside {req}"
        assert needing, f"pyproject no longer declares {needed_by}; re-derive the {dist} ceiling"


def _faster_whisper_audio() -> Path:
    """faster-whisper's decoder source, found without importing the package: importing it makes
    torch resident, which ``tests/native_omp_guard.py`` forbids in a test worker."""
    import importlib.util

    import pytest

    spec = importlib.util.find_spec("faster_whisper")
    if spec is None or spec.origin is None:
        pytest.skip("faster-whisper is not installed here (it is part of `uv sync --extra dev`)")
    return Path(spec.origin).parent / "audio.py"


def test_the_keyword_the_av_ceiling_protects_is_still_passed() -> None:
    """Vacuity floor: once faster-whisper stops passing the keyword, the ceiling argues for
    nothing, and it should be widened rather than carried on faith."""
    source = _faster_whisper_audio().read_text(encoding="utf-8")
    assert 'metadata_errors="ignore"' in source, (
        "faster-whisper's decode_audio no longer passes metadata_errors to av.open — re-derive "
        "whether the stt extra's av ceiling is still needed"
    )


def test_the_locked_pyav_decodes_audio_through_faster_whisper(tmp_path: Path) -> None:
    """The behaviour itself: faster-whisper's decoder reads a WAV with the PyAV this environment
    installed from the lock. It runs in a child process, for the reason above."""
    import math
    import os
    import struct
    import subprocess
    import sys
    import wave

    _faster_whisper_audio()
    recording = tmp_path / "tone.wav"
    with wave.open(str(recording), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 16000)))
                for n in range(8000)
            )
        )
    probe = (
        "import sys\n"
        "from faster_whisper.audio import decode_audio\n"
        "print(len(decode_audio(sys.argv[1])))\n"
    )
    env = {**os.environ, "HOME": str(tmp_path), "HF_HOME": str(tmp_path / "hf")}
    done = subprocess.run(
        [sys.executable, "-c", probe, str(recording)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "8000", done.stdout
