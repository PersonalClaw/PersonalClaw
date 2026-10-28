"""App provenance + collision precedence — issues 2528 and 2514.

One fact, several representations, and the surfaces disagreed about it:

* **2528 finding 2.** The shipped git source carries bundles with the SAME NAMES as
  locally-added ones, so an unfiltered Store could render the REMOTE card for a bundle the
  user had just added from disk. The measured symptom was a card showing the git divider and
  the stale remote description ("Unattended research campaigns as an agent tool…") in place
  of the on-disk manifest's ("Ask one question and walk away…"). Those two strings are the
  before/after anchors here.
* **2528 finding 3.** ``_scan_registries``' docstring promised it skips apps "already
  surfaced by a dir-scan" while its body only knew its own ``seen`` set — and because its git
  loop ran first and shared that set, a remote pointer DROPPED the local pointer for the same
  name. The docstring was the correct spec; the body never implemented it. Findings 2 and 3
  are one defect.
* **2528 finding 1.** The bundled git source could not be turned off AND was fetched before
  any consent. Both halves are addressed: ``apps.bundled_source_enabled`` and
  ``network_source_hosts()``.
* **2514.** Settings → Tools badged every non-locked native provider ``built-in``, so apps
  installed from a user-created local source read as shipped-with-the-product.

The rails at the bottom keep the answer in ONE place, in both directions: they name the sites
that may resolve a collision or label provenance, and they carry vacuity floors so a census
that silently matches nothing cannot read as agreement.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import app_catalog_provenance_shared
import pytest

from personalclaw.apps import catalog

_isolate = app_catalog_provenance_shared._isolate

# The issue's own measured strings. Kept verbatim so the anchors in the bug report and the
# anchors in this test are the same bytes.
LOCAL_DESC = "Ask one question and walk away…"
REMOTE_DESC = "Unattended research campaigns as an agent tool…"
COLLIDING_NAME = "deep-research"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _manifest(description: str, *, network: bool, storage: bool) -> str:
    """One app manifest. The two copies of the colliding app differ in DESCRIPTION (what the
    user reads) and in PERMISSIONS (what the app is granted) — the second is what makes this
    a consent-surface defect rather than a cosmetic one.

    The second permission used to be ``"filesystem": "read"|"write"``, which is not a
    permission this project has ever had — so that half of the fixture was inert, no
    assertion in this file read it, and the two copies really differed only in ``network``.
    It is ``storage`` now, which is real and enforced, so the fixture states what it claims.
    """
    return json.dumps(
        {
            "name": COLLIDING_NAME,
            "version": "0.1.0",
            "displayName": "Deep Research",
            "description": description,
            "permissions": {"network": network, "storage": storage},
        }
    )


def _local_copy(root: Path, *, subdir: str = COLLIDING_NAME) -> Path:
    """The copy the user put on disk: no network, no storage."""
    d = root / subdir
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        _manifest(LOCAL_DESC, network=False, storage=False), encoding="utf-8"
    )
    return d


def _write_index(root: Path, *, subdirectory: str, description: str) -> None:
    (root / "app-registry.json").write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "name": COLLIDING_NAME,
                        "subdirectory": subdirectory,
                        "displayName": "Deep Research",
                        "description": description,
                        "version": "0.1.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _remote_copy_repo(root: Path, *, with_index: bool = False) -> str:
    """A real bare git repo carrying the SAME app name, driven over ``file://`` — the
    identical git code path with no network. Its copy declares network + storage, i.e.
    strictly MORE than the local one."""
    work = root / "work"
    work.mkdir(parents=True)
    _git("init", "--initial-branch=main", ".", cwd=work)
    (work / "README.md").write_text("fixture apps repo\n", encoding="utf-8")
    d = work / COLLIDING_NAME
    d.mkdir()
    (d / "app.json").write_text(
        _manifest(REMOTE_DESC, network=True, storage=True), encoding="utf-8"
    )
    if with_index:
        _write_index(work, subdirectory=COLLIDING_NAME, description=REMOTE_DESC)
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "fixture apps", cwd=work)
    bare = root / "apps.git"
    _git("clone", "--bare", str(work), str(bare), cwd=root)
    return f"file://{bare}"


def _cards(cat: dict) -> list[dict]:
    """Every available-to-install card the payload carries, across all four lists — the union
    a frontend consumer sees. Order is the wire order; the point of the fix is that no NAME
    appears twice, so no consumer's concatenation order can change an answer."""
    return [
        *cat.get("bundled", []),
        *cat.get("localApps", []),
        *cat.get("remoteApps", []),
        *cat.get("gitApps", []),
    ]


# ── The collision, driven through the real catalog path ──────────────────────


def test_a_remote_bundle_does_not_shadow_the_local_one_you_just_added(tmp_path):
    """The measured symptom: ONE card, and it is the copy on disk.

    Before the fix both copies reached the wire — `localApps` carried the local one and
    `gitApps` the remote one — so which copy the user saw was decided by whichever consumer
    happened to concatenate the lists, and they disagreed."""
    local_root = tmp_path / "local-src"
    _local_copy(local_root)
    url = _remote_copy_repo(tmp_path / "repo")
    catalog.add_local_source(str(local_root))
    catalog.add_git_source(url)

    cards = [c for c in _cards(catalog.available_catalog()) if c["name"] == COLLIDING_NAME]

    # Vacuity floor: a fixture that surfaced NOTHING would satisfy "the remote did not win".
    assert len(cards) == 1, cards
    assert cards[0]["description"] == LOCAL_DESC
    assert cards[0]["sourceKind"] == "local"
    # …and specifically NOT the stale remote copy the issue measured.
    assert cards[0]["description"] != REMOTE_DESC


def test_the_permissions_on_the_card_are_the_permissions_that_would_install(tmp_path):
    """The consent-surface claim, stated as an equality rather than as a description.

    This is what makes the defect a security bug: the card discloses what the app will be
    GRANTED, and the local and remote copies of one name can declare different grants. Before
    the fix the Store grid resolved to the local copy while the Sources-panel consent lookup
    resolved to the remote one — the same UI, at the same moment, disclosing `network: false`
    in one place and `network: true` in the other. Now there is one card, and its permissions
    are read from the same manifest as the bytes its install pointer fetches."""
    local_root = tmp_path / "local-src"
    local_app = _local_copy(local_root)
    url = _remote_copy_repo(tmp_path / "repo")
    catalog.add_local_source(str(local_root))
    catalog.add_git_source(url)

    cards = [c for c in _cards(catalog.available_catalog()) if c["name"] == COLLIDING_NAME]
    assert len(cards) == 1, cards
    card = cards[0]

    # The bytes this card installs, read straight off disk — not off another catalog call.
    install_from = card["pointer"] or card["source"]
    assert install_from == str(local_app), install_from
    on_disk = json.loads((Path(install_from) / "app.json").read_text(encoding="utf-8"))

    assert card["permissions"].get("network") == on_disk["permissions"]["network"] is False
    # The remote copy asks for strictly more. If it had won, the card would have disclosed
    # network access for bytes that do not request it — or vice versa.
    assert card["permissions"].get("network") is not True


def test_a_local_registry_pointer_is_not_shadowed_by_the_remote_one(tmp_path):
    """The exact shape the issue measured, and the reason findings 2 and 3 are one defect.

    A local source whose index puts its app in a NESTED subdir is invisible to the
    immediate-subdir dir-scan, so ``_scan_registries`` is the only path that can surface it —
    and that function ran its git loop first over a SHARED ``seen`` set, so the remote pointer
    silently dropped the local one. The card then carried `sourceKind: git` (hence the git
    divider) and the stale remote description, which is what the bug report shows."""
    local_root = tmp_path / "local-src"
    _local_copy(local_root, subdir="packages/" + COLLIDING_NAME)
    _write_index(local_root, subdirectory="packages/" + COLLIDING_NAME, description=LOCAL_DESC)
    url = _remote_copy_repo(tmp_path / "repo", with_index=True)
    catalog.add_local_source(str(local_root))
    catalog.add_git_source(url)

    cat = catalog.available_catalog()
    # The dir-scan genuinely cannot see it — this is the fixture's own premise, asserted so a
    # future change that makes localApps carry it turns this into a different test knowingly.
    assert [c for c in cat["localApps"] if c["name"] == COLLIDING_NAME] == []

    cards = [c for c in _cards(cat) if c["name"] == COLLIDING_NAME]
    assert len(cards) == 1, cards
    assert cards[0]["sourceKind"] == "local"
    assert cards[0]["description"] == LOCAL_DESC


def test_the_docstring_promise_holds_a_dir_scan_beats_an_index(tmp_path):
    """``_scan_registries`` claims it skips apps "already surfaced by a dir-scan". With one
    source publishing BOTH an index and a scannable subdir, the dir-scanned entry is what
    survives — the promise, implemented rather than edited away."""
    local_root = tmp_path / "local-src"
    _local_copy(local_root)
    # The same source's index describes the same app with the stale remote text.
    _write_index(local_root, subdirectory=COLLIDING_NAME, description=REMOTE_DESC)
    catalog.add_local_source(str(local_root))

    cat = catalog.available_catalog()
    cards = [c for c in _cards(cat) if c["name"] == COLLIDING_NAME]
    assert len(cards) == 1, cards
    # The dir-scan card, whose description comes from the real manifest.
    assert cards[0]["description"] == LOCAL_DESC
    assert [c for c in cat["remoteApps"] if c["name"] == COLLIDING_NAME] == []


def test_a_first_party_dir_outranks_a_user_added_dir(tmp_path, monkeypatch):
    """Two LOCAL roots carrying one name resolve by rule, not by iteration order."""
    first_party = tmp_path / "first-party"
    _local_copy(first_party)
    user_dir = tmp_path / "user-src"
    d = user_dir / COLLIDING_NAME
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        _manifest(REMOTE_DESC, network=True, storage=True), encoding="utf-8"
    )
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(first_party))
    catalog.add_local_source(str(user_dir))

    cards = [c for c in _cards(catalog.available_catalog()) if c["name"] == COLLIDING_NAME]
    assert len(cards) == 1, cards
    assert cards[0]["sourceKind"] == "first-party"
    assert cards[0]["description"] == LOCAL_DESC


def test_precedence_is_ordered_most_to_least_vouched_for():
    """The ladder itself, pinned. A reorder that let ``git`` outrank ``local`` would restore
    the defect while every collision test above still had exactly one card."""
    assert catalog.SOURCE_PRECEDENCE == ("native", "bundled", "first-party", "local", "git")
    ranks = [catalog.precedence_rank(k) for k in catalog.SOURCE_PRECEDENCE]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    # A remote never outranks anything on disk.
    assert catalog.precedence_rank("git") > catalog.precedence_rank("local")
    # An unknown kind LOSES rather than winning by accident.
    assert catalog.precedence_rank("something-new") > catalog.precedence_rank("git")


def test_an_installed_app_is_not_offered_by_any_source(tmp_path):
    """The Library exclusion, now performed once for every source rather than five times."""
    local_root = tmp_path / "local-src"
    _local_copy(local_root)
    catalog.add_local_source(str(local_root))
    assert [c for c in _cards(catalog.available_catalog()) if c["name"] == COLLIDING_NAME]

    from personalclaw.apps import app_manager

    app_manager.install(str(local_root / COLLIDING_NAME), confirm=True)
    assert COLLIDING_NAME in catalog._installed_names()
    assert [c for c in _cards(catalog.available_catalog()) if c["name"] == COLLIDING_NAME] == []


# ── Provenance vocabulary (2514) ─────────────────────────────────────────────


def test_source_kind_for_origin_never_guesses():
    """The one translation between the Library's ``origin`` and the Store's ``sourceKind``.

    The empty string for an unreadable origin is the point: the Tools page renders NO badge
    for it. Defaulting to a bundled reading is exactly issue 2514 — an absent fact rendered
    as a claim on the screen where the user is asking "did this ship with the product?"."""
    assert catalog.source_kind_for_origin("builtin") == "bundled"
    assert catalog.source_kind_for_origin("registry") == "bundled"
    assert catalog.source_kind_for_origin("local") == "local"
    assert catalog.source_kind_for_origin("external") == "git"
    assert catalog.source_kind_for_origin("builtin", native=True) == "native"
    assert catalog.source_kind_for_origin("local", native=True) == "native"
    # No reading ⇒ no claim.
    assert catalog.source_kind_for_origin("") == ""
    assert catalog.source_kind_for_origin("something-else") == ""
    # Every value it can return is a term the precedence ladder knows.
    for origin in ("builtin", "registry", "local", "external"):
        assert catalog.source_kind_for_origin(origin) in catalog.SOURCE_PRECEDENCE


@pytest.mark.usefixtures("shipped_app_sources")
def test_the_bundled_default_source_can_be_turned_off(tmp_path, monkeypatch):
    """ "Cannot be turned off" no longer holds. The bundled tuple is folded into every read,
    so there is no row to delete — ``apps.bundled_source_enabled`` is the off switch, and it
    is reachable from the config PATCH allowlist."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ("https://example.invalid/apps.git",))
    assert "https://example.invalid/apps.git" in catalog.list_git_sources()

    (tmp_path / "config.json").write_text(
        json.dumps({"apps": {"bundled_source_enabled": False}}), encoding="utf-8"
    )
    assert catalog.bundled_source_enabled() is False
    assert catalog.list_git_sources() == []
    assert catalog.builtin_git_sources() == []

    from personalclaw.config.editable import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG["apps.bundled_source_enabled"]["type"] == "bool"


@pytest.mark.usefixtures("shipped_app_sources")
def test_an_unparseable_config_still_lists_the_default(tmp_path, monkeypatch):
    """Polarity, stated: losing the Store's only source on a corrupt config is the worse
    failure, and the listing is disclosed and refusable either way.

    This covers the TOLERANT path. ``AppConfig.load()`` does not raise on malformed JSON —
    It logs ``Failed to load config from …`` and returns defaults — so a corrupt
    file reaches ``bundled_source_enabled`` through the NORMAL return, carrying the
    dataclass default. Its ``except`` branch is a different mechanism and is covered by the
    test below.
    """
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ("https://example.invalid/apps.git",))
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")
    assert catalog.bundled_source_enabled() is True
    assert "https://example.invalid/apps.git" in catalog.list_git_sources()


@pytest.mark.usefixtures("shipped_app_sources")
def test_a_config_read_that_RAISES_still_lists_the_default(tmp_path, monkeypatch):
    """🔴 The fail-open guarantee, actually exercised. Added after a mutation battery.

    The test above was the only cover for ``bundled_source_enabled``'s fail-open branch, and
    a mutant that flipped that branch to ``return False`` — losing the Store's only source on
    an unreadable config, the exact failure the branch exists to prevent — **survived**.
    The reason is measured, not guessed: ``AppConfig.load()`` swallows malformed JSON and
    returns defaults, so the corrupt file above never makes it raise, and the ``except`` branch
    was unreachable from that fixture. A test named for a guarantee it never entered.

    So this one makes the read genuinely fail. Any exception from the config read must still
    leave the default listed, because a Store with no sources is worse than a Store showing a
    default the user can see and turn off.
    """
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ("https://example.invalid/apps.git",))

    import personalclaw.config.loader as cfg

    def _explode() -> None:
        raise OSError("config unreadable (permissions, bad mount, truncated read)")

    monkeypatch.setattr(cfg.AppConfig, "load", staticmethod(_explode))
    assert catalog.bundled_source_enabled() is True
    assert "https://example.invalid/apps.git" in catalog.list_git_sources()


def test_network_source_hosts_names_the_egress_and_stays_silent_when_local(tmp_path):
    """The disclosure the Store renders. It names HOSTS, and it is empty exactly when opening
    the Store reaches nothing off this machine — so the UI shows a warning about a real fetch
    or nothing at all, never a warning about nothing."""
    url = _remote_copy_repo(tmp_path / "repo")
    catalog.add_git_source(url)
    # A file:// source is not egress. Asserted in BOTH spellings, because the common
    # `file:///path` form has an empty hostname anyway — only `file://host/path` actually
    # exercises the scheme check, and without it that URL would be disclosed as a remote
    # host the Store never dials.
    assert catalog.network_source_hosts() == []
    assert catalog.available_catalog()["networkSources"] == []
    catalog.add_git_source("file://localhost/srv/apps.git")
    assert catalog.network_source_hosts() == []

    catalog.add_git_source("https://github.com/acme/cool-app.git")
    catalog.add_git_source("git@gitlab.example.org:acme/other.git")
    hosts = catalog.network_source_hosts()
    assert hosts == ["github.com", "gitlab.example.org"], hosts


# ── The rails: one owner, and a vacuity floor in both directions ─────────────
