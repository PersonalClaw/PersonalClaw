"""A text PersonalClaw cannot mask is withheld: never shown, sent, stored or logged as it came.

🔴 ``model_downloads._mask`` builds the local-model health message, and when the masker raised it
returned the text as it came. Ten more maskers did the same:
the run ledger's, a crash record's, the doctor's, a run-completion notification's ("sending the
untouched text"), the trigger history's, a send-message hook's, the after-turn review's two
proposal paths, a session skill draft's and a refinement's quote. And the log formatter every sink
masks with handed a record it could not mask, or whose arguments did not fit its message, to the
handler's error path, which writes that record's message and arguments to stderr as they came:
for the gateway, the console its service manager keeps. A log file that could not take a record
did the same.

A masker that raised proves nothing about what the text holds, so none of it goes on: each of
these says ``[redaction failed; text withheld]`` in its place, a log record it cannot mask is
written as its time, level and logger with the words withheld, and a record a sink cannot write
is named without its words.

Every masker raises in these tests (``broken_masker``): the credential pass and the
exfiltration-URL pass, which every mask PersonalClaw applies is made of. The controls show the same
paths mask as before when the masker works.
"""

from __future__ import annotations

import ast
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: A login a text can carry: a URL with a password in it, which every mask removes.
SECRET = "hunter2-unmaskable-pass"
LOGIN_URL = f"https://ada:{SECRET}@git.example.com/r.git"

#: What stands in for a text its masker failed on.
WITHHELD = "[redaction failed; text withheld]"


@pytest.fixture
def broken_masker(monkeypatch):
    from personalclaw import security

    def broken(*_args, **_kwargs):
        raise RuntimeError("the masker broke")

    monkeypatch.setattr(security, "redact_credentials", broken)
    monkeypatch.setattr(security, "redact_exfiltration_urls", broken)


# ── the local model health message and selftest detail (#527) ───────────────────────────────


@pytest.fixture
def local_provider():
    from personalclaw.local_models import registry as reg
    from personalclaw.local_models.provider import LocalModelProvider

    class _Says(LocalModelProvider):
        """A local provider whose health message carries a login."""

        @property
        def name(self) -> str:
            return "withheld-fixture"

        @property
        def display_name(self) -> str:
            return "Withheld fixture"

        async def is_available(self) -> bool:
            return False

        async def availability_detail(self) -> tuple[bool, str]:
            return False, f"could not reach {LOGIN_URL}"

        async def list_models(self):
            return []

        async def download_model(self, model_name: str) -> bool:
            return False

        async def delete_model(self, model_name: str) -> bool:
            return False

    reg.register_provider(_Says(), capabilities=["stt"], name="withheld-fixture")
    yield "withheld-fixture"
    reg.unregister_provider("withheld-fixture")


async def _health(name: str) -> dict:
    from personalclaw.dashboard.handlers import model_downloads as md

    request = make_mocked_request(
        "GET",
        f"/api/models/local/{name}/health",
        match_info={"provider": name},
        app=web.Application(),
    )
    response = await md.api_local_model_health(request)
    return json.loads(response.body.decode())


@pytest.mark.asyncio
async def test_the_health_message_is_withheld_when_it_cannot_be_masked(
    broken_masker, local_provider
) -> None:
    body = await _health(local_provider)

    assert SECRET not in json.dumps(body)
    assert body["message"] == WITHHELD


@pytest.mark.asyncio
async def test_the_health_message_is_masked_when_it_can_be(local_provider) -> None:
    body = await _health(local_provider)

    assert SECRET not in body["message"]
    assert body["message"].startswith("could not reach https://")


def test_a_failed_selftest_names_only_its_failures_type_when_that_cannot_be_masked(
    broken_masker,
) -> None:
    """The selftest's detail is ``failure_copy.failure_detail``'s, which says nothing it cannot
    mask, and the failure's type in its place."""
    from personalclaw.dashboard.handlers import model_downloads as md

    row = md._error_result(RuntimeError(f"sign-in to {LOGIN_URL} failed"), 7)

    assert (row["detail"], row["reason"]) == ("RuntimeError", "selftest_error:RuntimeError")


# ── the rest of the family ─────────────────────────────────────────────────────────────────


def test_the_run_ledger_journals_no_value_it_could_not_mask(broken_masker) -> None:
    from personalclaw.ledger.redaction import redact

    record = redact({"output": LOGIN_URL, "lines": [f"pushed to {LOGIN_URL}"], "exit": 0})

    assert record == {"output": WITHHELD, "lines": [WITHHELD], "exit": 0}


def test_a_crash_record_keeps_no_text_it_could_not_mask(broken_masker) -> None:
    from personalclaw.resilience.crashes import record_crash

    path = record_crash(
        "turn",
        RuntimeError(f"push to {LOGIN_URL} failed"),
        session_key="s-1",
        last_turns=[f"clone {LOGIN_URL}"],
        now=1_790_000_000.0,
    )

    assert path is not None, "no crash record was written"
    written = path.read_text(encoding="utf-8")
    assert SECRET not in written
    payload = json.loads(written)
    assert payload["exception"]["message"] == WITHHELD
    assert payload["last_turns"] == [WITHHELD]


@pytest.mark.asyncio
async def test_the_doctor_shows_no_probe_words_it_could_not_mask(broken_masker, tmp_path) -> None:
    from personalclaw.resilience.doctor import (
        DoctorContext,
        Probe,
        ProbeResult,
        Tier,
        _safe_run,
    )

    async def says(ctx):
        return ProbeResult(ok=False, detail=f"cannot sign in to {LOGIN_URL}")

    async def raises(ctx):
        raise RuntimeError(f"cannot sign in to {LOGIN_URL}")

    ctx = DoctorContext(home=tmp_path)
    said = await _safe_run(Probe(id="says", capability="core", tier=Tier.PROCESS, run=says), ctx)
    raised = await _safe_run(
        Probe(id="raises", capability="core", tier=Tier.PROCESS, run=raises), ctx
    )

    assert said.detail == WITHHELD
    assert (raised.detail, raised.evidence) == (WITHHELD, {"error": WITHHELD})


def test_a_run_notification_sends_no_text_it_could_not_mask(broken_masker) -> None:
    from personalclaw.triggers.delivery import build_delivery

    delivery = build_delivery(
        trigger_id="t-1", trigger_name="Nightly sync", ok=False, summary=f"push to {LOGIN_URL}"
    )

    assert SECRET not in f"{delivery.title} {delivery.body}"
    assert (delivery.title, delivery.body) == (WITHHELD, WITHHELD)


def test_a_run_notification_is_masked_when_it_can_be() -> None:
    from personalclaw.triggers.delivery import build_delivery

    delivery = build_delivery(
        trigger_id="t-1", trigger_name="Nightly sync", ok=False, summary=f"push to {LOGIN_URL}"
    )

    assert SECRET not in delivery.body
    assert delivery.body.startswith("push to https://") and delivery.body != WITHHELD


def test_the_trigger_history_shows_no_reason_it_could_not_mask(broken_masker) -> None:
    from personalclaw.triggers.history import schedule_run_to_record

    record = schedule_run_to_record(
        {"run_id": "r-1", "status": "failed", "error": f"push to {LOGIN_URL} failed"}
    )

    assert record.reason == WITHHELD


@pytest.mark.asyncio
async def test_a_send_message_hook_sends_no_text_it_could_not_mask(broken_masker) -> None:
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.send_message_provider import SendMessageActionProvider

    state = MagicMock()
    state.channel_delivery = None
    with patch(
        "personalclaw.action_providers.send_message_provider.get_action_services",
        return_value=MagicMock(state=state),
    ):
        result = await SendMessageActionProvider().execute(
            {"text_template": f"the deploy key is at {LOGIN_URL}"},
            ActionContext(event="clock", context=""),
        )

    assert result.success, result.error
    state.notify.assert_called_once()
    assert SECRET not in repr(state.notify.call_args) + result.stdout
    assert state.notify.call_args.args[2] == WITHHELD


def test_a_session_skill_draft_keeps_no_body_it_could_not_mask(broken_masker) -> None:
    from personalclaw.skills.ephemeral import _session_dir, remember

    draft = remember("s-withheld", "Deploy", f"clone {LOGIN_URL} first")

    assert draft is not None and draft.body == WITHHELD
    stored = "".join(p.read_text(encoding="utf-8") for p in _session_dir("s-withheld").glob("*"))
    assert stored and SECRET not in stored


def test_a_refinement_quotes_no_words_it_could_not_mask(broken_masker) -> None:
    from personalclaw.skills.refine import refinement_body

    body = refinement_body(
        "correction",
        quote=f"no, clone {LOGIN_URL}",
        now=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    assert SECRET not in body
    assert f"> {WITHHELD}" in body


# ── the log sinks ──────────────────────────────────────────────────────────────────────────


class _FullDisk:
    """A log file's stream on a full disk."""

    def write(self, _text: str) -> int:
        raise OSError(28, "No space left on device")

    def tell(self) -> int:
        return 0

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def sink_logger():
    """A logger of this test's own that writes only to *handler*. A new name each test, and
    propagating again after it: pytest gives every logger that does not propagate its own
    capture handlers when the next test starts."""
    made: list[tuple[logging.Logger, logging.Handler]] = []

    def attach(handler: logging.Handler) -> logging.Logger:
        log = logging.getLogger(f"personalclaw.test.withheld.{uuid.uuid4().hex[:8]}")
        log.setLevel(logging.DEBUG)
        log.propagate = False
        log.addHandler(handler)
        made.append((log, handler))
        return log

    yield attach
    for log, handler in made:
        log.removeHandler(handler)
        log.propagate = True
        handler.close()


def test_a_record_the_masker_fails_on_is_withheld_not_written_as_it_came(
    broken_masker, sink_logger, capsys
) -> None:
    from personalclaw import cli

    log = sink_logger(cli._console_log_handler())

    log.warning("clone of %s failed", LOGIN_URL)

    written = capsys.readouterr().err
    assert SECRET not in written
    assert "Logging error" not in written, "the handler's error path wrote the record as it came"
    assert f"WARNING {log.name}: [log record withheld" in written


def test_a_record_whose_arguments_do_not_fit_is_withheld_not_written_as_it_came(
    sink_logger, capsys
) -> None:
    from personalclaw import cli

    log = sink_logger(cli._console_log_handler())

    log.warning("token %s for %s", LOGIN_URL)  # one argument for two

    written = capsys.readouterr().err
    assert SECRET not in written
    assert "Logging error" not in written
    assert "[log record withheld" in written and "TypeError" in written


def test_a_record_the_log_file_cannot_take_is_named_without_its_words(
    sink_logger, tmp_path, capsys
) -> None:
    from personalclaw import cli

    handler = cli._gateway_log_handler(tmp_path / "gateway.log", logging.DEBUG)
    assert handler.stream is not None
    handler.stream.close()
    handler.stream = _FullDisk()  # type: ignore[assignment]
    log = sink_logger(handler)

    log.error("sync pushed to %s", LOGIN_URL)

    said = capsys.readouterr().err
    assert SECRET not in said
    assert "Logging error" not in said
    assert f"a log record from {log.name}" in said and "could not be written (OSError)" in said


def test_a_record_the_masker_can_mask_is_written_masked(sink_logger, capsys) -> None:
    from personalclaw import cli

    log = sink_logger(cli._console_log_handler())

    log.warning("clone of %s failed", LOGIN_URL)

    written = capsys.readouterr().err
    assert SECRET not in written
    assert "clone of https://" in written and "[log record withheld" not in written


# ── the rail: no try that masks a text falls back to it ─────────────────────────────────────

#: `security`'s maskers: what a text goes through before it is shown, sent or stored.
_MASKERS = frozenset(
    {
        "redact",
        "redact_credentials",
        "redact_exfiltration_urls",
        "redact_for_display",
        "redact_for_model",
        "redact_url_userinfo",
        "redact_webhook_urls",
        "redact_known_values",
        "redact_field",
        "redact_and_truncate",
        "redact_values_for_display",
        "mask_child_output",
    }
)


def _tail(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _masker_calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _tail(n) in _MASKERS]


def _handed(arg: ast.AST) -> set[str]:
    """The names whose text *arg* hands a masker: through ``or``, a conditional, ``str()`` and
    an f-string, and not through any other call (``super().format(record)`` hands the masker
    what that call made, not ``record``)."""
    if isinstance(arg, ast.Name):
        return {arg.id}
    if isinstance(arg, ast.BoolOp):
        return set().union(*(_handed(v) for v in arg.values))
    if isinstance(arg, ast.IfExp):
        return _handed(arg.body) | _handed(arg.orelse)
    if isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "str" and arg.args:
        return _handed(arg.args[0])
    if isinstance(arg, ast.JoinedStr):
        parts = [_handed(v.value) for v in arg.values if isinstance(v, ast.FormattedValue)]
        return set().union(set(), *parts)
    return set()


def _made_by_a_masker(value: ast.AST) -> bool:
    """A masker's own result (``redact(text)``, ``redact_credentials(text)[0]``)."""
    if isinstance(value, ast.Subscript):
        value = value.value
    return isinstance(value, ast.Call) and _tail(value) in _MASKERS


def _masks_only(stmt: ast.stmt) -> bool:
    """Whether *stmt* belongs to a try that exists to mask: an import, an assignment or a return
    of what a masker made (or of a plain name, list or constant), an ``append``, a loop of those.
    A statement that does something else with a masked text (writes a record with it, sends it)
    is not: when its masker raises, nothing is written or sent."""
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
        return True
    if isinstance(stmt, ast.For):
        return all(_masks_only(s) for s in stmt.body + stmt.orelse)
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.Return)):
        value = stmt.value
        return (
            value is None
            or _made_by_a_masker(value)
            or isinstance(value, (ast.Name, ast.Constant, ast.List, ast.Tuple))
        )
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _tail(stmt.value) == "append" or _made_by_a_masker(stmt.value)
    return False


def _reads(value: ast.AST, names: set[str]) -> bool:
    """Whether *value* reads one of *names*, other than for its type."""
    if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "type":
        return False
    if isinstance(value, ast.Name):
        return value.id in names
    return any(_reads(child, names) for child in ast.iter_child_nodes(value))


def _fail_open_maskers(tree: ast.AST) -> tuple[list[int], int]:
    """``(lines, seen)``: each handler of a try that exists to mask a text and, when the masker
    raises, goes on with that text (returns it, assigns it, or falls through to the code after,
    which reads it); and how many such tries there are."""
    hits: list[int] = []
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        calls = _masker_calls(ast.Module(body=node.body, type_ignores=[]))
        if not calls or not all(_masks_only(s) for s in node.body):
            continue
        seen += 1
        handed = set().union(*(_handed(c.args[0]) for c in calls if c.args))
        for handler in node.handlers:
            last = handler.body[-1]
            goes_on = [
                n
                for n in ast.walk(handler)
                if isinstance(n, (ast.Return, ast.Assign, ast.AnnAssign))
                and n.value is not None
                and _reads(n.value, handed)
            ]
            if goes_on or not isinstance(last, (ast.Return, ast.Raise)):
                hits.append(handler.lineno)
    return hits, seen


def test_no_masker_in_the_tree_falls_back_to_the_text_it_could_not_mask() -> None:
    found: dict[str, list[int]] = {}
    seen = 0
    for path in sorted(_SRC.rglob("*.py")):
        hits, n = _fail_open_maskers(ast.parse(path.read_text(encoding="utf-8")))
        seen += n
        if hits:
            found[path.relative_to(_SRC).as_posix()] = hits
    assert not found, (
        "a try that masks a text goes on with the text when the masker raises. Use "
        f"`security.redact_or_withhold`, or return a fixed placeholder: {found}"
    )
    # The fail-closed maskers the tree has (`redact_or_withhold` itself, the log formatter, the
    # capture store's, the capture proxy's, a confirmation preview's, a batch's recall view): a
    # rail that saw none of them would pass on any tree.
    assert seen >= 5, f"the rail saw only {seen} masking tries: it would pass vacuously"


def test_the_rail_sees_the_shapes_it_is_for() -> None:
    src = (
        "def returns_it(text):\n"
        "    try:\n"
        "        return redact(text or '')\n"
        "    except Exception:\n"
        "        return text or ''\n"
        "def assigns_it(text):\n"
        "    try:\n"
        "        red = redact(str(text))\n"
        "    except Exception:\n"
        "        red = str(text)\n"
        "    return red\n"
        "def falls_through(text):\n"
        "    try:\n"
        "        text, _ = redact_credentials(text)\n"
        "    except Exception:\n"
        "        log.debug('skipped')\n"
        "    return text\n"
        "def withholds(text):\n"
        "    try:\n"
        "        return redact(text)\n"
        "    except Exception:\n"
        "        return WITHHELD\n"
        "def names_its_type(exc):\n"
        "    try:\n"
        "        cleaned, _ = redact_credentials(str(exc))\n"
        "    except Exception:\n"
        "        return type(exc).__name__\n"
        "    return cleaned\n"
        "def raises(text):\n"
        "    try:\n"
        "        return redact(text)\n"
        "    except Exception:\n"
        "        raise\n"
        "def reads_first(path, text):\n"
        "    try:\n"
        "        data = path.read_text()\n"
        "        return redact(data)\n"
        "    except OSError:\n"
        "        return text\n"
    )
    hits, seen = _fail_open_maskers(ast.parse(src))
    assert hits == [4, 9, 15], hits
    assert seen == 6, seen
