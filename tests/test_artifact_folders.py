"""PEP-6 — artifact folders: CRUD, metadata-only filing, non-destructive delete, nesting.

Every test drives a provider + folder store rooted at ``tmp_path`` so nothing here can
reach the real home.
"""

import json

import pytest

from personalclaw.artifacts.folders import (
    MAX_FOLDERS,
    ArtifactFolder,
    ArtifactFolderStore,
    delete_folder,
)
from personalclaw.artifacts.native import NativeArtifactProvider


@pytest.fixture
def prov(tmp_path):
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def store(prov):
    # Same root as the provider: folders.json lives inside the artifacts tree.
    return ArtifactFolderStore(prov.root)


def _make(prov, name):
    return prov.create(name=name, content="<p>x</p>", kind="html")


# ── CRUD ──


def test_folder_crud_round_trip(store):
    created = store.create("Reports", icon="📊")
    assert len(created.id) == 12 and int(created.id, 16) >= 0  # opaque 12-char hex
    assert created.name == "Reports"
    assert created.parent_id == ""
    assert created.icon == "📊"

    assert [f.id for f in store.list()] == [created.id]
    assert store.get(created.id) == created
    assert store.exists(created.id) is True

    renamed = store.update(created.id, name="Weekly reports")
    assert renamed is not None and renamed.name == "Weekly reports"
    assert store.get(created.id).name == "Weekly reports"

    assert store._remove_record(created.id) is True
    assert store.list() == []
    assert store.exists(created.id) is False
    assert store.get(created.id) is None


def test_folder_update_and_remove_missing_id(store):
    assert store.update("deadbeefcafe", name="x") is None
    assert store._remove_record("deadbeefcafe") is False


def test_folder_name_required(store):
    with pytest.raises(ValueError, match="folder name required"):
        store.create("   ")
    folder = store.create("Keep")
    with pytest.raises(ValueError, match="folder name required"):
        store.update(folder.id, name="")
    # The failed rename persisted nothing.
    assert store.get(folder.id).name == "Keep"


def test_folder_limit_is_bounded(store, monkeypatch):
    monkeypatch.setattr("personalclaw.artifacts.folders.MAX_FOLDERS", 2)
    store.create("a")
    store.create("b")
    with pytest.raises(ValueError, match="folder limit reached"):
        store.create("c")
    assert len(store.list()) == 2
    assert MAX_FOLDERS >= 2  # the shipped cap is a real number, not a stub


# ── filing is metadata-only ──


def test_filing_does_not_bump_updated_at(prov, store):
    """The contract: ``updated_at`` means the CONTENT changed. Filing is organization,
    so it must leave the recency order of the library untouched."""
    art = _make(prov, "Budget")
    folder = store.create("Finance")
    before = prov.get(art.slug)
    assert before.updated_at  # a real timestamp exists to be (not) bumped

    filed = prov.set_folder(art.slug, folder.id)

    assert filed.folder_id == folder.id
    assert filed.updated_at == before.updated_at
    assert filed.created_at == before.created_at
    assert filed.version == before.version
    assert prov.get(art.slug).updated_at == before.updated_at
    # And unfiling is equally free.
    assert prov.set_folder(art.slug, "").updated_at == before.updated_at


def test_filing_does_not_snapshot_or_touch_content(prov, store):
    art = _make(prov, "Widget")
    folder = store.create("Tools")
    prov.set_folder(art.slug, folder.id)
    after = prov.get(art.slug)
    assert after.content == "<p>x</p>"
    assert after.version == 1
    assert prov.list_versions(art.slug) == [1]
    assert [e.type for e in after.events] == ["created"]  # filing logs no event


def test_filing_a_readonly_artifact_is_allowed(prov, store):
    """A frozen record is still the owner's to organize — the read-only guard covers
    content mutation, and filing changes no body."""
    art = prov.create(name="Shared transcript", content="hi", kind="markdown")
    meta = prov.root / art.slug / "meta.json"
    data = json.loads(meta.read_text())
    data["readonly"] = True
    meta.write_text(json.dumps(data))
    with pytest.raises(PermissionError):
        prov.update(art.slug, content="tampered")

    folder = store.create("Archive")
    filed = prov.set_folder(art.slug, folder.id)
    assert filed is not None and filed.folder_id == folder.id


def test_set_folder_unknown_slug_returns_none(prov):
    assert prov.set_folder("no-such-artifact", "") is None


# ── rename leaves artifacts untouched ──


def test_rename_folder_leaves_artifact_records_untouched(prov, store):
    art = _make(prov, "Q3")
    folder = store.create("Finance")
    prov.set_folder(art.slug, folder.id)
    before = prov.get(art.slug).to_dict(persist=True)
    raw_before = (prov.root / art.slug / "meta.json").read_text()

    store.update(folder.id, name="Finance (2026)")

    assert store.get(folder.id).name == "Finance (2026)"
    assert prov.get(art.slug).to_dict(persist=True) == before
    assert (prov.root / art.slug / "meta.json").read_text() == raw_before
    # Membership is by opaque id, so the rename cannot break the link.
    assert [a.slug for a in prov.list(folder=folder.id)] == [art.slug]


# ── delete falls members back to unfiled ──


def test_delete_folder_unfiles_members_and_destroys_nothing(prov, store):
    kept = _make(prov, "Kept")
    other = _make(prov, "Other")
    folder = store.create("Doomed")
    keeper = store.create("Keeper")
    prov.set_folder(kept.slug, folder.id)
    prov.set_folder(other.slug, keeper.id)
    kept_before = prov.get(kept.slug).updated_at

    deleted, unfiled = delete_folder(store, prov, folder.id)

    assert (deleted, unfiled) == (True, 1)
    assert store.get(folder.id) is None
    # The artifact survives, is unfiled, and did not get a recency bump on the way.
    surviving = prov.get(kept.slug)
    assert surviving is not None
    assert surviving.content == "<p>x</p>"
    assert surviving.folder_id == ""
    assert surviving.updated_at == kept_before
    assert {a.slug for a in prov.list()} == {kept.slug, other.slug}
    assert [a.slug for a in prov.list(folder="")] == [kept.slug]
    # An unrelated folder's membership is undisturbed.
    assert [a.slug for a in prov.list(folder=keeper.id)] == [other.slug]


def test_delete_folder_reparents_child_folders_to_root(prov, store):
    parent = store.create("Parent")
    child = store.create("Child", parent_id=parent.id)
    grandchild = store.create("Grandchild", parent_id=child.id)

    assert delete_folder(store, prov, parent.id) == (True, 0)

    assert store.get(child.id).parent_id == ""
    # Only direct children are reparented; deeper links are untouched.
    assert store.get(grandchild.id).parent_id == child.id


def test_delete_unknown_folder_is_a_miss(prov, store):
    art = _make(prov, "Safe")
    assert delete_folder(store, prov, "deadbeefcafe") == (False, 0)
    assert prov.get(art.slug) is not None


# ── nested folders validated ──


def test_create_with_missing_parent_is_refused(store):
    with pytest.raises(ValueError, match="parent folder not found"):
        store.create("Orphan", parent_id="deadbeefcafe")
    assert store.list() == []
    assert not store.path.exists()  # refusal persisted nothing


def test_reparent_to_missing_parent_is_refused(store):
    folder = store.create("Real")
    with pytest.raises(ValueError, match="parent folder not found"):
        store.update(folder.id, parent_id="deadbeefcafe")
    assert store.get(folder.id).parent_id == ""


def test_self_parent_is_refused(store):
    folder = store.create("Loop")
    with pytest.raises(ValueError, match="cannot be its own parent"):
        store.update(folder.id, parent_id=folder.id)
    assert store.get(folder.id).parent_id == ""


def test_cycle_through_a_descendant_is_refused(store):
    a = store.create("A")
    b = store.create("B", parent_id=a.id)
    c = store.create("C", parent_id=b.id)

    with pytest.raises(ValueError, match="own descendant"):
        store.update(a.id, parent_id=c.id)

    # Nothing moved: the tree is still A > B > C.
    assert store.get(a.id).parent_id == ""
    assert store.get(b.id).parent_id == a.id
    assert store.get(c.id).parent_id == b.id
    assert store.descendants(a.id) == [b.id, c.id]
    assert store.children(a.id) == [store.get(b.id)]


def test_nesting_round_trips_and_moves(store):
    a = store.create("A")
    b = store.create("B")
    child = store.create("Child", parent_id=a.id)
    assert store.descendants(a.id) == [child.id]
    moved = store.update(child.id, parent_id=b.id)
    assert moved.parent_id == b.id
    assert store.descendants(a.id) == []
    assert store.descendants(b.id) == [child.id]
    # And back to the root.
    assert store.update(child.id, parent_id="").parent_id == ""
    assert store.children("") == sorted(store.list(), key=lambda f: (f.order, f.name.lower()))


# ── persistence across reload ──


def test_membership_and_tree_persist_across_reload(tmp_path):
    root = tmp_path / "artifacts"
    prov = NativeArtifactProvider(root=root)
    store = ArtifactFolderStore(root)
    parent = store.create("Parent", icon="📁")
    child = store.create("Child", parent_id=parent.id)
    art = _make(prov, "Filed")
    prov.set_folder(art.slug, child.id)
    updated_at = prov.get(art.slug).updated_at

    # Fresh instances over the same path — no shared in-process state.
    prov2 = NativeArtifactProvider(root=root)
    store2 = ArtifactFolderStore(root)

    assert [(f.name, f.parent_id, f.icon) for f in store2.list()] == [
        ("Parent", "", "📁"),
        ("Child", parent.id, ""),
    ]
    reloaded = prov2.get(art.slug)
    assert reloaded.folder_id == child.id
    assert reloaded.updated_at == updated_at
    assert [a.slug for a in prov2.list(folder=child.id)] == [art.slug]


def test_folders_file_lives_inside_the_artifacts_tree(tmp_path):
    root = tmp_path / "artifacts"
    store = ArtifactFolderStore(root)
    store.create("Anywhere")
    assert store.path == root / "folders.json"
    assert json.loads(store.path.read_text())[0]["name"] == "Anywhere"


def test_corrupt_folders_file_reads_as_empty(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir(parents=True)
    (root / "folders.json").write_text("{not json")
    assert ArtifactFolderStore(root).list() == []


def test_folders_file_does_not_confuse_the_artifact_listing(prov, store):
    art = _make(prov, "Real")
    store.create("A folder")
    # provider.list enumerates DIRECTORIES, so folders.json is not read as an artifact.
    assert [a.slug for a in prov.list()] == [art.slug]


# ── list filter semantics ──


def test_folder_filter_is_present_vs_absent(prov, store):
    filed = _make(prov, "Filed")
    loose = _make(prov, "Loose")
    folder = store.create("Box")
    prov.set_folder(filed.slug, folder.id)

    assert {a.slug for a in prov.list()} == {filed.slug, loose.slug}
    assert {a.slug for a in prov.list(folder=None)} == {filed.slug, loose.slug}
    assert [a.slug for a in prov.list(folder="")] == [loose.slug]
    assert [a.slug for a in prov.list(folder=folder.id)] == [filed.slug]
    assert prov.list(folder="deadbeefcafe") == []


def test_folder_id_tolerant_load_defaults_to_unfiled(prov):
    """A meta.json written before PEP-6 has no folder_id key: it loads as unfiled."""
    art = _make(prov, "Legacy")
    meta = prov.root / art.slug / "meta.json"
    data = json.loads(meta.read_text())
    del data["folder_id"]
    meta.write_text(json.dumps(data))

    assert prov.get(art.slug).folder_id == ""
    assert [a.slug for a in prov.list(folder="")] == [art.slug]


def test_folder_dataclass_round_trip():
    folder = ArtifactFolder(id="abc123abc123", name="N", parent_id="p", order=3, icon="📁")
    assert ArtifactFolder.from_dict(folder.to_dict()) == folder
    # Tolerant of a record missing every optional key.
    assert ArtifactFolder.from_dict({"id": "x"}) == ArtifactFolder(id="x", name="")
