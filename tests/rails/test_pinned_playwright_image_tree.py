"""The pinned Playwright image is named by its linux/amd64 digest alone, and runs the locked
Playwright (#3943).

``web/e2e/visual-image.json`` pins ``mcr.microsoft.com/playwright`` by the digest of its
linux/amd64 manifest, with the tag and the index digest it was read from recorded beside it. The
rules, each failed by a planted edit below:

1. The pin holds exactly its five fields: the image as ``mcr.microsoft.com/playwright@sha256:``
   and 64 hex digits, ``linux/amd64``, the Playwright version, its ``v<version>-noble`` tag and
   the index digest.
2. Its Playwright is the one the lock resolves for ``@playwright/test``: ``@playwright/test``,
   its ``playwright`` and that one's ``playwright-core`` (nested, when the top level holds
   another package's older one) are all that version, so a bump moves the lock and the pin in one
   change.
3. No tracked file but the pin names the image: no tag reference anywhere, and no other digest.
   The runner (``web/e2e/run-pinned.mjs``) and the refresh workflow read the pin, never a copy.
   The one other file that names it is the baselines' manifest
   (``web/e2e/visual-environment.json``), which records the environment they were made in, and
   its image is the pinned one.
4. Every committed baseline is ``-linux``: the screenshots, the accessibility trees and the
   findings are made by the Refresh visual baselines workflow against the pinned image's browser,
   so a file with another suffix was made somewhere else. ``web/.gitignore`` keeps a local run's
   own set out of ``git add``; this fails one committed anyway.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import pytest
from rails_repo import REPO

_PIN = "web/e2e/visual-image.json"
_MANIFEST = "web/e2e/visual-environment.json"
_LOCK = "package-lock.json"
_IMAGE = re.compile(r"mcr\.microsoft\.com/playwright@sha256:[0-9a-f]{64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_KEYS = {"image", "platform", "playwright", "tag", "index"}
#: Any reference to the image's repository, with a tag or a digest.
_NAMED = re.compile(r"mcr\.microsoft\.com/playwright(?![\w/-])[:@][^\s\"'`)]+")


def _locked(lock: dict[str, Any]) -> dict[str, str | None]:
    packages = lock.get("packages") or {}

    def version(key: str) -> str | None:
        entry = packages.get(key)
        return entry.get("version") if isinstance(entry, dict) else None

    return {
        "@playwright/test": version("node_modules/@playwright/test"),
        "playwright": version("node_modules/playwright"),
        "playwright-core": version("node_modules/playwright/node_modules/playwright-core")
        or version("node_modules/playwright-core"),
    }


def _pin_problems(pin: Any, lock: dict[str, Any]) -> list[str]:
    if not isinstance(pin, dict):
        return [f"{_PIN} is not an object"]
    bad: list[str] = []
    if set(pin) != _KEYS:
        bad.append(f"{_PIN} holds {sorted(pin)}, not {sorted(_KEYS)}")
    if not _IMAGE.fullmatch(str(pin.get("image"))):
        bad.append(f"{_PIN}'s image {pin.get('image')!r} is not named by its digest")
    if pin.get("platform") != "linux/amd64":
        bad.append(f"{_PIN}'s platform is {pin.get('platform')!r}, not linux/amd64")
    if pin.get("tag") != f"v{pin.get('playwright')}-noble":
        bad.append(f"{_PIN}'s tag {pin.get('tag')!r} is not v{pin.get('playwright')}-noble")
    if not _DIGEST.fullmatch(str(pin.get("index"))):
        bad.append(f"{_PIN}'s index {pin.get('index')!r} is no digest")
    for name, version in _locked(lock).items():
        if version != pin.get("playwright"):
            bad.append(
                f"the lock resolves {name} {version}, and {_PIN} pins {pin.get('playwright')}"
            )
    return bad


def _tracked() -> list[str]:
    done = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [f for f in done.stdout.split("\0") if f]


#: Tests plant the references this forbids, so they are not read.
_TESTS = re.compile(r"^tests/|\.test\.(?:ts|mts|mjs|js)$")


def _texts() -> dict[str, str]:
    """Every tracked text file but the lock and the tests that names the image's repository."""
    out: dict[str, str] = {}
    for rel in _tracked():
        if rel == _LOCK or _TESTS.search(rel):
            continue
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if "mcr.microsoft.com/playwright" in text:
            out[rel] = text
    return out


def _reference_problems(texts: dict[str, str], pin: Any) -> list[str]:
    image = pin.get("image") if isinstance(pin, dict) else None
    return [
        f"{rel} names the image as {found}"
        for rel, text in sorted(texts.items())
        if rel != _PIN
        for found in _NAMED.findall(text)
        if not (rel == _MANIFEST and found == image)
    ]


#: The baseline folders and the one name each file in them may have.
_BASELINES = {
    "web/e2e/__screenshots__/": re.compile(r"-linux\.png$"),
    "web/e2e/__aria__/": re.compile(r"-linux\.aria\.yml$"),
    "web/e2e/__ui-baselines__/": re.compile(r"-linux\.json$"),
}


def _baseline_problems(tracked: list[str]) -> list[str]:
    return [
        f"{rel} is a baseline made outside the pinned image: only -linux ones are committed"
        for rel in tracked
        for folder, name in _BASELINES.items()
        if rel.startswith(folder) and not name.search(rel)
    ]


def _problems(
    pin: Any, lock: dict[str, Any], texts: dict[str, str], tracked: list[str] | None = None
) -> list[str]:
    return [
        *_pin_problems(pin, lock),
        *_reference_problems(texts, pin),
        *_baseline_problems(tracked or []),
    ]


def _pin() -> Any:
    return json.loads((REPO / _PIN).read_text(encoding="utf-8"))


def _lock() -> dict[str, Any]:
    return json.loads((REPO / _LOCK).read_text(encoding="utf-8"))


def test_the_image_is_named_by_its_amd64_digest_with_the_locked_playwright() -> None:
    assert _problems(_pin(), _lock(), _texts(), _tracked()) == []


def test_there_are_baselines_to_hold() -> None:
    """Vacuity: each folder holds -linux baselines, so rule 4 reads a real set."""
    tracked = _tracked()
    for folder in _BASELINES:
        assert any(rel.startswith(folder) for rel in tracked), f"no baseline under {folder}"


@pytest.mark.parametrize(
    "rel",
    [
        "web/e2e/__screenshots__/visual.spec.ts/chat-light-darwin.png",
        "web/e2e/__aria__/chat-darwin.aria.yml",
        "web/e2e/__ui-baselines__/chat-win32.json",
    ],
)
def test_a_baseline_made_elsewhere_fails(rel: str) -> None:
    found = _problems(_pin(), _lock(), {}, [*_tracked(), rel])
    assert any(rel in p and "outside the pinned image" in p for p in found), found


@pytest.mark.parametrize(
    "change, says",
    [
        ({"image": "mcr.microsoft.com/playwright:v1.64.1-noble"}, "is not named by its digest"),
        ({"platform": "linux/arm64"}, "not linux/amd64"),
        ({"playwright": "1.64.0", "tag": "v1.64.0-noble"}, "the lock resolves @playwright/test"),
        ({"tag": "v1.64.1-jammy"}, "is not v1.64.1-noble"),
        ({"index": "latest"}, "is no digest"),
        ({"pulled": "today"}, "not ['image', 'index', 'platform', 'playwright', 'tag']"),
    ],
)
def test_a_planted_pin_fails(change: dict[str, str], says: str) -> None:
    found = _problems({**_pin(), **change}, _lock(), {})
    assert any(says in p for p in found), found


def test_a_lock_that_moves_without_the_pin_fails() -> None:
    lock = _lock()
    nested = "node_modules/playwright/node_modules/playwright-core"
    key = nested if nested in lock["packages"] else "node_modules/playwright-core"
    lock["packages"][key] = {**lock["packages"][key], "version": "1.65.0"}
    found = _problems(_pin(), lock, {})
    assert any("the lock resolves playwright-core 1.65.0" in p for p in found), found


@pytest.mark.parametrize(
    "rel, text, says",
    [
        (
            ".github/workflows/visual-baselines.yml",
            "        run: docker run mcr.microsoft.com/playwright:v1.64.1-noble npx playwright\n",
            "names the image as mcr.microsoft.com/playwright:v1.64.1-noble",
        ),
        (
            "web/e2e/run-pinned.mjs",
            "const IMAGE = 'mcr.microsoft.com/playwright@sha256:" + "0" * 64 + "'\n",
            "web/e2e/run-pinned.mjs names the image",
        ),
        (
            _MANIFEST,
            '{"image": "mcr.microsoft.com/playwright@sha256:' + "0" * 64 + '"}\n',
            f"{_MANIFEST} names the image",
        ),
    ],
)
def test_a_file_that_names_the_image_itself_fails(rel: str, text: str, says: str) -> None:
    found = _problems(_pin(), _lock(), {**_texts(), rel: text})
    assert any(says in p for p in found), found
