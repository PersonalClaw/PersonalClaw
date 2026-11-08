"""No test fetches the code map's grammars: the run fetches them once, before its first test.

The code map's grammars are a download of about 25 MB from the language pack's releases on GitHub.
The first test that parsed code fetched them itself, inside its 30 s timeout. So when those release
downloads answered 504, or hung, both Python suites went red on every change, whatever it touched:
the code map's tests failed, and the parse tests skipped.

Now a test that needs a grammar asks for the ``code_map_grammars`` fixture. A run that collected
one fetches the grammars before its first test, in a process of its own, trying three times
(``tool_homes.fetch_grammars``). A test that reaches for them on the network is refused
(``tool_homes.refuse_grammar_fetches``). A run that could not fetch them fails each test that
asked, saying why, rather than letting it fetch them itself or skip.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import tool_homes

from personalclaw import net
from personalclaw.codegraph import grammars

THE_RELEASES = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download"


def test_a_test_that_asks_for_the_grammars_finds_them_on_this_machine(code_map_grammars):
    """The grammars are unpacked before the test starts, so it parses with no fetch of its own."""
    import tree_sitter_language_pack as pack

    assert "python" in pack.downloaded_languages()


def test_a_test_is_refused_the_grammars_from_the_network(monkeypatch):
    """The grammar list and the bundle are refused before anything is sent, and the refusal says
    which fixture brings them."""
    asked: list[str] = []

    async def fetch(url, **_):
        asked.append(url)
        raise AssertionError(f"a test reached the network for {url}")

    monkeypatch.setattr(net, "fetch", fetch)
    for url in (
        f"{THE_RELEASES}/v1.20.0/parsers.json",
        f"{THE_RELEASES}/v1.20.0/parsers-linux-x86_64.tar.zst",
    ):
        with pytest.raises(grammars.GrammarUnavailable, match="code_map_grammars"):
            grammars._get(url, max_bytes=1_000, timeout_s=1.0, what="the grammar list")
    assert asked == [], "a test's grammar fetch was sent"


def test_a_server_the_test_runs_itself_is_still_fetched_from(monkeypatch):
    """A test of the fetch itself (``test_code_map_grammars_go_through_the_guard``) points it at
    a server on this computer, which the refusal leaves alone."""

    async def fetch(url, **_):
        return SimpleNamespace(status=200, body=b"a manifest", truncated=False)

    monkeypatch.setattr(net, "fetch", fetch)
    body = grammars._get(
        "http://127.0.0.1:9/releases/v1/parsers.json", max_bytes=100, timeout_s=1.0, what="x"
    )
    assert body == b"a manifest"


# ── The fetch before the first test ──────────────────────────────────────────────────────────


def _runs(*outcomes):
    """A stand-in for ``subprocess.run`` that answers with *outcomes* in turn, and records each
    call's environment."""
    calls: list[dict[str, str]] = []

    def run(argv, *, env, timeout, **_):
        calls.append(env)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, outcome[0], "", outcome[1])

    return run, calls


ANSWERED_504 = "The code map could not fetch the grammar list: https://github.com/... answered 504."


def test_a_failed_fetch_is_tried_again(tmp_path):
    run, calls = _runs((3, ANSWERED_504), (3, ANSWERED_504), (0, ""))
    waited: list[float] = []
    assert tool_homes.fetch_grammars(tmp_path, run=run, sleep=waited.append) == ""
    assert len(calls) == 3
    assert waited == list(tool_homes.GRAMMAR_FETCH_WAITS_S)


def test_the_last_failure_is_said_after_every_try(tmp_path):
    run, calls = _runs(*[(3, f"Traceback ...\n{ANSWERED_504}\n")] * tool_homes.GRAMMAR_FETCH_TRIES)
    why = tool_homes.fetch_grammars(tmp_path, run=run, sleep=lambda _: None)
    assert len(calls) == tool_homes.GRAMMAR_FETCH_TRIES
    assert f"{tool_homes.GRAMMAR_FETCH_TRIES} tries" in why
    assert why.endswith(ANSWERED_504)


def test_a_fetch_that_hangs_is_ended_and_tried_again(tmp_path):
    hung = subprocess.TimeoutExpired(["python"], tool_homes.GRAMMAR_FETCH_TIMEOUT_S)
    run, calls = _runs(hung, (0, ""))
    assert tool_homes.fetch_grammars(tmp_path, run=run, sleep=lambda _: None) == ""
    assert len(calls) == 2


def test_the_fetch_runs_in_a_home_of_its_own_and_keeps_the_runs_grammars(tmp_path, monkeypatch):
    """The fetch's network settings and its audit are a home of its own, never the owner's, and
    the grammars land where every test of the run reads them."""
    run, calls = _runs((0, ""))
    tool_homes.fetch_grammars(tmp_path, run=run, sleep=lambda _: None)
    env = calls[0]
    assert Path(env["PERSONALCLAW_HOME"]).is_relative_to(tmp_path)
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    for name in tool_homes.GRAMMAR_SETTINGS:
        assert env[name] == tool_homes.os.environ[name]


def test_only_a_run_with_a_test_that_asks_fetches_them():
    asks = SimpleNamespace(fixturenames=["tmp_path", tool_homes.GRAMMAR_FIXTURE])
    other = SimpleNamespace(fixturenames=["tmp_path"])
    assert tool_homes.asks_for_grammars([other, asks])
    assert not tool_homes.asks_for_grammars([other, SimpleNamespace()])
