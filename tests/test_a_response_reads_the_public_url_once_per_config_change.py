"""The exposure readers read the config once per change of ``config.json``, not once a response.

Every dashboard response's CSP header asks for the public host, on the event loop, and a full
``AppConfig.load()`` is 0.5 to 0.9 ms. A cold reload of production v0.3.9 ran 238 of them one
after another: one per asset and read. The readers in ``dashboard/exposure.py`` only read fields,
so they share one read until the file holds something new, keyed on its bytes (never its stat,
which two quick same-size writes can share on Linux), its path and the loader itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from personalclaw.config import loader
from personalclaw.dashboard import exposure


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(exposure, "_last_read", None, raising=False)  # main has no cache to clear
    return tmp_path


@pytest.fixture()
def loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the real loads behind the readers."""
    counted: list[int] = []
    real = loader.AppConfig.__dict__["load"].__func__

    def counting(cls: type[loader.AppConfig]) -> loader.AppConfig:
        counted.append(1)
        return real(cls)  # type: ignore[no-any-return]

    monkeypatch.setattr(loader.AppConfig, "load", classmethod(counting))
    return counted


def _connect_sources(policy: str) -> list[str]:
    """The sources a policy's ``connect-src`` names, each one whole."""
    for directive in policy.split(";"):
        name, *sources = directive.split() or [""]
        if name == "connect-src":
            return sources
    return []


def _write(home: Path, public_url: str) -> Path:
    path = home / "config.json"
    path.write_text(json.dumps({"dashboard": {"public_url": public_url}}), encoding="utf-8")
    return path


def test_responses_read_the_config_once_while_it_is_unchanged(home: Path, loads: list[int]) -> None:
    from personalclaw.dashboard.server import dashboard_csp

    _write(home, "https://pc.example.com")
    policies = [dashboard_csp(10000) for _ in range(5)]
    for policy in policies:
        named = {(u.scheme, u.hostname) for u in map(urlsplit, _connect_sources(policy))}
        assert {("wss", "pc.example.com"), ("https", "pc.example.com")} <= named, policy
    assert exposure.public_url() == "https://pc.example.com"
    assert exposure.trusted_proxies() == []
    assert len(loads) == 1, f"{len(loads)} loads for five responses and two reads of one file"


def test_a_same_size_write_in_the_same_tick_is_read(home: Path, loads: list[int]) -> None:
    path = _write(home, "https://a.example.com")
    assert exposure.public_host() == "a.example.com"
    before = path.stat()

    # The same size, the same file and, as a coarse clock stamps it, the same time.
    _write(home, "https://b.example.com")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_ino, after.st_mtime_ns) == (
        before.st_size,
        before.st_ino,
        before.st_mtime_ns,
    )

    assert exposure.public_host() == "b.example.com", "a write its stat could not tell apart"
    assert len(loads) == 2


def test_the_same_bytes_in_another_home_are_read_there(
    home: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    loads: list[int],
) -> None:
    _write(home, "https://pc.example.com")
    assert exposure.public_host() == "pc.example.com"
    other = tmp_path_factory.mktemp("same-bytes-home")
    _write(other, "https://pc.example.com")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(other))
    assert exposure.public_host() == "pc.example.com"
    assert len(loads) == 2, "a read made for one home was served for another"


def test_a_patched_loader_is_asked(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home, "https://file.example.com")
    assert exposure.public_host() == "file.example.com"
    real = loader.AppConfig.__dict__["load"].__func__

    def patched(cls: type[loader.AppConfig]) -> loader.AppConfig:
        cfg = real(cls)  # a real read of the same file, as a test's wrapper makes it
        cfg.dashboard.public_url = "https://patched.example.com"
        return cfg  # type: ignore[no-any-return]

    monkeypatch.setattr(loader.AppConfig, "load", classmethod(patched))
    assert (
        exposure.public_host() == "patched.example.com"
    ), "a read from before the patch was served"


def test_a_config_built_in_memory_is_never_kept(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(home, "https://file.example.com")
    built = [0]

    def in_memory(cls: type[loader.AppConfig]) -> loader.AppConfig:
        built[0] += 1
        cfg = cls()
        cfg.dashboard.public_url = f"https://built-{built[0]}.example.com"
        return cfg

    monkeypatch.setattr(loader.AppConfig, "load", classmethod(in_memory))
    assert [exposure.public_host() for _ in range(2)] == [
        "built-1.example.com",
        "built-2.example.com",
    ], "a config that names no file it was read from was kept"


def test_a_file_that_disappears_is_read_as_gone(home: Path) -> None:
    path = _write(home, "https://gone.example.com")
    assert exposure.public_host() == "gone.example.com"
    path.unlink()
    assert exposure.public_host() == "", "a removed file still read as its last config"


def test_a_file_that_cannot_be_read_is_never_kept(home: Path, loads: list[int]) -> None:
    """No file, read and kept as none; then one that is there and cannot be read: never the
    absent file's read, and never kept."""
    assert exposure.public_host() == ""
    path = _write(home, "https://locked.example.com")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("this user reads a file with no permissions (root)")
        before = len(loads)
        exposure.public_host()
        exposure.public_host()
        assert len(loads) - before == 2, "a file that cannot be read was served a kept read"
    finally:
        path.chmod(0o600)


def test_a_change_undone_while_a_load_runs_is_not_kept(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write and its undo (a change rolled back) landing while a load runs: the load read the
    undone config, and the file holds the first again (review probe, G1 F1)."""
    path = _write(home, "https://a.example.com")
    first = path.read_text(encoding="utf-8")
    real = loader.AppConfig.__dict__["load_with_migration_state"].__func__
    undone: list[int] = []

    def mid(cls: type[loader.AppConfig]) -> tuple[loader.AppConfig, bool]:
        if not undone:
            undone.append(1)
            _write(home, "https://b.example.com")
            out = real(cls)
            path.write_text(first, encoding="utf-8")
            return out  # type: ignore[no-any-return]
        return real(cls)  # type: ignore[no-any-return]

    monkeypatch.setattr(loader.AppConfig, "load_with_migration_state", classmethod(mid))
    assert exposure.public_url() == "https://b.example.com"  # the read the undo landed in
    assert exposure.public_url() == "https://a.example.com", "an undone change was kept"


def test_a_file_that_appears_is_read(home: Path) -> None:
    assert exposure.public_host() == ""
    _write(home, "https://late.example.com")
    assert exposure.public_host() == "late.example.com"


#: While set, the audit hook below records every file this process opens.
_OPENED: list[str] | None = None


def _record_opens(event: str, args: tuple[object, ...]) -> None:
    if _OPENED is not None and event == "open":
        _OPENED.append(str(args[0]))


sys.addaudithook(_record_opens)


class _EnvReads(dict[str, str]):
    """``os.environ`` as a load sees it, with every name it asks for recorded."""

    def __init__(self, base: dict[str, str]) -> None:
        super().__init__(base)
        self.asked: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.asked.add(key)
        return super().__getitem__(key)

    def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
        self.asked.add(key)
        return super().get(key, default)

    def __contains__(self, key: object) -> bool:
        self.asked.add(str(key))
        return super().__contains__(key)

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.asked.add("<every name>")
        return super().__iter__()

    def items(self):  # type: ignore[no-untyped-def]
        self.asked.add("<every name>")
        return super().items()


def test_a_load_takes_these_fields_from_the_home_and_config_json_alone(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key covers every input a load reads: the home (``PERSONALCLAW_HOME``, resolved into
    the path) and ``config.json``'s bytes.

    No overlay or include file and no environment variable reaches the public URL, the inbound
    public URL or the trusted proxies today: ``config/loader.py`` builds them from
    ``data["dashboard"]`` and ``data["external_access"]`` alone, and ``config/migrations.py``
    never sets them. A load that comes to read another file or another variable fails here, and
    that input must join the key before this passes again."""
    global _OPENED
    path = home / "config.json"
    path.write_text(
        json.dumps(
            {
                "dashboard": {
                    "public_url": "https://pc.example.com",
                    "trusted_proxies": ["10.0.0.1"],
                },
                "external_access": {"public_url": "https://in.example.com"},
            }
        ),
        encoding="utf-8",
    )
    # Where an overlay would sit, each naming another public URL: a load that read one opens it.
    decoy = {"dashboard": {"public_url": "https://overlay.example.com"}}
    (home / "config.d").mkdir()
    for name in ("config.local.json", "config.override.json", "config.d/overlay.json"):
        (home / name).write_text(json.dumps(decoy), encoding="utf-8")
    (home / ".env").write_text(
        "PERSONALCLAW_PUBLIC_URL=https://env.example.com\n", encoding="utf-8"
    )
    loader.AppConfig.load()  # the first load imports what it needs; only a load's own reads count
    env = _EnvReads(dict(os.environ))
    monkeypatch.setattr(os, "environ", env)
    _OPENED = []
    try:
        cfg = loader.AppConfig.load()
        opened = list(_OPENED)
    finally:
        _OPENED = None

    assert (
        cfg.dashboard.public_url,
        cfg.dashboard.trusted_proxies,
        cfg.external_access.public_url,
    ) == (
        "https://pc.example.com",
        ["10.0.0.1"],
        "https://in.example.com",
    )
    assert env.asked == {
        "PERSONALCLAW_HOME"
    }, f"a load read {sorted(env.asked)} from the environment"
    assert [os.path.realpath(p) for p in opened] == [
        os.path.realpath(path)
    ], f"a load opened {opened}"
