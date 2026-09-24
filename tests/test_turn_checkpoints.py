"""EI-8 — turn-bound two-phase file checkpointing + /rewind-to-turn.

Every claim here is measured on the STORED BYTES or on a post-restore hash, never on a
config value or an exclusion list. That distinction is the point: an exclusion list can be
correct while the store still holds the secret (DAS-10 shipped exactly that defect), and a
cap constant can be right while nothing enforces it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from personalclaw import turn_checkpoints as tc

SESSION = "dashboard:chat-ei8"

# A real-SHAPED secret (not a real one): long, high-entropy, and unique enough that a
# substring search over the whole store is a meaningful test.
PLANTED_SECRET = "AKIA7SDFJK23LKJ4POIU-sk_live_9f8e7d6c5b4a3928176054-EI8CANARY"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _all_store_bytes(root: Path) -> bytes:
    """Every byte the checkpoint store holds on disk, concatenated.

    Walks the WHOLE tree — blobs, manifests, state files, journals — so a secret that
    leaked into any of them is caught. Reading the manifest and asserting on its
    ``skipped`` field would only prove the code's own bookkeeping.
    """
    chunks = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            chunks.append(p.read_bytes())
    return b"\n".join(chunks)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A workspace with three ordinary files plus a `.env` holding a planted secret."""
    w = tmp_path / "workspace"
    w.mkdir()
    (w / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (w / "beta.txt").write_text("beta original\nline two\n", encoding="utf-8")
    (w / "gamma.json").write_text('{"k": "original"}\n', encoding="utf-8")
    (w / ".env").write_text(f"API_TOKEN={PLANTED_SECRET}\n", encoding="utf-8")
    return w


def _mangle_three(ws: Path) -> dict[str, str]:
    """Overwrite the three real files; return path → ORIGINAL sha for the byte check."""
    originals = {
        str(ws / "alpha.py"): _sha(ws / "alpha.py"),
        str(ws / "beta.txt"): _sha(ws / "beta.txt"),
        str(ws / "gamma.json"): _sha(ws / "gamma.json"),
    }
    for p in originals:
        tc.capture_pre_edit(SESSION, p, cwd=ws)
    (ws / "alpha.py").write_text("MANGLED\n", encoding="utf-8")
    (ws / "beta.txt").write_text("MANGLED\n", encoding="utf-8")
    (ws / "gamma.json").write_text("MANGLED\n", encoding="utf-8")
    return originals


# ── SC8: preview names exactly the mangled files; restore is byte-identical ────────


def test_rewind_previews_exactly_the_mangled_files_and_restores_them_byte_identical(ws):
    assert tc.begin_turn(SESSION, cwd=ws) == 1
    assert tc.begin_turn(SESSION, cwd=ws) == 2
    originals = _mangle_three(ws)

    pv = tc.preview_rewind(SESSION, 1)
    previewed = {f.path for f in pv.files if f.action == "restore"}
    # BOTH directions: no omissions and no extras. A preview that named a fourth file
    # would be as wrong as one that missed the third.
    assert previewed == set(originals), previewed
    assert all(f.diff for f in pv.files if f.action == "restore"), "each restore needs a diff"
    for f in pv.files:
        if f.action == "restore":
            assert f.current_sha256 != f.restored_sha256
            assert "MANGLED" in f.diff

    res = tc.apply_rewind(SESSION, 1)
    assert res.ok, res.errors
    assert set(res.restored) == set(originals)
    for path, want in originals.items():
        assert _sha(Path(path)) == want, f"{path} did not come back byte-identical"


def test_preview_writes_nothing(ws):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    _mangle_three(ws)
    before = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}
    tc.preview_rewind(SESSION, 1)
    after = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}
    assert before == after


def test_a_file_created_after_the_target_turn_rewinds_to_deleted(ws):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    new = ws / "created_later.py"
    assert tc.capture_pre_edit(SESSION, new, cwd=ws) == "absent"
    new.write_text("brand new\n", encoding="utf-8")
    pv = tc.preview_rewind(SESSION, 1)
    assert [f.action for f in pv.files if f.path == str(new)] == ["delete"]
    res = tc.apply_rewind(SESSION, 1)
    assert res.ok, res.errors
    assert not new.exists()


# ── the secrecy claim, measured on the stored bytes ───────────────────────────────


def test_the_planted_env_secret_is_absent_from_every_byte_of_the_store(ws):
    tc.begin_turn(SESSION, cwd=ws)
    env = ws / ".env"
    # Sanity: the secret really is on disk where a naive capture would find it. Without
    # this the byte assertion below could pass on an empty fixture.
    assert PLANTED_SECRET in env.read_text(encoding="utf-8")

    status = tc.capture_pre_edit(SESSION, env, cwd=ws)
    assert status == "secret"
    # ...and mangle it, so a "restore" would have had something to restore.
    env.write_text("API_TOKEN=clobbered\n", encoding="utf-8")

    blob = _all_store_bytes(tc.store_root())
    assert PLANTED_SECRET.encode() not in blob, "the .env body reached the checkpoint store"
    assert b"API_TOKEN" not in blob, "even the variable name should not be copied"

    # And the user is TOLD, rather than silently getting nothing back.
    pv = tc.preview_rewind(SESSION, 0)
    entry = [f for f in pv.files if f.path == str(env)]
    assert entry and entry[0].action == "not_captured" and entry[0].reason == "secret"
    assert any("never captured" in w for w in pv.warnings)
    res = tc.apply_rewind(SESSION, 0)
    assert str(env) not in res.restored
    assert env.read_text(encoding="utf-8") == "API_TOKEN=clobbered\n"


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        "prod.env",
        ".envrc",
        "id_rsa",
        "server.key",
        "cert.pem",
        ".netrc",
        ".npmrc",
        "credentials.json",
        "secrets.yaml",
        ".local_secret",
    ],
)
def test_the_secrecy_floor_covers_every_credential_shape(tmp_path, name):
    p = tmp_path / name
    p.write_text(f"SECRET={PLANTED_SECRET}\n", encoding="utf-8")
    assert tc.is_never_captured(p) is True
    assert tc.capture_pre_edit(SESSION, p, cwd=tmp_path) == "secret"
    assert PLANTED_SECRET.encode() not in _all_store_bytes(tc.store_root())


def test_an_ordinary_file_is_captured_so_the_floor_is_not_vacuous(tmp_path):
    p = tmp_path / "ordinary.py"
    p.write_text("x = 1\n", encoding="utf-8")
    assert tc.is_never_captured(p) is False
    assert tc.capture_pre_edit(SESSION, p, cwd=tmp_path) == "captured"
    assert b"x = 1" in _all_store_bytes(tc.store_root())


# ── the caps, measured at the boundary ────────────────────────────────────────────


def _set_bounds(monkeypatch, **kw):
    base = {"enabled": True, "max_mb": 200, "max_turns": 50, "max_file_mb": 8}
    base.update(kw)
    monkeypatch.setattr(tc, "_bounds", lambda: tc._Bounds(**base))


def test_the_byte_cap_prunes_the_oldest_turn_rather_than_growing_past_it(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_mb=1, max_file_mb=0)
    body = b"A" * (400 * 1024)  # 400KB — three do not fit under a 1MB cap
    for i in range(3):
        tc.begin_turn(SESSION, cwd=None)
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(body + bytes([i]))  # unique content, so no dedupe masks the cap
        assert tc.capture_pre_edit(SESSION, f, cwd=tmp_path) == "captured"
    assert tc.store_bytes(SESSION) <= 1024 * 1024, tc.store_bytes(SESSION)
    # Enforcement is by EVICTION, not refusal: the newest turn kept its body and an
    # older one is gone.
    assert len(tc._turn_numbers(SESSION)) < 3


def test_the_cap_boundary_at_exactly_the_limit_does_not_prune(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_mb=1, max_file_mb=0)
    half = b"B" * (512 * 1024)
    tc.begin_turn(SESSION, cwd=None)
    a = tmp_path / "a.bin"
    a.write_bytes(half)
    assert tc.capture_pre_edit(SESSION, a, cwd=tmp_path) == "captured"
    tc.begin_turn(SESSION, cwd=None)
    b = tmp_path / "b.bin"
    b.write_bytes(half.replace(b"B", b"C"))
    assert tc.capture_pre_edit(SESSION, b, cwd=tmp_path) == "captured"
    # Exactly 1MB stored, cap 1MB → both turns survive. One byte more must evict.
    assert tc.store_bytes(SESSION) == 1024 * 1024
    assert tc._turn_numbers(SESSION) == [1, 2]

    tc.begin_turn(SESSION, cwd=None)
    c = tmp_path / "c.bin"
    c.write_bytes(b"D")
    assert tc.capture_pre_edit(SESSION, c, cwd=tmp_path) == "captured"
    assert tc.store_bytes(SESSION) <= 1024 * 1024
    assert 1 not in tc._turn_numbers(SESSION), "the oldest turn should have been evicted"


def test_a_body_bigger_than_the_whole_cap_is_recorded_manifest_only(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_mb=1, max_file_mb=0)
    tc.begin_turn(SESSION, cwd=None)
    big = tmp_path / "big.bin"
    big.write_bytes(b"E" * (2 * 1024 * 1024))
    assert tc.capture_pre_edit(SESSION, big, cwd=tmp_path) == "too_large"
    assert tc.store_bytes(SESSION) == 0
    pv = tc.preview_rewind(SESSION, 0)
    assert [f.reason for f in pv.files if f.path == str(big)] == ["over_cap"]


def test_the_per_file_cap_records_manifest_only_and_warns(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_file_mb=1)
    tc.begin_turn(SESSION, cwd=None)
    big = tmp_path / "big.bin"
    big.write_bytes(b"F" * (2 * 1024 * 1024))
    assert tc.capture_pre_edit(SESSION, big, cwd=tmp_path) == "too_large"
    assert tc.store_bytes(SESSION) == 0
    pv = tc.preview_rewind(SESSION, 0)
    assert any("not captured" in w for w in pv.warnings)


def test_the_turn_cap_drops_the_oldest_turns(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_turns=3)
    for i in range(6):
        tc.begin_turn(SESSION, cwd=None)
        f = tmp_path / f"t{i}.txt"
        f.write_text(f"body {i}\n", encoding="utf-8")
        tc.capture_pre_edit(SESSION, f, cwd=tmp_path)
    turns = tc._turn_numbers(SESSION)
    assert len(turns) == 3 and turns == [4, 5, 6], turns
    # ...and the evicted turns' blobs were garbage-collected, not orphaned.
    store = _all_store_bytes(tc.store_root())
    assert b"body 0" not in store and b"body 5" in store


def test_a_pruned_turn_makes_the_preview_say_so_instead_of_pretending(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, max_turns=2)
    for i in range(4):
        tc.begin_turn(SESSION, cwd=None)
        f = tmp_path / f"p{i}.txt"
        f.write_text(f"v{i}\n", encoding="utf-8")
        tc.capture_pre_edit(SESSION, f, cwd=tmp_path)
    pv = tc.preview_rewind(SESSION, 1)
    assert any("were pruned" in w for w in pv.warnings), pv.warnings


def test_dedupe_means_a_second_write_in_the_same_turn_keeps_the_original_bytes(tmp_path):
    tc.begin_turn(SESSION, cwd=None)
    f = tmp_path / "twice.txt"
    f.write_text("first\n", encoding="utf-8")
    assert tc.capture_pre_edit(SESSION, f, cwd=tmp_path) == "captured"
    f.write_text("second\n", encoding="utf-8")
    assert tc.capture_pre_edit(SESSION, f, cwd=tmp_path) == "deduped"
    f.write_text("third\n", encoding="utf-8")
    tc.apply_rewind(SESSION, 0)
    assert f.read_text(encoding="utf-8") == "first\n", "the turn's FIRST state is the checkpoint"


# ── pruning with the session ───────────────────────────────────────────────────────


def test_prune_session_removes_the_whole_tree(tmp_path):
    tc.begin_turn(SESSION, cwd=None)
    f = tmp_path / "x.txt"
    f.write_text("x\n", encoding="utf-8")
    tc.capture_pre_edit(SESSION, f, cwd=tmp_path)
    assert tc.session_dir(SESSION).is_dir()
    assert tc.prune_session(SESSION) is True
    assert not tc.session_dir(SESSION).exists()
    assert tc.current_turn(SESSION) == 0


def test_prune_orphans_keeps_live_sessions_and_drops_the_rest(tmp_path):
    for key in ("live-1", "dead-1", "dead-2"):
        tc.begin_turn(key, cwd=None)
    assert tc.prune_orphans(["live-1"]) == 2
    assert tc.session_dir("live-1").is_dir()
    assert not tc.session_dir("dead-1").exists()


def test_the_hard_delete_handler_purges_the_checkpoint_tree(tmp_path):
    """The cap is per session, so a tree the delete path forgets is never reclaimed.
    Asserts the CALL SITE, not just that prune_session works in isolation."""
    import ast
    import inspect

    from personalclaw.dashboard import chat_handlers

    src = inspect.getsource(chat_handlers.api_chat_session_delete)
    tree = ast.parse(src.lstrip())
    calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "prune_session" in calls, "session hard-delete must prune the checkpoint store"


# ── the unhappy path: a death between the two restore phases ───────────────────────


def test_a_death_between_staging_and_commit_leaves_a_resumable_journal(ws, monkeypatch):
    """Two-phase means there IS a window; the test is that the window is recoverable.

    `os.replace` is made to fail on the SECOND file, so the commit stops half-done — the
    exact state that is worse than no restore. The journal survives, and resuming
    completes every file byte-identical.
    """
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    originals = _mangle_three(ws)

    real_replace = tc.os.replace
    calls = {"n": 0}
    targets = set(originals)

    def flaky(src, dst):
        # Scoped to the COMMIT renames only. `os.replace` is also how `atomic_write`
        # lands the store's own manifests, so an unscoped patch fails `begin_turn`
        # instead of the commit — a harness artifact that fakes this finding.
        if str(dst) in targets:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(5, "simulated I/O error mid-commit")
        return real_replace(src, dst)

    monkeypatch.setattr(tc.os, "replace", flaky)
    res = tc.apply_rewind(SESSION, 1)
    assert not res.ok and res.errors, "a partial commit must be reported, not swallowed"
    # The journal is still there — the plan for the remaining files was not lost.
    tokens = tc.pending_rewinds(SESSION)
    assert tokens, "a failed commit must leave its journal for resume"
    # Exactly one file did NOT come back yet: the honest half-restored state.
    unrestored = [p for p, want in originals.items() if _sha(Path(p)) != want]
    assert len(unrestored) == 1, unrestored

    monkeypatch.setattr(tc.os, "replace", real_replace)
    out = tc.resume_incomplete_rewind(SESSION)
    assert out["resumed"] and not out["errors"], out
    for path, want in originals.items():
        assert _sha(Path(path)) == want, f"{path} not byte-identical after resume"
    assert tc.pending_rewinds(SESSION) == [], "a completed journal must be removed"


def test_a_staging_failure_leaves_the_working_tree_completely_untouched(ws, monkeypatch):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    _mangle_three(ws)
    mangled = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}

    def boom(path, data, **kw):
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(tc, "atomic_write_bytes", boom)
    res = tc.apply_rewind(SESSION, 1)
    assert not res.ok and any("no files were modified" in e for e in res.errors)
    assert {p: _sha(p) for p in ws.rglob("*") if p.is_file()} == mangled


def test_resume_is_idempotent(ws):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    originals = _mangle_three(ws)
    tc.apply_rewind(SESSION, 1)
    assert tc.resume_incomplete_rewind(SESSION) == {"resumed": [], "errors": []}
    for path, want in originals.items():
        assert _sha(Path(path)) == want


def test_the_rewind_is_itself_rewindable_via_the_safety_turn(ws):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    _mangle_three(ws)
    mangled = _sha(ws / "alpha.py")
    res = tc.apply_rewind(SESSION, 1)
    assert res.safety_turn > 0
    # Rewinding to just before the safety turn returns the MANGLED state — proving the
    # rewind captured what it overwrote rather than destroying it.
    res2 = tc.apply_rewind(SESSION, res.safety_turn - 1)
    assert res2.ok, res2.errors
    assert _sha(ws / "alpha.py") == mangled


# ── guards ─────────────────────────────────────────────────────────────────────────


def test_rewinding_to_the_current_or_a_future_turn_is_refused(ws):
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    for target in (2, 3, 99):
        pv = tc.preview_rewind(SESSION, target)
        assert pv.files == []
        assert any("nothing to undo" in w for w in pv.warnings), (target, pv.warnings)


def test_disabled_config_records_nothing(tmp_path, monkeypatch):
    _set_bounds(monkeypatch, enabled=False)
    assert tc.begin_turn(SESSION, cwd=tmp_path) == 0
    f = tmp_path / "y.txt"
    f.write_text("y\n", encoding="utf-8")
    assert tc.capture_pre_edit(SESSION, f, cwd=tmp_path) == "disabled"
    assert not tc.session_dir(SESSION).exists()


def test_session_slug_is_injective_for_keys_that_sanitize_alike():
    a, b = tc.session_slug("dashboard:chat-1"), tc.session_slug("dashboard/chat-1")
    assert a != b, "two distinct session keys must not share a checkpoint tree"


def test_the_identity_set_records_paths_without_copying_bytes(ws):
    turn = tc.begin_turn(SESSION, cwd=ws)
    man = json.loads((tc._turn_dir(SESSION, turn) / "manifest.json").read_text())
    names = {e["path"] for e in man["identity"]}
    assert {"alpha.py", "beta.txt", "gamma.json"} <= names
    assert all("size" in e and "mtime" in e for e in man["identity"])
    # Phase 1 is a manifest, not a copy: no blob directory exists yet.
    assert not (tc.session_dir(SESSION) / "blobs").exists()
    # ...and the identity set is metadata only — it must not contain file CONTENT.
    assert b"def alpha" not in _all_store_bytes(tc.store_root())


# ── the interception point: the real tool handlers ─────────────────────────────────


def _provider(cwd: Path):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    return NativeBuiltinToolProvider(cwd=cwd, session_key=SESSION)


def _observe(p, *names: str) -> None:
    """AG-14: the pre-edit read gate refuses a write to a file whose current content was
    never observed, so these drives must read what they are about to mangle. Reading
    through the real tool is the point — it is the same seam the agent goes through."""
    for name in names:
        r = asyncio.run(p.invoke("read_file", {"path": name}))
        assert r.success, f"could not observe {name}: {r.error}"


def test_the_real_write_file_tool_checkpoints_before_it_writes(ws):
    """Drives the actual tool handler, not the store — proves the seam is wired."""
    tc.begin_turn(SESSION, cwd=ws)
    p = _provider(ws)
    original = _sha(ws / "alpha.py")
    _observe(p, "alpha.py")
    r = asyncio.run(p.invoke("write_file", {"path": "alpha.py", "content": "WRECKED\n"}))
    assert "Wrote" in str(r) or getattr(r, "success", True)
    assert _sha(ws / "alpha.py") != original
    res = tc.apply_rewind(SESSION, 0)
    assert res.ok, res.errors
    assert _sha(ws / "alpha.py") == original


def test_the_real_edit_file_tool_checkpoints_before_it_writes(ws):
    tc.begin_turn(SESSION, cwd=ws)
    p = _provider(ws)
    original = _sha(ws / "beta.txt")
    _observe(p, "beta.txt")
    asyncio.run(
        p.invoke("edit_file", {"path": "beta.txt", "old_str": "beta original", "new_str": "RUINED"})
    )
    assert _sha(ws / "beta.txt") != original
    res = tc.apply_rewind(SESSION, 0)
    assert res.ok, res.errors
    assert _sha(ws / "beta.txt") == original


def test_the_real_write_file_tool_never_stores_a_dotenv_body(ws):
    tc.begin_turn(SESSION, cwd=ws)
    p = _provider(ws)
    _observe(p, ".env")
    asyncio.run(p.invoke("write_file", {"path": ".env", "content": "API_TOKEN=replaced\n"}))
    assert PLANTED_SECRET.encode() not in _all_store_bytes(tc.store_root())


def test_three_files_mangled_through_the_real_tools_restore_byte_identical(ws):
    """SC8 end to end through the tool surface an agent actually calls."""
    tc.begin_turn(SESSION, cwd=ws)
    tc.begin_turn(SESSION, cwd=ws)
    originals = {str(ws / n): _sha(ws / n) for n in ("alpha.py", "beta.txt", "gamma.json")}
    p = _provider(ws)
    _observe(p, "alpha.py", "beta.txt", "gamma.json", ".env")
    asyncio.run(p.invoke("write_file", {"path": "alpha.py", "content": "no\n"}))
    asyncio.run(
        p.invoke("edit_file", {"path": "beta.txt", "old_str": "line two", "new_str": "nope"})
    )
    asyncio.run(p.invoke("write_file", {"path": "gamma.json", "content": "{}\n"}))
    # ...and touch the .env in the same turn, so the secrecy leg rides the same drive.
    asyncio.run(p.invoke("write_file", {"path": ".env", "content": "API_TOKEN=x\n"}))

    pv = tc.preview_rewind(SESSION, 1)
    assert {f.path for f in pv.files if f.action == "restore"} == set(originals)
    assert PLANTED_SECRET.encode() not in _all_store_bytes(tc.store_root())

    res = tc.apply_rewind(SESSION, 1, preview=pv)
    assert res.ok, res.errors
    for path, want in originals.items():
        assert _sha(Path(path)) == want


# ── the secrecy floor is about what will be READ, not what the path is called ──────


def test_a_symlinked_dotenv_is_never_captured(ws):
    """A basename list is defeated by a symlink. Measured on ``main`` before the fix:
    ``ws/config.txt -> ws/.env`` returned ``"captured"`` and the dotenv body landed in a
    blob, because the check matched the LITERAL basename while ``read_bytes`` follows the
    link. ``is_sensitive_path`` does not cover it either — it is ``$HOME``-anchored, so a
    workspace ``.env`` is invisible to it.
    """
    # Vacuity: the search below is only meaningful if the secret is really in the file.
    assert PLANTED_SECRET in (ws / ".env").read_text(encoding="utf-8")
    tc.begin_turn(SESSION, cwd=ws)

    (ws / "config.txt").symlink_to(ws / ".env")
    (ws / "deep").mkdir()
    (ws / "deep" / "notes.md").symlink_to(ws / ".env")

    assert tc.capture_pre_edit(SESSION, ws / "config.txt") == "secret"
    assert tc.capture_pre_edit(SESSION, ws / "deep" / "notes.md") == "secret"
    # VACUITY FLOOR: a check that simply refused everything would satisfy both assertions
    # above while breaking the store. An ordinary file must still be captured.
    assert tc.capture_pre_edit(SESSION, ws / "alpha.py") == "captured"

    assert PLANTED_SECRET.encode() not in _all_store_bytes(tc.store_root())


# ── a restore writes inside the session's roots, or it refuses ─────────────────────


def test_a_rewind_refuses_a_target_outside_the_sessions_roots(ws, tmp_path):
    """A rewind is the one path that writes a RECORDED path back to disk, so it verifies
    the destination itself rather than trusting the manifest that named it.

    The manifest is edited to name a traversal path, carrying a VALID blob sha — so the
    only thing that can stop the write is the confinement check, not a missing body.
    """
    assert tc.begin_turn(SESSION, cwd=ws) == 1
    original = _sha(ws / "alpha.py")
    assert tc.capture_pre_edit(SESSION, ws / "alpha.py", cwd=ws) == "captured"

    outside = tmp_path / "outside.txt"
    outside.write_text("untouched by any rewind\n", encoding="utf-8")
    outside_sha = _sha(outside)

    man_path = tc.session_dir(SESSION) / "turn-000001" / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    good = next(f for f in man["files"] if f.get("sha256"))
    traversal = str(ws / ".." / "outside.txt")
    man["files"].append(
        {"path": traversal, "existed": True, "sha256": good["sha256"], "size": good["size"]}
    )
    man_path.write_text(json.dumps(man), encoding="utf-8")

    (ws / "alpha.py").write_text("MANGLED\n", encoding="utf-8")
    assert tc.begin_turn(SESSION, cwd=ws) == 2

    res = tc.apply_rewind(SESSION, 0)

    # The refusal is REPORTED, and it makes the whole rewind not-ok. Reporting `ok=True`
    # here would be the swallowed-write shape: the user is told a restore happened while a
    # file they asked about was never written.
    assert res.refused == [traversal], res.refused
    assert not res.ok
    assert any("outside the session" in e for e in res.errors), res.errors
    assert _sha(outside) == outside_sha, "a rewind must not write outside its roots"
    # VACUITY FLOOR: the guard must refuse the traversal WITHOUT refusing the legitimate
    # restore in the same plan — a guard that blocked everything would also pass the
    # assertion above.
    assert _sha(ws / "alpha.py") == original
