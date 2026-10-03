"""Core features: what this core offers apps, by name, and that each name keeps its promise.

An app asks ``personalclaw.sdk.features.core_has`` whether this core offers a contract, and names
the ones it relies on in ``requiresCoreFeatures``, which the compatibility check reads. A name is
therefore a published surface twice over: an app's code branches on it, and an app's manifest is
refused or admitted by it. So a name, once offered, is never withdrawn, and every offered name is
held here to the contract it stands for: a core that kept the name and lost the contract would be
the version skew this exists to catch, one level down.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw.apps.core_features import FEATURE_NAME_RE
from personalclaw.sdk import features
from personalclaw.sdk.features import APPROVAL_ANSWERS, CORE_FEATURES, GUARDED_DOWNLOAD, core_has

#: Every name a core has offered. A name leaves this set only with a deliberate break of every app
#: that declares it, so a removal from CORE_FEATURES fails here first.
OFFERED_ONCE = {"approval-answers", "guarded-download"}


def test_the_sdk_publishes_the_names_and_the_question():
    assert set(features.__all__) == {
        "APPROVAL_ANSWERS",
        "CORE_FEATURES",
        "GUARDED_DOWNLOAD",
        "core_has",
    }
    assert APPROVAL_ANSWERS == "approval-answers"
    assert GUARDED_DOWNLOAD == "guarded-download"
    for name in (APPROVAL_ANSWERS, GUARDED_DOWNLOAD):
        assert name in CORE_FEATURES
        assert core_has(name) is True


def test_a_feature_this_core_does_not_offer_is_answered_no():
    assert core_has("a-feature-of-a-newer-core") is False
    assert core_has("") is False


def test_a_name_once_offered_is_never_withdrawn():
    assert OFFERED_ONCE <= CORE_FEATURES, (
        f"withdrawn: {sorted(OFFERED_ONCE - CORE_FEATURES)} — an app that declares a "
        "withdrawn name can no longer be installed anywhere"
    )


@pytest.mark.parametrize("name", sorted(CORE_FEATURES))
def test_every_name_is_a_name(name):
    assert FEATURE_NAME_RE.match(name), name


# ── each name is held to its contract ─────────────────────────────────────────────────────────


def _approval_answers_hold() -> None:
    """A channel's approval prompt is handed answers it can offer, whichever way it gets its brief:
    the one core stamps when it asks (from the dashboard's pending entry), one composed from the
    event for an approval the channel's own turn raised, and a stamped brief that came without
    answers, which is composed again rather than handed over with nothing to press."""
    from personalclaw.approval_brief import (
        APPROVAL_BRIEF_META_KEY,
        compose_approval_brief,
        entry_approval_brief,
    )
    from personalclaw.channel_delivery import APPROVAL_ENDINGS, ApprovalAnswer
    from personalclaw.sdk.channel import approval_brief_for

    def offerable(brief: dict | None) -> list[dict]:
        assert brief is not None
        answers = brief.get("answers")
        assert isinstance(answers, list) and answers, f"no answers in {brief!r}"
        for answer in answers:
            assert set(answer) == set(ApprovalAnswer.__dataclass_fields__)
            assert all(isinstance(v, str) for v in answer.values())
            assert answer["key"] and answer["label"] and answer["word"]
            assert answer["ends"] in APPROVAL_ENDINGS[:2]
        return answers

    event = SimpleNamespace(
        title="write_file", tool_input='{"path": "notes.md"}', tool_purpose="", tool_meta={}
    )
    composed = offerable(compose_approval_brief(event))
    assert [a["ends"] for a in composed] == ["approved", "rejected"]

    stamped = offerable(entry_approval_brief({"tool": "write_file", "tool_input": "{}"}))
    assert {a["key"] for a in stamped} >= {"approved", "rejected"}

    bare = {"tool": "write_file", "input": "", "purpose": "", "summary": ""}
    event.tool_meta = {APPROVAL_BRIEF_META_KEY: bare}
    assert offerable(approval_brief_for(event)) == composed


def _guarded_download_holds() -> None:
    """``open_url`` asks the guard before a request is sent: a source on this machine, which the
    owner has not allowed, is refused and never contacted, and once allowed it is read."""
    import http.server
    import json
    import threading

    from personalclaw.config.loader import config_dir
    from personalclaw.sdk.net import EgressBlocked, open_url

    asked: list[str] = []

    class _Source(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server's name
            asked.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"bytes")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/file"
    try:
        with pytest.raises(EgressBlocked):
            open_url(url, timeout_s=10)
        assert asked == [], "a refused source was contacted"
        (config_dir() / "config.json").write_text(
            json.dumps({"security": {"egress": {"allow_hosts": ["127.0.0.1"]}}}), encoding="utf-8"
        )
        with open_url(url, timeout_s=10) as response:
            assert response.read() == b"bytes"
        assert asked == ["/file"]
    finally:
        server.shutdown()
        server.server_close()


#: The check that holds each offered feature to its contract. A name without one fails below.
WITNESSES = {APPROVAL_ANSWERS: _approval_answers_hold, GUARDED_DOWNLOAD: _guarded_download_holds}


def test_every_offered_feature_has_a_witness():
    assert set(WITNESSES) == set(CORE_FEATURES), (
        "a core feature is offered with nothing holding it to what it promises: add its check "
        "to WITNESSES"
    )


@pytest.mark.parametrize("name", sorted(WITNESSES))
def test_each_offered_feature_keeps_its_promise(name):
    WITNESSES[name]()
