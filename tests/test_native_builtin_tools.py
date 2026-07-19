"""Tests for the native builtin file/code/shell tools."""

from __future__ import annotations

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nsecond line\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def f():\n    return 42  # marker\n")
    return tmp_path


@pytest.mark.asyncio
async def test_tools_listed(ws):
    names = {t.name for t in await NativeBuiltinToolProvider(ws).list_tools()}
    assert {"read_file", "write_file", "edit_file", "list_dir", "glob", "grep", "bash"} <= names


@pytest.mark.asyncio
async def test_read_write_edit(ws):
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("read_file", {"path": "a.txt"})
    assert r.success and "hello world" in r.output

    w = await p.invoke("write_file", {"path": "new/c.txt", "content": "fresh"})
    assert w.success and (ws / "new" / "c.txt").read_text() == "fresh"

    e = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello", "new_str": "HI"})
    assert e.success and (ws / "a.txt").read_text().startswith("HI world")

    miss = await p.invoke("edit_file", {"path": "a.txt", "old_str": "nope", "new_str": "x"})
    assert not miss.success and "not found" in miss.error


@pytest.mark.asyncio
async def test_write_file_rejects_directory_and_parent_file_cleanly(ws):
    # write_file on a path that's a directory (or whose parent segment is a file)
    # would raise a raw OSError caught by the generic invoke() handler — leaking the
    # absolute server path + a misleading "check the arguments" hint. Both must be
    # reported as clean, relative-path errors with targeted recovery hints.
    p = NativeBuiltinToolProvider(ws)
    d = await p.invoke("write_file", {"path": "sub", "content": "x"})  # sub/ is a dir
    assert not d.success and d.error == "path is a directory, not a file: sub"
    assert d.recovery_hints and "file path" in d.recovery_hints[0]
    # a.txt is a file; writing "through" it must not leak NotADirectoryError
    f = await p.invoke("write_file", {"path": "a.txt/inner.py", "content": "x"})
    assert not f.success and "parent path segment is a file" in f.error
    # the absolute workspace path must never appear in the surfaced error
    assert str(ws) not in d.error and str(ws) not in f.error


@pytest.mark.asyncio
async def test_edit_rejects_ambiguous_match(ws):
    # A non-unique old_str must be rejected (would silently patch the wrong line),
    # with a hint to add context or pass replace_all.
    (ws / "dup.txt").write_text("x = 1\ny = 1\nz = 1\n")
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("edit_file", {"path": "dup.txt", "old_str": "1", "new_str": "2"})
    assert not r.success
    assert "3 times" in r.error and "not unique" in r.error
    assert any("replace_all" in h for h in (r.recovery_hints or []))
    # file untouched
    assert (ws / "dup.txt").read_text() == "x = 1\ny = 1\nz = 1\n"


@pytest.mark.asyncio
async def test_edit_replace_all(ws):
    (ws / "dup.txt").write_text("a = 1\nb = 1\nc = 1\n")
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("edit_file", {"path": "dup.txt", "old_str": "1", "new_str": "9", "replace_all": True})
    assert r.success and "3 replacements" in r.output
    assert (ws / "dup.txt").read_text() == "a = 9\nb = 9\nc = 9\n"


@pytest.mark.asyncio
async def test_edit_unique_match_still_works(ws):
    # The common case — a unique old_str — edits with no replace_all needed.
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "second line", "new_str": "2nd line"})
    assert r.success and "1 replacement" in r.output
    assert "2nd line" in (ws / "a.txt").read_text()


@pytest.mark.asyncio
async def test_read_file_flags_binary(ws):
    # A binary file (NUL bytes) must report "binary", NOT decode to mojibake the model
    # would try to read as source.
    (ws / "blob.bin").write_bytes(b"\x89PNG\r\n\x00\x00data\x00")
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("read_file", {"path": "blob.bin"})
    assert not r.success and "binary" in r.error.lower()
    # a normal text file still reads fine
    r2 = await p.invoke("read_file", {"path": "a.txt"})
    assert r2.success and "hello world" in r2.output
    # binary detection scans the first 8KB (git's heuristic, matches the FE file-read
    # handler) — a NUL only AFTER 8KB reads as text, so the two read paths agree.
    (ws / "late.txt").write_bytes(b"a" * 9000 + b"\x00tail")
    r3 = await p.invoke("read_file", {"path": "late.txt"})
    assert r3.success


@pytest.mark.asyncio
async def test_edit_rejects_empty_old_str(ws):
    # An empty old_str would corrupt the file (replace("", new) inserts between every
    # char) — reject it, file untouched.
    before = (ws / "a.txt").read_text()
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "", "new_str": "X", "replace_all": True})
    assert not r.success and "empty" in r.error
    assert (ws / "a.txt").read_text() == before


@pytest.mark.asyncio
async def test_edit_rejects_noop_identical(ws):
    # old==new reported "Edited" success while changing nothing — the worker would
    # believe it made an edit it didn't. Reject as a no-op.
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("edit_file", {"path": "a.txt", "old_str": "hello world", "new_str": "hello world"})
    assert not r.success and "identical" in r.error


@pytest.mark.asyncio
async def test_list_glob_grep(ws):
    p = NativeBuiltinToolProvider(ws)
    ls = await p.invoke("list_dir", {"path": "."})
    assert ls.success and "a.txt" in ls.output and "sub/" in ls.output

    g = await p.invoke("glob", {"pattern": "**/*.py"})
    assert g.success and "sub/b.py" in g.output

    gr = await p.invoke("grep", {"query": "marker"})
    assert gr.success and "b.py" in gr.output and "42" in gr.output


@pytest.mark.asyncio
async def test_grep_regex_and_skip_dirs(tmp_path):
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n")
    (tmp_path / "other.py").write_text("def helper():\n    pass\n")
    # a vendored dir whose contents must be skipped
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("def handler():\n    pass\n")
    p = NativeBuiltinToolProvider(tmp_path)

    # regex: match either def name
    r = await p.invoke("grep", {"query": r"def (handler|helper)", "regex": True})
    assert r.success and "app.py" in r.output and "other.py" in r.output
    # node_modules is skipped → its dep.py never appears
    assert "node_modules" not in r.output and "dep.py" not in r.output

    # substring default still works + still skips node_modules
    s = await p.invoke("grep", {"query": "handler"})
    assert s.success and "app.py" in s.output and "dep.py" not in s.output

    # a bad regex is a usable error, not a crash
    bad = await p.invoke("grep", {"query": "(unclosed", "regex": True})
    assert not bad.success and "invalid regex" in bad.error


@pytest.mark.asyncio
async def test_grep_signals_max_results_cap(tmp_path):
    # More matches than max_results → the worker must be told the result is capped
    # (no-silent-truncation), so it can narrow or raise the cap rather than assume
    # these are all the matches.
    (tmp_path / "many.py").write_text("\n".join("x = 1  # needle" for _ in range(10)) + "\n")
    p = NativeBuiltinToolProvider(tmp_path)
    r = await p.invoke("grep", {"query": "needle", "max_results": 3})
    assert r.success
    assert "max_results=3" in r.output and "more matches may exist" in r.output


@pytest.mark.asyncio
async def test_glob_signals_truncation(tmp_path):
    for i in range(600):
        (tmp_path / f"f{i}.txt").write_text("x")
    p = NativeBuiltinToolProvider(tmp_path)
    r = await p.invoke("glob", {"pattern": "*.txt"})
    assert r.success and "showing 500 of 600" in r.output


@pytest.mark.asyncio
async def test_path_confinement(ws):
    p = NativeBuiltinToolProvider(ws)
    esc = await p.invoke("read_file", {"path": "../../../etc/passwd"})
    assert not esc.success and "escape" in esc.error.lower()


# NOTE: git / run_tests / diagnostics are NO LONGER tools — the agent runs git,
# the test runner, and the linter via `bash` (shell-first; models are strong at it).
# git push stays blocked at the security/bash deny layer (see test_bash_* below).


@pytest.mark.asyncio
async def test_bash_runs_a_command(ws):
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("bash", {"command": "echo hello-bash"})
    assert r.success and "hello-bash" in r.output


@pytest.mark.asyncio
async def test_bash_timeout_arg_is_capped(ws):
    # The agent-settable timeout is clamped to _BASH_TIMEOUT_MAX so a background turn
    # can't wedge forever; an over-max request is silently capped (not rejected).
    import personalclaw.agents.native.builtin_tools as BT
    seen = {}
    p = BT.NativeBuiltinToolProvider(ws)
    real_wait_for = BT.asyncio.wait_for
    async def _spy(coro, timeout=None):
        seen["timeout"] = timeout
        return await real_wait_for(coro, timeout=timeout)
    BT.asyncio.wait_for = _spy
    try:
        await p.invoke("bash", {"command": "true", "timeout": 99999})
    finally:
        BT.asyncio.wait_for = real_wait_for
    assert seen["timeout"] == BT._BASH_TIMEOUT_MAX  # capped, not 99999


@pytest.mark.asyncio
async def test_bash_blocks_git_push(ws):
    # Dropping the git TOOL must not weaken push protection — the deny layer blocks
    # `git push` issued via bash.
    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("bash", {"command": "git push origin main"})
    assert not r.success and ("denied" in r.error.lower() or "block" in r.error.lower())


@pytest.mark.asyncio
async def test_extra_roots_allow_engine_dir_outside_cwd(ws):
    # A brownfield Code/Goal-Loop worker's cwd is the user workspace, but its engine
    # files (findings/, status.json) live in the project files dir OUTSIDE it. With
    # that dir as an extra root, absolute reads/writes there are allowed; a path in
    # neither cwd nor an extra root is still rejected. `ws` IS tmp_path, so an engine
    # dir genuinely outside cwd must live under ws.parent.
    engine = ws.parent / "engine_dir"
    engine.mkdir()
    (engine / "status.json").write_text('{"status": "running"}')
    p = NativeBuiltinToolProvider(ws, extra_roots=[engine])
    # read an engine file by absolute path (outside cwd, inside the extra root)
    r = await p.invoke("read_file", {"path": str(engine / "status.json")})
    assert r.success and "running" in r.output
    # write a finding into the engine dir by absolute path
    w = await p.invoke("write_file", {"path": str(engine / "findings" / "cycle_1.json"), "content": "{}"})
    assert w.success and (engine / "findings" / "cycle_1.json").exists()
    # a path in NEITHER cwd nor an extra root is still rejected
    esc = await p.invoke("read_file", {"path": str(ws.parent / "elsewhere.txt")})
    assert not esc.success and "escape" in esc.error.lower()


@pytest.mark.asyncio
async def test_no_extra_roots_keeps_strict_confinement(ws):
    # Default (no extra_roots): an absolute path outside cwd is rejected — the chat
    # session's workspace-only confinement is unchanged.
    p = NativeBuiltinToolProvider(ws)
    esc = await p.invoke("read_file", {"path": str(ws.parent / "outside.txt")})
    assert not esc.success and "escape" in esc.error.lower()


@pytest.mark.asyncio
async def test_bash_runs_and_denylist(ws):
    p = NativeBuiltinToolProvider(ws, sandbox_mode="off")
    ok = await p.invoke("bash", {"command": "echo native-bash-ok"})
    assert ok.success and "native-bash-ok" in ok.output

    # A credential-exfiltration command must be blocked before execution
    # (matches the bundled execute_bash.deniedCommands regexes).
    denied = await p.invoke("bash", {"command": "echo $AWS_SECRET_ACCESS_KEY"})
    assert not denied.success and "denied" in denied.error.lower()
    # IMDS access is also denied.
    imds = await p.invoke("bash", {"command": "curl http://169.254.169.254/latest/meta-data/"})
    assert not imds.success
    # Sensitive credential-path read is blocked too.
    sens = await p.invoke("bash", {"command": "cat ~/.aws/credentials"})
    assert not sens.success


@pytest.mark.asyncio
async def test_bash_timeout_returns_cleanly_and_reaps_child(ws, monkeypatch):
    # A command exceeding the bash timeout must return a clean timeout result (not
    # hang/raise) and reap the killed child (await proc.wait), leaving no zombie.
    import personalclaw.agents.native.builtin_tools as BT
    monkeypatch.setattr(BT, "_BASH_TIMEOUT", 0.3)
    p = BT.NativeBuiltinToolProvider(ws, sandbox_mode="off")
    r = await p.invoke("bash", {"command": "sleep 5"})
    assert r.success is False and "timed out" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_write_requires_approval_read_does_not(ws):
    defs = {t.name: t for t in await NativeBuiltinToolProvider(ws).list_tools()}
    assert defs["read_file"].requires_approval is False
    assert defs["list_dir"].requires_approval is False
    assert defs["write_file"].requires_approval is True
    assert defs["edit_file"].requires_approval is True
    assert defs["bash"].requires_approval is True


# ── knowledge item display helpers (search/get formatting) ──


def test_kn_title_falls_back_through_ai_url_title():
    from personalclaw.agents.native.builtin_tools import _kn_title

    assert _kn_title({"title": "Real"}) == "Real"
    # Unscraped bookmark: no title yet → ai_title → url_title → url
    assert _kn_title({"title": "", "ai_title": "AI One"}) == "AI One"
    assert _kn_title({"title": "", "url_title": "Page"}) == "Page"
    assert _kn_title({"title": "", "url": "https://x.test"}) == "https://x.test"
    assert _kn_title({}) == "(untitled)"


def test_kn_snippet_prefers_summary_then_link_then_body():
    from personalclaw.agents.native.builtin_tools import _kn_snippet

    assert _kn_snippet({"summary": "S", "content": "C"}) == "S"
    assert _kn_snippet({"ai_summary": "AS"}) == "AS"
    assert _kn_snippet({"insights": {"summary": "IS"}}) == "IS"
    # A bookmark before enrichment has only its scraped description.
    assert _kn_snippet({"url_description": "desc"}) == "desc"
    assert _kn_snippet({"content": "body text"}) == "body text"
    assert _kn_snippet({}) == ""
    # Newlines collapsed, capped.
    assert "\n" not in _kn_snippet({"content": "a\nb"})
    assert len(_kn_snippet({"content": "x" * 500})) == 160


def test_kn_redact_scrubs_credentials():
    """The knowledge tool path must scrub secrets the same way the HTTP context-
    injection path does — an agent reading the library via tools shouldn't see
    credentials a chat-injection card would have redacted."""
    from personalclaw.agents.native.builtin_tools import _kn_redact

    out = _kn_redact("deploy key AKIAIOSFODNN7EXAMPLE then go")
    assert "AKIAIOSFODNN7EXAMPLE" not in out and "REDACTED" in out
    # Empty/None are safe passthroughs (never None).
    assert _kn_redact("") == "" and _kn_redact(None) == ""
    # Benign text is untouched.
    assert _kn_redact("just a normal note") == "just a normal note"


@pytest.mark.asyncio
async def test_knowledge_get_redacts_content(ws, monkeypatch, tmp_path):
    """knowledge_get returns item body to the model — it must run the redaction guard
    so a stored secret never lands in the agent's context."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    iid = store.create_typed_item(
        item_type="note", title="Creds", content="root password AKIAIOSFODNN7EXAMPLE here",
    )
    store.db.commit()
    # The tool imports get_knowledge_store from personalclaw.knowledge at call time.
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_get", {"id": iid})
    assert r.success
    assert "AKIAIOSFODNN7EXAMPLE" not in r.output and "REDACTED" in r.output


@pytest.mark.asyncio
async def test_knowledge_get_surfaces_document_shape(ws, monkeypatch, tmp_path):
    """knowledge_get tells the agent a file item's shape (pages/sheets/slides) so it
    knows what it's holding without opening the bytes."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    iid = store.create_typed_item(item_type="sheet", title="Budget", content="rows")
    store.update_item(
        iid, file_path="/x/budget.xlsx", mime_type="application/vnd.ms-excel",
        file_metadata={"format": "xlsx", "sheet_count": 3},
    )
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_get", {"id": iid})
    assert r.success and "3 sheets" in r.output


@pytest.mark.asyncio
async def test_knowledge_get_surfaces_gist_language(ws, monkeypatch, tmp_path):
    """A gist's language is shown in the type tag so the agent knows the code's
    language (e.g. '[gist · python]')."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    iid = store.create_typed_item(item_type="gist", title="bsearch", content="def f(): pass",
                                  extra={"gist_language": "python"})
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_get", {"id": iid})
    assert r.success and "gist · python" in r.output


@pytest.mark.asyncio
async def test_knowledge_get_flags_archived_item(ws, monkeypatch, tmp_path):
    """An archived item is hidden from search but still fetchable by id — knowledge_get
    must flag the archived state so the agent doesn't present retired content as current."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    iid = store.create_typed_item(item_type="note", title="Old Plan", content="superseded approach")
    store.update_item(iid, is_archived=1)
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_get", {"id": iid})
    assert r.success and "archived" in r.output.lower()


@pytest.mark.asyncio
async def test_knowledge_get_flags_unreachable_bookmark(ws, monkeypatch, tmp_path):
    """An unreachable bookmark's page couldn't be fetched, so its body is just the URL.
    knowledge_get must tell the agent WHY the content is missing (so it doesn't treat the
    item as empty or not-yet-scraped), and that a retry may recover it."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    iid = store.create_typed_item(item_type="bookmark", title="", url="https://nope.invalid/x")
    store.update_item(iid, processing_status="unreachable",
                      processing_error="bookmark_scrape: Couldn't reach the site.")
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_get", {"id": iid})
    assert r.success and "unreachable" in r.output.lower()
    assert "nope.invalid" in r.output  # the URL is still surfaced


@pytest.mark.asyncio
async def test_knowledge_search_uses_embedder_for_hybrid(ws, monkeypatch, tmp_path):
    """The agent's knowledge_search must run FULL hybrid retrieval (keyword+graph+vector)
    — the same as the gateway's context search — not a keyword-only degrade. It builds the
    process-wide embedder and hands its embed fn to the retriever."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore
    import personalclaw.knowledge.retrieval as retr

    store = KnowledgeStore(str(tmp_path / "k.db"))
    store.create_typed_item(item_type="note", title="Vector DB notes", content="pgvector and HNSW")
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    # A fake available embedder; assert the retriever receives its embed fn (not None).
    class _Emb:
        def is_available(self): return True
        def embed(self, text): return [0.1] * 8
    monkeypatch.setattr(kn, "get_knowledge_embedder", lambda: _Emb(), raising=False)

    seen = {}
    real_init = retr.HybridRetriever.__init__
    def _spy_init(self, store, embedder=None):
        seen["embedder"] = embedder
        real_init(self, store, embedder=embedder)
    monkeypatch.setattr(retr.HybridRetriever, "__init__", _spy_init)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_search", {"query": "vector database"})
    assert r.success
    assert callable(seen.get("embedder")), "knowledge_search must pass the embedder's embed fn for hybrid retrieval"


def test_get_knowledge_embedder_none_safe(monkeypatch):
    """A build failure / disabled-embeddings yields None without raising — callers degrade
    cleanly to keyword-only retrieval."""
    import personalclaw.knowledge as kn

    monkeypatch.setattr(kn, "_embedder", None, raising=False)
    monkeypatch.setattr(kn, "_embedder_spec", object(), raising=False)  # force rebuild
    monkeypatch.setattr("personalclaw.embedding_providers.registry._active_embedding_spec",
                        lambda: ("native", "all-MiniLM-L6-v2"))
    monkeypatch.setattr(
        "personalclaw.knowledge.embedder.create_embedder_from_config",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert kn.get_knowledge_embedder() is None


def test_get_knowledge_embedder_rebuilds_on_model_switch(monkeypatch):
    """The cache is keyed on the active embedding selection: switching embedding models in
    Settings rebuilds the embedder, so agent tools never write vectors of a stale model/
    dimension into the shared store (which would silently corrupt retrieval)."""
    import personalclaw.knowledge as kn

    monkeypatch.setattr(kn, "_embedder", None, raising=False)
    monkeypatch.setattr(kn, "_embedder_spec", False, raising=False)

    spec = {"v": ("native", "model-a")}
    monkeypatch.setattr("personalclaw.embedding_providers.registry._active_embedding_spec",
                        lambda: spec["v"])
    builds: list = []
    def _build(cfg):
        e = object(); builds.append((spec["v"], e)); return e
    monkeypatch.setattr("personalclaw.knowledge.embedder.create_embedder_from_config", _build)

    e1 = kn.get_knowledge_embedder()
    e2 = kn.get_knowledge_embedder()           # same selection → cached, no rebuild
    assert e1 is e2 and len(builds) == 1
    spec["v"] = ("native", "model-b")          # user switches embedding model
    e3 = kn.get_knowledge_embedder()           # selection changed → rebuild
    assert e3 is not e1 and len(builds) == 2


@pytest.mark.asyncio
async def test_knowledge_stats_reports_library_overview(ws, monkeypatch, tmp_path):
    """knowledge_stats gives the agent a gap-detection overview: total item count +
    by-type breakdown of the whole library."""
    import personalclaw.knowledge as kn
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    store.create_typed_item(item_type="note", title="d", content="x")
    store.create_typed_item(item_type="gist", title="a", content="y")
    store.db.commit()
    monkeypatch.setattr(kn, "get_knowledge_store", lambda: store, raising=False)

    p = NativeBuiltinToolProvider(ws)
    r = await p.invoke("knowledge_stats", {})
    assert "2 items" in r.output
    assert "note: 1" in r.output and "gist: 1" in r.output
