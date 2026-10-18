"""The weekly check that every action pinned by SHA is still the release its comment names.

``tools/ci/action_pins.py`` runs against a fake GitHub API that counts its calls, so each test
says what was asked as well as what was answered.
"""

from __future__ import annotations

import re

import pytest
from ci_action_pins_shared import OTHER, REPO, SHA, FakeAPI, _commit, _ref, _workflow

from tools.ci import action_pins

TAG_OBJECT = "c" * 40


def test_a_sha_that_is_its_tags_commit_passes() -> None:
    api = FakeAPI({_ref("actions/checkout", "v7.0.1"): _commit(SHA)})
    files = _workflow(f"uses: actions/checkout@{SHA} # v7.0.1")
    assert action_pins.check(files, api) == []
    assert api.calls == {_ref("actions/checkout", "v7.0.1"): 1}


def test_a_sha_that_is_not_its_tags_commit_fails() -> None:
    api = FakeAPI({_ref("actions/checkout", "v7.0.1"): _commit(OTHER)})
    problems = action_pins.check(_workflow(f"uses: actions/checkout@{SHA} # v7.0.1"), api)
    assert len(problems) == 1 and f"which is {OTHER}" in problems[0]


def test_an_annotated_tag_is_followed_to_its_commit() -> None:
    api = FakeAPI(
        {
            _ref("moby/buildkit", "v0.34.0"): (200, {"object": {"type": "tag", "sha": TAG_OBJECT}}),
            f"/repos/moby/buildkit/git/tags/{TAG_OBJECT}": _commit(SHA),
        }
    )
    assert action_pins.check(_workflow(f"uses: moby/buildkit@{SHA} # v0.34.0"), api) == []
    assert api.calls[f"/repos/moby/buildkit/git/tags/{TAG_OBJECT}"] == 1
    # The tag object's own id is not the commit: pinning it would fail.
    problems = action_pins.check(_workflow(f"uses: moby/buildkit@{TAG_OBJECT} # v0.34.0"), api)
    assert len(problems) == 1


def test_a_release_pinned_many_times_is_asked_about_once() -> None:
    api = FakeAPI({_ref("actions/checkout", "v7.0.1"): _commit(SHA)})
    line = f"uses: actions/checkout@{SHA} # v7.0.1"
    files = _workflow(line, line, line) + [("full.yml", f"    - {line}\n")]
    assert action_pins.check(files, api) == []
    assert sum(api.calls.values()) == 1


def test_a_subpath_action_is_asked_of_its_repository() -> None:
    api = FakeAPI({_ref("anchore/sbom-action", "v0.24.3"): _commit(SHA)})
    files = _workflow(f"uses: anchore/sbom-action/download-syft@{SHA} # v0.24.3")
    assert action_pins.check(files, api) == []
    assert list(api.calls) == [_ref("anchore/sbom-action", "v0.24.3")]


def test_a_tag_that_is_gone_fails() -> None:
    problems = action_pins.check(_workflow(f"uses: actions/checkout@{SHA} # v7.0.1"), FakeAPI({}))
    assert len(problems) == 1 and "the tag is gone" in problems[0]


def test_a_pin_whose_comment_names_no_release_fails_without_a_call() -> None:
    api = FakeAPI({})
    for comment in ("", " # v7", " # main"):
        problems = action_pins.check(_workflow(f"uses: actions/checkout@{SHA}{comment}"), api)
        assert len(problems) == 1 and "names no" in problems[0], comment
    assert api.calls == {}


@pytest.mark.parametrize(
    "answer",
    [(500, None), (403, None), (0, "network unreachable"), (200, {"message": "odd"})],
    ids=["server-error", "rate-limited", "unreachable", "malformed"],
)
def test_an_answer_the_api_could_not_give_is_unresolved_never_a_pass(answer) -> None:
    api = FakeAPI({_ref("actions/checkout", "v7.0.1"): answer})
    problems = action_pins.check(_workflow(f"uses: actions/checkout@{SHA} # v7.0.1"), api)
    assert len(problems) == 1 and "unresolved" in problems[0]


def test_a_tag_chain_that_never_reaches_a_commit_is_unresolved() -> None:
    loop = (200, {"object": {"type": "tag", "sha": TAG_OBJECT}})
    api = FakeAPI({_ref("x/y", "v1.0.0"): loop, f"/repos/x/y/git/tags/{TAG_OBJECT}": loop})
    problems = action_pins.check(_workflow(f"uses: x/y@{SHA} # v1.0.0"), api)
    assert len(problems) == 1 and "annotated tags deep" in problems[0]
    assert api.calls[f"/repos/x/y/git/tags/{TAG_OBJECT}"] == action_pins.MAX_TAG_HOPS


def test_the_repositorys_pins_are_all_read() -> None:
    """Every SHA-pinned `uses:` in the tree is one the check reads, with its release."""
    files = [
        (p.name, p.read_text(encoding="utf-8")) for p in action_pins.workflow_and_action_files(REPO)
    ]
    found, problems = action_pins.pins(files)
    assert problems == []
    # Counted apart from the reader: every line that is a `uses:` key (not a comment quoting
    # one) of a remote action.
    expected = sum(
        1
        for _, text in files
        for line in text.splitlines()
        if re.match(r"\s*(?:-\s+)?uses:\s*[^\s.#][^\s]*@", line)
        and not line.split("uses:", 1)[1].strip().startswith("docker://")
    )
    assert len(found) == expected >= 100


def _chain(depth: int) -> dict[str, tuple[int, object]]:
    """A tag ref, then *depth* annotated tag objects, the last of which names SHA."""
    objects = [f"{i:040x}" for i in range(1, depth + 1)]
    answers: dict[str, tuple[int, object]] = {
        _ref("x/y", "v1.0.0"): (200, {"object": {"type": "tag", "sha": objects[0]}})
    }
    for i, name in enumerate(objects):
        target = (
            {"type": "commit", "sha": SHA}
            if i == depth - 1
            else {"type": "tag", "sha": objects[i + 1]}
        )
        answers[f"/repos/x/y/git/tags/{name}"] = (200, {"object": target})
    return answers


def test_a_chain_exactly_max_tag_hops_deep_resolves() -> None:
    api = FakeAPI(_chain(action_pins.MAX_TAG_HOPS))
    assert action_pins.check(_workflow(f"uses: x/y@{SHA} # v1.0.0"), api) == []
    assert sum(n for p, n in api.calls.items() if "/git/tags/" in p) == action_pins.MAX_TAG_HOPS


def test_a_chain_one_deeper_is_unresolved_after_max_fetches() -> None:
    api = FakeAPI(_chain(action_pins.MAX_TAG_HOPS + 1))
    problems = action_pins.check(_workflow(f"uses: x/y@{SHA} # v1.0.0"), api)
    assert len(problems) == 1 and "annotated tags deep" in problems[0]
    assert sum(n for p, n in api.calls.items() if "/git/tags/" in p) == action_pins.MAX_TAG_HOPS


def test_one_release_pinned_at_two_shas_is_one_call_and_one_mismatch() -> None:
    api = FakeAPI({_ref("actions/checkout", "v7.0.1"): _commit(SHA)})
    files = _workflow(f"uses: actions/checkout@{SHA} # v7.0.1") + [
        ("other.yml", f"    - uses: actions/checkout@{OTHER} # v7.0.1\n")
    ]
    problems = action_pins.check(files, api)
    assert sum(api.calls.values()) == 1
    assert len(problems) == 1 and problems[0].startswith("other.yml:1:")


class _Server:
    """A local socket server that answers each connection with *reply*, then closes it."""

    def __init__(self, reply: bytes) -> None:
        import socket
        import threading

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.reply = reply
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                if self.reply:
                    conn.sendall(self.reply)

    def close(self) -> None:
        self.sock.close()


@pytest.mark.parametrize(
    "reply",
    [b"", b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"object"'],
    ids=["closed-before-status", "short-body"],
)
def test_a_connection_that_fails_below_http_is_unresolved_not_a_crash(
    reply: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _Server(reply)
    try:
        monkeypatch.setattr(action_pins, "API", f"http://127.0.0.1:{server.port}")
        fetch = action_pins.github_fetch(None)
        files = _workflow(f"uses: a/b@{SHA} # v1.0.0", f"uses: c/d@{SHA} # v1.0.0")
        problems = action_pins.check(files, fetch)
    finally:
        server.close()
    assert len(problems) == 2
    assert all("unresolved: the API answered 0" in p for p in problems), problems
