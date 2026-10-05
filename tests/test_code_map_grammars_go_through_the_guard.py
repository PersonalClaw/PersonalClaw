"""The code map's grammars arrive through the egress guard, and the language pack downloads nothing.

The language pack downloaded each grammar by itself, with a client of its own, from its releases:
past the egress guard, so a host on Denied hosts was reached all the same and nothing was audited.
Now PersonalClaw fetches the pack's manifest and this machine's bundle through ``net.fetch``, checks
the bundle against the manifest, and hands the pack a manifest that names only files on this machine
(``codegraph/grammars.py``). These drive that with a loopback stand-in for the releases and a
stand-in for the pack, and drive the real pack in a fresh interpreter with no route off the machine.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import zstandard

from personalclaw import record_files
from personalclaw.codegraph import grammars
from personalclaw.config.loader import config_dir
from personalclaw.library_env import library_env
from personalclaw.sel import sel

VERSION = grammars.pack_version()
KEY = grammars.platform_key()
#: What a grammar's shared library is called inside a bundle on this machine.
LIBRARY = f"libtree_sitter_python.{'dylib' if sys.platform == 'darwin' else 'so'}"


def _bundle(payload: bytes = b"a grammar for python") -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo(f"./{LIBRARY}")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return zstandard.ZstdCompressor().compress(raw.getvalue())


class _Releases(http.server.ThreadingHTTPServer):
    """The pack's releases, on this machine: a manifest and one bundle, each request recorded."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ReleasesHandler)
        self.paths: list[str] = []
        self.bundle = _bundle()
        self.listed: dict[str, object] = {}  # overrides for the bundle's manifest entry

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def manifest(self) -> bytes:
        entry = {
            "url": f"{self.url}/bundle.tar.zst",
            "sha256": hashlib.sha256(self.bundle).hexdigest(),
            "size": len(self.bundle),
            **self.listed,
        }
        return json.dumps(
            {
                "version": VERSION,
                "platforms": {KEY: entry},
                "languages": {"python": {"group": "all", "size": 0}},
                "groups": {"all": ["python"]},
            }
        ).encode()


class _ReleasesHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        if self.path == f"/releases/v{VERSION}/parsers.json":
            body = self.server.manifest()  # type: ignore[attr-defined]
        elif self.path == "/bundle.tar.zst":
            body = self.server.bundle  # type: ignore[attr-defined]
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Pack:
    """The language pack's calls the code map makes, reading what the real pack reads: the
    manifest file its setting names, and the bundle that manifest names, which it checks."""

    #: The languages the pack itself knows: python, which the releases' manifest lists, and go,
    #: which it does not.
    known = frozenset({"python", "go"})

    def __init__(self) -> None:
        self.unpacked: set[str] = set()
        self.read: list[str] = []

    def has_language(self, language: str) -> bool:
        return language in self.known

    def downloaded_languages(self) -> list[str]:
        return sorted(self.unpacked)

    def get_parser(self, language: str) -> object:
        url = os.environ[grammars.MANIFEST_SETTING]
        self.read.append(url)
        path = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
        if not path.is_file():
            raise RuntimeError(f"Failed to read manifest from {url}")
        entry = json.loads(path.read_text(encoding="utf-8"))["platforms"][KEY]
        assert entry["url"].startswith("file://"), f"the pack was handed {entry['url']}"
        self.read.append(entry["url"])
        body = Path(urllib.request.url2pathname(urllib.parse.urlparse(entry["url"]).path))
        assert hashlib.sha256(body.read_bytes()).hexdigest() == entry["sha256"]
        self.unpacked.add(language)
        return object()


def _egress(**settings: list[str]) -> None:
    """This test home's Network egress settings."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": settings}}), encoding="utf-8"
    )


def _audited() -> list[dict]:
    return [row for row in sel().recent(500) if row.get("operation") == "egress_fetch"]


@pytest.fixture
def releases(monkeypatch):
    server = _Releases()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(grammars, "RELEASES", f"{server.url}/releases")
    # The stand-in serves its bundle over plain HTTP; the real releases are https only.
    monkeypatch.setattr(grammars, "BUNDLE_SCHEMES", frozenset({"https", "http"}))
    grammars.forget()
    try:
        yield server
    finally:
        grammars.forget()
        server.shutdown()
        server.server_close()


@pytest.fixture
def pack(monkeypatch, tmp_path):
    stand_in = _Pack()
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", stand_in)
    folder = tmp_path / "grammars"
    monkeypatch.setenv(grammars.CACHE_SETTING, str(folder))
    monkeypatch.setenv(grammars.MANIFEST_SETTING, (folder / "parsers.json").as_uri())
    return stand_in


def test_a_grammar_arrives_through_the_guard_and_is_audited(releases, pack, tmp_path):
    _egress(allow_hosts=["127.0.0.1"])

    grammars.ensure("python")

    assert pack.unpacked == {"python"}
    assert releases.paths == [f"/releases/v{VERSION}/parsers.json", "/bundle.tar.zst"]
    assert all(url.startswith("file://") for url in pack.read), pack.read
    audited = [(row["outcome"], row["resources"]) for row in _audited()]
    for path in releases.paths:
        assert ("allowed", f"{releases.url}{path}") in audited, audited
    assert not (
        tmp_path / "grammars" / f"parsers-{KEY}.tar.zst"
    ).exists(), "the copy fetched for the pack stayed after the pack had its own"


def test_a_host_on_denied_hosts_is_never_contacted(releases, pack):
    _egress(allow_hosts=["127.0.0.1"], deny_hosts=["127.0.0.1"])

    with pytest.raises(grammars.GrammarUnavailable, match="is on Denied hosts in Settings"):
        grammars.ensure("python")

    assert releases.paths == [], "a denied host was contacted"
    assert pack.unpacked == set()
    assert [row["outcome"] for row in _audited()] == ["denied"]


def test_this_computer_is_refused_unless_the_owner_allows_it(releases, pack):
    with pytest.raises(grammars.GrammarUnavailable, match="Allowed hosts"):
        grammars.ensure("python")
    assert releases.paths == []


def test_a_bundle_that_is_not_the_one_listed_is_not_kept(releases, pack, tmp_path):
    _egress(allow_hosts=["127.0.0.1"])
    releases.listed = {"sha256": "0" * 64}

    with pytest.raises(grammars.GrammarUnavailable, match="digest differs"):
        grammars.ensure("python")

    assert pack.unpacked == set()
    folder = tmp_path / "grammars"
    # Nothing that arrived is kept: the folder holds no more than the empty lock a fetch holds.
    lock = record_files.lock_path(grammars.manifest_path())
    assert [p for p in folder.iterdir() if p != lock] == [], list(folder.iterdir())
    assert lock.read_bytes() == b""


def test_more_than_the_manifest_lists_is_not_kept(releases, pack, tmp_path):
    _egress(allow_hosts=["127.0.0.1"])
    releases.listed = {"size": len(releases.bundle) - 10}

    with pytest.raises(
        grammars.GrammarUnavailable, match="more arrived than the grammar list says"
    ):
        grammars.ensure("python")
    assert pack.unpacked == set()


def test_a_language_the_pack_has_no_grammar_for_asks_the_network_nothing(releases, pack):
    """The pack knows its languages without a manifest, so a language it has no grammar for is
    answered on this machine: nothing is fetched to say no."""
    _egress(allow_hosts=["127.0.0.1"])

    with pytest.raises(grammars.GrammarUnavailable, match="has no grammar for klingon"):
        grammars.ensure("klingon")

    assert releases.paths == []
    assert pack.read == []


def test_a_language_the_release_does_not_list_fetches_no_bundle(releases, pack):
    _egress(allow_hosts=["127.0.0.1"])

    with pytest.raises(grammars.GrammarUnavailable, match="has no grammar for go"):
        grammars.ensure("go")

    assert releases.paths == [f"/releases/v{VERSION}/parsers.json"]


def test_a_fetch_another_process_is_making_is_waited_for_and_not_made_again(releases, pack):
    """Every process on one home shares its grammar folder: two gateways, a command beside one, the
    workers of one test run. While one fetches, another that needs a grammar waits for it and then
    uses what it unpacked, rather than fetching the same bundle beside it. The other process here
    is this test, holding the folder's lock as a fetch holds it."""
    _egress(allow_hosts=["127.0.0.1"])
    finished = threading.Event()
    failed: list[BaseException] = []

    def needs_python() -> None:
        try:
            grammars.ensure("python")
        except BaseException as exc:  # noqa: BLE001 — the assertion below reports it
            failed.append(exc)
        finally:
            finished.set()

    with record_files.locked(grammars.manifest_path()):
        threading.Thread(target=needs_python, daemon=True).start()
        assert not finished.wait(1.0), "it went ahead while another process was fetching"
        assert releases.paths == []
        pack.unpacked.add("python")  # what the other process's fetch leaves behind

    assert finished.wait(10), "it was still waiting after the other process had finished"
    assert failed == []
    assert releases.paths == [], "the bundle was fetched again beside the other process's fetch"


def test_a_grammar_already_unpacked_waits_for_no_other_process(releases, pack):
    """The control arm: a grammar the pack holds is used at once, while another process fetches."""
    pack.unpacked.add("python")

    with record_files.locked(grammars.manifest_path()):
        worker = threading.Thread(target=grammars.ensure, args=("python",), daemon=True)
        worker.start()
        worker.join(5)
        assert not worker.is_alive(), "a grammar on this machine waited for another fetch"

    assert releases.paths == []


def test_a_refused_fetch_is_not_asked_again_for_every_file(releases, pack):
    """The code map indexes file after file; each would ask the network again and be refused, and
    each refusal is an audit row. The first one's reason answers the rest for a while."""
    _egress(allow_hosts=["127.0.0.1"], deny_hosts=["127.0.0.1"])
    for _ in range(3):
        with pytest.raises(grammars.GrammarUnavailable, match="is on Denied hosts"):
            grammars.ensure("python")
    assert [row["outcome"] for row in _audited()] == ["denied"]


def test_allowing_the_host_lets_the_next_grammar_need_fetch_it(releases, pack):
    """A refusal says the code map fetches the grammar the next time it needs one once the host is
    allowed, so a change to the settings is not answered from the refusal remembered before it."""
    with pytest.raises(grammars.GrammarUnavailable, match="the next time it needs a grammar"):
        grammars.ensure("python")
    assert releases.paths == []

    _egress(allow_hosts=["127.0.0.1"])
    grammars.ensure("python")

    assert pack.unpacked == {"python"}
    assert releases.paths == [f"/releases/v{VERSION}/parsers.json", "/bundle.tar.zst"]


def test_a_process_not_started_as_a_command_is_given_the_settings_every_command_has(monkeypatch):
    """Without the pack's settings it would download from its own address: a process that did not
    start as a ``personalclaw`` command is given what every command gives itself first."""
    for name in (grammars.CACHE_SETTING, grammars.MANIFEST_SETTING):
        monkeypatch.delenv(name, raising=False)

    path = grammars.manifest_path()

    told = library_env()
    assert path.as_uri() == told[grammars.MANIFEST_SETTING] == os.environ[grammars.MANIFEST_SETTING]
    assert os.environ[grammars.CACHE_SETTING] == told[grammars.CACHE_SETTING]
    assert path.parent == Path(told[grammars.CACHE_SETTING])


def test_a_setting_that_names_the_network_is_refused(pack, monkeypatch):
    """The pack would download from an address on the network by itself; PersonalClaw refuses to
    hand it one rather than let it."""
    monkeypatch.setenv(grammars.MANIFEST_SETTING, "https://grammars.example.com/parsers.json")
    with pytest.raises(grammars.GrammarUnavailable, match="would download from by itself"):
        grammars.ensure("python")
    assert pack.read == []


def test_releases_is_the_address_the_pack_itself_would_use():
    """The manifest is fetched from where the installed pack would fetch it itself: a pack that
    moves its releases reds here, rather than leaving the code map without grammars."""
    import tree_sitter_language_pack

    native = next(Path(tree_sitter_language_pack.__file__).parent.glob("_native*"))
    assert grammars.RELEASES.encode() in native.read_bytes(), "the pack publishes elsewhere now"


# ── the real pack, in a fresh interpreter with no route off this machine ───────────────────────

_NO_REMOTE_NETWORK = (
    "(version 1)(allow default)(deny network-outbound (remote ip))"
    '(allow network-outbound (remote ip "localhost:*"))'
)
_NOWHERE = "http://127.0.0.1:9"


def _fresh(tmp_path: Path, code: str, settings: dict[str, str]) -> subprocess.CompletedProcess:
    """*code* in a fresh interpreter with *settings*, a scratch ``HOME``, and nowhere to send a
    request: macOS's sandbox takes the network away where it can, and every proxy goes nowhere."""
    home = tmp_path / "user-home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "HTTPS_PROXY": _NOWHERE,
        "https_proxy": _NOWHERE,
        "ALL_PROXY": _NOWHERE,
        "all_proxy": _NOWHERE,
        **settings,
    }
    argv = [sys.executable, "-c", code]
    if os.path.exists("/usr/bin/sandbox-exec"):
        argv = ["/usr/bin/sandbox-exec", "-p", _NO_REMOTE_NETWORK, *argv]
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)


_LOAD = """
import tree_sitter_language_pack as pack
try:
    pack.get_parser("python")
    print("loaded")
except Exception as exc:
    print(f"refused: {exc}")
"""


def test_told_as_every_command_tells_it_the_pack_reaches_nothing(tmp_path, monkeypatch):
    """Before PersonalClaw has fetched anything, the pack fails reading a file on this machine,
    and writes nothing outside the home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))
    done = _fresh(tmp_path, _LOAD, library_env())

    assert "refused: " in done.stdout and "file://" in done.stdout, done.stdout + done.stderr
    assert sorted((tmp_path / "user-home").rglob("*")) == [], "the pack wrote into HOME"


def test_the_pack_unpacks_from_a_bundle_on_this_machine(tmp_path, monkeypatch):
    """Handed the manifest PersonalClaw writes, the real pack copies the bundle it names from this
    machine, checks its digest, and unpacks the grammar from it: here a stand-in for one, which it
    then cannot load, so the files it made are the proof."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))
    settings = library_env()
    folder = Path(settings[grammars.CACHE_SETTING])
    folder.mkdir(parents=True)
    bundle = _bundle(b"a stand-in for a grammar")
    (folder / f"parsers-{KEY}.tar.zst").write_bytes(bundle)
    digest = hashlib.sha256(bundle).hexdigest()
    (folder / "parsers.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "platforms": {
                    KEY: {
                        "url": (folder / f"parsers-{KEY}.tar.zst").as_uri(),
                        "sha256": digest,
                        "size": len(bundle),
                    }
                },
                "languages": {"python": {"group": "all", "size": 0}},
                "groups": {"all": ["python"]},
            }
        ),
        encoding="utf-8",
    )

    done = _fresh(tmp_path, _LOAD, settings)

    cache = folder / "tree-sitter-language-pack" / f"v{VERSION}"
    assert (cache / "bundles" / f"{KEY}-{digest}.tar.zst").is_file(), done.stdout + done.stderr
    assert (cache / "libs" / LIBRARY).read_bytes() == b"a stand-in for a grammar"
    assert sorted((tmp_path / "user-home").rglob("*")) == [], "the pack wrote into HOME"
