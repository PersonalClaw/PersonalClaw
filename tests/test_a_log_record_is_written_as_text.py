"""A log record reaches every log sink as text: each character a terminal would act on is
written as a visible escape.

A record's words can quote a model, a provider or a program, and the log is read in a terminal:
``personalclaw logs`` hands ``gateway.log`` and the service manager's files to ``tail``, which
prints them as they are. So the formatter every sink is given (``security.MaskingFormatter``)
writes an escape, a carriage return, a bell or a bidirectional override as ``\\x1b``, ``\\r``,
``\\x07``, ``\\u202e``, after the mask. A record's line breaks and tabs are kept, so a traceback
reads as one. An error nothing caught is logged through the same sinks.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
from types import SimpleNamespace

import pytest

from personalclaw import cli
from personalclaw.security import MaskingFormatter

pytestmark = pytest.mark.security

#: Characters a terminal acts on rather than shows, around a tab, which is ordinary text.
ACTS = "ok\x1b\rdone\x07\tx\u202eend"
#: The same words as the log writes them.
SHOWN = "ok\\x1b\\rdone\\x07\tx\\u202eend"
RAW = ("\x1b", "\r", "\x07", "\u202e")


def _record(message: str, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        "personalclaw.test", logging.WARNING, __file__, 1, message, (), exc_info
    )


def _a_traceback():
    try:
        raise ValueError(ACTS)
    except ValueError:
        import sys

        return sys.exc_info()


def _as_text(written: str) -> None:
    assert SHOWN in written, repr(written)
    assert not any(ch in written for ch in RAW), repr(written)


def test_the_gateway_log_file_writes_a_record_as_text(tmp_path) -> None:
    log_file = tmp_path / "gateway.log"
    handler = cli._gateway_log_handler(log_file)
    try:
        handler.emit(_record(f"the reply was {ACTS}"))
        handler.emit(_record("it failed", exc_info=_a_traceback()))
    finally:
        handler.close()
    written = log_file.read_text(encoding="utf-8")
    _as_text(written)
    assert written.count(SHOWN) == 2, written
    assert "\nTraceback (most recent call last):\n" in written, "a traceback keeps its lines"


def test_the_console_writes_a_record_as_text() -> None:
    """The console is the stream a service manager keeps, which `personalclaw logs` tails."""
    handler = cli._console_log_handler()
    stream = io.StringIO()
    handler.setStream(stream)  # type: ignore[attr-defined]
    handler.emit(_record(f"the reply was {ACTS}"))
    _as_text(stream.getvalue())


def test_the_formatter_masks_first_and_keeps_line_breaks() -> None:
    """The mask still applies before the escape, a line break is still a line break, and a record
    with nothing to escape is written as it came."""
    formatter = MaskingFormatter("%(message)s")
    password = "fake-password-1"
    written = formatter.format(
        _record(f"fetch https://user:{password}@example.com/r.git\x1b\nnext\tline")
    )
    assert password not in written and "[REDACTED" in written, written
    assert written.endswith("\\x1b\nnext\tline"), repr(written)
    assert formatter.format(_record("plain words")) == "plain words"


def test_a_withheld_records_own_fields_are_written_as_text() -> None:
    """A record that cannot be masked is withheld, with its time, level and logger; a logger's
    name is a library's or an app's, so it is written as text too."""
    handler = cli._console_log_handler()
    stream = io.StringIO()
    handler.setStream(stream)  # type: ignore[attr-defined]
    record = logging.LogRecord(
        "lib" + ACTS, logging.WARNING, __file__, 1, "x %s %s", ("one",), None
    )
    handler.emit(record)
    out = stream.getvalue()
    assert "[log record withheld" in out, out
    _as_text(out)


def test_a_record_a_sink_could_not_write_is_named_as_text(capsys) -> None:
    """A record a sink could not write is named on stderr by its logger and file, as text."""
    from personalclaw.security import MaskedStreamHandler, MaskingFormatter

    class _Failing(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError("full")

    handler = MaskedStreamHandler(_Failing())
    handler.setFormatter(MaskingFormatter("%(message)s"))
    handler.emit(logging.LogRecord("lib" + ACTS, logging.WARNING, __file__, 1, "x", (), None))
    _as_text(capsys.readouterr().err)


def _the_hooks_as_they_are(monkeypatch) -> None:
    """The exception hooks and the streams, put back after the test whatever it installs."""
    import threading

    for owner, name in (
        (sys, "excepthook"),
        (sys, "unraisablehook"),
        (threading, "excepthook"),
        (sys, "stdout"),
        (sys, "stderr"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))


@contextlib.contextmanager
def _the_console(stream: io.StringIO):
    handler = cli._console_log_handler()
    handler.setStream(stream)  # type: ignore[attr-defined]
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def test_an_error_nothing_caught_is_logged_as_text(monkeypatch) -> None:
    """An exception that ends a thread, or the command, is logged through the sinks, not printed
    by the default hook straight to stderr. A thread ended by SystemExit says nothing, and a
    Ctrl-C is left to the default hook, as Python's own hooks do."""
    import threading

    _the_hooks_as_they_are(monkeypatch)
    defaulted: list[type] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda kind, value, tb: defaulted.append(kind))
    stream = io.StringIO()
    with _the_console(stream):
        cli._log_what_nothing_caught()
        try:
            raise ValueError(ACTS)
        except ValueError as exc:
            hooked = SimpleNamespace(
                exc_type=ValueError,
                exc_value=exc,
                exc_traceback=exc.__traceback__,
                thread=SimpleNamespace(name="t" + ACTS),
            )
            threading.excepthook(hooked)
            sys.excepthook(ValueError, exc, exc.__traceback__)
        threading.excepthook(
            SimpleNamespace(
                exc_type=SystemExit, exc_value=SystemExit(0), exc_traceback=None, thread=None
            )
        )
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    out = stream.getvalue()
    _as_text(out)
    assert out.count("Traceback (most recent call last):") == 2, out
    assert "SystemExit" not in out and "KeyboardInterrupt" not in out, out
    assert defaulted == [KeyboardInterrupt]


def test_an_error_python_could_not_raise_is_logged_as_text(monkeypatch) -> None:
    """One Python could not raise (in a finalizer, say) is logged through the sinks too."""
    _the_hooks_as_they_are(monkeypatch)
    stream = io.StringIO()
    with _the_console(stream):
        cli._log_what_nothing_caught()
        try:
            raise ValueError(ACTS)
        except ValueError as exc:
            sys.unraisablehook(
                SimpleNamespace(
                    exc_type=ValueError,
                    exc_value=exc,
                    exc_traceback=exc.__traceback__,
                    err_msg="Exception ignored in",
                    object=None,
                )
            )
    out = stream.getvalue()
    _as_text(out)
    assert "Traceback (most recent call last):" in out, out


@pytest.mark.parametrize(
    ("argv", "stubbed"),
    [
        (["agent", "list"], ("cli", "_handle_agent")),
        (["mcp-core"], ("mcp_core", "run_mcp_core_server")),
    ],
    ids=["a-command", "the-tool-server"],
)
def test_a_command_installs_the_hooks(argv, stubbed, monkeypatch, tmp_path) -> None:
    """``cli.main`` installs the hooks for every command, the agent CLI's tool server included,
    which returns before the rest of the command's setup."""
    import threading

    from personalclaw import mcp_core

    _the_hooks_as_they_are(monkeypatch)
    module, name = stubbed
    monkeypatch.setattr({"cli": cli, "mcp_core": mcp_core}[module], name, lambda *a, **kw: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["personalclaw", *argv])
    with contextlib.suppress(SystemExit):
        cli.main()
    assert sys.excepthook.__name__ == "command_stopped"
    assert threading.excepthook.__name__ == "thread_stopped"
    assert sys.unraisablehook.__name__ == "could_not_raise"


def test_the_diagnostics_buffer_keeps_a_record_as_text() -> None:
    """The Diagnostics page's buffer is given the same formatter."""
    from personalclaw import log_sinks
    from personalclaw.dashboard.handlers import updates

    handler = updates.install_log_ring_handler()
    try:
        logging.getLogger("personalclaw.test").warning("the reply was %s", ACTS)
        entry = updates._log_ring[-1]
        shown = json.loads(entry)["msg"] if isinstance(entry, str) else entry["msg"]
    finally:
        log_sinks.detach(handler)
    _as_text(shown)
