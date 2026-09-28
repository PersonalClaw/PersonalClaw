"""The update check and every apply pick one release, and a pin set back is said as such.

**One selection rule.** The check read GitHub's "Latest" marker on the stable channel, while every
apply picked the highest non-pre-release version from the releases list. The release pipeline
marks every stable cut Latest, so a back-patch of an older line (0.1.9 cut after 0.2.1) takes the
marker: the check then compared an install on 0.2.0 with 0.1.9 and said nothing newer existed,
while `personalclaw update` would have installed 0.2.1. Here GitHub is faked at the HTTP layer,
answering the marker and the list the way the real API does after such a cut, so the check and
the applies are driven through their real fetches.

**A pin set back.** A pin naming a release older than the one running is a rollback set up and
not applied yet. Nothing newer is offered then, so the check reported no update, and the panel
read "Up to date" for an install its own pin was about to take back. `pin_older` names the state.
"""

from __future__ import annotations

import aiohttp
import pytest

from personalclaw import self_update as su

#: The releases list after a back-patch, newest-created first, as GitHub orders it.
_BACK_PATCHED = [
    {"tag_name": "v0.1.9", "name": "0.1.9", "body": "back-patch notes", "prerelease": False},
    {"tag_name": "v0.2.1", "name": "0.2.1", "body": "0.2.1 notes", "prerelease": False},
    {"tag_name": "v0.2.0", "name": "0.2.0", "body": "0.2.0 notes", "prerelease": False},
    {"tag_name": "v0.1.3", "name": "0.1.3", "body": "0.1.3 notes", "prerelease": False},
]


class _Response:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self._payload = payload

    async def json(self) -> object:
        return self._payload

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _GitHub:
    """api.github.com after a back-patch: the release it marks Latest is the back-patch."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, *args: object, **kwargs: object) -> "_GitHub":
        return self

    def get(self, url: str, headers: dict[str, str] | None = None) -> _Response:
        self.urls.append(url)
        if url.endswith("/releases/latest"):
            return _Response(dict(_BACK_PATCHED[0]))
        return _Response([dict(r) for r in _BACK_PATCHED])

    async def __aenter__(self) -> "_GitHub":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch, tmp_path) -> _GitHub:
    fake = _GitHub()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.setattr(aiohttp, "ClientSession", fake)
    return fake


@pytest.mark.asyncio
async def test_a_back_patch_cannot_split_the_check_from_the_apply(github: _GitHub) -> None:
    status = await su.build_update_status("0.2.0")

    # The check names the release every apply installs...
    assert status["latest"] == "0.2.1", status
    assert status["update_available"] is True
    assert status["release_notes"] == "0.2.1 notes"
    assert await su.resolve_wheel_target("stable") == "v0.2.1"
    assert await su.resolve_target("stable") == "v0.2.1"
    assert await su.resolve_image("stable") == ("v0.2.1", "0.2")
    # ...and never asks GitHub which release is marked Latest.
    assert github.urls, "the check fetched nothing, so this test measured nothing"
    assert not [url for url in github.urls if url.endswith("/releases/latest")], github.urls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "running, pin, available, older",
    [
        ("0.2.1", "0.2.0", False, True),  # a pin back: the rollback, set up and not applied
        ("0.2.0", "0.2.0", False, False),  # on the pinned release already
        ("0.2.0", "0.2.1", True, False),  # a pin forward is an update like any other
        ("0.2.1", "", False, False),  # no pin: the channel's newest, and nothing older is named
        ("0.2.1", "0.2.2", False, False),  # a pin naming no release is a pin-miss, not "older"
    ],
)
async def test_a_pin_set_back_is_said_as_such(
    github: _GitHub, tmp_path, running: str, pin: str, available: bool, older: bool
) -> None:
    import json

    (tmp_path / "config.json").write_text(json.dumps({"updates": {"pin": pin}}), encoding="utf-8")

    status = await su.build_update_status(running)

    assert status["update_available"] is available, status
    assert status["pin_older"] is older, status
