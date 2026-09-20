"""Unbounded `>=` version claims — for a dependency OR for the interpreter — stay bounded.

A `>=` spec with no upper bound is not a preference, it is a bet that every future major
release keeps the symbols this repo imports. `mcp` lost that bet: `mcp 2.0.0` renamed
`mcp.client.streamable_http.streamablehttp_client`, which `personalclaw/mcp_client.py`
imports by name for the streamable-HTTP transport. The repo's own venv had `mcp 1.28.1`
and was green, so nothing here failed — and **CI installs from the lockfile, so CI could
not see it either**. Only a fresh `pip install 'personalclaw[mcp]'` resolved 2.0.0 and
broke, which is the one path a new user takes.

This rail asserts an upper bound on the extras whose modules are imported for a *named*
attribute. It deliberately does not police every extra: a bound costs real maintenance
(someone must widen it), so it is spent where a rename is known to break an import rather
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
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: extra name -> the distribution inside it that must carry an upper bound, and why.
_MUST_BE_BOUNDED = {
    "mcp": (
        "mcp",
        "mcp 2.0.0 renamed mcp.client.streamable_http.streamablehttp_client, which "
        "src/personalclaw/mcp_client.py imports by name",
    ),
}


def _optional_dependencies() -> dict[str, list[str]]:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["optional-dependencies"]


def test_import_sensitive_extras_declare_an_upper_bound() -> None:
    extras = _optional_dependencies()
    unbounded: list[str] = []
    for extra, (dist, reason) in _MUST_BE_BOUNDED.items():
        assert extra in extras, f"extra {extra!r} disappeared from pyproject.toml"
        specs = [s for s in extras[extra] if s.split(">=")[0].split("[")[0].strip() == dist]
        assert specs, f"extra {extra!r} no longer declares {dist!r}"
        for spec in specs:
            if "<" not in spec and "==" not in spec and "~=" not in spec:
                unbounded.append(f"{extra}: {spec!r} — {reason}")
    assert not unbounded, (
        "these specs would let a FRESH install resolve a major release that renamed an "
        "imported symbol (CI installs from the lockfile and will not catch it): "
        + "; ".join(unbounded)
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


#: The workflow whose matrix is the *only* thing that runs the suite on a given interpreter.
_FULL_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "full.yml"


def _ci_verified_python_minors() -> set[tuple[int, int]]:
    """Every `(major, minor)` the full matrix really runs the suite on.

    Reads the workflow rather than a second hand-kept list, because a hand-kept copy is the
    thing that drifts — and a rail comparing metadata against a stale copy of the matrix
    would pass while the matrix said something else.
    """
    workflow = yaml.safe_load(_FULL_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["matrix-shard"]["strategy"]["matrix"]
    declared = [str(value) for value in (matrix.get("python") or [])]
    # `include` legs add real jobs (the arm64 pair), so an interpreter reached only there is
    # still verified. Missing them would let the bound narrow below what CI proves.
    declared += [
        str(entry["python"])
        for entry in (matrix.get("include") or [])
        if isinstance(entry, dict) and "python" in entry
    ]
    minors: set[tuple[int, int]] = set()
    for value in declared:
        major, _, minor = value.partition(".")
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
        f"{_FULL_WORKFLOW.name} yielded {sorted(verified)} — the matrix parse collapsed, so "
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
        f"pyproject declares requires-python = {declared!r} but {_FULL_WORKFLOW.name} "
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
        f"Python classifiers name {sorted(declared)} but {_FULL_WORKFLOW.name} verifies "
        f"{sorted(verified)} — every version-specific classifier must be an interpreter the "
        "suite actually runs on, and every verified interpreter must be advertised"
    )
