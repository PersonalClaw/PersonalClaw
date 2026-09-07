"""A credential-shaped setting in a first-party app manifest must be ``x-meta.sensitive``.

Core masks a provider/app setting on the wire only when the manifest marks it
``x-meta.sensitive``. That flag is the sole input to every masker core has — verified by
reading them: :func:`personalclaw.dashboard.handlers.apps._sensitive_field_names` (which
feeds ``_mask_secret_config``, whose own comment names the consequence, "leaving the
backend in cleartext on every config-panel open (#43)"),
:func:`personalclaw.config.validation._is_sensitive_path`, and the frontend's
``pages/apps/appConfigForm.tsx``, which decides ``type="password"`` and the write-only
blank-input behaviour from the same flag. So a manifest that declares an ``api_key``
*without* it gets no masking anywhere, on any route: the maskers are correct and the
**data** is wrong. Nothing objected to that before this rail.

Measured 2026-09-06 over the 54 first-party manifests: 25 credential-shaped fields carried
the flag and **one did not** — ``openai-tools.api_key``. That one matters more than its
count suggests, because ``openai-tools`` is one of only two apps a cold
Settings → Providers load actually fetches an instance config for, which made it the single
browser-reachable cleartext credential in the set.

**What this rail can and cannot prove — stated up front, because its sibling's history is a
warning.** ``tests/test_apps_import_boundary.py`` had *never run anywhere* (issue 1777): it
resolved a path that was always absent and module-skipped, so the one lint enforcing the
provider-agnostic-core tenet reported "skipped" on every run while a violation could have
landed at any time. This rail reuses that file's :func:`_app_roots` so it inherits the fixed
resolution, **including the bundled tree that is present in every clone**.

But the bundled tree carries **zero** credential-shaped fields (measured: 30 manifests, 0
fields — they are all internal action/tool apps with no external provider). So the two halves
below do different jobs and both are required:

* the **planted-fixture** tests prove the *detector* works, deterministically, in any clone;
* the **corpus** test proves the *real manifests* are clean, and gets stronger wherever a
  first-party apps clone is actually present.

Consequently this file asserts ``manifests_scanned > 0`` (satisfiable everywhere — the bundled
tree guarantees it, so a broken resolver or renamed tree reds). It deliberately does **not**
assert ``marked_fields > 0``, which would red every clean CI clone. The planted fixtures carry
that weight instead.

**Corrected 2026-09-07 — the earlier version of this note was not enough.** It said the corpus
arm "gets stronger wherever a first-party apps clone is present", which is true and was being
read as sufficient. It is not: with no clone resolved, that arm examined **zero** credential
fields and reported a pass, which is indistinguishable from a clean catalog. It now SKIPS in
that case, naming what was not checked. And because ``_app_roots()`` resolves the clone as a
sibling of this checkout, a CI runner never has one — so **core structurally cannot gate the
apps repository at all.** The enforcing gate is ``PersonalClawApps``'
``.github/scripts/check_settings_schema_posture.py`` rule 4, in the CI of the repo that owns
the manifests. What lives here is the detector plus its planted floors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# The resolution of "where do first-party apps live" has ONE owner already — reuse it rather
# than re-deriving it here. A second answer to that question is the defect shape this rail
# exists to catch, and issue 1777 is what a wrong answer costs.
from tests.test_apps_import_boundary import _app_roots

#: Substrings that make a settings key credential-shaped. Matched case-insensitively on the
#: key NAME, so it fires on `api_key`, `apiKey`, `refresh_token`, `client_secret` alike.
#: Deliberately broad: a false positive costs one `x-meta.sensitive` annotation, while a false
#: negative ships a cleartext credential.
_CRED_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "access_key",
)


def _is_credential_shaped(key: str) -> bool:
    low = key.lower()
    return any(hint in low for hint in _CRED_HINTS)


def _credential_fields(props: dict[str, Any] | None, trail: str = "") -> list[tuple[str, bool]]:
    """Every credential-shaped field under *props*, as ``(dotted_name, is_marked_sensitive)``.

    Recurses into nested ``object`` properties. That recursion is the reason this is a parser
    and not a grep: a nested schema hides the field from any line-oriented scan, and the
    ``x-meta`` block sits on a different line from the key it belongs to.
    """
    out: list[tuple[str, bool]] = []
    for key, spec in (props or {}).items():
        if not isinstance(spec, dict):
            continue
        if _is_credential_shaped(key):
            meta = spec.get("x-meta")
            marked = bool(isinstance(meta, dict) and meta.get("sensitive"))
            out.append((trail + key, marked))
        if spec.get("type") == "object" and isinstance(spec.get("properties"), dict):
            out.extend(_credential_fields(spec["properties"], trail + key + "."))
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


# ---------------------------------------------------------------------------
# Vacuity floor: the corpus scan must actually have a corpus.
# ---------------------------------------------------------------------------


def test_the_scan_finds_manifests_at_all() -> None:
    """A rail that silently scanned nothing would report the same green as a clean tree.

    The bundled app tree ships inside this repo, so this holds in a bare CI clone with no
    sibling apps checkout. If it ever fails, the resolver or the tree layout moved — which is
    exactly the failure that made the import-boundary lint inert for its whole life.
    """
    found = _manifests()
    assert found, (
        "no first-party app manifests were found, so the corpus assertion below proves "
        f"nothing. Roots searched: {[str(r) for r in _app_roots()]}"
    )


# ---------------------------------------------------------------------------
# The invariant, over the real corpus.
# ---------------------------------------------------------------------------


def test_no_credential_field_ships_unmarked() -> None:
    """Every credential-shaped setting in every reachable manifest carries ``x-meta.sensitive``.

    Without the flag, no masker can know the value is a secret, so no route masks it and the
    credential goes back to the caller in cleartext.

    **This test SKIPS rather than passing when no credential-shaped field is in scope, and the
    distinction is the whole point.** Core's bundled tree carries zero credential-shaped fields
    by design (30 manifests, all internal action/tool apps with no external provider), and
    ``_app_roots()`` resolves the first-party apps clone as a SIBLING of this checkout — which
    does not exist on a CI runner. So in CI this scan examines a corpus that structurally cannot
    fail, and an empty ``unmarked`` list there means "nothing was checked", not "everything is
    clean". Reporting that as a pass is the same inert-lint failure as issue 1777, just one
    layer up: green from matching zero sites.

    Measured 2026-09-07: run from a worktree, this passed with **0 credential fields examined**;
    run with ``PERSONALCLAW_FIRST_PARTY_APPS_DIR`` pointed at the real clone, it correctly
    reddened on ``openai-tools.api_key`` — a browser-reachable cleartext bearer token.

    **Core cannot gate another repository, so the real gate is not here.** It is
    ``PersonalClawApps``' own ``.github/scripts/check_settings_schema_posture.py`` rule 4, which
    runs in the CI of the repo that owns the manifests. This test stays as the detector's
    corpus arm for anyone running with both clones present; the planted-fixture tests below are
    what actually hold everywhere.
    """
    unmarked: list[str] = []
    credential_fields_seen = 0
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - malformed manifest
            pytest.fail(f"{path} is not readable JSON: {exc}")
        if not isinstance(manifest, dict):
            continue
        for props in _schema_property_blocks(manifest):
            for name, marked in _credential_fields(props):
                credential_fields_seen += 1
                if not marked:
                    unmarked.append(f"{path.parent.name}: {name}")

    if credential_fields_seen == 0:
        pytest.skip(
            "no credential-shaped field is in scope, so this assertion would pass by matching "
            "nothing — which is indistinguishable from a clean catalog. Core's bundled tree has "
            "none by design and no first-party apps clone was resolved "
            f"(roots searched: {[str(r) for r in _app_roots()]}). Set "
            "PERSONALCLAW_FIRST_PARTY_APPS_DIR to a real clone to run this arm for real. The "
            "enforcing gate is PersonalClawApps' check_settings_schema_posture.py rule 4, which "
            "runs where the manifests actually live."
        )

    assert not unmarked, (
        "a credential-shaped app setting is not marked `x-meta.sensitive`, so nothing will "
        'mask it on any route — add `"x-meta": {"sensitive": true}` to each field below, '
        "or rename it if it does not actually hold a credential:\n  "
        + "\n  ".join(sorted(unmarked))
    )


# ---------------------------------------------------------------------------
# Detector floors: planted fixtures, so these hold in ANY clone.
# ---------------------------------------------------------------------------


def test_the_detector_flags_a_planted_unmarked_field() -> None:
    """The load-bearing floor. If this passed vacuously the corpus test would too."""
    planted = {"api_key": {"type": "string", "title": "API key"}}
    assert _credential_fields(planted) == [("api_key", False)]


def test_the_detector_accepts_a_planted_marked_field() -> None:
    """Negative twin: a correctly-marked field must NOT be reported, or the rail cries wolf."""
    planted = {"api_key": {"type": "string", "x-meta": {"sensitive": True}}}
    assert _credential_fields(planted) == [("api_key", True)]


def test_a_nested_credential_field_is_walked() -> None:
    """A grep would miss this, which is why the scan parses instead of matching lines."""
    planted = {
        "auth": {
            "type": "object",
            "properties": {"refresh_token": {"type": "string"}},
        }
    }
    assert _credential_fields(planted) == [("auth.refresh_token", False)]


def test_a_non_credential_field_is_ignored() -> None:
    """Vacuity in the other direction: the matcher must not flag every setting there is."""
    planted = {"default_model": {"type": "string"}, "timeout_s": {"type": "integer"}}
    assert _credential_fields(planted) == []
