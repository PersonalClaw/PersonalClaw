"""An update right after a release waits for the package index, then says it is still publishing.

The update check names its target from GitHub's release list, which has a release at once. The
installer resolves `personalclaw==<version>` through PyPI's simple index, whose page is served from
a CDN cache (`max-age=600`), so in the minutes after a publish it can answer "there is no version
of personalclaw==<version>" for a release that is there, and the panel showed the resolver's text
as the failure. The same update a few minutes later worked.

An installer that finds no version of the release the check named is asked again after a wait
(`self_update.UNPUBLISHED_RETRY_WAITS`: 30 s, then 90 s), inside the update's progress. A Cancel
during a wait stops it. Still none, the update fails with one plain sentence. Any other installer
failure is reported as it was.

Driven through the dashboard's `POST /api/update` on a wheel install, with the stand-in `uv` of
`test_a_failed_update_puts_the_checkout_back` answering as uv does when the index lacks the release.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw import self_update
from personalclaw.dashboard.handlers import updates as upd
from tests.test_a_failed_update_puts_the_checkout_back import (  # noqa: F401 (the fixtures)
    _request,
    _settle,
    _State,
    installer,
    release,
)

#: What uv says when the index has no `personalclaw==0.0.2` yet (the release the check names).
_NO_VERSION = (
    "  × No solution found when resolving dependencies:\n"
    "  ╰─▶ Because there is no version of personalclaw==0.0.2 and you require\n"
    "      personalclaw==0.0.2, we can conclude that your requirements are unsatisfiable.\n"
)
_STILL = "PyPI is still publishing 0.0.2. Try again in a few minutes."


def _answers_no_version(ran, tmp_path, times: int) -> None:
    """The stand-in `uv` answers "no version" *times* times, then installs."""
    count = tmp_path / "asked"
    uv = ran.log.parent / "bin" / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'echo x >> "{count}"\n'
        f'printf "uv||%s\\n" "$*" >> "{ran.log}"\n'
        f'if [ "$(wc -l < "{count}")" -le {times} ]; then\n'
        f"  printf '%s' '{_NO_VERSION}' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    uv.chmod(0o755)


def _installs(ran) -> int:
    return sum(1 for line in ran.log.read_text().splitlines() if line.startswith("uv|"))


@pytest.fixture
def wheel(installer, release, monkeypatch, tmp_path):  # noqa: F811 - the imported fixtures
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "pip")
    return installer("ok")


async def _until(state: _State, said: str) -> None:
    for _ in range(400):
        if any(said in detail for _step, detail in state.progress):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the update never said {said!r}: {state.progress}")


def test_the_waits_are_half_a_minute_then_a_minute_and_a_half():
    assert self_update.UNPUBLISHED_RETRY_WAITS == (30.0, 90.0)


@pytest.mark.asyncio
async def test_a_release_the_index_has_not_published_yet_is_installed_once_it_has(
    wheel, release, monkeypatch, tmp_path  # noqa: F811 - the imported fixture
):
    monkeypatch.setattr(self_update, "UNPUBLISHED_RETRY_WAITS", (0.01, 0.01), raising=False)
    _answers_no_version(wheel, tmp_path, times=1)
    state = _State()

    await upd.api_update_apply(_request(state))
    await _settle(state)

    assert _installs(wheel) == 2, "asked the installer once only"
    assert any("not published 0.0.2 yet" in d for _s, d in state.progress), state.progress
    assert not any(step == "error" for step, _d in state.progress), state.progress
    assert release.restarts, "the update that installed did not restart into it"


@pytest.mark.asyncio
async def test_a_release_still_unpublished_after_the_waits_fails_in_one_plain_sentence(
    wheel, release, monkeypatch, tmp_path  # noqa: F811 - the imported fixture
):
    monkeypatch.setattr(self_update, "UNPUBLISHED_RETRY_WAITS", (0.01, 0.01), raising=False)
    _answers_no_version(wheel, tmp_path, times=3)
    state = _State()

    await upd.api_update_apply(_request(state))
    await _settle(state)

    assert _installs(wheel) == 3
    assert state.progress[-1] == ("error", _STILL), state.progress
    assert not release.restarts


@pytest.mark.asyncio
async def test_a_cancel_during_a_wait_stops_the_update(
    wheel, release, monkeypatch, tmp_path  # noqa: F811 - the imported fixture
):
    monkeypatch.setattr(self_update, "UNPUBLISHED_RETRY_WAITS", (30.0, 90.0), raising=False)
    _answers_no_version(wheel, tmp_path, times=3)
    state = _State()
    await upd.api_update_apply(_request(state))
    try:
        await _until(state, "not published 0.0.2 yet")

        await upd.api_update_cancel(_request(state))
        await asyncio.wait_for(_settle(state), timeout=10)

        assert _installs(wheel) == 1, "it asked the installer again after the Cancel"
        assert state.progress[-1][0] == "cancelled", state.progress
        assert not release.restarts
    finally:
        await _settle(state)


def test_both_installers_saying_no_version_read_as_not_yet_published():
    assert self_update.finds_no_version(_NO_VERSION, "0.0.2")
    pip = "ERROR: No matching distribution found for personalclaw==0.0.2\n"
    assert self_update.finds_no_version(pip, "v0.0.2")
    assert not self_update.finds_no_version(_NO_VERSION, "0.0.3"), "another release's"
    assert not self_update.finds_no_version("ERROR: alpha==2.0 needs beta>=3\n", "0.0.2")


def test_a_release_for_another_python_is_not_still_publishing():
    """pip answers "No matching distribution found" for a release the index has only for a newer
    Python, after naming it as one that requires a different Python version. That is no wait's to
    fix: the installer's own failure is reported."""
    pip = (
        "ERROR: Ignored the following versions that require a different python version: "
        "0.0.2 Requires-Python >=3.14\n"
        "ERROR: Could not find a version that satisfies the requirement personalclaw==0.0.2 "
        "(from versions: 0.0.1)\n"
        "ERROR: No matching distribution found for personalclaw==0.0.2\n"
    )

    assert not self_update.finds_no_version(pip, "0.0.2")
    other = pip.replace("0.0.2 Requires-Python", "0.0.3 Requires-Python")
    assert self_update.finds_no_version(other, "0.0.2"), "another release needs another Python"


def test_the_version_is_the_one_named_after_personalclaw():
    """A version anywhere else in the failure is not the release's: only `personalclaw==<v>`
    after the installer's no-version words counts."""
    elsewhere = "ERROR: No matching distribution found for helper==0.0.2 (personalclaw==0.0.1)\n"

    assert not self_update.finds_no_version(elsewhere, "0.0.2")


def test_a_candidates_tag_is_the_version_uv_names():
    uv = _NO_VERSION.replace("0.0.2", "0.3.0rc1")

    assert self_update.finds_no_version(uv, "v0.3.0-rc.1")
    assert not self_update.finds_no_version(uv, "v0.3.0")
