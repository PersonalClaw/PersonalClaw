"""#3424 — a `config.json` we cannot read must not widen the operator's security posture.

``AppConfig.load()`` discarded an unparseable ``config.json`` and substituted the dataclass
defaults, and two of those defaults are **less safe than the narrowest value the operator can
store**. So a truncated file — a disk-full write, an interrupted save, a partially synced home —
silently converted a confirm-before-acting posture into unattended execution:

====================  =====================  ==============================
``config.json``       ``agent.approval_mode``  ``subagent_cwd_allowed_roots``
====================  =====================  ==============================
intact (control)      ``interactive``        ``[]``
truncated             ``auto``               ``['~/workspace','~/workplace']``
non-object            ``auto``               ``['~/workspace','~/workplace']``
non-UTF-8             *raised*               *raised*
====================  =====================  ==============================

The shape worth naming is the second column. An empty ``subagent_cwd_allowed_roots`` is not an
absence of configuration — **it is how the feature is disabled** — and ``subagent.py``'s
``except`` arm carries a comment explicitly forbidding this re-widening. That arm never ran,
because the loader does not raise: it returns defaults. The protection read as present and was
unreachable.

This file is the same contract ``providers/entity_routes._load_entity_settings`` acquired in
#3411, applied to ``config.json``: *absent* and *unreadable* are different claims, and only the
first one means "the defaults are the user's intent".

**The blast radius of failing closed is asserted here too, not assumed.** A fail-closed default
that bricks a new install is its own defect, so :func:`test_an_absent_config_still_yields_the
_permissive_defaults` and its zero-byte sibling pin that a first run is untouched. Zero bytes
reads as *absent*, matching :func:`personalclaw.config.loader.read_config_for_merge`, which
already made that call for the same file on the write side ("Zero bytes hold no block a write
could destroy").

Every arm runs against a ``PERSONALCLAW_HOME`` under ``tmp_path``. The real
``~/.personalclaw`` is never read or written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.config.loader import AppConfig

#: What the operator STORED: the narrowest posture each field can express. The control column
#: of the table above, and the thing a discarded read must never trade away.
STORED_NARROW: dict[str, Any] = {
    "agent": {
        "approval_mode": "interactive",
        "subagent_cwd_allowed_roots": [],
        "unattended_requires_verified_adapter": True,
    }
}

#: What a DISCARDED read must resolve to. Identical to ``STORED_NARROW`` by construction, and
#: that is the point: the restrictive value is the safe answer whether or not it is what the
#: operator happened to store, because a read that failed is not consent.
FAIL_CLOSED: dict[str, Any] = {
    "agent.approval_mode": "interactive",
    "agent.subagent_cwd_allowed_roots": [],
    "agent.unattended_requires_verified_adapter": True,
}

#: The dataclass defaults — correct for a first run, wrong for a discard. Asserted explicitly
#: so a future default change that happens to make one of these restrictive cannot quietly turn
#: an arm below into a tautology.
PERMISSIVE_DEFAULTS: dict[str, Any] = {
    "agent.approval_mode": "auto",
    "agent.subagent_cwd_allowed_roots": ["~/workspace", "~/workplace"],
    "agent.unattended_requires_verified_adapter": False,
}


def _get(cfg: AppConfig, dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        node = getattr(node, part)
    return node


def _posture(cfg: AppConfig) -> dict[str, Any]:
    return {key: _get(cfg, key) for key in FAIL_CLOSED}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home. ``config_dir()`` re-reads ``$PERSONALCLAW_HOME`` on every call."""
    root = tmp_path / "home-3424"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    return root


def _write_stored_narrow(home: Path) -> str:
    """The operator's intact config. Returns its exact text, so a corruption can be derived
    from the real document rather than from a hand-typed lookalike."""
    text = json.dumps(STORED_NARROW, indent=2) + "\n"
    (home / "config.json").write_text(text, encoding="utf-8")
    return text


# ── the vacuity floor ───────────────────────────────────────────────────────────────────


def test_the_intact_config_is_actually_read(home: Path) -> None:
    """The control. Without it every arm below could pass because nothing reads the file.

    If this is red, the fixture is not pointing the loader at ``home`` and the whole file is
    measuring the dataclass defaults three times over.
    """
    _write_stored_narrow(home)
    assert _posture(AppConfig.load()) == FAIL_CLOSED


def test_the_permissive_defaults_are_still_permissive() -> None:
    """The defaults this file calls unsafe ARE the defaults, measured from the dataclass.

    A default that drifts to a restrictive value would make every corruption arm below pass for
    the wrong reason — the discard resolution would be untested and indistinguishable from
    "the default happened to be safe".
    """
    from personalclaw.config.loader import AgentConfig

    fresh = AppConfig(agent=AgentConfig())
    assert _posture(fresh) == PERMISSIVE_DEFAULTS
    assert PERMISSIVE_DEFAULTS != FAIL_CLOSED


# ── the three corruption modes ──────────────────────────────────────────────────────────


def test_a_truncated_config_fails_closed(home: Path) -> None:
    """A write that stopped mid-document. The issue's first row."""
    text = _write_stored_narrow(home)
    (home / "config.json").write_text(text[: len(text) // 2], encoding="utf-8")

    cfg = AppConfig.load()
    assert _posture(cfg) == FAIL_CLOSED, (
        "a truncated config.json widened the posture: a file we cannot parse was read as 'no "
        "restrictions declared'. approval_mode=auto auto-approves every tool call for a "
        "subagent's lifetime, and a non-empty subagent_cwd_allowed_roots re-enables cwd "
        "overrides the operator disabled with []."
    )
    assert cfg.agent.yolo is False


def test_a_non_object_config_fails_closed(home: Path) -> None:
    """Valid JSON, wrong shape — a list where an object belongs. The issue's second row."""
    (home / "config.json").write_text(json.dumps(["agent"]) + "\n", encoding="utf-8")

    cfg = AppConfig.load()
    assert _posture(cfg) == FAIL_CLOSED
    assert cfg.agent.yolo is False


def test_a_non_utf8_config_fails_closed_instead_of_raising(home: Path) -> None:
    """Bytes that are not text. The issue's third row, which was a THIRD behaviour again.

    ``read_text()`` carried no ``encoding`` and the ``except`` did not name
    ``UnicodeDecodeError``, so this input escaped the loader entirely — while
    ``_load_entity_settings`` had already widened its own ``except`` to include it. The two
    loader families disagreed about the same input.
    """
    (home / "config.json").write_bytes(b'{"agent": {"bot_name": "\xff\xfe\xff"}}\n')

    cfg = AppConfig.load()  # must not raise
    assert _posture(cfg) == FAIL_CLOSED
    assert cfg.agent.yolo is False


# ── the blast radius: a first run must still work ────────────────────────────────────────


def test_an_absent_config_still_yields_the_permissive_defaults(home: Path) -> None:
    """The negative control the fix must not break. No file = first run, and the defaults ARE
    the user's intent there. Failing closed here would ship an install that asks permission for
    every read before the user has expressed any preference."""
    assert not (home / "config.json").exists()
    assert _posture(AppConfig.load()) == PERMISSIVE_DEFAULTS


def test_a_zero_byte_config_reads_as_absent_not_as_a_discard(home: Path) -> None:
    """A bare ``touch``, or a create that never got its bytes. Same answer as absent.

    Deliberate, and consistent with :func:`read_config_for_merge`, which already ruled on this
    exact file: "Zero bytes hold no block a write could destroy, and refusing would leave a
    config truncated by a crashed write or a bare ``touch`` permanently unwritable." A
    zero-byte file is reachable on a first run, so resolving it restrictively is the
    make-the-product-unusable half of this fix, not the safety half.
    """
    (home / "config.json").write_text("", encoding="utf-8")
    assert _posture(AppConfig.load()) == PERMISSIVE_DEFAULTS

    (home / "config.json").write_text("   \n\t\n", encoding="utf-8")
    assert _posture(AppConfig.load()) == PERMISSIVE_DEFAULTS


# ── the operator's file is never overwritten by the substitute ───────────────────────────


def test_a_discarded_read_does_not_rewrite_the_operators_file(home: Path) -> None:
    """The restrictive posture is in MEMORY. The corrupt bytes stay on disk, recoverable.

    A fail-closed substitution that persisted itself would destroy the only copy of the
    operator's settings while claiming to protect them.
    """
    text = _write_stored_narrow(home)
    broken = text[: len(text) // 2]
    (home / "config.json").write_text(broken, encoding="utf-8")

    AppConfig.load()
    AppConfig.load_with_migration_state()

    assert (home / "config.json").read_text(encoding="utf-8") == broken


def test_a_discarded_read_reports_no_pending_migration(home: Path) -> None:
    """``migrated`` is False on a discard, which is what keeps the writing boot path away.

    ``config.migrations.load_and_persist_migrations`` returns early when ``migrated`` is
    False; a True here would send the substitute through ``save()``.
    """
    text = _write_stored_narrow(home)
    (home / "config.json").write_text(text[: len(text) // 2], encoding="utf-8")

    _, migrated = AppConfig.load_with_migration_state()
    assert migrated is False


# ── the resolution is declared once, not re-typed per call site ──────────────────────────


def test_the_fail_closed_resolution_is_a_declared_surface(home: Path) -> None:
    """The production overlay is the table this file asserts, imported rather than copied.

    Mirrors ``entity_routes.INBOX_ON_DISCARDED_READ``: the fail-closed half of a loader's
    contract is data a reader can find, not a value buried in a branch.
    """
    from personalclaw.config.loader import CONFIG_ON_DISCARDED_READ

    assert dict(CONFIG_ON_DISCARDED_READ) == FAIL_CLOSED


def test_a_discard_is_observable_and_a_repair_clears_it(home: Path) -> None:
    """The operator can SEE that their config was unreadable — silence is how this hid.

    Also asserts the signal is not sticky: repairing the file clears it, so ``doctor`` cannot
    keep reporting a problem the user already fixed.
    """
    from personalclaw.config.loader import config_discard

    text = _write_stored_narrow(home)
    (home / "config.json").write_text(text[: len(text) // 2], encoding="utf-8")

    AppConfig.load()
    state = config_discard()
    assert state is not None
    assert state.path == home / "config.json"
    assert state.reason

    (home / "config.json").write_text(text, encoding="utf-8")
    AppConfig.load()
    assert config_discard() is None


def test_doctor_reports_an_unreadable_config_as_an_issue(home: Path, capsys) -> None:
    """The user-visible half. ``doctor`` is where an operator goes to ask what is wrong.

    Silent substitution is what kept this invisible: every surface, ``doctor``'s own
    ``approval:`` line included, displayed the substitute as if it were the stored setting.
    """
    from personalclaw.cli_doctor import _doctor_config_readable

    text = _write_stored_narrow(home)
    (home / "config.json").write_text(text[: len(text) // 2], encoding="utf-8")

    AppConfig.load()
    issues = _doctor_config_readable()
    printed = capsys.readouterr().out
    assert issues, "an unreadable config.json must count as a doctor issue, not just a line"
    assert "UNREADABLE" in printed
    assert str(home / "config.json") in printed
    # Rendered from the table, so the advice cannot describe a posture that is no longer applied.
    for key in FAIL_CLOSED:
        assert key in printed

    (home / "config.json").write_text(text, encoding="utf-8")
    AppConfig.load()
    capsys.readouterr()
    assert _doctor_config_readable() == []
    assert capsys.readouterr().out == "", "a readable config must print no row at all"
