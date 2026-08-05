"""The notification kind registry (INBOX-NOTIFICATIONS-UNIFICATION T1.1).

The load-bearing tests here are the two that catch a *silent* regression:

* ``test_every_default_mode_is_immediate`` — this plan replaces the delivery path with no
  gate, so a `badge` default would stop delivering a kind as a side effect of a refactor.
  The user would read that as "notifications broke."
* ``test_every_emitted_kind_string_resolves`` — walks the AST of `src/` for real
  `.notify()` call sites and asserts each flat kind string maps to a registration. A new
  emitter with an unregistered kind is caught here rather than by a warning in a log
  nobody reads.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from personalclaw import notification_kinds as nk

SRC = pathlib.Path(nk.__file__).parent


# ── registration mechanics ──────────────────────────────────────────────


def test_registry_is_populated():
    kinds = nk.all_kinds()
    assert len(kinds) >= 15
    assert all(isinstance(k, nk.NotificationKind) for k in kinds)


def test_all_kinds_is_sorted_and_stable():
    """The rules UI draws rows in this order; it must not depend on import order."""
    kinds = nk.all_kinds()
    assert kinds == sorted(kinds, key=lambda k: (k.source, k.kind))
    assert nk.all_kinds() == kinds


def test_key_is_source_slash_kind():
    k = nk.resolve_kind("cron", "result")
    assert k.key == "cron/result"


def test_duplicate_registration_raises():
    """Two emitters disagreeing about a pair is a bug worth failing on."""
    dupe = nk.NotificationKind("cron", "result", "Dupe")
    with pytest.raises(ValueError, match="duplicate"):
        nk.register(dupe)


def test_register_rejects_unknown_mode():
    bad = nk.NotificationKind("t", "bad-mode", "T", "sometimes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown mode"):
        nk.register(bad)


@pytest.mark.parametrize("sev", [0, 4, -1, 99])
def test_register_rejects_out_of_range_severity(sev):
    with pytest.raises(ValueError, match="severity must be"):
        nk.register(nk.NotificationKind("t", f"bad-sev-{sev}", "T", "immediate", sev))


def test_kind_is_frozen():
    """A registration is a constant; a consumer must not be able to retune delivery."""
    k = nk.resolve_kind("cron", "result")
    with pytest.raises(Exception):
        k.default_mode = "never"  # type: ignore[misc]


# ── fail-open resolution ────────────────────────────────────────────────


def test_unknown_pair_resolves_to_generic_not_raise(caplog):
    """Fail-OPEN: a kind we can't classify is still shown, mirroring the delivery gate."""
    with caplog.at_level("WARNING"):
        k = nk.resolve_kind("nope", "nada")
    assert (k.source, k.kind) == (nk.GENERIC_SOURCE, nk.GENERIC_KIND)
    assert k.default_mode == "immediate", "an unclassifiable notification must still deliver"
    assert "nope/nada" in caplog.text


def test_unknown_pair_label_preserves_the_original_pair():
    """The label carries what was asked for, so the UI isn't a dead end."""
    assert nk.resolve_kind("weird", "thing").label == "weird/thing"


def test_generic_is_itself_registered():
    """The fallback needs a row in the rules matrix like any other kind."""
    assert (nk.GENERIC_SOURCE, nk.GENERIC_KIND) in {(k.source, k.kind) for k in nk.all_kinds()}


@pytest.mark.parametrize(
    "flat,expected",
    [
        ("cron", "cron/result"),
        ("schedule", "cron/result"),
        ("heartbeat", "heartbeat/status"),
        ("inbox_alert", "inbox/alert"),
        ("subagent", "agent/subagent"),
        ("agent", "agent/message"),
        ("hook", "hook/fired"),
        ("warning", "system/warning"),
        ("error", "system/error"),
        ("info", "system/info"),
        ("success", "system/success"),
        ("app.route.drift", "system/route_drift"),
        ("session", "system/session"),
        ("feedback_retire", "learning/retire"),
        ("loop", "loop/progress"),
    ],
)
def test_legacy_flat_strings_resolve(flat, expected):
    """The persisted log and SSE wire carry a flat string; it must map back."""
    assert nk.kind_for_legacy(flat).key == expected


def test_legacy_lookup_is_case_and_space_insensitive():
    assert nk.kind_for_legacy("  CRON ").key == "cron/result"


@pytest.mark.parametrize("junk", ["", "   ", "not-a-kind", "None"])
def test_legacy_junk_resolves_to_generic(junk):
    assert nk.kind_for_legacy(junk).key == f"{nk.GENERIC_SOURCE}/{nk.GENERIC_KIND}"


# ── the behavior-preservation invariants ────────────────────────────────


def test_every_default_mode_is_immediate():
    """No gate hides this rollout, so defaults MUST reproduce today's delivery.

    Every emitter that passes the global gate produces a toast today. A `badge` default
    would silently stop delivering that kind — experienced as "notifications broke," with
    no setting the user knowingly changed. `badge` is opt-in per row in the rules matrix.
    """
    offenders = [k.key for k in nk.all_kinds() if k.default_mode != "immediate"]
    assert not offenders, (
        "these kinds would change delivery behavior with no rules file: "
        f"{offenders} — see the module docstring"
    )


def test_severity_vocabulary_matches_the_existing_delivery_gate():
    """The registry supplies the severity the existing gate reads; ranks must agree."""
    from personalclaw.providers.entity_routes import _MIN_SEVERITY_RANK

    assert set(_MIN_SEVERITY_RANK.values()) == {nk.SEV_INFO, nk.SEV_WARNING, nk.SEV_ERROR}


def test_severity_of_legacy_kinds_matches_the_old_hardcoded_map():
    """`_KIND_SEVERITY` ranked error=3, warning=2, inbox_alert=2. Preserve exactly."""
    from personalclaw.providers.entity_routes import _KIND_SEVERITY

    for flat, old_rank in _KIND_SEVERITY.items():
        assert (
            nk.kind_for_legacy(flat).default_severity == old_rank
        ), f"{flat} ranked {old_rank} before this plan; the registry must not requalify it"


def test_reachable_pairs_preserve_their_old_severity_exactly():
    """A pair reachable from a legacy flat string must keep that string's old rank.

    The old gate computed `_KIND_SEVERITY.get(kind, 1)` on the flat string, so for any
    pair a live emitter can still reach, re-ranking it changes min-severity filtering for
    a user who never touched a setting — either hiding a notification they used to get, or
    surfacing one they had filtered out.

    Pairs NOT reachable from any flat string are new (they exist for the attention kinds
    S2 introduces via `emit_attention_item`) and are free to carry their honest severity —
    there is no established behavior to preserve.
    """
    from personalclaw.providers.entity_routes import _KIND_SEVERITY

    drift = []
    for flat, ident in nk._LEGACY_FLAT.items():
        old_rank = _KIND_SEVERITY.get(flat, nk.SEV_INFO)
        new_rank = nk.resolve_kind(*ident).default_severity
        if new_rank != old_rank:
            drift.append(f"{flat!r} → {'/'.join(ident)}: was {old_rank}, now {new_rank}")
    assert not drift, "severity drift on kinds a live emitter still reaches:\n" + "\n".join(drift)


def test_attention_pairs_have_no_legacy_history():
    """Pins WHY the attention kinds are exempt from the severity invariant.

    They DO have a wire string (S2 added `_ATTENTION_FLAT`, because `notify()` resolves a
    rule from the wire value — without one, a "always interrupt me for needs_input" rule
    would silently do nothing). But they have no entry in `_LEGACY_FLAT`, which is the
    historical record of what a pre-existing emitter passed. No history ⇒ no severity
    obligation ⇒ free to carry their honest rank.

    If an attention wire string ever appears in `_LEGACY_FLAT`, that means a real emitter
    used to pass it, and the invariant above must start applying to it.
    """
    for flat in nk._ATTENTION_FLAT:
        assert flat not in nk._LEGACY_FLAT, (
            f"{flat!r} is in BOTH maps — if a pre-existing emitter passed it, it belongs "
            "only in _LEGACY_FLAT and must keep that string's historical severity"
        )


def test_attention_pairs_round_trip_through_the_wire():
    """pair → wire → pair must be lossless, or the kind loses its own rule.

    Caught a real bug: `loop/needs_input` and `skills/proposal` had registrations and wire
    strings but no resolution entry, so `notify()` resolved them to system/generic and
    every rule configured against them was ignored.
    """
    for flat, (source, kind) in nk._ATTENTION_FLAT.items():
        assert nk.kind_for_legacy_pair(source, kind) == flat
        assert nk.kind_for_legacy(flat).key == f"{source}/{kind}"


def test_legacy_strings_win_a_collision_with_an_attention_kind():
    """A newly added attention kind must never re-point an existing persisted kind."""
    for flat, ident in nk._LEGACY_FLAT.items():
        assert nk._WIRE_TO_PAIR[flat] == ident, f"{flat!r} was re-pointed away from {ident}"


# ── drift guard against the real call sites ─────────────────────────────


def _emitted_kind_strings() -> set[str]:
    """Flat kind strings passed to a real ``.notify(...)`` call anywhere in src/.

    Dynamic first arguments (the loop watchdog's event map, the notify action provider's
    config-driven kind) can't be read statically; their possible values are covered by
    the dedicated tests below.
    """
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "notify" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def test_every_wire_constant_resolves():
    """A constant that lost its registration silently downgrades every site using it."""
    for const in nk.WIRE_CONSTANTS:
        assert const in nk._LEGACY_FLAT, f"{const!r} has no registration"
        assert nk.kind_for_legacy(const).kind != nk.GENERIC_KIND or const == nk.GENERIC


def test_no_call_site_passes_a_bare_string_literal():
    """T1.2: every emitter goes through a named constant, so a typo can't invent a kind.

    Before this, `notify("warnign", …)` delivered a generic notification forever with no
    error. The constant makes the same typo an ImportError at startup.
    """
    literals = sorted(_emitted_kind_strings())
    assert not literals, (
        f"these .notify() sites still pass bare literals: {literals} — "
        "use a notification_kinds.* constant"
    )


def _emitted_constant_names() -> set[str]:
    """``notification_kinds.X`` attribute names passed as the kind at a call site."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "notify" or not node.args:
                continue
            first = node.args[0]
            if (
                isinstance(first, ast.Attribute)
                and isinstance(first.value, ast.Name)
                and first.value.id == "notification_kinds"
            ):
                found.add(first.attr)
    return found


def test_the_ast_sweep_actually_finds_call_sites():
    """Guards the guards: a refactor that renames notify() must not silently pass.

    Both sweeps above return the empty set when they find nothing, which would make
    `test_no_call_site_passes_a_bare_string_literal` vacuously green. Assert the tree
    really does contain migrated call sites.
    """
    assert len(_emitted_constant_names()) >= 8


def test_every_emitted_constant_resolves():
    """Every constant used at a call site exists and maps to a registration."""
    unknown = sorted(n for n in _emitted_constant_names() if not hasattr(nk, n))
    assert not unknown, f"call sites reference nonexistent constants: {unknown}"
    unregistered = sorted(
        n for n in _emitted_constant_names() if getattr(nk, n) not in nk._LEGACY_FLAT
    )
    assert not unregistered, (
        f"these constants are emitted but unregistered: {unregistered} — "
        "add a NotificationKind + a _LEGACY_FLAT entry"
    )


def test_loop_watchdog_dynamic_kinds_are_all_registered():
    """The watchdog's kind comes from `_NOTIFY_EVENTS`; every value must resolve."""
    from personalclaw.loop.watchdog import LoopWatchdog

    for event, (kind, _title) in LoopWatchdog._NOTIFY_EVENTS.items():
        assert kind.lower() in nk._LEGACY_FLAT, f"watchdog event {event!r} emits {kind!r}"


def test_notify_action_provider_allowed_kinds_are_registered():
    """The action provider clamps to `_ALLOWED_KINDS`; each must resolve."""
    from personalclaw.action_providers.notify_provider import _ALLOWED_KINDS

    for kind in _ALLOWED_KINDS:
        assert kind.lower() in nk._LEGACY_FLAT, f"notify hook allows {kind!r}"


def _frontend_kind_keys():
    """The SPA display map's keys, or None when `web/` is absent from this checkout.

    Quotes are stripped because a dotted key must be quoted in TS (`'app.route.drift'`), and the
    original parser's `isidentifier()` filter silently DROPPED exactly those — so the one key most
    likely to drift was the one key never checked.
    """
    meta = SRC.parent.parent / "web" / "src" / "pages" / "notifications" / "notificationMeta.ts"
    if not meta.exists():
        return None
    text = meta.read_text(encoding="utf-8")
    block = text.split("const KINDS", 1)[1].split("}\n", 1)[0]
    keys = set()
    for line in block.splitlines():
        stripped = line.strip()
        if ":" not in line or "{" not in line or stripped.startswith("//"):
            continue
        key = line.split(":", 1)[0].strip().strip("'\"")
        if key and (key.isidentifier() or "." in key):
            keys.add(key)
    return keys


def _frontend_kind_labels():
    """The SPA display map's ``key -> label``, or None when ``web/`` is absent.

    Same block/line parse as `_frontend_kind_keys`, extended to the `label:` string, so the
    two halves of the map (which keys exist, what each is CALLED) are checked by the same
    reader against the same source.
    """
    meta = SRC.parent.parent / "web" / "src" / "pages" / "notifications" / "notificationMeta.ts"
    if not meta.exists():
        return None
    text = meta.read_text(encoding="utf-8")
    block = text.split("const KINDS", 1)[1].split("}\n", 1)[0]
    labels = {}
    for line in block.splitlines():
        stripped = line.strip()
        if ":" not in line or "{" not in line or stripped.startswith("//"):
            continue
        key = line.split(":", 1)[0].strip().strip("'\"")
        found = re.search(r"label:\s*'([^']*)'", line)
        if key and found and (key.isidentifier() or "." in key):
            labels[key] = found.group(1)
    return labels


def _registry_labels_for(key: str) -> set[str]:
    """Every label the registry declares for a frontend map key.

    Normally one: a wire string resolves to exactly one registration. Returns MORE than one
    only for a bare `kind` registered under two sources — `failed` is the sole such case
    (`cron/failed` "Scheduled job failed" and `loop/failed` "Loop failed"), and since the FE
    map is keyed by bare kind alone it cannot express both. Either is accepted here rather
    than picking a winner in the test; the map's own comment records which it chose and why.
    """
    pair = nk._WIRE_TO_PAIR.get(key)
    if pair is not None:
        return {nk.resolve_kind(*pair).label}
    return {k.label for k in nk.all_kinds() if k.kind == key}


def test_frontend_labels_are_the_registry_declared_labels():
    """The display map's own header calls the registry "the authority for the wording" and
    says to add a row "with the SAME label" — this is the assertion that holds it to that.

    The colocated vitest suite can only check a label is not the raw wire key
    (`label !== 'info'`), which `info -> 'Info'` passes while being exactly the forbidden
    drift. Only this tier can read the registry, so this is where the contract lives.
    """
    labels = _frontend_kind_labels()
    if labels is None:
        pytest.skip("web/ not present in this checkout")
    assert labels, "failed to parse any labels out of the frontend kind map"
    drift = {
        key: (label, sorted(expected))
        for key, label in labels.items()
        if (expected := _registry_labels_for(key)) and label not in expected
    }
    assert not drift, (
        "frontend labels disagree with the registry's declared display names "
        f"(key: frontend vs registry): {drift}. notification_kinds.py owns the wording — "
        "copy its label verbatim rather than inventing one here."
    )


def _wire_vocabulary():
    """Every flat string a registered kind can actually put on the wire.

    🔴 This is the map to reconcile against, and it is NOT `_LEGACY_FLAT`.
    `kind_for_legacy_pair` is the ONE function every emitter routes through, and it returns the
    legacy flat string when one maps to the pair and **the bare `kind`** when none does. So the
    ATTENTION kinds (`proposal`, `needs_input`, `agent_request`, `digest`) travel as bare strings —
    verified against a real machine's `notifications.jsonl`, which holds 115 rows of `proposal`.
    Checking `_LEGACY_FLAT` alone declared those unresolvable even though they are the ones actually
    on disk.
    """
    return {nk.kind_for_legacy_pair(k.source, k.kind) for k in nk.all_kinds()}


def test_every_emittable_kind_has_a_frontend_row():
    """A kind the backend can emit but the SPA cannot label falls through to the raw wire string —
    so the filter row reads "Info", "Subagent", beside a bare "proposal". This is the direction that
    matters: a MISSING row is a visible defect for the user."""
    keys = _frontend_kind_keys()
    if keys is None:
        pytest.skip("web/ not present in this checkout")
    assert keys, "failed to parse the frontend kind map"
    missing = sorted(_wire_vocabulary() - keys)
    assert not missing, f"the backend emits kinds the frontend cannot label: {missing}"


def test_frontend_display_map_kinds_all_resolve():
    """The other direction: a row for a string nothing can emit.

    Tolerated rather than forbidden, and the list is PINNED so it can only shrink. Each entry is a
    bare `kind` whose pair also owns a legacy flat string, so `kind_for_legacy_pair` prefers the
    legacy one and the bare form never reaches the wire from THIS build — but a notification
    persisted by an older build still carries it, and the log is append-only. Deleting those rows
    would make old history render as raw strings. `schedule` is the same case from the other end:
    pre-existing drift the T1.1 inventory found, kept so an older `schedule` row still resolves.
    """
    keys = _frontend_kind_keys()
    if keys is None:
        pytest.skip("web/ not present in this checkout")
    tolerated = {
        "schedule",  # pre-existing drift (T1.1 inventory); no emitter, kept for old rows
        # Bare kinds whose pair emits a legacy flat string instead. Kept for persisted history.
        "alert",  # inbox/alert     → emits `inbox_alert`
        "result",  # cron/result     → emits `cron`
        "failed",  # cron|loop/failed → emits `cron` / `failed`
        "fired",  # hook/fired      → emits `hook`
        "message",  # agent/message   → emits `agent`
        "status",  # heartbeat/status → emits `heartbeat`
        "progress",  # loop/progress   → emits `loop`
        "complete",  # loop/complete   → emits `loop`
        "stalled",  # loop/stalled    → emits `loop`
        "retire",  # learning/retire → emits `feedback_retire`
        "route_drift",  # system/route_drift → emits `app.route.drift`
    }
    unresolvable = sorted(k for k in keys - _wire_vocabulary() if k not in tolerated)
    assert not unresolvable, f"frontend shows kinds the registry can't resolve: {unresolvable}"


def test_the_tolerated_list_does_not_outlive_its_reason():
    """Every tolerated key must still be a REGISTERED bare kind (or the known `schedule` drift).

    Without this, the tolerance list becomes a place to hide a genuine typo: a misspelled row would
    be waved through forever by adding it above.
    """
    keys = _frontend_kind_keys()
    if keys is None:
        pytest.skip("web/ not present in this checkout")
    bare = {k.kind for k in nk.all_kinds()}
    for key in keys - _wire_vocabulary():
        assert key in bare or key == "schedule", f"{key!r} is not a registered kind at all"
