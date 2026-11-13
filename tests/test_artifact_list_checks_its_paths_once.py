"""The artifacts list resolves the protected places once a list for the store's own files, and
refuses what it refused.

``is_sensitive_path`` resolves every protected location on each call (``SensitivePaths``'s
docstring), and the list asked it twice an artifact: once for the root, through
``_artifact_dir``, and once for the artifact's ``meta.json``. Those two calls were nearly all of
a list's time, and the library's one read waits on it. So a list makes one ``SensitivePaths``
and asks it about the root, every ``meta.json`` and, in a search, every ``current.html`` it
reads, as a walk is meant to. Each path is still resolved on its own, so a file that is a link
to a protected file, by its name or by its place, stays unread, and a list still answers exactly
what it answered: the same artifacts, fields and order, for every filter ``GET /api/artifacts``
takes. One bad folder, or a meta that is no object, leaves the rest listed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import security
from personalclaw.artifacts.native import NativeArtifactProvider

pytestmark = pytest.mark.security


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A ``$HOME`` of the test's own, where the place rules' folders are made."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture
def prov(tmp_path, home, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path))
    return NativeArtifactProvider(root=tmp_path / "artifacts")


def _seed(prov: NativeArtifactProvider) -> list[str]:
    made = []
    for n, (kind, source, tags) in enumerate(
        [
            ("markdown", "chat", ["garden"]),
            ("html", "manual", ["garden", "trip"]),
            ("markdown", "cron", ["trip"]),
            ("csv", "chat", []),
            ("markdown", "manual", ["backup"]),
        ]
    ):
        art = prov.create(
            name=f"Note {n}",
            content=f"# Note {n}\n\nbody {n}",
            kind=kind,
            source=source,
            tags=tags,
            description=f"about {n}",
            project_id="p-1" if n == 1 else "",
            collection="Trips" if n == 1 else "",
        )
        made.append(art.slug)
    return made


def _counting(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[str]]:
    """Count every ``SensitivePaths`` made, by any caller (``is_sensitive_path`` makes one), and
    every path one is asked about."""
    made: list[int] = [0]
    asked: list[str] = []
    real_init, real_kind = security.SensitivePaths.__init__, security.SensitivePaths.kind

    def counted(self, *args, **kwargs) -> None:
        made[0] += 1
        real_init(self, *args, **kwargs)

    def kind(self, path_str: str) -> str:
        asked.append(path_str)
        return real_kind(self, path_str)

    monkeypatch.setattr(security.SensitivePaths, "__init__", counted)
    monkeypatch.setattr(security.SensitivePaths, "kind", kind)
    return made, asked


# ── the place rules: a file protected only by where it is ─────────────────────────────────────

_PLACE_RULES = ["aws", "gnupg is a link", "an ssh key that is a link"]


def _protected_by_place(rule: str, home: Path, tmp_path: Path, text: str, n: int = 0) -> Path:
    """A file holding *text* that only the place rules protect (``$HOME``'s credential folders),
    made as *rule* says; returns the path a link to it names."""
    if rule == "aws":
        (home / ".aws").mkdir(exist_ok=True)
        target = home / ".aws" / f"notes-{n}.json"
    elif rule == "gnupg is a link":
        real = tmp_path / "gnupg-real"
        real.mkdir(exist_ok=True)
        if not (home / ".gnupg").is_symlink():
            (home / ".gnupg").symlink_to(real)
        target = home / ".gnupg" / f"pubring-{n}.kbx"
    elif rule == "an ssh key that is a link":
        dotfiles = tmp_path / "dotfiles"
        dotfiles.mkdir(exist_ok=True)
        target = dotfiles / f"id_ed25519_{n}"
        (home / ".ssh").mkdir(exist_ok=True)
        (home / ".ssh" / target.name).symlink_to(target)
    else:
        raise AssertionError(rule)
    target.write_text(text, encoding="utf-8")
    return target


def _link_to_place(path: Path, rule: str, home: Path, tmp_path: Path, n: int = 0) -> None:
    """Make the artifact file *path* a link to a copy of itself that only *rule* protects."""
    target = _protected_by_place(rule, home, tmp_path, path.read_text(encoding="utf-8"), n)
    path.unlink()
    path.symlink_to(target)
    assert security.is_sensitive_path(str(path)), rule
    assert not security.SensitivePaths(home_credential_dirs=False)(str(path)), rule


# ── one set of protected places a list ────────────────────────────────────────────────────────


def test_a_list_resolves_the_protected_places_once(prov, monkeypatch) -> None:
    _seed(prov)
    made, _ = _counting(monkeypatch)
    assert len(prov.list()) == 5
    assert made[0] == 1, f"{made[0]} sets of protected places resolved for one list of 5"
    assert len(prov.list()) == 5
    assert made[0] == 2, "each list makes its own"


@pytest.mark.parametrize("count", [3, 30])
def test_a_lists_checks_stay_one_set_as_the_artifacts_grow(prov, monkeypatch, count) -> None:
    """At any size, one set of protected places a list, and one check of each path it reads:
    the root and each artifact's meta."""
    for n in range(count):
        prov.create(name=f"Item {n}", content=f"body {n}", kind="markdown", source="chat")
    made, asked = _counting(monkeypatch)
    assert len(prov.list()) == count
    assert made[0] == 1, f"{made[0]} sets of protected places for a list of {count}"
    metas = [p for p in asked if p.endswith("/meta.json")]
    assert len(metas) == count and len(set(metas)) == count, asked
    assert asked.count(str(prov.root)) == 1, asked
    assert len(asked) == count + 1, asked


@pytest.mark.parametrize("count", [3, 30])
def test_a_body_search_checks_each_body_with_the_lists_one_set(prov, monkeypatch, count) -> None:
    """A search that finds nothing in the metadata reads every body: one more check a body, and
    still one set of protected places."""
    for n in range(count):
        prov.create(name=f"Item {n}", content=f"body {n}", kind="markdown", source="chat")
    made, asked = _counting(monkeypatch)
    assert prov.list(q="zzz") == []
    assert made[0] == 1, f"{made[0]} sets of protected places for a search of {count}"
    assert len([p for p in asked if p.endswith("/current.html")]) == count, asked
    assert len(asked) == 2 * count + 1, asked


def test_a_body_search_finds_what_the_body_holds(prov, monkeypatch) -> None:
    slugs = _seed(prov)
    made, _ = _counting(monkeypatch)
    assert [a.slug for a in prov.list(q="body 3")] == [slugs[3]]
    assert made[0] == 1


def test_a_file_backed_artifacts_file_is_admitted_by_its_own_check(
    prov, tmp_path, monkeypatch
) -> None:
    """A file-backed artifact's body is its file, which ``source_files.admitted`` checks with a
    checker of its own: a search over one such artifact and one plain one makes two."""
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nnothing here", encoding="utf-8")
    prov.create(
        name="Notes",
        content="# Notes\n\nnothing here",
        kind="markdown",
        source="chat",
        source_path=str(source),
    )
    prov.create(name="Plain", content="nothing here either", kind="markdown", source="chat")
    made, _ = _counting(monkeypatch)
    assert prov.list(q="zzz") == []
    assert made[0] == 2, f"{made[0]} sets of protected places"


# ── a list answers what each read checking for itself answers ─────────────────────────────────

#: Every filter the list takes, as ``GET /api/artifacts`` passes them; ``<file>`` and ``<folder>``
#: stand for the file-backed artifact's path and the filed artifact's folder.
_FILTERS = [
    {},
    {"tag": "garden"},
    {"kind": "markdown"},
    {"source": "chat"},
    {"project_id": "p-1"},
    {"collection": "Trips"},
    {"folder": ""},
    {"q": "note 3"},
    {"q": "body 2"},
    {"source_path": "<file>"},
    {"folder": "<folder>"},
]


@pytest.mark.parametrize(
    "filters", _FILTERS, ids=lambda f: ",".join(f"{k}={v}" for k, v in f.items()) or "all"
)
def test_the_list_answers_as_each_read_checking_for_itself(
    prov, home, tmp_path, monkeypatch, filters
) -> None:
    """The list with one checker equals the list whose every read checks for itself, with three
    artifacts every filter but the file and the folder would select, each refused by one place
    rule, beside a file-backed artifact and a filed one."""
    _seed(prov)
    for n, rule in enumerate(_PLACE_RULES):
        art = prov.create(
            name=f"Note 3 kept away {n}",
            content=f"# Kept away {n}\n\nbody 2",
            kind="markdown",
            source="chat",
            tags=["garden"],
            description="body 2, about it",
            project_id="p-1",
            collection="Trips",
        )
        _link_to_place(prov.root / art.slug / "meta.json", rule, home, tmp_path, n)
    source = tmp_path / "workspace-note.md"
    source.write_text("# Workspace\n\nbody 2", encoding="utf-8")
    backed = prov.create(
        name="Workspace note",
        content="# Workspace\n\nbody 2",
        kind="markdown",
        source="chat",
        source_path=str(source),
    )
    filed = prov.create(name="Filed", content="body 2", kind="markdown", source="chat")
    prov.set_folder(filed.slug, "f-1")
    wanted = {
        k: (backed.source_path if v == "<file>" else "f-1" if v == "<folder>" else v)
        for k, v in filters.items()
    }
    shared = [a.to_dict() for a in prov.list(**wanted)]

    # Each read checking for itself, as before the change: no meta read or body read gets a
    # checker, and the root is checked by a call of its own.
    read: list[str] = []
    real_meta = NativeArtifactProvider._read_meta
    real_root = NativeArtifactProvider._ensure_root
    real_body = NativeArtifactProvider._live_body

    def meta_alone(self, slug, **_):
        read.append(slug)
        return real_meta(self, slug)

    def body_alone(self, art, places=None, **_):
        return real_body(self, art, places)

    monkeypatch.setattr(NativeArtifactProvider, "_read_meta", meta_alone)
    monkeypatch.setattr(NativeArtifactProvider, "_ensure_root", lambda self, *_: real_root(self))
    monkeypatch.setattr(NativeArtifactProvider, "_live_body", body_alone)
    alone = [a.to_dict() for a in prov.list(**wanted)]
    assert sorted(read) == sorted(p.name for p in prov.root.iterdir() if p.is_dir())
    assert shared == alone
    assert shared, "every filter selects something"
    assert not any(a["name"].startswith("Note 3 kept away") for a in shared), shared


# ── what stays refused ─────────────────────────────────────────────────────────────────────────


def test_a_meta_that_links_to_a_protected_file_is_still_refused(prov, tmp_path) -> None:
    slugs = _seed(prov)
    target = slugs[0]
    meta = prov.root / target / "meta.json"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # A sign-in file by name, refused wherever it sits; it holds a meta that would be listed.
    protected = elsewhere / ".credentials.json"
    protected.write_text(meta.read_text(encoding="utf-8"), encoding="utf-8")
    assert json.loads(protected.read_text(encoding="utf-8"))["slug"] == target
    meta.unlink()
    meta.symlink_to(protected)
    assert security.is_sensitive_path(str(meta))
    listed = [a.slug for a in prov.list()]
    assert target not in listed and len(listed) == 4


@pytest.mark.parametrize("rule", _PLACE_RULES)
def test_a_meta_that_links_to_a_file_protected_by_its_place_is_still_refused(
    prov, home, tmp_path, rule
) -> None:
    slugs = _seed(prov)
    _link_to_place(prov.root / slugs[0] / "meta.json", rule, home, tmp_path)
    listed = [a.slug for a in prov.list()]
    assert slugs[0] not in listed and len(listed) == 4


def test_a_body_that_links_to_a_file_protected_by_its_place_is_not_searched(
    prov, home, tmp_path
) -> None:
    slugs = _seed(prov)
    body = prov.root / slugs[2] / "current.html"
    body.write_text("the secretneedle is here", encoding="utf-8")
    assert [a.slug for a in prov.list(q="secretneedle")] == [slugs[2]]
    _link_to_place(body, "aws", home, tmp_path)
    assert prov.list(q="secretneedle") == []


def test_a_list_under_a_root_that_is_a_protected_name_is_still_refused(tmp_path) -> None:
    root = tmp_path / ".credentials.json"
    with pytest.raises(PermissionError, match="sensitive path"):
        NativeArtifactProvider(root=root).list()


def test_a_list_under_a_root_in_a_protected_place_is_still_refused(home) -> None:
    (home / ".ssh").mkdir()
    root = home / ".ssh" / "artifacts"
    assert security.is_sensitive_path(str(root))
    assert not security.SensitivePaths(home_credential_dirs=False)(str(root))
    with pytest.raises(PermissionError, match="sensitive path"):
        NativeArtifactProvider(root=root).list()
    assert not root.exists()


def test_a_place_that_becomes_protected_is_seen_by_the_next_list(prov, home, tmp_path) -> None:
    """Each list makes its own checker: a file one list read is refused by the next once its
    place is protected."""
    slugs = _seed(prov)
    meta = prov.root / slugs[0] / "meta.json"
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    kept = dotfiles / "kept.json"
    kept.write_text(meta.read_text(encoding="utf-8"), encoding="utf-8")
    meta.unlink()
    meta.symlink_to(kept)
    assert slugs[0] in [a.slug for a in prov.list()]
    (home / ".aws").symlink_to(dotfiles)
    assert security.is_sensitive_path(str(meta))
    listed = [a.slug for a in prov.list()]
    assert slugs[0] not in listed and len(listed) == 4


# ── one bad entry leaves the rest listed ───────────────────────────────────────────────────────


def test_a_meta_that_is_no_object_leaves_the_rest_listed(prov) -> None:
    slugs = _seed(prov)
    (prov.root / slugs[0] / "meta.json").write_text("[]", encoding="utf-8")
    listed = [a.slug for a in prov.list()]
    assert slugs[0] not in listed and len(listed) == 4


def test_a_meta_whose_events_are_not_objects_leaves_the_rest_listed(prov) -> None:
    slugs = _seed(prov)
    meta = prov.root / slugs[0] / "meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["events"] = ["x"]
    meta.write_text(json.dumps(data), encoding="utf-8")
    listed = [a.slug for a in prov.list()]
    assert slugs[0] not in listed and len(listed) == 4


def test_a_search_skips_the_body_of_a_meta_whose_slug_names_no_folder(prov) -> None:
    """A meta's slug is the meta's own word: one that is no valid slug leaves its body unread,
    and the search still answers for the rest."""
    slugs = _seed(prov)
    meta = prov.root / slugs[0] / "meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["slug"] = ""
    meta.write_text(json.dumps(data), encoding="utf-8")
    assert [a.slug for a in prov.list(q="body 3")] == [slugs[3]]


def test_a_folder_that_escapes_the_root_leaves_the_rest_listed(prov, tmp_path) -> None:
    slugs = _seed(prov)
    outside = tmp_path / "outside"
    outside.mkdir()
    meta = (prov.root / slugs[0] / "meta.json").read_text(encoding="utf-8")
    (outside / "meta.json").write_text(meta.replace(slugs[0], "escaped"), encoding="utf-8")
    (prov.root / "escaped").symlink_to(outside)
    listed = [a.slug for a in prov.list()]
    assert "escaped" not in listed and len(listed) == 5


def test_a_folder_that_names_no_slug_leaves_the_rest_listed(prov) -> None:
    _seed(prov)
    (prov.root / "Not A Slug").mkdir()
    assert len(prov.list()) == 5


def test_a_walks_root_must_be_the_providers(prov, tmp_path) -> None:
    slugs = _seed(prov)
    with pytest.raises(ValueError, match="not this provider's root"):
        prov._read_meta(slugs[0], root=tmp_path)
