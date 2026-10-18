"""The source-tree checks split from
``tests/test_native_capability_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import functools
import io
import json
import tokenize

import pytest
import source_index
from native_capability_contract_shared import _SRC_DIR

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    bundle_modules,
    is_bundle_relative,
    native_bundle_dirs,
)


@functools.lru_cache(maxsize=1)
def _core_sources() -> dict[str, str]:
    """``{repo-relative path: source with # comments stripped}`` for every core module
    outside the bundled tree. Comments are dropped because a core module may legitimately
    document that a capability moved into a bundle; code that REACHES for it is the defect.
    """
    out: dict[str, str] = {}
    for path in source_index.package_files():
        if NATIVE_DIR in path.parents or "egg-info" in str(path):
            continue
        text = source_index.text(path)
        try:
            tokens = list(tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline))
        except (SyntaxError, tokenize.TokenError):
            out[str(path.relative_to(_SRC_DIR))] = text
            continue
        out[str(path.relative_to(_SRC_DIR))] = "\n".join(
            t.string for t in tokens if t.type != tokenize.COMMENT
        )
    return out


def _bundle_owned() -> list[tuple[str, AppManifest]]:
    """Every bundled app whose ``implementation`` names a bundle-local module."""
    out: list[tuple[str, AppManifest]] = []
    for bundle in native_bundle_dirs():
        manifest = AppManifest.from_json_file(bundle / "app.json")
        for cfg in [manifest.provider, *manifest.providers]:
            if cfg and is_bundle_relative(cfg.implementation.rpartition(":")[0]):
                out.append((bundle.name, manifest))
                break
    return out


@pytest.mark.parametrize("bundle_name", [n for n, _ in _bundle_owned()])
def test_bundle_owned_capability_has_no_core_implementation(bundle_name):
    """No core module implements, imports or resolves a bundle-owned provider.

    This is the testable form of "a bundled app gains a provider method without core
    edits": if the capability still needed a core module (a factory to call, a class to
    import), that module would name the bundle or its module, and this rail reds. It is
    what stops the exemplar from quietly regressing to "declared in the bundle,
    implemented in core".
    """
    bundle = NATIVE_DIR / bundle_name
    module_stems = {m.stem for m in bundle_modules(bundle)}
    offenders: dict[str, list[str]] = {}
    for rel, text in _core_sources().items():
        hits = []
        for stem in module_stems:
            # A bundle's module is not on an importable package path, so a core
            # dependency on it can only be spelled as a path or a dotted pseudo-path.
            # Comments are stripped first: a core module is allowed to EXPLAIN that a
            # capability moved into a bundle — it just may not reach for it.
            if f"apps.native.{bundle_name}" in text or f"apps/native/{bundle_name}/{stem}" in text:
                hits.append(f"references {bundle_name}/{stem}")
        if hits:
            offenders[rel] = sorted(set(hits))
    assert not offenders, (
        f"core modules reference the bundle-owned code of {bundle_name!r} — the "
        "capability is not actually bundle-owned:\n" + json.dumps(offenders, indent=2)
    )
