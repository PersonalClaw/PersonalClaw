"""The code map's grammars arrive through the egress guard, and the language pack downloads nothing.

The language pack keeps each language's grammar as a shared library, and the first time a language
is parsed it downloads one by itself: a manifest from its releases on GitHub, then one bundle of
every grammar for this machine, tens of megabytes. Its own client made those requests, where the
egress guard never saw them: a host on Denied hosts was reached all the same, and nothing was
audited.

So the pack reads its manifest from a file in the home (``library_env``:
``TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL``), which only this module writes, and with no file there
it fails before it reaches anything. :func:`ensure` fetches the manifest and the bundle through
``net.fetch``, under the connector policy with the owner's Network egress settings on it: a refused
host is never contacted, every redirect is checked again, and every request is audited. It checks
the bundle against the size and digest the manifest gives, and writes the manifest with the
bundle's address on this machine. The pack then copies the bundle into its cache, checks its digest
itself, and unpacks the grammar it was asked for; the copy here is removed once the pack holds its
own. The manifest names only files on this machine, so the pack can never reach the network, even
for a language nothing has asked for yet.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Where the language pack publishes its manifests: the pack's own default, which a test holds to
#: the address the installed pack would use (``tests/test_code_map_grammars_go_through_the_guard``).
RELEASES = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download"

#: The pack's settings this module reads, which ``library_env`` sets.
CACHE_SETTING = "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"
MANIFEST_SETTING = "TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL"

#: The most a manifest may be (the 1.20 manifest is about 25 KB).
MANIFEST_MAX_BYTES = 2_000_000
#: The most a bundle may claim to be (the 1.20 bundles are 21 to 26 MB).
BUNDLE_MAX_BYTES = 256_000_000
#: How long one bundle may take to arrive.
BUNDLE_TIMEOUT_S = 600.0
#: How long a failed fetch is remembered before the next parse may try again: a code map indexes
#: file after file, and each would otherwise fetch, and be refused, once more. A change to the
#: owner's Network egress settings ends it at once, so a refusal's "the next time" holds.
RETRY_AFTER_S = 900.0
#: The schemes a manifest may give for a bundle: the digest is checked either way, and the transfer
#: is private too.
BUNDLE_SCHEMES = frozenset({"https"})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_lock = threading.Lock()
#: The pack version's manifest as fetched, for the life of the process.
_manifests: dict[str, dict[str, Any]] = {}
#: The pack version's last failed fetch: when, the sentence that says why, and the owner's
#: Network egress settings it was made under.
_failed: dict[str, tuple[float, str, tuple[Any, ...]]] = {}


class GrammarUnavailable(RuntimeError):
    """No grammar can be loaded for a language; the message says why, in the owner's words."""


def pack_version() -> str:
    """The installed language pack's version: its manifests are published per version."""
    return importlib.metadata.version("tree-sitter-language-pack")


def platform_key() -> str:
    """This machine's name in the pack's manifest (``macos-arm64``, ``linux-x86_64``, ...)."""
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform == "darwin":
        return f"macos-{'arm64' if arch == 'aarch64' else arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    if sys.platform == "win32":
        return f"windows-{arch}"
    return f"{sys.platform}-{arch}"


def manifest_path() -> Path:
    """The manifest file the pack reads, from the setting it reads it from.

    Every ``personalclaw`` command sets it (``library_env``); a process that did not start as one
    is given here what every command gives itself, so the pack in it cannot fall back to its own
    address. A setting that names anything but a file on this machine is refused: the pack would
    download from there itself."""
    if CACHE_SETTING not in os.environ or MANIFEST_SETTING not in os.environ:
        from personalclaw.library_env import keep_the_libraries_in_the_home

        keep_the_libraries_in_the_home()
    url = os.environ[MANIFEST_SETTING]
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        raise GrammarUnavailable(
            f"The code map's grammars are not fetched: {MANIFEST_SETTING} names {url}, which the "
            "language pack would download from by itself, past PersonalClaw's network settings."
        )
    return Path(urllib.request.url2pathname(parsed.path))


def bundle_path(folder: Path) -> Path:
    """Where the bundle this module fetched waits for the pack to copy it."""
    return folder / f"parsers-{platform_key()}.tar.zst"


def ensure(language: str) -> None:
    """Make *language*'s grammar something the pack can load without downloading anything.

    Raises :class:`GrammarUnavailable` with the reason when it cannot: the language has no grammar,
    the fetch was refused or did not complete, or what arrived is not what the manifest promised.
    """
    import tree_sitter_language_pack as pack

    with _lock:
        manifest_file = manifest_path()
        if not _unpacked(pack, language):
            _fetch_for(pack, language, manifest_file)
        # The pack keeps its own copy of the bundle once it has unpacked from it.
        bundle_path(manifest_file.parent).unlink(missing_ok=True)


def _fetch_for(pack: Any, language: str, manifest_file: Path) -> None:
    """Fetch what *language*'s grammar needs through the egress guard, and have the pack unpack it
    from there; a failure is remembered for :data:`RETRY_AFTER_S`, so each file the code map
    indexes in the meantime is told why rather than asking the network again."""
    version = pack_version()
    settings = _egress_settings()
    failed = _failed.get(version)
    if (
        failed is not None
        and time.monotonic() - failed[0] < RETRY_AFTER_S
        and failed[2] == settings
    ):
        raise GrammarUnavailable(failed[1])
    manifest = _manifest_or_remember_why_not(version, settings)
    if language not in (manifest.get("languages") or {}):
        raise GrammarUnavailable(f"The language pack has no grammar for {language}.")
    try:
        _fetch_bundle(manifest, manifest_file)
        pack.get_parser(language)
    except GrammarUnavailable as exc:
        _failed[version] = (time.monotonic(), str(exc), settings)
        raise
    except Exception as exc:  # noqa: BLE001 — the pack's own refusal, told as a reason
        reason = f"The language pack could not unpack the grammar for {language}: {exc}"
        _failed[version] = (time.monotonic(), reason, settings)
        raise GrammarUnavailable(reason) from exc
    if language not in pack.downloaded_languages():
        reason = f"The language pack did not unpack the grammar for {language}."
        _failed[version] = (time.monotonic(), reason, settings)
        raise GrammarUnavailable(reason)
    _failed.pop(version, None)


def _egress_settings() -> tuple[Any, ...]:
    """What of the owner's Network egress settings decides a fetch here."""
    from personalclaw.net import CONNECTOR, egress_policy_for

    policy = egress_policy_for(CONNECTOR)
    return (policy.allow_hosts, policy.deny_hosts, policy.allow_private)


def _unpacked(pack: Any, language: str) -> bool:
    """Whether the pack holds *language*'s grammar, unpacking it from what is on this machine if it
    can: the manifest it reads names no address anywhere else, and a bundle it already holds serves
    every language in it. Loading a parser is what unpacks one (the pack's ``download`` answers
    without unpacking anything), and what it holds afterwards is the answer."""
    if language in pack.downloaded_languages():
        return True
    try:
        pack.get_parser(language)
    except Exception:  # noqa: BLE001 — not on this machine yet: the caller fetches it
        logger.debug("grammars: no grammar for %s on this machine yet", language, exc_info=True)
        return False
    return language in pack.downloaded_languages()


def _manifest_or_remember_why_not(version: str, settings: tuple[Any, ...]) -> dict[str, Any]:
    try:
        return _manifest(version)
    except GrammarUnavailable as exc:
        _failed[version] = (time.monotonic(), str(exc), settings)
        raise


def _manifest(version: str) -> dict[str, Any]:
    """The pack's manifest for *version*, fetched through the egress guard once a process."""
    if version in _manifests:
        return _manifests[version]
    url = f"{RELEASES}/v{version}/parsers.json"
    body = _get(url, max_bytes=MANIFEST_MAX_BYTES, timeout_s=60.0, what="the grammar list")
    try:
        manifest = json.loads(body)
    except ValueError as exc:
        raise GrammarUnavailable(f"The grammar list from {url} is not JSON ({exc}).") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("platforms"), dict):
        raise GrammarUnavailable(f"The grammar list from {url} has no bundles in it.")
    _manifests[version] = manifest
    return manifest


def _fetch_bundle(manifest: dict[str, Any], manifest_file: Path) -> None:
    """Fetch this machine's bundle through the egress guard, check it against *manifest*, and
    write the manifest the pack reads, naming the bundle's place on this machine."""
    from personalclaw.atomic_write import atomic_write, atomic_write_bytes

    key = platform_key()
    entry = manifest["platforms"].get(key)
    if not isinstance(entry, dict):
        raise GrammarUnavailable(f"The language pack publishes no grammars for {key}.")
    url, digest, size = entry.get("url"), entry.get("sha256"), entry.get("size")
    if not (isinstance(url, str) and urllib.parse.urlparse(url).scheme in BUNDLE_SCHEMES):
        raise GrammarUnavailable(f"The grammar list gives no https address for {key}'s grammars.")
    if not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
        raise GrammarUnavailable(f"The grammar list gives no digest for {key}'s grammars.")
    if not (isinstance(size, int) and 0 < size <= BUNDLE_MAX_BYTES):
        raise GrammarUnavailable(f"The grammar list gives no usable size for {key}'s grammars.")
    body = _get(url, max_bytes=size + 1, timeout_s=BUNDLE_TIMEOUT_S, what="the grammars")
    if len(body) != size:
        raise GrammarUnavailable(
            f"The grammars from {url} were {len(body)} bytes, and the grammar list says {size}."
        )
    if hashlib.sha256(body).hexdigest() != digest:
        raise GrammarUnavailable(
            f"The grammars from {url} are not the ones the grammar list names (their digest "
            "differs), so they were not kept."
        )
    folder = manifest_file.parent
    bundle = bundle_path(folder)
    atomic_write_bytes(bundle, body)
    local = {
        **manifest,
        "platforms": {key: {"url": bundle.as_uri(), "sha256": digest, "size": size}},
    }
    atomic_write(manifest_file, json.dumps(local))


def _get(url: str, *, max_bytes: int, timeout_s: float, what: str) -> bytes:
    """GET *url* through the egress chokepoint, from sync code, and return the body."""
    from personalclaw.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch
    from personalclaw.net.guard import refusal_for

    policy = egress_policy_for(CONNECTOR).with_overrides(max_bytes=max_bytes, timeout_s=timeout_s)
    try:
        response = _run(fetch(url, policy=policy))
    except EgressBlocked as exc:
        then = "and the code map fetches it the next time it needs a grammar"
        raise GrammarUnavailable(
            f"The code map could not fetch {what}. " + refusal_for(url, exc.decision, then=then)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — the network's failure, told as a reason
        raise GrammarUnavailable(f"The code map could not fetch {what} from {url}: {exc}") from exc
    if response.status != 200:
        raise GrammarUnavailable(
            f"The code map could not fetch {what}: {url} answered {response.status}."
        )
    if response.truncated:
        raise GrammarUnavailable(
            f"The code map did not keep {what} from {url}: more arrived than the grammar list says."
        )
    return response.body


def _run(coro: Any) -> Any:
    """Run *coro* to its end from sync code: here when no loop runs in this thread, else in a
    thread of its own (the code map indexes on a worker thread; a caller on a loop waits here, as
    it waited on the pack's own download before), which carries the caller's context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    from personalclaw.memory_writes import ScopeCarryingExecutor

    with ScopeCarryingExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def forget() -> None:
    """Forget the fetched manifests and the failures remembered (for a test, or a retry now)."""
    with _lock:
        _manifests.clear()
        _failed.clear()


__all__ = ["GrammarUnavailable", "ensure", "forget", "manifest_path", "pack_version"]
