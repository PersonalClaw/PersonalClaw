"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_manifest_secret_fields.py``

Its checks:
``tests/rails/test_app_manifest_secret_fields_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The resolution of "where do first-party apps live" has ONE owner already — reuse it rather
# than re-deriving it here. A second answer to that question is the defect shape this rail
# exists to catch, and issue 1777 is what a wrong answer costs.
from app_roots import _app_roots


def _all_setting_names(props: dict[str, Any] | None, trail: str = "") -> list[str]:
    """Every settings field name under *props*, credential-shaped or not, as dotted paths.

    Same traversal as :func:`_credential_fields` minus the classification, so the two cannot
    disagree about which fields exist. Used to pin the anchored pattern's snake_case
    precondition over the whole corpus rather than over the credential subset, which is the
    only place a camelCase key would be invisible.
    """
    out: list[str] = []
    for key, spec in (props or {}).items():
        if not isinstance(spec, dict):
            continue
        out.append(trail + key)
        if spec.get("type") == "object" and isinstance(spec.get("properties"), dict):
            out.extend(_all_setting_names(spec["properties"], trail + key + "."))
    return out


def _schema_property_blocks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Both places a first-party manifest may declare settings: top level and under ``provider``."""
    blocks: list[dict[str, Any]] = []
    top = manifest.get("settingsSchema")
    if isinstance(top, dict) and isinstance(top.get("properties"), dict):
        blocks.append(top["properties"])
    provider = manifest.get("provider")
    if isinstance(provider, dict):
        inner = provider.get("settingsSchema")
        if isinstance(inner, dict) and isinstance(inner.get("properties"), dict):
            blocks.append(inner["properties"])
    return blocks


def _manifests() -> list[Path]:
    """Every ``app.json`` under every resolved app root, deduped by resolved path."""
    seen: set[Path] = set()
    out: list[Path] = []
    for root in _app_roots():
        for path in sorted(root.glob("*/app.json")):
            try:
                key = path.resolve()
            except OSError:  # pragma: no cover - unreadable path
                key = path
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out
