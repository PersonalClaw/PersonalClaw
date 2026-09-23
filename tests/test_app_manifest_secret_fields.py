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

**THIS MODULE IS THE CANONICAL STATEMENT OF "credential-shaped", as of 2026-09-23.** It was
not, and that was the bug. Core matched an *unanchored substring* list containing ``token``,
so ``max_tokens`` — an integer request parameter, ``{"type": "integer", "minimum": 0}`` —
was credential-shaped, and the corpus arm above reddened on ``bedrock-models: max_tokens``
and ``claude-subscription: max_tokens``. Neither remedy the failure message offers applies:
marking an integer ``x-meta.sensitive`` makes ``appConfigForm.tsx`` render it
``type="password"`` with write-only blank-input behaviour (masking a non-secret and making a
normal numeric setting un-editable), and it cannot be renamed because ``max_tokens`` is the
provider API's own parameter name.

That red is the DEFAULT outcome for a dual-clone workspace, not something you opt into:
``_app_roots()`` resolves the first-party apps clone as a sibling of the checkout. A CI
runner has no sibling, so CI stayed green and only humans and agent lanes ever saw it —
the worst place for a false red, because it reads as product breakage or gets "fixed" by
annotating a non-secret as a credential.

The apps repo had already solved this and documented the divergence in its own comment
("An unanchored substring list — the shape core's rail uses — flags ``max_tokens`` and
would red on a normal numeric field"), which left the org holding **two answers to one
question** — the defect shape this very module says the rail exists to prevent, and what
issue 1777 is cited for. So core adopts the sibling's ``CRED_CLASS`` verbatim rather than
inventing a third answer, and states it here, once. An exception list for ``max_tokens``
would have been that third answer.

**Why the pattern is anchored, and why it cannot be narrowed further.** ``_CRED_CLASS``
matches a credential noun only at the END of the name (or a whole-name special case), so
``max_tokens``, ``token_limit``, ``api_keys`` and ``auth_mode`` do not match. Measured, this
is not adjustable: dropping ``key`` from the alternation to stop matching ``ssh_key`` would
also stop matching ``api_key`` (19 live sites) and ``secret_access_key`` — real credentials.
A name that is a credential noun in the wrong position is handled as a *subject* exemption
(``_PATH_VALUED_EXEMPT``), never by loosening the pattern.

**The false-negative census that licensed this change, measured 2026-09-23** over all 100
manifests (31 core bundled + 69 ``PersonalClawApps``), 257 setting-field occurrences, 147
distinct leaf names, classified under both patterns. The difference runs in BOTH directions
and is one field each way:

* **stops being flagged:** ``max_tokens`` only (``bedrock-models``, ``claude-subscription``)
  — an integer ceiling, not a credential. Nothing that is or could hold a credential loses
  flagging; the 8 names in the intersection (``api_key``, ``access_key_id``,
  ``secret_access_key``, ``session_token``, ``app_token``, ``bot_token``, ``hf_token``,
  ``token_credential`` — 28 sites) are untouched.
* **starts being flagged:** ``ssh_key`` (``rsync-sync``) — core's substring list held
  ``api_key``/``access_key`` but no bare ``key``, so core never flagged it; the anchored
  pattern does. Its value is a *path* handed to ``ssh`` ("The key itself is never read by
  PersonalClaw — only handed to ssh by path"), so masking it would hide a path the user must
  verify. The sibling gate already exempts exactly this pair with exactly this reason, so
  adopting rule 4 means adopting both of its halves — pattern *and* exemption. Adopting only
  the pattern would trade the ``max_tokens`` false red for an identical ``ssh_key`` one.

Net effect on what core requires masked: ``max_tokens`` stops being required, and nothing
else moves in either direction.

**Inherited limitation, named because it is real.** The superseded substring list matched
case-insensitively on the whole name, so it fired on ``apiKey``/``clientSecret``/
``refreshToken``; the anchored pattern does not (``key`` must follow ``_`` or start). That
is sound only while settings keys stay snake_case — measured true, 0 of 147 distinct names
are anything else — so the precondition is pinned by a test below rather than left to rot.
Widening the pattern to cover camelCase would have reopened the two-answers problem.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

# The resolution of "where do first-party apps live" has ONE owner already — reuse it rather
# than re-deriving it here. A second answer to that question is the defect shape this rail
# exists to catch, and issue 1777 is what a wrong answer costs.
from tests.test_apps_import_boundary import _app_roots

#: What makes a settings key credential-shaped. **This is the org's one definition** — the
#: module docstring explains why it lives here and what the second copy cost. The credential
#: noun must END the name (or be the whole-name special case), so a qualifier or a plural
#: cannot match: `max_tokens`, `token_limit`, `api_keys` and `auth_mode` are normal fields.
#: `access_key_id` is spelled out because it ends in `_id` yet is half of an AWS credential
#: pair. Byte-identical to `PersonalClawApps`' `check_settings_schema_posture.py::CRED_CLASS`,
#: the gate that actually enforces this where the manifests live — deliberately, so the two
#: cannot drift again. Do not narrow it: dropping `key` also drops `api_key` (measured).
_CRED_CLASS = re.compile(
    r"((^|_)(key|token|secret|password|passphrase|credential)$)|(^access_key_id$)",
    re.I,
)

#: `(app, field)` pairs whose value is a PATH to a credential, not the credential. Masking
#: these would hide something the user must be able to read back, so they are exempted by
#: SUBJECT — never by loosening `_CRED_CLASS`, which is the one place the rule could be
#: weakened invisibly. Mirrors the sibling gate's `PATH_VALUED_EXEMPT` for the same reason.
#: Keep it tiny and always give the reason; a stale entry reds (floor in the corpus test).
_PATH_VALUED_EXEMPT = {
    # Handed to `ssh` by path; the app never reads the key bytes. Its own help says so.
    ("rsync-sync", "ssh_key"),
}


def _is_credential_shaped(key: str) -> bool:
    return bool(_CRED_CLASS.search(key))


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
    apps_in_scope: set[str] = set()
    exempt_seen: set[tuple[str, str]] = set()
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - malformed manifest
            pytest.fail(f"{path} is not readable JSON: {exc}")
        if not isinstance(manifest, dict):
            continue
        app = path.parent.name
        apps_in_scope.add(app)
        for props in _schema_property_blocks(manifest):
            for name, marked in _credential_fields(props):
                credential_fields_seen += 1
                if (app, name) in _PATH_VALUED_EXEMPT:
                    exempt_seen.add((app, name))
                    continue
                if not marked:
                    unmarked.append(f"{app}: {name}")

    # An exemption that outlives its subject silently widens the rule, so it must red
    # instead. Only checkable for apps actually in scope — a bare CI clone resolves none of
    # the exempt apps, and asserting there would red every clean clone.
    stale = {
        (app, field)
        for app, field in _PATH_VALUED_EXEMPT
        if app in apps_in_scope and (app, field) not in exempt_seen
    }
    assert not stale, (
        "a path-valued exemption no longer resolves to a credential-shaped field in an app "
        f"that IS in scope: {sorted(stale)}. Delete the entry rather than leaving it to "
        "silently widen the rule."
    )

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


# ---------------------------------------------------------------------------
# Pattern floors: BOTH directions, because anchoring the pattern LOOSENED the rail.
#
# Nothing pinned `_CRED_HINTS`' membership or any name's classification before 2026-09-23
# — measured, with a fired positive control — so replacing the substring list with the
# anchored pattern turned nothing red and no existing test would have noticed a mistake in
# either direction. The negative half below (false positives stop being flagged) is the
# change's point; the positive half (real credentials keep being flagged) is what makes a
# loosened security rail reviewable. Neither half is sufficient alone.
# ---------------------------------------------------------------------------


def test_a_genuine_credential_name_is_still_flagged() -> None:
    """The half that guards the loosening: anchoring must not drop a real credential.

    Every name here either appears in the live catalog (the 8 intersection names the
    2026-09-23 census measured across 28 sites) or is the canonical spelling of a secret a
    future app would plausibly declare. If any of these stops matching, the rail has been
    narrowed into shipping a cleartext credential — the exact failure the superseded
    substring list accepted false positives in order to avoid.
    """
    genuine = [
        # measured live in the catalog
        "api_key",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "app_token",
        "bot_token",
        "hf_token",
        "token_credential",
        # canonical spellings not currently in the catalog
        "slack_token",
        "password",
        "client_secret",
        "refresh_token",
        "passphrase",
        "signing_key",
        "webhook_secret",
        "db_password",
    ]
    missed = [name for name in genuine if not _is_credential_shaped(name)]
    assert not missed, (
        "the credential-shape pattern no longer flags names that DO hold credentials, so "
        f"these would ship unmasked with nothing objecting: {missed}"
    )


def test_the_documented_false_positives_are_not_flagged() -> None:
    """The half the fix exists for: a credential noun in a non-terminal position is a field.

    ``max_tokens`` is the field that reddened the corpus arm on every dual-clone workspace
    while core CI stayed green. It is ``{"type": "integer", "minimum": 0}`` in both
    ``bedrock-models`` and ``claude-subscription``, and it is the provider API's own
    parameter name, so it can be neither masked nor renamed.
    """
    not_credentials = [
        "max_tokens",  # the reported defect: an integer ceiling
        "token_limit",
        "api_keys",
        "auth_mode",
        "context_window",
        "max_turns",
        "default_model",
        "embedding_model",
        "timeout_secs",
        "keyring_backend",
        "secrets_dir",
        "tokenizer",
    ]
    flagged = [name for name in not_credentials if _is_credential_shaped(name)]
    assert not flagged, (
        "the credential-shape pattern flags fields that hold no credential. Marking one "
        '`x-meta.sensitive` renders it type="password" with write-only blank-input '
        f"behaviour, which masks a non-secret and makes it un-editable: {flagged}"
    )


def test_a_path_valued_exemption_is_a_subject_exemption_not_a_pattern_hole() -> None:
    """``ssh_key`` IS credential-shaped; only the named ``(app, field)`` pair is exempt.

    The distinction is the whole reason the exemption is a pair and not a pattern tweak. The
    same field name in any other app still has to carry the flag, and the pattern itself is
    never weakened — the one place the rule could be loosened invisibly.
    """
    assert _is_credential_shaped("ssh_key")
    assert _credential_fields({"ssh_key": {"type": "string"}}) == [("ssh_key", False)]
    assert ("rsync-sync", "ssh_key") in _PATH_VALUED_EXEMPT
    assert ("some-other-app", "ssh_key") not in _PATH_VALUED_EXEMPT


def test_settings_keys_are_snake_case_which_the_anchored_pattern_depends_on() -> None:
    """The anchored pattern's precondition, pinned so it cannot lapse silently.

    ``_CRED_CLASS`` requires the credential noun to follow ``_`` or start the name, so it
    does NOT match ``apiKey``, ``clientSecret`` or ``refreshToken`` — names the superseded
    substring list did match. That is safe only while every settings key is snake_case,
    which was measured true on 2026-09-23 (0 of 147 distinct leaf names were anything else).
    If a camelCase key ever lands, this reds and names the consequence rather than letting
    a real credential slip past the pattern unnoticed.

    Holds in a bare CI clone: the bundled tree carries 53 setting fields over 14 manifests.
    """
    snake = re.compile(r"^[a-z][a-z0-9_]*$")
    offenders: list[str] = []
    names_seen = 0
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - covered by the arm above
            continue
        if not isinstance(manifest, dict):
            continue
        for props in _schema_property_blocks(manifest):
            for dotted in _all_setting_names(props):
                names_seen += 1
                leaf = dotted.rsplit(".", 1)[-1]
                if not snake.match(leaf):
                    offenders.append(f"{path.parent.name}: {dotted}")

    assert names_seen, (
        "no settings fields were examined, so this precondition proves nothing — the "
        "bundled tree should supply them in any clone"
    )
    assert not offenders, (
        "a settings key is not snake_case, which the anchored credential pattern depends "
        "on: it matches a credential noun only after `_` or at the start, so `apiKey` and "
        "`clientSecret` would NOT be flagged and would ship unmasked. Rename the key to "
        f"snake_case (the catalog convention) rather than widening the pattern: {offenders}"
    )
