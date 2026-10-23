"""The weekly check names every workflow image whose tag has moved since it was pinned.

``tools/ci/image_pins.py`` runs against a fake registry that answers a pull token and each tag's
index; each case says what was asked as well as what was answered. A pin it cannot check (no
platform or index in its comment, an unknown registry, a tag that is gone, a platform the index
no longer lists) is reported, never passed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from tools.ci import image_pins as ip

INDEX = "sha256:" + "1" * 64
AMD64 = "sha256:" + "2" * 64
ARM64 = "sha256:" + "3" * 64
NEW_INDEX = "sha256:" + "4" * 64
NEW_AMD64 = "sha256:" + "5" * 64
TOKEN = (
    "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"
)
MANIFEST = "https://registry-1.docker.io/v2/library/python/manifests/3.12.15-slim"


def _index(index: str, amd64: str) -> tuple[int, Mapping[str, str], bytes]:
    body = {
        "manifests": [
            {"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
            {
                "digest": ARM64,
                "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"},
            },
        ]
    }
    return 200, {"docker-content-digest": index}, json.dumps(body).encode()


class FakeRegistry:
    def __init__(self, answers: dict[str, tuple[int, Mapping[str, str], bytes]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self, url: str, headers: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], bytes]:
        self.calls.append((url, dict(headers)))
        return self.answers.get(url, (404, {}, b""))


def _registry(index: str = INDEX, amd64: str = AMD64) -> FakeRegistry:
    return FakeRegistry(
        {TOKEN: (200, {}, json.dumps({"token": "anon"}).encode()), MANIFEST: _index(index, amd64)}
    )


def _workflow(
    pin: str = f"python:3.12.15-slim@{AMD64}", comment: str | None = None
) -> list[tuple[str, str]]:
    note = (
        comment
        if comment is not None
        else f"# python:3.12.15-slim, the linux/amd64 manifest of index {INDEX}."
    )
    text = f"jobs:\n  it:\n    {note}\n    container: {pin}\n"
    return [("ci.yml", text)]


def test_a_pin_that_is_its_tags_digest_passes() -> None:
    registry = _registry()
    assert ip.check(_workflow(), registry) == ([], [])
    assert [u for u, _ in registry.calls] == [TOKEN, MANIFEST]
    assert registry.calls[1][1]["Authorization"] == "Bearer anon"


def test_a_moved_tag_names_the_new_pin_and_index() -> None:
    moved, problems = ip.check(_workflow(), _registry(NEW_INDEX, NEW_AMD64))
    assert problems == [] and len(moved) == 1
    assert moved[0].line() == (
        f"ci.yml:4: python:3.12.15-slim has moved: index {INDEX} -> {NEW_INDEX}; "
        f"pin python:3.12.15-slim@{NEW_AMD64} (its linux/amd64 manifest)"
    )


def test_an_index_that_moved_while_the_platform_kept_its_digest_is_still_named() -> None:
    moved, _ = ip.check(_workflow(), _registry(NEW_INDEX, AMD64))
    assert len(moved) == 1 and moved[0].digest == AMD64


def test_a_comment_with_no_platform_or_index_is_reported() -> None:
    _, problems = ip.check(_workflow(comment="# python, pinned"), _registry())
    assert problems == ["ci.yml:4: the comment above names no platform manifest and index"]


def test_the_comment_may_say_its_platform_and_run_over_lines() -> None:
    comment = (
        f"# ubuntu, as its linux/amd64 manifest (this runner's platform) of index\n    # {INDEX}."
    )
    assert ip.check(_workflow(comment=comment), _registry()) == ([], [])


def test_a_tag_that_is_gone_is_reported_not_passed() -> None:
    registry = _registry()
    registry.answers[MANIFEST] = (404, {}, b"")
    _, problems = ip.check(_workflow(), registry)
    assert problems == ["ci.yml:4: python:3.12.15-slim: the registry answered 404"]


def test_a_platform_the_index_no_longer_lists_is_reported() -> None:
    registry = _registry()
    body = {"manifests": [{"digest": ARM64, "platform": {"os": "linux", "architecture": "arm64"}}]}
    registry.answers[MANIFEST] = (200, {"docker-content-digest": INDEX}, json.dumps(body).encode())
    _, problems = ip.check(_workflow(), registry)
    assert problems == ["ci.yml:4: python:3.12.15-slim: the index lists no linux/amd64 manifest"]


def test_an_unknown_registry_is_reported() -> None:
    _, problems = ip.check(_workflow(f"quay.io/org/tool:1.0@{AMD64}"), _registry())
    assert problems == [
        "ci.yml:4: quay.io/org/tool: quay.io is a registry this check does not know"
    ]


def test_no_pull_token_is_reported() -> None:
    registry = _registry()
    registry.answers[TOKEN] = (503, {}, b"")
    _, problems = ip.check(_workflow(), registry)
    assert problems == ["ci.yml:4: python: no pull token (503)"]


def test_an_answer_with_no_content_digest_is_reported() -> None:
    registry = _registry()
    status, _, body = registry.answers[MANIFEST]
    registry.answers[MANIFEST] = (status, {}, body)
    _, problems = ip.check(_workflow(), registry)
    assert problems == ["ci.yml:4: python:3.12.15-slim: no content digest in the answer"]


@pytest.mark.parametrize(
    "image, host, repository",
    [
        ("ubuntu", "registry-1.docker.io", "library/ubuntu"),
        ("docker.io/tonistiigi/binfmt", "registry-1.docker.io", "tonistiigi/binfmt"),
        ("tonistiigi/binfmt", "registry-1.docker.io", "tonistiigi/binfmt"),
        ("ghcr.io/personalclaw/gateway", "ghcr.io", "personalclaw/gateway"),
        ("mirror.gcr.io/library/node", "mirror.gcr.io", "library/node"),
    ],
)
def test_an_image_is_asked_of_its_registry(image: str, host: str, repository: str) -> None:
    assert ip.registry(image)[:2] == (host, repository)


def test_an_image_pinned_twice_is_asked_about_once_and_docker_urls_are_read() -> None:
    registry = _registry()
    note = f"# the linux/amd64 manifest of index {INDEX}"
    text = (
        f"jobs:\n  a:\n    {note}\n    container: python:3.12.15-slim@{AMD64}\n"
        f"  b:\n    steps:\n      - uses: some/action@x\n        with:\n          {note}\n"
        f"          image: docker://python:3.12.15-slim@{AMD64}\n"
    )
    assert ip.check([("ci.yml", text)], registry) == ([], [])
    assert len(registry.calls) == 2


class FakeGh:
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.commands.append(args)
        if args[0] == "api":
            return json.dumps(self.issues)
        if args[:2] == ("issue", "create"):
            return "https://github.com/owner/project/issues/12\n"
        return ""


BOT_USER = {"login": "github-actions[bot]", "type": "Bot"}


def test_stale_pins_open_the_issue_once_and_then_edit_it() -> None:
    gh = FakeGh([])
    assert ip.publish("owner/project", "body", True, gh) == 12
    gh = FakeGh([{"number": 7, "title": ip.ISSUE_TITLE, "user": BOT_USER}])
    assert ip.publish("owner/project", "body", True, gh) == 7
    assert gh.commands[-1][:3] == ("issue", "edit", "7")


def test_current_pins_close_the_issue_and_touch_no_other() -> None:
    gh = FakeGh(
        [
            {"number": 7, "title": ip.ISSUE_TITLE, "user": BOT_USER},
            {"number": 8, "title": ip.ISSUE_TITLE, "user": {"login": "a-person"}},
        ]
    )
    assert ip.publish("owner/project", "all current", False, gh) is None
    assert [c[:3] for c in gh.commands[1:]] == [("issue", "close", "7")]


def test_the_report_says_what_to_do_and_what_it_could_not_check() -> None:
    moved, _ = ip.check(_workflow(), _registry(NEW_INDEX, NEW_AMD64))
    text = ip.markdown(moved, ["ci.yml:9: quay.io/x: unknown"], "the run")
    assert "put the new digest on the pin and the new index in the comment" in text
    assert f"python:3.12.15-slim@{NEW_AMD64}" in text and "- not checked: ci.yml:9" in text
    assert ip.markdown([], [], "the run").startswith("Every pinned workflow image is its tag's")
