"""The brownfield context pass (UP-R17, WF2UNI-11).

The pass exists to be TRUE about a real directory, so these tests build real trees under tmp_path
and assert the synthesis reflects them. The cache tests are the load-bearing ones: a stale reading
served after the codebase changed is the exact failure the tree-hash key prevents, and a TTL that
never expires is a reading trusted forever — both are asserted by driving the clock the code takes
as a parameter, never by sleeping.
"""

from pathlib import Path

from personalclaw.workflows.brownfield import (
    DEFAULT_TTL_SECS,
    BrownfieldCache,
    build_codebase_context,
    codebase_context,
    depth_filtered_tree,
    tree_hash,
)


def _project(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text("def test(): pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "Makefile").write_text("build:\n\techo hi\n", encoding="utf-8")
    (root / "README.md").write_text("# X\n\nA tool that does the thing.\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("//\n", encoding="utf-8")
    return root


def test_the_synthesis_reflects_the_real_project(tmp_path):
    ctx = build_codebase_context(_project(tmp_path))
    assert ctx is not None
    rendered = ctx.render()
    assert "Python (pyproject.toml)" in rendered
    assert "Makefile present" in rendered
    assert "A tool that does the thing." in rendered
    assert "src/" in rendered and "tests/" in rendered


def test_common_ignores_are_dropped(tmp_path):
    entries, _ = depth_filtered_tree(_project(tmp_path))
    assert not any("node_modules" in e for e in entries), "dependency trees must not be walked"


def test_the_tree_is_depth_limited(tmp_path):
    root = tmp_path
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "b" / "c" / "deep.py").write_text("x\n", encoding="utf-8")
    entries, _ = depth_filtered_tree(root, max_depth=2)
    assert "a/" in entries
    assert "a/b/" in entries
    assert not any("a/b/c" in e for e in entries), "depth 2 must not reach the third level"


def test_a_non_directory_is_not_a_project(tmp_path):
    assert build_codebase_context(tmp_path / "does-not-exist") is None
    assert codebase_context("p", tmp_path / "nope") == ""


def test_the_cache_hits_on_the_same_tree_hash(tmp_path):
    # Cache lives OUTSIDE the scanned project, mirroring production (config_dir vs workspace) — a
    # cache file written into the tree would itself change the tree-hash.
    project = _project(tmp_path / "proj")
    cache = BrownfieldCache(tmp_path / "cache.json")
    first = codebase_context("proj-1", project, cache=cache, now=1000.0)
    assert "A tool that does the thing." in first

    # Rewriting the README CONTENT (same filename) leaves the tree listing unchanged, so the
    # tree-hash is unchanged and the call returns the cached render — a re-read would pick up the
    # new content, a hit keeps the old. That is exactly the README read the cache exists to skip.
    (project / "README.md").write_text("# X\n\nCompletely different now.\n", encoding="utf-8")
    second = codebase_context("proj-1", project, cache=cache, now=1000.0)
    assert second == first
    assert "A tool that does the thing." in second


def test_the_cache_misses_when_the_tree_changes(tmp_path):
    project = _project(tmp_path / "proj")
    cache = BrownfieldCache(tmp_path / "cache.json")
    codebase_context("proj-1", project, cache=cache, now=1000.0)

    # Adding a file within the depth-limited listing changes the tree-hash → a re-synthesis, not the
    # stale entry. `src/` is walked, so a file directly under it appears in the depth-2 tree.
    (project / "src" / "new_module.py").write_text("y = 2\n", encoding="utf-8")
    refreshed = codebase_context("proj-1", project, cache=cache, now=1000.0)
    assert "new_module.py" in refreshed


def test_the_cache_expires_after_the_ttl(tmp_path):
    project = _project(tmp_path)
    cache = BrownfieldCache(tmp_path / "cache.json", ttl_secs=100)
    tree, truncated = depth_filtered_tree(project)
    digest = tree_hash(tree, truncated)

    cache.put("proj-1", digest, "RENDER", now=1000.0)
    assert cache.get("proj-1", digest, now=1050.0) == "RENDER", "within TTL is a hit"
    assert cache.get("proj-1", digest, now=1200.0) is None, "past TTL is a miss"


def test_a_changed_tree_hash_is_a_cache_miss_even_within_ttl(tmp_path):
    cache = BrownfieldCache(tmp_path / "cache.json", ttl_secs=DEFAULT_TTL_SECS)
    cache.put("proj-1", "hash-a", "RENDER", now=1000.0)
    assert cache.get("proj-1", "hash-b", now=1001.0) is None


def test_the_tree_hash_changes_with_the_tree(tmp_path):
    a = tree_hash(["src/", "src/core.py"], 0)
    b = tree_hash(["src/", "src/core.py", "src/new.py"], 0)
    assert a != b


def test_the_cache_survives_a_corrupt_file(tmp_path):
    cache = BrownfieldCache(tmp_path / "cache.json")
    (tmp_path / "cache.json").write_text("{not json", encoding="utf-8")
    # A corrupt cache reads as empty (a miss), never raises.
    assert cache.get("proj-1", "h", now=1.0) is None
    cache.put("proj-1", "h", "RENDER", now=1.0)
    assert cache.get("proj-1", "h", now=2.0) == "RENDER"
