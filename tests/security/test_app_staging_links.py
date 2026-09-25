"""Installing an app never copies a byte from outside its bundle.

Every install, update and install preview begins by copying the bundle it was given — a git
clone, a local folder — into quarantine, and every later gate reads that copy. The copy was
``shutil.copytree(src, staged)``, which FOLLOWS symbolic links, so a bundle shipping
``data/key -> ~/.ssh/id_ed25519`` arrived in the installed app holding the key's BYTES, where
the app's own code could read them, while anything that looked at the source saw only a link.

These tests plant a secret OUTSIDE the home, point a bundle at it every way the filesystem
allows, drive the real lifecycle entry points, and then search the apps dir for the secret's
bytes. They also pin the other half of the contract: a link to one of the bundle's own files
still installs, as that link.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.apps import source as app_source

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="POSIX links and pipes"
)

SECRET = b"pclaw-outside-secret-7f3a91c2"
APP = "link-app"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import personalclaw.config.loader as loader

    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: h)
    monkeypatch.setattr(manager, "config_dir", lambda: h)
    return h


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    """A file the owner never meant to hand an app: outside the home, outside every bundle."""
    p = tmp_path / "outside" / "secret.txt"
    p.parent.mkdir()
    p.write_bytes(SECRET)
    return p


def _bundle(parent: Path, *, dirname: str = APP, version: str = "1.0.0") -> Path:
    d = parent / dirname
    d.mkdir(parents=True)
    manifest = {"name": APP, "version": version, "displayName": "Link App", "description": "x"}
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "README.md").write_text("# Link App\n", encoding="utf-8")
    return d


def _link(bundle: Path, rel: str, target: str | Path) -> None:
    p = bundle / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(target), p)


def _files_holding_secret(root: Path) -> list[Path]:
    """Every REGULAR file under ``root`` whose bytes contain the secret — walked without
    following a single link, so a link is never mistaken for the bytes it names."""
    hits: list[Path] = []
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = Path(dirpath) / name
            if stat.S_ISREG(os.lstat(p).st_mode) and SECRET in p.read_bytes():
                hits.append(p)
    return hits


def _links_leaving(root: Path) -> list[Path]:
    """Every link under ``root`` whose target resolves outside ``root``."""
    real_root = os.path.realpath(root)
    out: list[Path] = []
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            p = Path(dirpath) / name
            if p.is_symlink():
                real = os.path.realpath(p)
                if os.path.commonpath([real_root, real]) != real_root:
                    out.append(p)
    return out


def _assert_nothing_landed(home: Path) -> None:
    apps = manager.apps_dir()
    assert not manager.app_dir(APP).exists(), "a refused bundle must not be installed"
    assert _files_holding_secret(home) == [], "the secret's bytes reached the home"
    quarantine = apps / ".quarantine"
    leftovers = sorted(p.name for p in quarantine.iterdir()) if quarantine.is_dir() else []
    assert leftovers == [], f"a refused bundle left staging behind: {leftovers}"


# ── a bundle pointing outside itself is refused, and nothing is written ─────────────────


def _absolute_link(b: Path, s: Path) -> str:
    _link(b, "data/key", s)
    return "data/key"


def _relative_escape(b: Path, s: Path) -> str:
    # <tmp>/src/link-app/assets/key -> <tmp>/outside/secret.txt
    _link(b, "assets/key", "../../../outside/secret.txt")
    return "assets/key"


def _outside_folder(b: Path, s: Path) -> str:
    _link(b, "vendor", s.parent)
    return "vendor"


def _hard_link(b: Path, s: Path) -> str:
    (b / "data").mkdir()
    os.link(s, b / "data" / "key")
    return "data/key"


def _chained_escape(b: Path, s: Path) -> str:
    # Each link alone looks local; the chain ends outside.
    _link(b, "a.txt", "b.txt")
    _link(b, "b.txt", s)
    return "a.txt"


@pytest.mark.parametrize(
    "plant",
    [_absolute_link, _relative_escape, _outside_folder, _hard_link, _chained_escape],
    ids=["absolute-link", "relative-escape", "outside-folder", "hard-link", "chained-escape"],
)
def test_a_bundle_reaching_outside_itself_is_refused_and_nothing_lands(
    tmp_path: Path, home: Path, secret: Path, plant
) -> None:
    src = _bundle(tmp_path / "src")
    offender = plant(src, secret)

    res = app_manager.install(src, confirm=True)

    assert not res.ok, "installed a bundle that reaches outside itself"
    assert res.error.startswith("install refused: "), res.error
    assert repr(offender) in res.error, f"the refusal must name {offender!r}: {res.error}"
    _assert_nothing_landed(home)


def test_the_preview_refuses_it_too_and_stages_nothing(
    tmp_path: Path, home: Path, secret: Path
) -> None:
    """The consent dialog reads `preview` — it must refuse the same bundle the install
    refuses, not show a clean review of a copy holding the secret."""
    src = _bundle(tmp_path / "src")
    _link(src, "data/key", secret)

    res = app_manager.preview(src)

    assert not res.ok and not res.consent, "a preview offered consent over a secret"
    assert res.error.startswith("install refused: ") and "'data/key'" in res.error, res.error
    _assert_nothing_landed(home)


@pytest.mark.parametrize(
    ("plant", "offender", "why"),
    [
        (lambda b: _link(b, "lib", "src/lib"), "lib", "folder"),
        (lambda b: _link(b, "a.md", "missing.md"), "a.md", "does not contain"),
        (lambda b: (_link(b, "a.md", "b.md"), _link(b, "b.md", "a.md")), "a.md", "loop"),
        (lambda b: _link(b, "self", "."), "self", "folder"),
        (lambda b: os.mkfifo(b / "pipe"), "pipe", "named pipe"),
    ],
    ids=["folder-link", "dangling", "loop", "link-to-itself", "fifo"],
)
def test_entries_that_are_not_the_bundles_own_files_are_refused(
    tmp_path: Path, home: Path, plant, offender: str, why: str
) -> None:
    src = _bundle(tmp_path / "src")
    (src / "src" / "lib").mkdir(parents=True)
    (src / "src" / "lib" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    plant(src)

    res = app_manager.install(src, confirm=True)

    assert not res.ok, f"installed a bundle holding a {why} entry"
    assert repr(offender) in res.error and why in res.error, res.error
    _assert_nothing_landed(home)


@pytest.mark.parametrize(
    ("rel", "target", "says"),
    [
        # Leaves data/ at the first `..` — and then the app itself, which is what matters.
        ("data/key", "../../../../config.json", "links outside the app"),
        # Dangling today, and inside the folder the app writes: it can supply the file later.
        ("skills/s/SKILL.md", "../../data/later.md", "links into the app's data folder"),
    ],
    ids=["escape-beats-data", "data-beats-dangling"],
)
def test_a_link_breaking_several_rules_is_refused_for_the_gravest(
    tmp_path: Path, home: Path, rel: str, target: str, says: str
) -> None:
    src = _bundle(tmp_path / "src")
    (src / "data").mkdir()
    _link(src, rel, target)

    res = app_manager.install(src, confirm=True)

    assert not res.ok and f"{rel!r} {says}" in res.error, res.error
    _assert_nothing_landed(home)


def test_a_link_that_climbs_above_the_bundle_is_refused_even_when_it_comes_back(
    tmp_path: Path, home: Path
) -> None:
    """``notes.md -> ../victim-app/notes-real.md`` from a source folder NAMED victim-app
    resolves inside the bundle where it sits — and, installed at ``apps/link-app/``, into
    ``apps/victim-app/``. A link must mean the same thing wherever the bundle is copied, so
    one that leaves the top folder for even a step is refused."""
    src = _bundle(tmp_path / "src", dirname="victim-app")
    (src / "notes-real.md").write_text("mine\n", encoding="utf-8")
    _link(src, "notes.md", "../victim-app/notes-real.md")

    res = app_manager.install(src, confirm=True)

    assert not res.ok and "'notes.md'" in res.error, res.error
    assert not manager.app_dir(APP).exists()


def _manifest_from_data(b: Path) -> str:
    (b / "data").mkdir()
    (b / "app.json").rename(b / "data" / "app.json")
    _link(b, "app.json", "data/app.json")
    return "app.json"


def _skill_from_data(b: Path) -> str:
    (b / "data").mkdir()
    (b / "data" / "skill.md").write_text("# skill\n", encoding="utf-8")
    _link(b, "skills/x/SKILL.md", "../../data/skill.md")
    return "skills/x/SKILL.md"


def _through_data(b: Path) -> str:
    # Ends outside data/, but PASSES through it — and data/sub is the app's to replace.
    (b / "data" / "sub").mkdir(parents=True)
    _link(b, "notes.md", "data/sub/../../README.md")
    return "notes.md"


def _out_of_data(b: Path) -> str:
    (b / "data").mkdir()
    _link(b, "data/defaults.json", "../README.md")
    return "data/defaults.json"


def _installed_json_link(b: Path) -> str:
    _link(b, "installed.json", "README.md")
    return "installed.json"


def _data_is_a_link(b: Path) -> str:
    (b / "store").mkdir()
    _link(b, "data", "store")
    return "data"


@pytest.mark.parametrize(
    "plant",
    [
        _manifest_from_data,
        _skill_from_data,
        _through_data,
        _out_of_data,
        _installed_json_link,
        _data_is_a_link,
    ],
    ids=[
        "manifest-into-data",
        "skill-into-data",
        "through-data",
        "out-of-data",
        "installed-json",
        "data-itself",
    ],
)
def test_links_never_cross_the_data_folder_or_stand_in_for_platform_files(
    tmp_path: Path, home: Path, plant
) -> None:
    """``data/`` is where the app WRITES, and the gateway re-reads what an app ships (its
    grants from ``app.json`` on every permission check). A shipped file backed by ``data/``
    is one the app can rewrite after the owner consented."""
    src = _bundle(tmp_path / "src")
    offender = plant(src)

    res = app_manager.install(src, confirm=True)

    assert not res.ok and repr(offender) in res.error, res.error
    assert not manager.app_dir(APP).exists()


def _swap_for_link(b: Path, s: Path) -> str:
    (b / "notes.md").unlink()
    os.symlink(s, b / "notes.md")
    return "notes.md"


def _swap_for_hard_link(b: Path, s: Path) -> str:
    (b / "notes.md").unlink()
    os.link(s, b / "notes.md")
    return "notes.md"


def _retarget_link(b: Path, s: Path) -> str:
    (b / "alias.md").unlink()
    os.symlink(s, b / "alias.md")
    return "alias.md"


@pytest.mark.parametrize(
    "swap",
    [_swap_for_link, _swap_for_hard_link, _retarget_link],
    ids=["file-becomes-link", "file-becomes-hard-link", "link-retargeted"],
)
def test_the_copy_is_exactly_what_was_checked(
    tmp_path: Path, home: Path, secret: Path, monkeypatch: pytest.MonkeyPatch, swap
) -> None:
    """The manifest peek runs between the check and the copy; an entry swapped in that
    window is refused, never followed — the copy writes only the inodes and link texts the
    check passed."""
    src = _bundle(tmp_path / "src")
    (src / "notes.md").write_text("mine\n", encoding="utf-8")
    _link(src, "alias.md", "notes.md")
    real_peek = app_manager._load_staged_manifest
    swapped: list[str] = []

    def peek_then_swap(path: Path, **kw):
        manifest = real_peek(path, **kw)
        if Path(path) == src and not swapped:
            swapped.append(swap(src, secret))
        return manifest

    monkeypatch.setattr(app_manager, "_load_staged_manifest", peek_then_swap)

    res = app_manager.install(src, confirm=True)

    assert swapped, "the swap never ran"
    assert not res.ok, "installed a bundle that changed after it was checked"
    assert f"{swapped[0]!r} changed while it was being staged" in res.error, res.error
    _assert_nothing_landed(home)


# ── a link to one of the bundle's own files installs, as that link ───────────────────────


def test_a_link_to_its_own_file_installs_as_that_link(tmp_path: Path, home: Path) -> None:
    src = _bundle(tmp_path / "src")
    (src / "docs").mkdir()
    (src / "docs" / "guide.md").write_text("the guide\n", encoding="utf-8")
    (src / "README.md").unlink()
    _link(src, "README.md", "docs/guide.md")
    (src / "data").mkdir()
    (src / "data" / "v2.json").write_text("{}", encoding="utf-8")
    _link(src, "data/latest.json", "v2.json")
    (src / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (src / "run.sh").chmod(0o755)
    (src / "assets" / "empty").mkdir(parents=True)
    (src / ".hidden").write_text("h\n", encoding="utf-8")

    res = app_manager.install(src, confirm=True)

    assert res.ok, res.error
    live = manager.app_dir(APP)
    assert (live / "README.md").is_symlink(), "an inside link was dereferenced into a copy"
    assert os.readlink(live / "README.md") == "docs/guide.md"
    assert (live / "README.md").read_text(encoding="utf-8") == "the guide\n"
    assert os.readlink(live / "data" / "latest.json") == "v2.json"
    assert os.stat(live / "run.sh").st_mode & 0o111, "the executable bit was lost"
    assert (live / "assets" / "empty").is_dir() and (live / ".hidden").is_file()
    assert _links_leaving(live) == []


def test_consent_binds_to_a_link_not_to_the_bytes_it_names(tmp_path: Path, home: Path) -> None:
    """A link and a regular copy of the same file are different bundles; the preview's
    digest — what the owner's consent is bound to — must tell them apart."""
    linked = _bundle(tmp_path / "a")
    (linked / "docs").mkdir()
    (linked / "docs" / "guide.md").write_text("the guide\n", encoding="utf-8")
    (linked / "README.md").unlink()
    _link(linked, "README.md", "docs/guide.md")
    copied = _bundle(tmp_path / "b")
    (copied / "docs").mkdir()
    (copied / "docs" / "guide.md").write_text("the guide\n", encoding="utf-8")
    (copied / "README.md").write_text("the guide\n", encoding="utf-8")

    a, b = app_manager.preview(linked), app_manager.preview(copied)

    assert a.consent and b.consent, (a.error, b.error)
    assert a.consent != b.consent
    assert app_manager.install(linked, consent=a.consent).ok
    assert (manager.app_dir(APP) / "README.md").is_symlink()


# ── update ───────────────────────────────────────────────────────────────────────────────


def test_an_update_that_reaches_outside_is_refused_and_the_installed_version_stands(
    tmp_path: Path, home: Path, secret: Path
) -> None:
    assert app_manager.install(_bundle(tmp_path / "v1"), confirm=True).ok
    before = sorted(p.relative_to(manager.app_dir(APP)) for p in manager.app_dir(APP).rglob("*"))
    v2 = _bundle(tmp_path / "v2", version="2.0.0")
    _link(v2, "assets/key.txt", secret)

    res = app_manager.update(v2, confirm=True)

    assert not res.ok and res.error.startswith("update refused: "), res.error
    assert "'assets/key.txt'" in res.error, res.error
    assert manager._read_installed(APP).version == "1.0.0"
    after = sorted(p.relative_to(manager.app_dir(APP)) for p in manager.app_dir(APP).rglob("*"))
    assert after == before, "a refused update changed the installed tree"
    assert _files_holding_secret(home) == []
    assert not manager.app_dir(APP).with_name(f".{APP}.rollback").exists()


# ── the gateway's own copies of an app's data/ never read through a link either ─────────


def test_an_update_keeps_a_link_the_app_left_in_data_as_a_link(
    tmp_path: Path, home: Path, secret: Path
) -> None:
    """A confined app may write only its ``data/`` — but it can PLANT a link there, and the
    update copies ``data/`` forward with the gateway's authority. Copying through the link
    would hand the app the target's bytes on the next update."""
    assert app_manager.install(_bundle(tmp_path / "v1"), confirm=True).ok
    os.symlink(secret, manager.app_dir(APP) / "data" / "key")

    res = app_manager.update(_bundle(tmp_path / "v2", version="2.0.0"), confirm=True)

    assert res.ok, res.error
    key = manager.app_dir(APP) / "data" / "key"
    assert key.is_symlink() and os.readlink(key) == str(secret)
    assert _files_holding_secret(home) == []


def test_keep_data_uninstall_and_reinstall_keep_a_planted_link_as_a_link(
    tmp_path: Path, home: Path, secret: Path
) -> None:
    assert app_manager.install(_bundle(tmp_path / "v1"), confirm=True).ok
    os.symlink(secret, manager.app_dir(APP) / "data" / "key")

    assert app_manager.uninstall_keep_data(APP) is True
    parked = manager.apps_dir() / f".{APP}.data" / "key"
    assert parked.is_symlink(), "parking data/ read through the app's link"
    assert _files_holding_secret(home) == []

    assert app_manager.install(_bundle(tmp_path / "again"), confirm=True).ok
    key = manager.app_dir(APP) / "data" / "key"
    assert key.is_symlink() and os.readlink(key) == str(secret)
    assert _files_holding_secret(home) == []


# ── the `url#subdirectory` pointer stays inside the clone ────────────────────────────────


@pytest.fixture
def fake_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`_clone_git` replaced by a local tree, so the pointer logic runs without a network."""
    clone = tmp_path / "clone"
    (clone / "apps" / "good").mkdir(parents=True)
    (clone / "apps" / "good" / "app.json").write_text("{}", encoding="utf-8")
    beside = tmp_path / "beside-the-clone"
    beside.mkdir()
    (beside / "app.json").write_text("{}", encoding="utf-8")
    os.symlink(beside, clone / "apps" / "sneaky")

    def _fake(url: str) -> app_source.ResolvedSource:
        return app_source.ResolvedSource(path=clone, origin="external", cleanup=False)

    monkeypatch.setattr(app_source, "_clone_git", _fake)
    return clone


@pytest.mark.parametrize(
    "subdir", ["../beside-the-clone", "apps/../../beside-the-clone", "apps/sneaky"]
)
def test_a_pointer_subdirectory_outside_the_clone_is_refused(fake_clone: Path, subdir: str) -> None:
    """A registry index is untrusted and supplies `repo#subdirectory` verbatim; a
    subdirectory that walks — or links — out of the clone must not become the bundle."""
    with pytest.raises(app_source.SourceError, match="outside the cloned repo"):
        app_source.resolve(f"https://example.invalid/apps.git#{subdir}")


def test_a_pointer_subdirectory_inside_the_clone_resolves(fake_clone: Path) -> None:
    got = app_source.resolve("https://example.invalid/apps.git#apps/good")
    assert got.path == fake_clone / "apps" / "good"
