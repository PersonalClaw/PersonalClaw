"""`DCU-2` third clause: every computer-use attempt, allowed OR refused, produces a SEL record.

The clause names both halves, so both are proved here — a suite that only exercises the
allowed path leaves the refusal half (the one real code forgets, because the happy path is the
one people remember) unproven.

**Capture strategy.** :class:`personalclaw.sel.SecurityEventLog` is a ``__new__``-based
singleton whose ``__init__`` no-ops once ``_initialized``, so constructing one with a tmp
``base_dir`` can silently hand back a pre-existing instance bound to somebody else's
directory. ``conftest``'s autouse ``_reset_sel_singleton`` clears the class state around every
test and its home guard redirects ``sel._default_dir``, which keeps the real home safe — but
neither makes the *written rows* readable without touching disk. So these tests replace
``gate.SecurityEventLog`` in the module under test, which is why
:mod:`personalclaw.computer_use.gate` imports the class at module level rather than lazily:
the patch point has to be real.
:func:`test_capture_harness_observes_a_row` is the vacuity floor for that decision — if the
harness could not see rows at all, every "exactly one row" assertion below would pass
vacuously.
"""

import logging

import pytest

from personalclaw.computer_use import gate
from personalclaw.computer_use.enable_state import ERR_DISABLED
from personalclaw.sel import SecurityEvent, redact_event


class _CapturingLog:
    """Stands in for the SEL singleton, recording the events handed to :meth:`log`."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def log(self, event) -> None:
        self._rows.append(event)


class _RaisingLog:
    """A SEL whose write always fails — the disk-full / read-only-home / bad-key case."""

    def log(self, event) -> None:
        raise OSError("read-only file system")


@pytest.fixture
def rows(monkeypatch):
    """Capture every event :func:`gate.require_computer_use` writes, without touching disk."""
    captured: list = []
    monkeypatch.setattr(gate, "SecurityEventLog", lambda *a, **k: _CapturingLog(captured))
    return captured


@pytest.fixture
def raising_sel(monkeypatch):
    """Make every SEL write fail, to exercise the fail-open property."""
    monkeypatch.setattr(gate, "SecurityEventLog", lambda *a, **k: _RaisingLog())


# --------------------------------------------------------------------------------------
# Vacuity floor — prove the harness can observe a row before asserting on row counts.
# --------------------------------------------------------------------------------------


def test_capture_harness_observes_a_row(rows):
    """The floor under every count assertion below: a known-good call IS visible.

    Without this, a harness that silently captured nothing (a wrong patch target, a renamed
    attribute) would make ``len(rows) == 1`` fail loudly but ``len(rows) == 0``-shaped
    reasoning pass vacuously — and every "exactly one" claim here would be unfalsifiable.
    """
    assert rows == []
    gate.require_computer_use(tool="computer_click", outcome="completed")
    assert len(rows) == 1
    assert isinstance(rows[0], SecurityEvent)


# --------------------------------------------------------------------------------------
# Both halves of the clause: allowed AND refused.
# --------------------------------------------------------------------------------------


def test_allowed_attempt_produces_one_sel_row(rows):
    """An ALLOWED attempt lands exactly one row carrying the tool, app and outcome."""
    gate.require_computer_use(
        tool="computer_type",
        app="TextEdit",
        outcome="completed",
        caller_identity="session-abc",
        source="channel",
    )

    assert len(rows) == 1
    event = rows[0]
    assert event.event_type == gate.SEL_EVENT_TYPE == "computer_use"
    assert event.tool_kind == gate.SEL_TOOL_KIND
    assert event.operation == "computer_type"
    assert event.outcome == "completed"
    assert event.caller_identity == "session-abc"
    assert event.source == "channel"
    assert "TextEdit" in event.resources
    assert event.error == ""


def test_refused_attempt_produces_one_sel_row_with_the_refusal_code(rows):
    """A REFUSED attempt lands exactly one row: ``outcome="denied"`` plus the stable code.

    This is the half of the clause most likely to be missing in real code — the allowed path
    is the one a developer remembers to instrument. The refusal's code (not its prose) is what
    a later query branches on, so it must survive into ``error``.
    """
    gate.require_computer_use(
        tool="computer_click",
        app="Keychain Access",
        outcome="denied",
        caller_identity="session-xyz",
        error=f"{ERR_DISABLED}: desktop computer use is OFF on this machine",
    )

    assert len(rows) == 1
    event = rows[0]
    assert event.event_type == gate.SEL_EVENT_TYPE
    assert event.outcome == "denied"
    assert ERR_DISABLED in event.error
    assert "Keychain Access" in event.resources
    assert event.operation == "computer_click"


@pytest.mark.parametrize("outcome", ["completed", "denied", "rejected", "approved", "failed"])
def test_every_outcome_in_the_vocabulary_is_recorded(rows, outcome):
    """No outcome is dropped: the clause covers every verdict, not just the two extremes."""
    gate.require_computer_use(tool="computer_snapshot", outcome=outcome)

    assert len(rows) == 1
    assert rows[0].outcome == outcome


# --------------------------------------------------------------------------------------
# Exactly one row per call.
# --------------------------------------------------------------------------------------


def test_exactly_one_row_per_call(rows):
    """Not zero, not two. A double-log corrupts the audit as surely as a missing one.

    Two rows for one attempt make an operator counting attempts wrong in the same way a
    dropped row does, so the count is pinned per call and across calls.
    """
    gate.require_computer_use(tool="computer_click", outcome="completed")
    assert len(rows) == 1

    gate.require_computer_use(tool="computer_type", outcome="denied", error=ERR_DISABLED)
    assert len(rows) == 2

    for index in range(5):
        gate.require_computer_use(tool=f"computer_tool_{index}", outcome="completed")
    assert len(rows) == 7

    # Distinct event_ids — a repeated id would collapse rows in any id-keyed consumer.
    assert len({event.event_id for event in rows}) == 7


# --------------------------------------------------------------------------------------
# The never-decides property: it must not raise, and a swallow must stay visible.
# --------------------------------------------------------------------------------------


def test_a_failing_sel_write_does_not_raise(raising_sel):
    """Fail OPEN. An audit step that can raise is a decision-maker by accident.

    A full disk or a read-only home must not turn "record this" into "refuse this" — the
    keystone and the policy own refusal, and this module is not allowed to veto anything.
    """
    assert gate.require_computer_use(tool="computer_click", outcome="completed") is None


def test_a_swallowed_sel_failure_logs_a_warning(raising_sel, caplog):
    """...but the swallow is LOUD. Otherwise "fails open" == "never ran".

    A totally silent swallow makes the atom's clause unfalsifiable in production: a
    systematically broken audit would look exactly like a working one. The warning names the
    tool and outcome that did NOT reach the log, so the drop is attributable. Asserting the
    WARNING level (not merely "a log line") is the point — ``logger.debug`` would be invisible
    at the level an operator actually runs.
    """
    with caplog.at_level(logging.DEBUG, logger=gate.logger.name):
        assert gate.require_computer_use(tool="computer_type", outcome="denied") is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one WARNING, got {caplog.records}"
    message = warnings[0].getMessage()
    assert "computer_type" in message
    assert "denied" in message
    assert "NOT in the audit log" in message


def test_a_swallowed_failure_still_records_nothing_twice(raising_sel, caplog):
    """The fail-open path does not retry — one failed attempt is one warning, not a storm."""
    with caplog.at_level(logging.WARNING, logger=gate.logger.name):
        gate.require_computer_use(tool="computer_click", outcome="completed")

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


# --------------------------------------------------------------------------------------
# It never raises, for any input.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"tool": "computer_click", "outcome": "completed"}, id="minimal"),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "caller_identity": ""},
            id="no-caller-identity",
        ),
        pytest.param({"tool": "computer_click", "outcome": "completed", "app": ""}, id="no-app"),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "metadata": {"k": "v" * 100_000}},
            id="huge-metadata",
        ),
        pytest.param(
            {
                "tool": "computer_click",
                "outcome": "completed",
                "metadata": {str(i): i for i in range(5000)},
            },
            id="wide-metadata",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "metadata": ["not", "a", "dict"]},
            id="non-dict-metadata",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "metadata": "a string"},
            id="string-metadata",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "metadata": 42},
            id="int-metadata",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "not-a-real-outcome"}, id="bogus-outcome"
        ),
        pytest.param({"tool": "computer_click", "outcome": ""}, id="empty-outcome"),
        pytest.param({"tool": "", "outcome": "completed"}, id="empty-tool"),
        pytest.param({"tool": None, "outcome": None}, id="none-tool-and-outcome"),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "error": "x" * 100_000},
            id="huge-error",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "app": "A" * 100_000},
            id="huge-app",
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "source": ""}, id="empty-source"
        ),
        pytest.param(
            {"tool": "computer_click", "outcome": "completed", "agent": ""}, id="empty-agent"
        ),
    ],
)
def test_never_raises_for_any_input(rows, kwargs):
    """Every input returns ``None`` — and still records, which is the whole obligation."""
    assert gate.require_computer_use(**kwargs) is None
    assert len(rows) == 1


def test_prose_fields_are_truncated(rows):
    """``resources``/``error`` are bounded like every other SEL writer.

    An unbounded field would let one attempt's payload dominate the append-only log — the
    reason :data:`personalclaw.sel._MAX_ARG_LEN` exists at all.
    """
    gate.require_computer_use(
        tool="computer_type", app="A" * 100_000, outcome="denied", error="x" * 100_000
    )

    event = rows[0]
    assert len(event.resources) <= gate._MAX_LEN
    assert len(event.error) <= gate._MAX_LEN


def test_empty_source_falls_back_rather_than_writing_a_blank(rows):
    """A blank ``source`` is an unfilterable row, so it defaults instead."""
    gate.require_computer_use(tool="computer_click", outcome="completed", source="")

    assert rows[0].source == gate._DEFAULT_SOURCE


# --------------------------------------------------------------------------------------
# metadata carries no user text — the choice, pinned.
# --------------------------------------------------------------------------------------


def test_metadata_string_values_never_reach_the_record(rows):
    """The deliberate choice: no free text in ``metadata``, enforced structurally.

    ``redact_event`` cannot save us here. It runs only on the way OUT (forward callback +
    audit read surface) — :meth:`SecurityEventLog.log` writes ``asdict(event)`` to disk
    unredacted — and it delegates to :func:`personalclaw.security.redact`, which recognises
    *credential*-shaped strings, not personal data. A window title is neither, so it would
    pass through untouched and live in the audit log forever. So this module replaces string
    values with a type+length shape before the record is ever built.
    """
    secret_title = "Bank of America — Checking ••1234"
    gate.require_computer_use(
        tool="computer_type",
        outcome="completed",
        metadata={"window_title": secret_title, "field_label": "Social Security Number"},
    )

    event = rows[0]
    flat = repr(event.metadata)
    assert secret_title not in flat
    assert "Social Security Number" not in flat
    # The KEYS survive (developer-authored literals) and the shape is still auditable.
    assert set(event.metadata) == {"window_title", "field_label"}
    assert event.metadata["window_title"] == f"<str len={len(secret_title)}>"


def test_metadata_keeps_scalars_that_cannot_carry_user_text(rows):
    """Scalars survive verbatim — a count, a flag or an element index is real audit signal."""
    gate.require_computer_use(
        tool="computer_click",
        outcome="completed",
        metadata={"element_index": 7, "stale": False, "ttl_secs": 2.5, "prev": None},
    )

    assert rows[0].metadata == {
        "element_index": 7,
        "stale": False,
        "ttl_secs": 2.5,
        "prev": None,
    }


def test_nested_user_text_cannot_survive_at_any_depth(rows):
    """Containers are replaced WHOLESALE, so there is no depth at which a string survives.

    Replacing rather than walking is why no recursion is needed here — and why none can be
    got subtly wrong for the one nesting level nobody tested.
    """
    gate.require_computer_use(
        tool="computer_snapshot",
        outcome="completed",
        metadata={"tree": {"deep": {"deeper": ["Private Note: my password is hunter2"]}}},
    )

    assert "hunter2" not in repr(rows[0].metadata)
    assert rows[0].metadata["tree"] == "<dict len=1>"


def test_non_dict_metadata_is_recorded_as_a_shape_not_dropped(rows):
    """A caller bug stays visible: the reserved key says something non-dict arrived."""
    gate.require_computer_use(
        tool="computer_click", outcome="completed", metadata=["Confidential Doc.pdf"]
    )

    event = rows[0]
    assert "Confidential Doc.pdf" not in repr(event.metadata)
    assert event.metadata == {gate._SHAPE_KEY: "<list len=1>"}


def test_the_record_survives_redact_event_without_leaking(rows):
    """The record still round-trips the outward-facing redaction path cleanly.

    ``redact_event`` is what every consumer OUTSIDE this process sees. The structural fields
    must survive it (or the row becomes unfilterable on the audit surface), and nothing this
    module wrote may turn out to be credential-shaped.
    """
    from dataclasses import asdict

    gate.require_computer_use(
        tool="computer_type",
        app="TextEdit",
        outcome="denied",
        error=ERR_DISABLED,
        metadata={"window_title": "Bank of America", "element_index": 3},
    )

    redacted = redact_event(asdict(rows[0]))

    assert redacted["event_type"] == gate.SEL_EVENT_TYPE
    assert redacted["outcome"] == "denied"
    assert "Bank of America" not in repr(redacted)
    assert redacted["metadata"]["element_index"] == 3
