"""The source-tree checks split from
``tests/test_dependency_bounds.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import tomllib

import source_index
import yaml
from dependency_bounds_shared import (
    _MUST_BE_BOUNDED,
    _MUST_KEEP_A_CEILING,
    _REPO_ROOT,
    _declarations,
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
