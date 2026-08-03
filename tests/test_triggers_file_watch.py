"""The `file` trigger kind's runtime (AUTO §7 criterion 2 — S83).

Criterion 2: "*When a file in ~/notes changes, summarize it into my knowledge base*" is creatable in
chat in one message.

**Measured before writing.** The `file` kind is fully DECLARED — in `models.KINDS`, spec keys
`{paths, dedup}`, and a `file` trigger parses and stays `enabled=True` — and nothing watched a
filesystem for it. All nine `schedule_*` chat tools are clock-only, and the trigger handler had zero
references to the kind. A user could author one and it would never fire.

Every test here drives a REAL directory. A mocked filesystem cannot show the defect this module
exists
to prevent (an identical rewrite re-firing), because that defect is about `mtime` moving while
the content does not.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from personalclaw.triggers.file_watch import (
    HASH_BYTES,
    MAX_WATCHED_FILES,
    VCS_GLOBS,
    Delta,
    WatchState,
    changed_files,
    content_hash,
    expand_globs,
    fire_payload,
    should_fire,
    vcs_patterns,
)


@pytest.fixture
def notes(tmp_path):
    """A small notes tree, the criterion's own example shape."""
    root = tmp_path / "notes"
    root.mkdir()
    (root / "a.md").write_text("alpha")
    (root / "b.md").write_text("beta")
    (root / "sub").mkdir()
    (root / "sub" / "c.md").write_text("gamma")
    return root


def _pat(root: Path) -> list[str]:
    return [str(root / "**" / "*.md")]


def _names(paths: list[str]) -> list[str]:
    return sorted(Path(p).name for p in paths)


# ── glob expansion ──


def test_a_recursive_glob_finds_nested_files(notes):
    assert _names([str(p) for p in expand_globs(_pat(notes))]) == ["a.md", "b.md", "c.md"]


def test_a_bare_trailing_double_star_matches_files_on_every_python(notes):
    """🔴 CROSS-VERSION REGRESSION. On Python 3.12 a trailing `**` segment matches DIRECTORIES
    ONLY, so `~/notes/**` (exactly what nl_kind emits for a directory watch) expanded to ZERO files
    and the whole file-watch runtime silently saw nothing; 3.13 changed `**` to also match files.
    Measured 0-vs-2 files for `{dir}/**` on 3.12. `expand_globs` normalizes a trailing `/**` to
    `/**/*` so a directory watch finds its files identically on both — this is the shape the poll
    loop actually receives, and this test is why it can no longer silently watch nothing on 3.12."""
    found = expand_globs([str(notes / "**")])
    assert _names([str(p) for p in found]) == ["a.md", "b.md", "c.md"]


def test_directories_are_never_watched(notes):
    """A directory's mtime moves when any child changes, so watching it would double-fire alongside
    the child that actually changed."""
    found = expand_globs([str(notes / "**")])
    assert found and all(p.is_file() for p in found)


def test_a_tilde_is_expanded():
    """A chat-authored trigger says `~/notes/**`. A literal `~` directory is not what the user
    meant, and silently watching nothing is the worst outcome."""
    home = Path.home()
    expanded = expand_globs(["~"])
    # `~` itself is a directory so it is filtered out; what matters is that no path named "~"
    # appears.
    assert not any(p.name == "~" for p in expanded)
    # And a real pattern under home resolves to absolute paths under it.
    for p in expand_globs(["~/*"]):
        assert str(p).startswith(str(home))


def test_an_absolute_pattern_works(notes):
    assert expand_globs([str(notes / "*.md")])


def test_a_relative_pattern_resolves_against_base(notes):
    assert _names([str(p) for p in expand_globs(["*.md"], base=notes)]) == ["a.md", "b.md"]


def test_a_malformed_glob_watches_nothing_rather_than_raising():
    """A trigger with a typo'd glob must not take down the poll loop serving every other trigger."""
    assert expand_globs(["[[[bad"]) == []
    assert expand_globs([""]) == []
    assert expand_globs([]) == []


def test_expansion_is_sorted_and_deduped(notes):
    """Two overlapping patterns must not double-count a file, and order must be stable so the cap
    truncates the same subset every poll."""
    both = expand_globs([str(notes / "*.md"), str(notes / "a.md")])
    assert len(both) == 2
    assert both == sorted(both)


# ── the content hash ──


def test_the_hash_changes_with_content(notes):
    before = content_hash(notes / "a.md")
    (notes / "a.md").write_text("alpha edited")
    assert content_hash(notes / "a.md") != before


def test_the_hash_is_stable_across_an_identical_rewrite(notes):
    before = content_hash(notes / "a.md")
    time.sleep(0.01)
    (notes / "a.md").write_text("alpha")
    assert content_hash(notes / "a.md") == before


def test_the_hash_covers_size_so_appended_content_is_seen(tmp_path):
    """Only the first `HASH_BYTES` are read, so a file that grows past the window must still change
    its signature — otherwise an appending log would look static."""
    big = tmp_path / "log"
    big.write_bytes(b"x" * (HASH_BYTES + 10))
    before = content_hash(big)
    big.write_bytes(b"x" * (HASH_BYTES + 999))
    assert content_hash(big) != before


def test_an_unreadable_path_hashes_to_empty(tmp_path):
    assert content_hash(tmp_path / "missing.md") == ""


# ── the seeding pass ──


def test_the_first_pass_seeds_and_never_fires(notes):
    """The bug `ConfigFsWatcher` documents, and worse for a trigger: an automation firing over a
    whole directory the first time it is enabled."""
    delta, state = changed_files(_pat(notes), WatchState())
    assert delta.seeding is True
    assert delta.changed == [] and delta.removed == []
    assert should_fire(delta) is False
    assert state.seeded is True
    assert len(state.hashes) == 3


def test_a_quiet_directory_never_fires(notes):
    """A trigger that fired every poll regardless would be a timer wearing a file-watch costume."""
    _d, state = changed_files(_pat(notes), WatchState())
    for _ in range(3):
        delta, state = changed_files(_pat(notes), state)
        assert should_fire(delta) is False


# ── the change classes ──


def test_a_real_edit_is_modified_and_fires(notes):
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "a.md").write_text("alpha edited")
    delta, _state = changed_files(_pat(notes), state)
    assert _names(delta.modified) == ["a.md"]
    assert should_fire(delta) is True


def test_an_identical_rewrite_does_NOT_fire(notes):
    """🔴 The defect the plan names: dedup keyed on "(path, content_hash), not path-only (R12)".

    An editor saving twice, a `touch`, or a rewrite with the same bytes all move `mtime`. A
    path-only or mtime-only key re-fires the automation on a no-op save.
    """
    _d, state = changed_files(_pat(notes), WatchState())
    body = (notes / "b.md").read_text()
    time.sleep(0.01)
    (notes / "b.md").write_text(body)
    delta, _state = changed_files(_pat(notes), state)
    assert delta.changed == []
    assert should_fire(delta) is False


def test_a_new_file_is_added(notes):
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "new.md").write_text("delta")
    delta, _state = changed_files(_pat(notes), state)
    assert _names(delta.added) == ["new.md"]
    assert delta.modified == []


def test_a_deleted_file_is_removed(notes):
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "b.md").unlink()
    delta, _state = changed_files(_pat(notes), state)
    assert _names(delta.removed) == ["b.md"]
    assert should_fire(delta) is True


def test_the_three_classes_stay_separate(notes):
    """§2 says fired workflows "foreach only over new items". A summarize automation wants
    added+modified; a cleanup automation wants removed. One merged list forces every consumer to
    re-derive this."""
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "a.md").write_text("changed")
    (notes / "n.md").write_text("new")
    (notes / "b.md").unlink()
    delta, _state = changed_files(_pat(notes), state)
    assert _names(delta.modified) == ["a.md"]
    assert _names(delta.added) == ["n.md"]
    assert _names(delta.removed) == ["b.md"]
    assert _names(delta.changed) == ["a.md", "n.md"]  # NOT the removal


def test_a_change_is_reported_once_not_every_poll(notes):
    """The state advances, so a single edit fires exactly one automation run."""
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "a.md").write_text("edited")
    first, state = changed_files(_pat(notes), state)
    second, _state = changed_files(_pat(notes), state)
    assert should_fire(first) is True
    assert should_fire(second) is False


# ── state handling ──


def test_state_is_returned_not_mutated(notes):
    """A caller that fails to persist must not half-advance the watch: either the new state is
    stored and the delta consumed, or neither happened."""
    _d, state = changed_files(_pat(notes), WatchState())
    snapshot = dict(state.hashes)
    (notes / "a.md").write_text("edited")
    _delta, new_state = changed_files(_pat(notes), state)
    assert state.hashes == snapshot  # the old state is untouched
    assert new_state.hashes != snapshot


def test_state_round_trips_through_a_dict(notes):
    """It is persisted on the trigger, so it has to survive JSON."""
    _d, state = changed_files(_pat(notes), WatchState())
    revived = WatchState.from_dict(state.to_dict())
    assert revived.seeded is True
    assert revived.hashes == state.hashes
    # And a revived state does not re-report everything.
    delta, _s = changed_files(_pat(notes), revived)
    assert should_fire(delta) is False


def test_a_missing_or_malformed_state_is_treated_as_unseeded():
    """An absent state must seed rather than fire over every existing file."""
    assert WatchState.from_dict(None).seeded is False
    assert WatchState.from_dict({}).seeded is False
    assert WatchState.from_dict({"hashes": "not-a-dict"}).hashes == {}


# ── the cap ──


def test_the_cap_truncates_deterministically_and_reports_it(tmp_path):
    """A `~/**` glob is hundreds of thousands of paths — hashing them每 poll makes the gateway
    unusable, which is the `broad_watch_glob` finding `automation doctor` already flags."""
    for i in range(12):
        (tmp_path / f"f{i:02d}.md").write_text(str(i))
    delta, state = changed_files([str(tmp_path / "*.md")], WatchState(), cap=5)
    assert delta.truncated is True
    assert len(state.hashes) == 5
    # Deterministic: the same subset every poll, so files do not appear and vanish from the watch.
    _d2, state2 = changed_files([str(tmp_path / "*.md")], WatchState(), cap=5)
    assert sorted(state.hashes) == sorted(state2.hashes)


def test_an_uncapped_watch_reports_no_truncation(notes):
    delta, _state = changed_files(_pat(notes), WatchState())
    assert delta.truncated is False


def test_the_cap_default_is_bounded():
    assert 0 < MAX_WATCHED_FILES <= 100_000


# ── the fire payload ──


def test_the_payload_carries_paths_not_contents(notes):
    """Passing file bodies through a trigger payload would route arbitrary disk content into an
    action's arguments — the rule the lifecycle payloads follow (S82)."""
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "a.md").write_text("secret content here")
    delta, _s = changed_files(_pat(notes), state)
    payload = fire_payload(delta, trigger_id="file:1", trigger_name="notes watcher")
    assert "secret content here" not in str(payload)
    assert payload["kind"] == "file"
    assert payload["trigger_id"] == "file:1"
    assert _names(payload["changed"]) == ["a.md"]


def test_the_payload_counts_removals_too(notes):
    _d, state = changed_files(_pat(notes), WatchState())
    (notes / "a.md").write_text("edited")
    (notes / "b.md").unlink()
    delta, _s = changed_files(_pat(notes), state)
    assert fire_payload(delta)["count"] == 2


def test_the_payload_reports_truncation(tmp_path):
    for i in range(6):
        (tmp_path / f"f{i}.md").write_text(str(i))
    delta, _s = changed_files([str(tmp_path / "*.md")], WatchState(seeded=True), cap=2)
    assert fire_payload(delta)["truncated"] is True


# ── the vcs preset ──


def test_the_vcs_preset_watches_refs_and_HEAD(tmp_path):
    """`.git/HEAD` is included because a branch SWITCH moves HEAD without touching any ref, and an
    on-commit automation that ignored it would fire with a stale idea of its branch."""
    pats = vcs_patterns(tmp_path)
    assert any(p.endswith("refs/heads/*") for p in pats)
    assert any(p.endswith(".git/HEAD") for p in pats)
    assert all(str(tmp_path) in p for p in pats)


def test_the_vcs_preset_expands_a_tilde():
    assert all("~" not in p for p in vcs_patterns("~/repo"))


def test_the_vcs_globs_are_the_declared_pair():
    assert VCS_GLOBS == (".git/refs/heads/*", ".git/HEAD")


def test_a_real_commit_moves_the_watched_ref(tmp_path):
    """Drives the preset against a real git repo rather than asserting on the glob string."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("one")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=repo, check=True, env=env)

    pats = vcs_patterns(repo)
    _d, state = changed_files(pats, WatchState())
    (repo / "f.txt").write_text("two")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "two"], cwd=repo, check=True, env=env)
    delta, _s = changed_files(pats, state)
    assert should_fire(delta) is True


# ── the declared contract this satisfies ──


def test_the_spec_keys_this_module_serves_are_the_declared_ones():
    """`models.SPEC_KEYS['file']` is `{paths, dedup}` — the contract this runtime implements. Drift
    here means a trigger can be authored with a key nothing reads."""
    from personalclaw.triggers.models import SPEC_KEYS

    assert SPEC_KEYS["file"] == frozenset({"paths", "dedup"})


def test_a_file_trigger_parses_and_stays_enabled():
    """Measured before this module existed: it already did — which is exactly why the absent
    runtime was invisible."""
    from personalclaw.triggers.models import parse_trigger

    trigger, issues = parse_trigger(
        {
            "id": "t1",
            "name": "notes watcher",
            "kind": "file",
            "enabled": True,
            "spec": {"paths": ["~/notes/**"]},
            "workflow": {"provider": "run-prompt", "config": {"message": "summarize"}},
        }
    )
    assert trigger.kind == "file"
    assert trigger.enabled is True
    assert [i for i in issues if i.severity == "error"] == []


def test_an_empty_delta_is_falsy_for_any_change():
    assert Delta().any_change is False
    assert Delta(added=["x"]).any_change is True
    assert Delta(removed=["x"]).any_change is True
