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

**"Credential-shaped" IS NOT DEFINED HERE — it is imported, and that is the fix, as of
2026-09-23.** The question "does this name hold a credential?" had three implementations:

1. this rail's ``_CRED_HINTS``, an *unanchored substring* list containing ``token``;
2. ``PersonalClawApps``' ``check_settings_schema_posture.py::CRED_CLASS``, an anchored regex;
3. :func:`personalclaw.apps.secret_fields.is_credential_field_name`, a word-and-position
   predicate — the one core's **maskers actually run** on every ``personalclaw config get``.

(1) made ``max_tokens`` — an integer request parameter, ``{"type": "integer", "minimum": 0}``
— credential-shaped, so the corpus arm reddened on ``bedrock-models: max_tokens`` and
``claude-subscription: max_tokens``. Neither remedy its message offers applies: marking an
integer ``x-meta.sensitive`` makes ``appConfigForm.tsx`` render it ``type="password"`` with
write-only blank-input behaviour (masking a non-secret and making a normal numeric setting
un-editable), and it cannot be renamed because ``max_tokens`` is the provider API's own
parameter name. That red was the DEFAULT outcome for a dual-clone workspace, not something
you opt into — ``_app_roots()`` resolves the apps clone as a sibling of the checkout, and a CI
runner has no sibling, so CI stayed green and only humans and agent lanes ever saw it. That
is the worst place for a false red: it reads as product breakage, or gets "fixed" by
annotating a non-secret as a credential.

Replacing (1) with a copy of (2) fixed the ``max_tokens`` instance and left the CLASS intact,
because a rail that duplicates a pattern is still a second answer. Measured: (2) flags
``sort_key`` and ``cache_key`` — dict lookups, identical in kind to ``max_tokens`` — and
misses ``aws_access_key_id``, ``credentials`` and ``apiKey``, which are real credentials (3)
does catch. So this rail now **imports (3)** and defines nothing. There is one predicate, it
lives beside the maskers that consume it, and a rail requiring an annotation now asks exactly
the question the masker will ask at runtime. A rail that demanded ``x-meta.sensitive`` on a
field core's masker would not mask was never enforcing core's policy.

**Why the shared predicate is not promoted to ``personalclaw.sdk.*``**, which the
provider-agnostic-core tenet would otherwise require of a cross-repo import: the only
would-be cross-repo consumer is the sibling's ``settings-schema-posture`` CI job, which is
deliberately a **pure-stdlib job with no core install** ("Pure stdlib — no core install", its
own ``ci.yml`` comment) so that a contributor runs byte-identically what CI runs. Making it
import core would put an apps-CI gate behind a ``git+https://…@main`` resolve of core. No app
*bundle* needs this predicate, so promoting it to the SDK would add public surface with zero
consumers. The sibling therefore keeps its own regex **deliberately**, and the divergence is
measured rather than trusted:
:func:`test_the_sibling_rails_verdicts_match_ours_over_the_live_catalog` imports the
sibling's pattern wherever both clones are resolved and asserts the two reach the same verdict
on every field that exists — the assertion whose absence let ``max_tokens`` diverge for 16
days. It skips, naming what it did not check, where no sibling is resolved.

**The delta this convergence makes to what core requires, measured 2026-09-23** over all 100
manifests (31 core bundled + 69 ``PersonalClawApps``), 257 setting occurrences, 108 distinct
leaf names, classified under all three:

* against the superseded copy of (2), on the live catalog: **one name moves**, ``ssh_key``
  (``rsync-sync``), which stops being credential-shaped. Its value is a *path* handed to
  ``ssh`` ("The key itself is never read by PersonalClaw — only handed to ssh by path"), so
  masking it would hide a path the user must verify — which is why the sibling flags it and
  then exempts it by subject. Under (3) a bare ``key`` needs a qualifier, so it is not flagged
  in the first place and core needs no exemption list to un-flag it. Same verdict, one
  mechanism instead of two.
* against (3) as the maskers already ran it, on the live catalog: **nothing moves.** The 8
  flagged names (``api_key``, ``access_key_id``, ``secret_access_key``, ``session_token``,
  ``app_token``, ``bot_token``, ``hf_token``, ``token_credential`` — 28 sites) are exactly the
  8 (3) flagged before. No runtime masking behaviour changes.
* ``api_keys`` moves the other way, into the genuine list below. (2) and the old (3) both
  called it ordinary; it is the plural of ``api_key``, and
  :func:`~personalclaw.apps.secret_fields.mask_secrets_in_document` already masks every string
  inside a credential-named list, so the rail asking for the annotation is what core does.

**Inherited limitation, named because it is real.** (3) splits on non-alphanumerics, so
``apiKey`` collapses to one word it recognises but ``clientSecret`` and ``refreshToken``
collapse to words it does not. That is sound only while settings keys stay snake_case —
measured true, 0 of 108 distinct names are anything else — so the precondition is pinned by a
test below rather than left to rot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from personalclaw.apps.secret_fields import is_credential_field_name

# The resolution of "where do first-party apps live" has ONE owner already — reuse it rather
# than re-deriving it here. A second answer to that question is the defect shape this rail
# exists to catch, and issue 1777 is what a wrong answer costs.
from tests.test_apps_import_boundary import _app_roots


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
        if is_credential_field_name(key):
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
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - malformed manifest
            pytest.fail(f"{path} is not readable JSON: {exc}")
        if not isinstance(manifest, dict):
            continue
        app = path.parent.name
        for props in _schema_property_blocks(manifest):
            for name, marked in _credential_fields(props):
                credential_fields_seen += 1
                if not marked:
                    unmarked.append(f"{app}: {name}")

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
# Predicate floors: BOTH directions, because converging the rail onto the masker's predicate
# LOOSENS it on some names and TIGHTENS it on others.
#
# Nothing pinned any name's classification from this rail's side before 2026-09-23 —
# measured, with a fired positive control — so every one of the three implementations could
# have been wrong in either direction with no test noticing. The negative half below (false
# positives stop being flagged) is the change's point; the positive half (real credentials
# keep being flagged) is what makes a loosened security rail reviewable. Neither half is
# sufficient alone. The predicate's own floors live beside it in
# `test_provider_config_secrets.py`; these are the manifest-shaped names, which that file
# has no reason to carry.
# ---------------------------------------------------------------------------


def test_a_genuine_credential_name_is_still_flagged() -> None:
    """The half that guards the loosening: convergence must not drop a real credential.

    Every name here either appears in the live catalog (the 8 names the 2026-09-23 census
    measured across 28 sites) or is the canonical spelling of a secret a future app would
    plausibly declare. If any stops matching, the rail has been narrowed into shipping a
    cleartext credential.

    The last four are names the superseded anchored regex MISSED and the predicate catches, so
    they are also the measurement that this convergence is not a pure loosening: ``api_keys``
    is the plural of a credential (and
    :func:`~personalclaw.apps.secret_fields.mask_secrets_in_document` already masks every
    string inside a credential-named list, so requiring the flag is what core does),
    ``aws_access_key_id`` is the spelling every AWS SDK uses, and ``credentials``/``apiKey``
    are live shapes in ``config.json``'s unmodeled blocks.
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
        "private_key",
        "webhook_secret",
        "db_password",
        # caught by the predicate, MISSED by the regex this rail used to duplicate
        "api_keys",
        "aws_access_key_id",
        "credentials",
        "apiKey",
    ]
    missed = [name for name in genuine if not is_credential_field_name(name)]
    assert not missed, (
        "the credential-shape predicate no longer flags names that DO hold credentials, so "
        f"these would ship unmasked with nothing objecting: {missed}"
    )


def test_the_documented_false_positives_are_not_flagged() -> None:
    """The half the fix exists for: a credential noun that is not the LAST word is a field.

    ``max_tokens`` is the field that reddened the corpus arm on every dual-clone workspace
    while core CI stayed green. It is ``{"type": "integer", "minimum": 0}`` in both
    ``bedrock-models`` and ``claude-subscription``, and it is the provider API's own parameter
    name, so it can be neither masked nor renamed.

    The list is deliberately wider than that one field, because the defect was a CLASS and an
    instance fix would have left it open. Each group below is a way for a name to carry a
    credential word and hold no credential:

    * a **ceiling** (``max_tokens``, ``token_limit``, ``context_budget_tokens``);
    * a **location** (``secrets_dir``, ``api_key_env`` — an env-var *name*);
    * an **identifier** (``sort_key``, ``cache_key``, ``semantic_keys``, ``client_id``) —
      the group the anchored regex still flagged, which is why duplicating it was not a fix;
    * a **longer word that merely contains one** (``tokenizer``, ``monkey_patch``,
      ``keyboard``, ``keyring_backend``) — the group the unanchored substring list flagged.
    """
    not_credentials = [
        # ceilings
        "max_tokens",  # the reported defect: an integer ceiling
        "token_limit",
        "context_budget_tokens",
        "max_turns",
        "context_window",
        # locations and modes
        "secrets_dir",
        "api_key_env",
        "auth_mode",
        "timeout_secs",
        "default_model",
        "embedding_model",
        # identifiers: flagged by the anchored regex, which is why copying it was not a fix
        "sort_key",
        "cache_key",
        "semantic_keys",
        "client_id",
        # longer words that merely contain a credential word
        "tokenizer",
        "monkey_patch",
        "keyboard",
        "keyring_backend",
    ]
    flagged = [name for name in not_credentials if is_credential_field_name(name)]
    assert not flagged, (
        "the credential-shape predicate flags fields that hold no credential. Marking one "
        '`x-meta.sensitive` renders it type="password" with write-only blank-input '
        f"behaviour, which masks a non-secret and makes it un-editable: {flagged}"
    )


def test_a_bare_key_needs_a_qualifier_which_is_what_retires_the_exemption_list() -> None:
    """``ssh_key`` is not credential-shaped, so core needs no exemption to stop requiring it.

    This replaces a ``_PATH_VALUED_EXEMPT`` set that existed only to un-flag one pair the
    duplicated regex flagged. The sibling gate still needs its copy — its pattern matches any
    terminal ``key`` — and its reason is the right one: ``rsync-sync.ssh_key`` holds a *path*
    handed to ``ssh`` ("The key itself is never read by PersonalClaw — only handed to ssh by
    path"), so masking it would hide something the user must be able to read back.

    Core reaches the same verdict through the rule instead of through an exception, because
    ``key`` unqualified is an identifier. Both halves are asserted: the qualifier is what
    makes the difference, so ``api_key`` must still be flagged in the same breath — a
    predicate that simply stopped matching ``key`` would pass the first assertion and ship a
    cleartext credential.
    """
    assert not is_credential_field_name("ssh_key")
    assert _credential_fields({"ssh_key": {"type": "string"}}) == []
    assert is_credential_field_name("api_key"), "the qualifier, not `key`, is what was dropped"
    assert is_credential_field_name("secret_access_key")


def test_the_sibling_rails_verdicts_match_ours_over_the_live_catalog() -> None:
    """Two deliberate implementations, one verdict — measured, not trusted.

    ``PersonalClawApps``' ``settings-schema-posture`` job keeps its own anchored regex because
    it is a stated pure-stdlib job with no core install, so it cannot consume this predicate
    (the module docstring carries the full reason). That makes drift possible, and drift is
    exactly what shipped: for 16 days core required ``x-meta.sensitive`` on ``max_tokens``
    while the sibling did not, and **nothing compared them**. This is that comparison.

    It compares VERDICTS over the fields that actually exist, not patterns: the sibling's
    verdict is "matches ``CRED_CLASS`` and is not in ``PATH_VALUED_EXEMPT``", ours is
    ``is_credential_field_name``. Two spellings may legitimately disagree about a hypothetical
    name; they may not disagree about a shipped one, because then one of the two repos is
    demanding an annotation the other calls wrong.

    The sibling's pattern is read out of its source rather than retyped here, so this cannot
    pass against a stale copy of it. Skips — naming what it did not check — where no sibling
    clone is resolved, which is every CI runner.
    """
    sibling = next(
        (
            candidate
            for root in _app_roots()
            for candidate in [root / ".github" / "scripts" / "check_settings_schema_posture.py"]
            if candidate.is_file()
        ),
        None,
    )
    if sibling is None:
        pytest.skip(
            "no sibling apps clone with a settings-schema-posture rail was resolved, so the "
            "two implementations were not compared at all. Roots searched: "
            f"{[str(r) for r in _app_roots()]}"
        )

    source = sibling.read_text(encoding="utf-8")
    pattern = re.search(r"CRED_CLASS = re\.compile\(\s*\n\s*r\"(?P<body>.+?)\",", source)
    assert pattern, (
        f"{sibling} no longer spells CRED_CLASS the way this comparison reads it, so the "
        "comparison would silently check nothing. Re-derive the extraction rather than "
        "deleting the check."
    )
    sibling_class = re.compile(pattern.group("body"), re.I)

    # Read the exemptions as (app, field) PAIRS, scoped to their own block. The sibling exempts
    # a subject, not a name — another app's `ssh_key` is still a credential there — so matching
    # the bare field would make this comparison quietly more permissive than the gate it reads.
    block = re.search(r"PATH_VALUED_EXEMPT\s*=\s*\{(?P<body>.*?)\n\}", source, re.S)
    assert block, (
        f"{sibling} no longer spells PATH_VALUED_EXEMPT the way this comparison reads it. "
        "Without it every exempted pair reads as a disagreement, so re-derive the extraction."
    )
    exempt = set(re.findall(r'\(\s*"(?P<a>[^"]+)"\s*,\s*"(?P<f>[^"]+)"\s*\)', block.group("body")))
    assert exempt, "the sibling's exemption block parsed as empty, which would fake disagreements"

    disagreements: list[str] = []
    names_compared = 0
    for path in _manifests():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - covered by the corpus arm
            continue
        if not isinstance(manifest, dict):
            continue
        app = path.parent.name
        for props in _schema_property_blocks(manifest):
            for dotted in _all_setting_names(props):
                leaf = dotted.rsplit(".", 1)[-1]
                names_compared += 1
                theirs = bool(sibling_class.search(leaf)) and (app, leaf) not in exempt
                ours = is_credential_field_name(leaf)
                if theirs != ours:
                    disagreements.append(
                        f"{path.parent.name}: {dotted} — sibling says "
                        f"{'credential' if theirs else 'ordinary'}, core says "
                        f"{'credential' if ours else 'ordinary'}"
                    )

    assert names_compared, "no settings fields were compared, so this proves nothing"
    assert not disagreements, (
        "the two deliberate implementations of `credential-shaped` disagree about a field "
        "that SHIPS, so one repo requires an `x-meta.sensitive` annotation the other calls "
        "wrong — the `max_tokens` failure, recurring. Reconcile them (and record which one "
        "moved and why) rather than adding an exception:\n  " + "\n  ".join(sorted(disagreements))
    )


def test_settings_keys_are_snake_case_which_the_predicate_depends_on() -> None:
    """The predicate's precondition, pinned so it cannot lapse silently.

    :func:`~personalclaw.apps.secret_fields.is_credential_field_name` splits a name on
    non-alphanumerics, so ``clientSecret`` and ``refreshToken`` collapse to a single word it
    does not recognise and would NOT be flagged. (``apiKey`` happens to collapse to a word it
    does recognise, which is luck rather than coverage.) That is safe only while every
    settings key is snake_case, which was measured true on 2026-09-23 — 0 of 108 distinct leaf
    names were anything else. If a camelCase key ever lands, this reds and names the
    consequence rather than letting a real credential slip past unnoticed.

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
        "a settings key is not snake_case, which the credential-shape predicate depends on: "
        "it splits the name on non-alphanumerics, so `clientSecret` and `refreshToken` "
        "collapse to one unrecognised word and would NOT be flagged — they would ship "
        "unmasked. Rename the key to snake_case (the catalog convention) rather than widening "
        f"the predicate: {offenders}"
    )
