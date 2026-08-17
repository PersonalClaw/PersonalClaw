"""Optional-dependency specs whose imports name a version-sensitive symbol stay bounded.

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
"""

from __future__ import annotations

import tomllib
from pathlib import Path

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
