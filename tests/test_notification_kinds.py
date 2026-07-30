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


def test_new_attention_pairs_are_unreachable_from_legacy_strings():
    """Pins WHY the pairs above are exempt: nothing emits them yet.

    The loop watchdog passes flat `success`/`error`/`warning`/`info` — never `"loop"` — so
    `loop/needs_input` and friends are reachable only through S2's typed helper. If a later
    change points a legacy string at one of them, this test fails and the severity
    invariant above starts (correctly) applying to it.
    """
    reachable = set(nk._LEGACY_FLAT.values())
    for key in ("loop/needs_input", "loop/failed", "loop/stalled", "cron/failed"):
        source, kind = key.split("/")
        assert (source, kind) not in reachable, (
            f"{key} is now reachable from a legacy flat string — it must adopt that "
            "string's historical severity, or the emitter must move to the typed helper"
        )


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


def test_frontend_display_map_kinds_all_resolve():
    """The SPA's display map is the other end of this wire; keep them reconciled.

    `schedule` and `loop` have rows there but NO backend emitter — pre-existing drift
    found by the T1.1 inventory. They're mapped to their nearest real registration so a
    notification persisted by an older build still resolves, and this test pins that.
    """
    meta = SRC.parent.parent / "web" / "src" / "pages" / "notifications" / "notificationMeta.ts"
    if not meta.exists():
        pytest.skip("web/ not present in this checkout")
    text = meta.read_text(encoding="utf-8")
    block = text.split("const KINDS", 1)[1].split("}\n", 1)[0]
    keys = {
        line.split(":", 1)[0].strip()
        for line in block.splitlines()
        if ":" in line and "{" in line and not line.strip().startswith("//")
    }
    keys = {k for k in keys if k and k.isidentifier()}
    assert keys, "failed to parse the frontend kind map"
    unresolvable = sorted(k for k in keys if k not in nk._LEGACY_FLAT)
    assert not unresolvable, f"frontend shows kinds the registry can't resolve: {unresolvable}"
