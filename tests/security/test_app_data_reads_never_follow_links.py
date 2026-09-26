"""The gateway never reads an app's ``data/`` through a link the app planted there.

``data/`` is the one folder a confined app may write, and the gateway reads
``data/config.json`` there with its own authority: for the Apps config API
(``apps.app_config``), for the settings a provider is built with
(``providers.settings.ProviderSettings``), and for the boot-time move of plaintext secrets
into the credential store (``config.secret_refs``). All three opened the file THROUGH a
link, so an app holding ``storage`` could plant ``data/config.json -> <any JSON file>`` and
be handed that file as its own settings — and the boot move rewrote the link into a real
``data/config.json`` holding the named file's content, with its secrets filed in the
credential store under the app's name.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from personalclaw.apps import app_config, manager
from personalclaw.config import secret_refs
from personalclaw.providers.settings import ProviderSettings

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="POSIX links and pipes"
)

APP = "data-link-app"
OUTSIDE_SECRET = "sk-outside-file-3c2b1a0f"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import personalclaw.config.loader as loader

    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: h)
    monkeypatch.setattr(manager, "config_dir", lambda: h)
    # Secrets land in the home's `.env`, where a search can find them.
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    return h


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A JSON file the app was never given: outside the home and outside every app."""
    p = tmp_path / "outside" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"api_key": OUTSIDE_SECRET, "mode": "outside"}), encoding="utf-8")
    return p


def _data_dir() -> Path:
    d = manager.app_dir(APP) / "data"
    d.mkdir(parents=True)
    return d


def _hits(root: Path, needle: str) -> list[str]:
    """Home-relative REGULAR files holding ``needle`` — a link is never read."""
    out = []
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = Path(dirpath) / name
            if not p.is_symlink() and needle.encode() in p.read_bytes():
                out.append(p.relative_to(root).as_posix())
    return sorted(out)


def test_app_config_is_not_read_through_a_link(outside: Path) -> None:
    os.symlink(outside, _data_dir() / "config.json")

    assert app_config.read_config(APP) == {}, "the config API read a file the app linked to"


def test_provider_settings_are_not_read_through_a_link(outside: Path) -> None:
    os.symlink(outside, _data_dir() / "config.json")

    assert ProviderSettings.load(APP) == {}, "a provider was built from a file the app linked to"


def test_nor_through_a_data_folder_that_is_itself_a_link(tmp_path: Path, outside: Path) -> None:
    manager.app_dir(APP).mkdir(parents=True)
    os.symlink(outside.parent, manager.app_dir(APP) / "data")
    (outside.parent / "config.json").write_text(outside.read_text(encoding="utf-8"))

    assert app_config.read_config(APP) == {}
    assert ProviderSettings.load(APP) == {}


def test_a_real_config_file_still_reads() -> None:
    (_data_dir() / "config.json").write_text(json.dumps({"mode": "fast"}), encoding="utf-8")

    assert app_config.read_config(APP) == {"mode": "fast"}
    assert ProviderSettings.load(APP) == {"mode": "fast"}


def test_a_pipe_in_place_of_the_config_does_not_hang_the_reader() -> None:
    os.mkfifo(_data_dir() / "config.json")
    got: list[dict] = []
    reader = threading.Thread(target=lambda: got.append(app_config.read_config(APP)), daemon=True)

    reader.start()
    reader.join(timeout=5)

    assert not reader.is_alive(), "reading an app's data/ blocked on a named pipe"
    assert got == [{}]


def test_saving_config_replaces_a_planted_link_and_leaves_its_target_alone(outside: Path) -> None:
    link = _data_dir() / "config.json"
    os.symlink(outside, link)
    before = outside.read_bytes()
    schema = {"properties": {"mode": {"type": "string"}}}

    app_config.write_config(APP, {"mode": "mine"}, schema)

    assert not link.is_symlink() and json.loads(link.read_text()) == {"mode": "mine"}
    assert outside.read_bytes() == before


def test_the_boot_secret_move_never_reads_or_rewrites_through_a_data_link(
    home: Path, outside: Path
) -> None:
    link = _data_dir() / "config.json"
    os.symlink(outside, link)
    before = outside.read_bytes()

    secret_refs.migrate_plaintext_secrets()

    assert link.is_symlink(), "the boot move replaced the link with the file it named"
    assert outside.read_bytes() == before
    assert _hits(home, OUTSIDE_SECRET) == [], "the linked file's secret was filed under the app"
    assert _hits(home, "outside") == [], "the linked file's content was copied into the home"


def test_the_boot_secret_move_skips_a_link_in_a_parked_copy(home: Path, outside: Path) -> None:
    """A keep-data uninstall parks ``data/`` as ``apps/.<app>.data`` with links as links."""
    parked = manager.apps_dir() / f".{APP}.data"
    parked.mkdir(parents=True)
    link = parked / "config.json"
    os.symlink(outside, link)
    before = outside.read_bytes()

    secret_refs.migrate_plaintext_secrets()

    assert link.is_symlink(), "the boot move rewrote a parked link into a copy of its target"
    assert outside.read_bytes() == before
    assert _hits(home, "outside") == []
