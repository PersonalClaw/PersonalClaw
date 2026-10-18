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

import source_index
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: distribution that must carry an upper bound wherever it is declared, and why.
_MUST_BE_BOUNDED = {
    "mcp": (
        "mcp 2.0.0 renamed mcp.client.streamable_http.streamablehttp_client, which "
        "src/personalclaw/mcp_client.py imports by name"
    ),
    "openai": (
        "openai 3 declares AsyncOpenAI's http_client as an httpx2.AsyncClient, and the openai "
        "providers pass the egress-guarded httpx.AsyncClient, so whole-tree mypy fails where an "
        "unlocked install resolved 3.x"
    ),
}


def _declarations() -> dict[str, list[str]]:
    """Every requirement list in pyproject: the core dependencies and each extra."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    return {"dependencies": project["dependencies"], **project["optional-dependencies"]}


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


def test_the_client_the_openai_bound_protects_is_still_handed_to_the_sdk() -> None:
    """Vacuity floor for the openai bound: it argues from the providers passing core's own HTTP
    client to ``AsyncOpenAI``. If none does any more, re-derive the bound instead of keeping it."""
    passing = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in source_index.package_files()
        if "openai.AsyncOpenAI(" in (text := source_index.text(path))
        and "http_client=http_client(" in text
    ]
    assert passing, (
        "no module hands AsyncOpenAI the egress-guarded http_client() any more — re-derive "
        "whether the openai<3 bound is still needed instead of carrying it on faith"
    )


#: The workflow whose matrix is the *only* thing that runs the suite on a given interpreter.
_BREADTH_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "breadth.yml"


def _ci_verified_python_minors() -> set[tuple[int, int]]:
    """Every `(major, minor)` the breadth legs really run the suite on.

    Reads the workflow rather than a second hand-kept list, because a hand-kept copy is the
    thing that drifts — and a rail comparing metadata against a stale copy of the matrix
    would pass while the matrix said something else.
    """
    workflow = yaml.safe_load(_BREADTH_WORKFLOW.read_text(encoding="utf-8"))
    declared: list[str] = []
    for job in workflow["jobs"].values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        declared += [str(value) for value in (matrix.get("python") or [])]
        # `include` legs add real jobs, so an interpreter reached only there is still
        # verified. Missing them would let the bound narrow below what CI proves.
        declared += [
            str(entry["python"])
            for entry in (matrix.get("include") or [])
            if isinstance(entry, dict) and "python" in entry
        ]
    minors: set[tuple[int, int]] = set()
    for value in declared:
        # An exact patch (`3.13.16`) is verified at its minor.
        major, minor = value.split(".")[:2]
        minors.add((int(major), int(minor)))
    return minors


def test_the_matrix_this_rail_reads_still_declares_interpreters() -> None:
    """Vacuity floor: an empty parse would make both rails below unfailable.

    A renamed job, a moved `strategy:` block or a matrix expressed some other way all yield
    an empty set, and `max()` of nothing raises — but a *silently* empty set would let the
    two rails agree with anything. Asserting the census first is what keeps them honest.
    """
    verified = _ci_verified_python_minors()
    assert len(verified) >= 2, (
        f"{_BREADTH_WORKFLOW.name} yielded {sorted(verified)} — the matrix parse collapsed, so "
        "the requires-python and classifier rails below are comparing against nothing"
    )


def test_requires_python_is_bounded_to_the_interpreters_ci_verifies() -> None:
    """The declared support range equals the matrix, on BOTH ends.

    Not "has some upper bound": a bound that is merely present can still promise an
    interpreter nothing runs on. The oldest verified minor sets the floor and the first
    UNVERIFIED minor sets the ceiling, so the claim and its proof move together.
    """
    verified = _ci_verified_python_minors()
    oldest, newest = min(verified), max(verified)
    expected = f">={oldest[0]}.{oldest[1]},<{newest[0]}.{newest[1] + 1}"
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        declared = str(tomllib.load(fh)["project"]["requires-python"])
    assert declared.replace(" ", "") == expected, (
        f"pyproject declares requires-python = {declared!r} but {_BREADTH_WORKFLOW.name} "
        f"verifies {sorted(verified)}, so the honest spec is {expected!r}. Widening the "
        "range means adding the interpreter to the matrix in the SAME change and making it "
        "pass — an install that pip permits and nothing tests is how 3.14 shipped a "
        "connector-pack fence that refuses every import."
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


def test_python_classifiers_name_exactly_the_verified_interpreters() -> None:
    """The classifiers are the same promise in a second place, so they get the same rail.

    PyPI renders these as the supported-version list, so a stray `3.14` here advertises
    support just as loudly as `requires-python` does — and a *missing* one under-sells an
    interpreter CI already pays to verify. Both directions are a failure.
    """
    verified = _ci_verified_python_minors()
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        classifiers = list(tomllib.load(fh)["project"]["classifiers"])
    prefix = "Programming Language :: Python :: "
    declared = {
        tuple(int(part) for part in value.removeprefix(prefix).split("."))
        for value in classifiers
        if value.startswith(prefix) and "." in value.removeprefix(prefix)
    }
    assert declared == verified, (
        f"Python classifiers name {sorted(declared)} but {_BREADTH_WORKFLOW.name} verifies "
        f"{sorted(verified)} — every version-specific classifier must be an interpreter the "
        "suite actually runs on, and every verified interpreter must be advertised"
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


#: A distribution whose CEILING a behaviour needs, wherever pyproject declares the dependency
#: that needs it: that dependent, the newest release known to work, the first one that breaks it,
#: and why. The dependent does not bound it itself, so pyproject has to.
_MUST_KEEP_A_CEILING = {
    "av": (
        "faster-whisper",
        "18.1.0",
        "19.0.0",
        "PyAV 19 removed `av.open`'s `metadata_errors` keyword, which faster-whisper's "
        "decode_audio passes on every call (every release from 1.0 through 1.2.1 does), so "
        "every transcription fails with a TypeError",
    ),
}


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


#: Ceilings held by a comment beside them in pyproject rather than by a rail above: croniter's
#: (the cron grammar; ``tests/test_cron_expr_corpus.py`` asserts the specifier) and the lint
#: tools', bounded to the major ``uv.lock`` holds so a local gate runs the major CI runs.
_HELD_BY_COMMENT = ("croniter", "black", "isort", "flake8", "mypy")


def _held_ceilings() -> dict[str, str]:
    """Every distribution whose upper bound is held on purpose, and that bound's version."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    held = {*_MUST_BE_BOUNDED, *_MUST_KEEP_A_CEILING, *_HELD_BY_COMMENT}
    ceilings: dict[str, set[str]] = {}
    for specs in _declarations().values():
        for spec in specs:
            req = Requirement(spec)
            name = canonicalize_name(req.name)
            if name in held:
                ceilings.setdefault(name, set()).update(
                    s.version for s in req.specifier if s.operator == "<"
                )
    missing = sorted(held - ceilings.keys())
    assert not missing, f"pyproject.toml no longer declares {missing}; re-derive these holds"
    for name, versions in ceilings.items():
        assert len(versions) == 1, f"{name} is declared with ceilings {sorted(versions)}"
    return {name: versions.pop() for name, versions in ceilings.items()}


def _dependabot_pip_ignores() -> dict[str, list]:
    config = yaml.safe_load((_REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    [pip] = [u for u in config["updates"] if u["package-ecosystem"] == "pip"]
    ignores: dict[str, list] = {}
    for entry in pip.get("ignore") or []:
        name = entry["dependency-name"]
        assert name not in ignores, f"dependabot.yml ignores {name!r} twice"
        assert "update-types" not in entry, (
            f"dependabot.yml ignores {name!r} by update-types, which does not stop a PR that "
            "widens a constraint; use versions: ['>=N']"
        )
        ignores[name] = entry.get("versions")
    return ignores


def _exact_build_pins() -> dict[str, str]:
    """Every ``[build-system] requires`` entry pinned with ``==``, and its version.

    An exact pin is held equal to the version ``uv.lock`` resolves
    (``tests/test_verify_wheel_contract.py`` does it for setuptools), and Dependabot moves the pin
    without the lock, so its bump can never pass. Such a pin is bumped by hand with the lock and
    ignored outright in dependabot.yml."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        requires = tomllib.load(fh)["build-system"]["requires"]
    pins: dict[str, str] = {}
    for spec in requires:
        req = Requirement(spec)
        exact = [s.version for s in req.specifier if s.operator == "=="]
        if exact:
            pins[canonicalize_name(req.name)] = exact[0]
    return pins


def test_dependabot_never_proposes_widening_a_held_ceiling() -> None:
    """Dependabot cannot read the comments that argue a bound, so a held ceiling ``<N`` without
    a ``versions: [">=N"]`` ignore comes back as a widening PR every week, unmergeable on the
    merits. Both directions: every held ceiling is ignored at its own major, and every ignore
    names a ceiling still held, so moving a bound on purpose moves its ignore with it.

    An exact build-system pin is the other kind of hold: it is ignored outright, with no
    ``versions:``, because no version of it may arrive without the lock."""
    ceilings = _held_ceilings()
    pins = _exact_build_pins()
    assert pins, "[build-system] requires pins nothing exactly; re-derive the exact-pin ignores"
    ignores = _dependabot_pip_ignores()
    for name, version in sorted(pins.items()):
        assert name in ignores and ignores[name] is None, (
            f"[build-system] pins {name}=={version}, which only moves with uv.lock, but "
            f"dependabot.yml's pip ignore list has {ignores.get(name, 'no entry')!r} for it: add "
            f"`- dependency-name: {name}` with no `versions:`, so no bump of it is ever proposed"
        )
    ignores = {name: v for name, v in ignores.items() if name not in pins}
    for name, ceiling in sorted(ceilings.items()):
        assert name in ignores, (
            f"pyproject holds {name}<{ceiling} on purpose but dependabot.yml's pip ignore list "
            f"has no entry for it: add `- dependency-name: {name}` with "
            f'`versions: [">={ceiling}"]`'
        )
        assert ignores[name] == [f">={ceiling}"], (
            f"dependabot.yml ignores {name} at {ignores[name]} but pyproject holds "
            f'{name}<{ceiling}: the entry must be versions: [">={ceiling}"]'
        )
    stale = sorted(ignores.keys() - ceilings.keys())
    assert not stale, f"dependabot.yml ignores {stale}, which pyproject holds no ceiling on"


def test_the_python_image_is_never_proposed_past_the_supported_interpreters() -> None:
    """The gateway image runs the interpreter its ``python`` base ships, and ``requires-python``
    refuses every minor from its ceiling up, so an image Dependabot moves to that minor builds an
    image that cannot install the project. The docker block's ignore for the python base starts
    exactly at that ceiling, so widening the support range moves the ignore in the same change."""
    from packaging.specifiers import SpecifierSet

    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        declared = SpecifierSet(str(tomllib.load(fh)["project"]["requires-python"]))
    [ceiling] = [s.version for s in declared if s.operator == "<"]
    config = yaml.safe_load((_REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    [docker] = [u for u in config["updates"] if u["package-ecosystem"] == "docker"]
    entries = [e for e in docker.get("ignore") or [] if e["dependency-name"] == "*python"]
    assert len(entries) == 1, (
        f"dependabot.yml's docker block needs exactly one `*python` ignore (found {len(entries)}): "
        f"without one it proposes a python base past requires-python {declared}; "
        f'add `versions: [">={ceiling}"]`'
    )
    assert entries[0].get("versions") == [f">={ceiling}"] and "update-types" not in entries[0], (
        f"dependabot.yml ignores the python image at {entries[0]} but requires-python is "
        f'{declared}: the entry must be versions: [">={ceiling}"]'
    )
