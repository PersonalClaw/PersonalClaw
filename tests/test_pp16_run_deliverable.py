"""A run has a DOCUMENT deliverable, and every way of having none is NAMED (PP-16 unit 1).

PP-16's remaining work is five units, and this file rails the first one — the only one that was
agent-closable, in the atom's own words:

    (1) A RUN-LEVEL DOCUMENT DELIVERABLE — the run-side answer to GET /api/loops/{id}/report
    (store.read_deliverable + read_log, kind-named REPORT.md / MONITOR_LOG.md / DESIGN.md).

Measured on `origin/main` before this change, the run side answered none of it. Of the 26 run
routes, **none served a document**: `outbox` lists PUBLISHED artifacts (a different noun — a
document a worker maintains in place has not been published), `workspace` lists changed FILENAMES
with no content, and `outputs/{node_id}` serves a node's structured output, not a markdown file.
`api.uLoopReport` — the loop half — has had an FE consumer since loops shipped; the run half had
neither route nor panel.

Two families of property, and both matter:

* **Reachability + the DRIVEN mapping.** The filename is not a constant in the new module. It comes
  from walking `loop_aliases` FORWARD and asking each kind's own `deliverable_name`, so this file
  asserts agreement between the derived table and the kinds themselves, in BOTH drift directions —
  a kind that renames its document must move the table, and a table naming a template no kind
  resolves to must red.
* **Absent is not zero, in text.** Five different facts read as "no document" if you render a blank
  panel, and only one of them is a worker that has not written yet. Each is asserted by NAME, and
  the empty-string trap is asserted in both directions: an absent document reports `content: None`,
  and a document someone really wrote nothing into reports `""` and `present: True`.

Vacuity floors throughout: every read runs against a real run store in an isolated home with real
files on disk, and each census asserts it found rows before concluding anything about them.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from personalclaw.loop import store as loop_store
from personalclaw.workflows import deliverable as D
from personalclaw.workflows import loop_aliases

#: The three documents PP-16's unit-1 clause names, quoted above. Pinned here rather than read out
#: of the derived table so the atom's list and the code's answer are two independent statements — a
#: kind that stopped declaring its document would otherwise shrink both at once and stay green.
ATOM_NAMED_DOCUMENTS = ("REPORT.md", "MONITOR_LOG.md", "DESIGN.md")

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"


@pytest.fixture()
def run_home(monkeypatch, tmp_path):
    """A real run store in an isolated home, via the env var the loader honors."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _run(workflow: str, *, spec: dict | None = None, workspace: str = "") -> str:
    """Persist a run of ``workflow`` and return its id. Real store, real spec file."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    run = WorkflowRun(id=store.new_run_id(), workflow_name=workflow, status=RunStatus.RUNNING)
    if workspace:
        # The same key `provisioning.stamp_workspace` writes, so the roots resolver is exercised
        # through the record shape the engine really persists rather than a shape invented here.
        run.extra["workspace"] = {"path": workspace, "isolated": True}
    store.save(run)
    store.write_spec(run.id, spec if spec is not None else {"root": {"kind": "action"}})
    return run.id


# ── the mapping is DRIVEN, and it agrees with the kinds in both directions ──


def test_the_derived_table_names_the_three_documents_the_atom_names():
    """REPORT.md / MONITOR_LOG.md / DESIGN.md are all reachable, each from a real template."""
    table = D.template_deliverables()
    assert table, "vacuity floor: the forward walk produced no rows at all"
    derived = {source.name for source in table.values() if source.name}
    assert derived, "vacuity floor: every template resolved to an empty document name"
    missing = [name for name in ATOM_NAMED_DOCUMENTS if name not in derived]
    assert not missing, f"unit 1's own clause names documents no template produces: {missing}"


def test_each_named_document_belongs_to_the_template_its_kind_resolves_to():
    """The three names land on the right templates — the mapping, not just the vocabulary.

    Spelled out rather than derived, so a kind swapping two documents reds here. The templates come
    from `loop_aliases.resolve_kind`, not from literals, so a RENAMED template moves this test's
    expectation with it while a swapped DOCUMENT still fails.
    """
    table = D.template_deliverables()
    expected = {
        loop_aliases.resolve_kind("goal", variant="open_ended"): "REPORT.md",
        loop_aliases.resolve_kind("goal", variant="monitor"): "MONITOR_LOG.md",
        loop_aliases.resolve_kind("design"): "DESIGN.md",
    }
    assert all(expected), "vacuity floor: an alias resolved to no template"
    for template, document in expected.items():
        assert table[template].name == document, f"{template} should produce {document}"


def test_every_row_is_a_kind_the_alias_table_declares_and_every_kind_has_a_row():
    """Both drift directions. A kind added to the alias table appears here with no edit; a row
    naming a kind the table dropped reds instead of rotting."""
    table = D.template_deliverables()
    kinds_in_table = {source.kind for source in table.values()}
    declared = set(loop_aliases.KIND_TO_TEMPLATE)
    assert (
        kinds_in_table == declared
    ), f"rows cover {sorted(kinds_in_table)}; the alias table declares {sorted(declared)}"


def test_the_name_is_asked_of_the_kind_rather_than_read_from_a_constant_here():
    """The derived name for every template equals what that kind's own strategy says.

    This is the "driven, not asserted from a constant" property, checked against the strategies
    directly. A constant table in `deliverable.py` would pass every test above and fail this one the
    moment a kind renamed its document.
    """
    from personalclaw.loop import kinds as kinds_mod
    from personalclaw.loop.loop import Loop

    kinds_mod.ensure_loaded()
    table = D.template_deliverables()
    assert table, "vacuity floor: nothing to compare"
    for template, source in table.items():
        strategy = kinds_mod.get_or_none(source.kind)
        assert strategy is not None, f"{template}: no strategy for kind {source.kind}"
        loop = Loop(
            id="",
            name="",
            kind=source.kind,
            task="",
            kind_config={"goal_type": source.variant.replace("-", "_")} if source.variant else {},
        )
        assert source.name == (
            strategy.deliverable_name(loop) or ""
        ), f"{template}: the table says {source.name!r} and the kind says something else"


def test_the_source_file_hard_codes_none_of_the_document_names():
    """The module that RESOLVES the name must not contain one.

    A literal `REPORT.md` in `deliverable.py` is exactly the second source of truth this unit exists
    to avoid: it would keep working after a kind renamed its document, and the surface would show a
    file the worker no longer writes. Docstrings are stripped first — the module's prose names the
    documents to explain itself, and explaining is not deciding.
    """
    import ast

    source = (_SRC / "workflows" / "deliverable.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Docstrings are string constants too, so exclude them BY NODE IDENTITY — `ast.get_docstring`
    # returns a cleaned (dedented) string that never equals the raw literal, so comparing text
    # excludes nothing and the test would fire on its own explanatory prose.
    doc_nodes: set[int] = set()
    for holder in ast.walk(tree):
        if not isinstance(
            holder, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = (holder.body or [None])[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            doc_nodes.add(id(first.value))
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_nodes
        and any(name in node.value for name in (*ATOM_NAMED_DOCUMENTS, "RESEARCH.md"))
    ]
    assert doc_nodes, "vacuity floor: no docstrings found, so nothing was excluded"
    assert not offenders, f"deliverable.py spells a document name itself: {offenders}"


def test_the_log_name_is_the_loop_stores_own_declaration():
    """One on-disk convention, one string — the run side imports it rather than re-spelling it."""
    assert loop_store.LOG_NAME == "FINDINGS.md"
    assert loop_store.LOG_NAME in loop_store.DELIVERABLE_FALLBACKS
    for name in ATOM_NAMED_DOCUMENTS:
        assert (
            name in loop_store.DELIVERABLE_FALLBACKS
        ), f"the loop side's fallback list dropped {name}"


def test_the_loop_side_still_reads_the_log_by_that_name(run_home, monkeypatch):
    """The constant is load-bearing on the LOOP side too, driven rather than assumed.

    Without this, `LOG_NAME` could drift from what `read_log` opens and only the run side would
    notice — which is the two-spellings bug in a new costume.
    """
    from personalclaw.loop import files as loop_files

    loop_id = "aa11bb22"
    directory = loop_files.loop_dir(loop_id)
    assert directory is not None, "vacuity floor: no loop dir to write into"
    (directory / loop_store.LOG_NAME).write_text("cycle 1: looked around\n")
    assert "looked around" in loop_store.read_log(loop_id)


# ── absent is NAMED, five ways ──


def test_a_run_with_no_document_reports_not_written_and_no_content(run_home):
    """The common case, and the one clause 4 of this unit is about.

    `content is None`, not `""`: an empty string is a document someone wrote nothing into, and the
    panel would render an empty page for it rather than saying why there is none.
    """
    from personalclaw.workflows import service

    payload = service.run_deliverable(_run("goal-pursuit-open-ended"))
    assert payload["ok"] is True
    report = payload["report"]
    assert report["name"] == "REPORT.md", "the name is known even when the file is not there"
    assert report["present"] is False
    assert report["content"] is None
    assert report["absent_reason"] == D.NOT_WRITTEN
    assert report["absent_reason"] in D.ABSENT_REASONS


def test_a_kind_that_produces_no_document_says_so_rather_than_reporting_not_written(run_home):
    """A verifiable goal is FINISHED with no document; a young open-ended goal is WAITING for one.

    Both render as "no content", and collapsing them is the absent-vs-declared-zero mistake in text
    form: one tells the user to check back, the other tells them nothing is coming and that is
    correct.
    """
    from personalclaw.workflows import service

    for template in (
        loop_aliases.resolve_kind("goal", variant="verifiable"),
        loop_aliases.resolve_kind("code"),
        loop_aliases.resolve_kind("general"),
    ):
        report = service.run_deliverable(_run(template))["report"]
        assert report["name"] is None, f"{template} should name no document"
        assert report["absent_reason"] == D.KIND_HAS_NO_DOCUMENT, template


def test_a_template_no_kind_resolves_to_is_unknown_rather_than_documentless(run_home):
    """26 of the 33 bundled templates are not loop kinds. Their document name is UNKNOWN.

    Reporting `kind_has_no_document` for them would be a claim about a template nothing here has
    read — and it is exactly the claim that would make a bespoke template's real document invisible.
    """
    from personalclaw.workflows import service

    payload = service.run_deliverable(_run("morning-triage"))
    assert payload["report"]["absent_reason"] == D.TEMPLATE_UNKNOWN
    assert payload["derivation"]["declared_by"] is None, "nothing declared it, so nothing is named"


def test_a_run_with_no_directory_at_all_reports_no_root(run_home):
    """A run whose dir was swept reads as `no_root`, not as a worker that has not written."""
    import shutil

    from personalclaw.workflows import service, store

    run_id = _run("goal-pursuit-open-ended")
    shutil.rmtree(store.run_dir(run_id))
    payload = service.run_deliverable(run_id)
    assert payload["report"]["absent_reason"] == D.NO_ROOT
    assert all(root["exists"] is False for root in payload["roots"]), "the roots say so too"


def test_an_unreadable_document_is_reported_rather_than_blanked(run_home):
    """A file that is there and cannot be read must not read as "not written yet"."""
    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(run_home), True)])
    target = run_home / "REPORT.md"
    target.write_text("secret")
    target.chmod(0o000)
    try:
        document = D.read_document(docs, "REPORT.md")
    finally:
        target.chmod(0o600)
    if document.present:
        pytest.skip("this filesystem ignores mode 000 for the owner — nothing to assert")
    assert document.absent_reason == D.UNREADABLE
    assert document.found_in == D.ROOT_RUN_DIR, "we know WHERE the unreadable file is"


def test_every_absent_reason_the_module_can_report_is_in_the_declared_vocabulary():
    """A reason string with no member is an unrenderable value on the wire."""
    named = {
        D.TEMPLATE_UNKNOWN,
        D.KIND_HAS_NO_DOCUMENT,
        D.NOT_WRITTEN,
        D.NO_ROOT,
        D.UNREADABLE,
    }
    assert named == set(D.ABSENT_REASONS)
    assert len(named) == 5, "five distinct facts, not four spellings of one"


# ── present, and honestly present ──


def test_a_written_document_is_served_with_its_size_and_where_it_was_found(run_home):
    """The happy path, end to end through the service."""
    from personalclaw.workflows import service, store

    run_id = _run("design-project")
    body = "# Design\n\nTokens settled.\n"
    (store.run_dir(run_id) / "DESIGN.md").write_text(body)
    report = service.run_deliverable(run_id)["report"]
    assert report["present"] is True
    assert report["name"] == "DESIGN.md"
    assert report["content"] == body
    assert report["bytes"] == len(body.encode())
    assert report["found_in"] == D.ROOT_RUN_DIR
    assert report["absent_reason"] is None, "present and absent_reason are mutually exclusive"


def test_a_document_someone_wrote_nothing_into_is_present_with_an_empty_body(run_home):
    """The other direction of the empty-string trap.

    An existing empty file is a REAL observation — the worker touched it and wrote nothing — so it
    reports `present: True` and `content: ""`. A read path that folded this into the absent branch
    would pass every absence test above and quietly lose the distinction.
    """
    from personalclaw.workflows import service, store

    run_id = _run("goal-pursuit-open-ended")
    (store.run_dir(run_id) / "REPORT.md").write_text("")
    report = service.run_deliverable(run_id)["report"]
    assert report["present"] is True
    assert report["content"] == ""
    assert report["bytes"] == 0


def test_the_log_slot_is_served_beside_the_deliverable(run_home):
    """`report` + `log`, the same two slots `GET /api/loops/{id}/report` returns."""
    from personalclaw.workflows import service, store

    run_id = _run("goal-pursuit-open-ended")
    (store.run_dir(run_id) / loop_store.LOG_NAME).write_text("cycle 1: started\n")
    payload = service.run_deliverable(run_id)
    assert payload["log"]["present"] is True
    assert payload["log"]["name"] == loop_store.LOG_NAME
    assert payload["report"]["present"] is False, "the two slots are independent"


def test_the_workspace_wins_over_the_run_dir(run_home):
    """Workspace-FIRST, mirroring `loop/watchdog._deliverable_file`.

    The loop brief directs a worker to write its deliverable into the BOUND workspace when there is
    one, and only an unbound loop writes into its own dir. A run-dir-first resolver would serve a
    stale copy for every isolated run — which on a reading surface is worse than serving nothing.
    """
    from personalclaw.workflows import service, store

    workspace = run_home / "ws"
    workspace.mkdir()
    run_id = _run("goal-pursuit-open-ended", workspace=str(workspace))
    (store.run_dir(run_id) / "REPORT.md").write_text("the run dir copy")
    (workspace / "REPORT.md").write_text("the workspace copy")
    report = service.run_deliverable(run_id)["report"]
    assert report["found_in"] == D.ROOT_WORKSPACE
    assert report["content"] == "the workspace copy"


def test_a_credential_in_a_worker_authored_document_is_redacted(run_home):
    """A document is prose about whatever the worker was working on. That is where a token lands.

    The loop side redacts its copy (`loop/files._redact_str`); the run side must too, or PP-16's
    retirement would move the same document onto a surface that leaks it.
    """
    from personalclaw.workflows import service, store

    run_id = _run("goal-pursuit-open-ended")
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
    (store.run_dir(run_id) / "REPORT.md").write_text(f"the key is {secret} and it works\n")
    content = service.run_deliverable(run_id)["report"]["content"]
    assert secret not in content, "a credential reached the payload"
    assert "it works" in content, "vacuity floor: the whole document was dropped, not redacted"


def test_a_traversing_name_cannot_escape_the_root(run_home):
    """The confinement floor.

    The name comes off a strategy declaration today, so this is a floor rather than a fix — but the
    loop side already lets a user name the file (`kind_config['primary_deliverable']`), and a
    traversal that becomes reachable later is a traversal.
    """
    outside = run_home / "OUTSIDE.md"
    outside.write_text("not yours")
    root = run_home / "root"
    root.mkdir()
    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(root), True)])
    assert D.read_document(docs, "../OUTSIDE.md").present is False


def test_an_oversized_document_is_truncated_and_says_so(run_home):
    """Truncation is REPORTED, never silent — a document that stops mid-sentence is
    indistinguishable from a worker that stopped writing.

    Line-broken content on purpose. The first draft of this test wrote one unbroken 512 KB run and
    TIMED OUT at 120s in the full suite, which is how the quadratic redaction cost behind
    `MAX_TOKEN_CHARS` was found — see `test_a_blob_is_clipped_before_redaction_ever_sees_it`. Using
    prose here keeps this test about the BYTE ceiling and leaves the token ceiling to its own rail.
    """
    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(run_home), True)])
    body = ("the worker kept writing and writing and writing. " * 20 + "\n") * 600
    assert len(body) > D.MAX_DOC_BYTES, "vacuity floor: the fixture is under the ceiling"
    (run_home / "REPORT.md").write_text(body)
    document = D.read_document(docs, "REPORT.md")
    assert document.truncated is True
    assert document.bytes == len(body), "the REAL size, not the served slice"
    assert len(document.content or "") == D.MAX_DOC_BYTES
    assert document.clipped_blobs == 0, "prose carries no blob-shaped run"


# ── the cost of reading a document, which a byte ceiling alone does not bound ──


def test_a_blob_is_clipped_before_redaction_ever_sees_it(run_home):
    """A byte ceiling does NOT bound this read's cost, and that is a measured fact.

    `security.redact_credentials` is roughly quadratic in unbroken-TOKEN length and flat in document
    length: at ~512 KB, one unbroken token cost 111 SECONDS on this machine while the same bytes as
    ordinary prose cost 0.038s. A worker appending one base64 blob to its REPORT.md would therefore
    stall this GET for two minutes — an availability bug, not a slow page. So a run of more than
    `MAX_TOKEN_CHARS` non-space characters is replaced by a marker naming its length before the
    redactor runs, and the replacement is REPORTED.

    Found by this file's own first draft timing out in the full suite, which is worth recording:
    the ceiling was not designed in, it was measured in.
    """
    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(run_home), True)])
    blob = "A" * (D.MAX_TOKEN_CHARS * 4)
    (run_home / "REPORT.md").write_text(f"before\n{blob}\nafter\n")
    document = D.read_document(docs, "REPORT.md")
    assert document.clipped_blobs == 1
    content = document.content or ""
    assert blob not in content
    # Replaced WHOLE, not truncated: half of a credential is still half of a credential.
    assert "A" * (D.MAX_TOKEN_CHARS + 1) not in content
    assert f"{len(blob)}-character run with no whitespace" in content, "the clip names itself"
    assert "before" in content and "after" in content, "vacuity floor: the prose survived"


def test_prose_is_never_clipped(run_home):
    """The other direction. A ceiling that fires on real writing would be worse than none.

    A model routinely emits a whole paragraph on one line, so the ceiling is deliberately on the
    unbroken RUN rather than on the line: this paragraph is far longer than `MAX_TOKEN_CHARS` and is
    served intact.
    """
    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(run_home), True)])
    paragraph = "the finding is that latency comes from weight load rather than inference. " * 40
    assert len(paragraph) > D.MAX_TOKEN_CHARS * 4, "vacuity floor: the fixture is too short"
    (run_home / "REPORT.md").write_text(paragraph)
    document = D.read_document(docs, "REPORT.md")
    assert document.clipped_blobs == 0
    assert document.content == paragraph


def test_the_worst_case_document_is_served_in_bounded_time(run_home):
    """A full-ceiling document made entirely of at-the-limit tokens still answers quickly.

    A coarse floor rather than a benchmark: the unmitigated cost of this shape was 111s and the
    suite's own timeout is 120s, so a generous bound here is what distinguishes "bounded" from
    "accidentally fast". If this ever reds, `MAX_TOKEN_CHARS` is the knob — not this number.
    """
    import time

    docs = D.Roots([D.Root(D.ROOT_RUN_DIR, str(run_home), True)])
    token = "B" * D.MAX_TOKEN_CHARS
    body = " ".join([token] * (D.MAX_DOC_BYTES // (D.MAX_TOKEN_CHARS + 1)))
    (run_home / "REPORT.md").write_text(body)
    started = time.perf_counter()
    document = D.read_document(docs, "REPORT.md")
    elapsed = time.perf_counter() - started
    assert document.present is True
    assert document.clipped_blobs == 0, "at the limit is not past it"
    assert elapsed < 30.0, f"reading a worst-case document took {elapsed:.1f}s"


# ── the template gap this surface must not misreport as a slow worker ──


def test_instructed_is_false_when_the_runs_own_spec_never_names_the_document(run_home):
    """The measured finding: no bundled template names its kind's document.

    So "not written yet" is usually the wrong sentence — nothing ever asked. The panel reads this
    field to say so, and a payload that omitted it would leave the FE guessing.
    """
    from personalclaw.workflows import service

    payload = service.run_deliverable(_run("goal-pursuit-open-ended"))
    assert payload["instructed"] is False


def test_instructed_is_true_when_the_spec_does_name_it(run_home):
    """Both directions — a field that is always False proves nothing about the template."""
    from personalclaw.workflows import service

    spec = {"root": {"kind": "action", "prompt": "Maintain REPORT.md as you go."}}
    payload = service.run_deliverable(_run("goal-pursuit-open-ended", spec=spec))
    assert payload["instructed"] is True


def test_instructed_is_null_when_there_is_no_name_to_look_for(run_home):
    """ "We did not check" and "we checked and it is not there" are different facts."""
    from personalclaw.workflows import service

    payload = service.run_deliverable(_run(loop_aliases.resolve_kind("code")))
    assert payload["instructed"] is None


def test_no_bundled_template_the_five_kinds_resolve_to_names_its_own_document():
    """The measurement behind `instructed`, re-taken every run so the claim cannot rot.

    If a template ever starts naming its document, this reds and the prose above (and the panel's
    copy) must be re-done rather than left claiming a gap that closed.
    """
    bundled = _SRC / "workflows" / "bundled"
    table = D.template_deliverables()
    checked = 0
    naming: list[str] = []
    for template, source in table.items():
        if not source.name:
            continue
        path = bundled / template / "workflow.json"
        if not path.is_file():
            continue
        checked += 1
        if source.name in path.read_text(encoding="utf-8"):
            naming.append(template)
    assert checked >= 3, f"vacuity floor: only {checked} bundled templates were read"
    assert not naming, f"these templates now name their document — re-do the claim: {naming}"


# ── the route, resolved rather than grepped ──


@pytest.mark.asyncio
async def test_the_route_resolves_at_runtime_to_this_handler():
    """Built app, real resolution — not a substring in a source file.

    `introspection.py` shipped fully written and consumed by NOTHING, so a projection with no
    reachable route is a known failure mode in this package. Resolution (rather than a canonical
    path census) also proves the dynamic `{run_id}` resource MATCHES a concrete path and reaches the
    right handler — a route registered after a greedier pattern would pass a census and 404 in
    production.
    """
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.workflows.handlers import api_run_deliverable, register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    canonical = {
        getattr(r.resource, "canonical", "") for r in app.router.routes() if r.method == "GET"
    }
    assert "/api/workflows/runs/{run_id}/deliverable" in canonical

    match = await app.router.resolve(
        make_mocked_request("GET", "/api/workflows/runs/abcd1234/deliverable", app=app)
    )
    assert match.handler is api_run_deliverable
    assert dict(match) == {"run_id": "abcd1234"}


@pytest.mark.asyncio
async def test_the_route_answers_a_real_read_and_404s_an_unknown_run(run_home):
    """Through the HTTP layer, so the failure code really maps to a status."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.workflows import store
    from personalclaw.workflows.handlers import register_workflow_routes

    run_id = _run("goal-pursuit-open-ended")
    (store.run_dir(run_id) / "REPORT.md").write_text("# It worked\n")
    app = web.Application()
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        ok = await client.get(f"/api/workflows/runs/{run_id}/deliverable")
        assert ok.status == 200
        body = await ok.json()
        assert body["report"]["content"] == "# It worked\n"

        missing = await client.get("/api/workflows/runs/deadbeef/deliverable")
        assert missing.status == 404, "a deleted run must be distinguishable from a young one"


def test_the_route_is_documented_in_the_offline_reference():
    """An agent reads `reference/routes.md`; an undocumented route is unreachable to it."""
    import personalclaw

    routes_md = (pathlib.Path(personalclaw.__file__).parent / "reference" / "routes.md").read_text(
        encoding="utf-8"
    )
    assert "/api/workflows/runs/{run_id}/deliverable" in routes_md


# ── what this payload deliberately does NOT carry ──


def test_the_payload_carries_no_money_field(run_home):
    """Issue #2566, held at the surface rather than fixed underneath.

    `ledger.reader.run_totals` reports `cost_usd 0.0` and `tokens 0` for a LOOP, because
    `LoopJournal.cycle` writes no money keys at all — loop money lives in `usage/turns.jsonl` via
    `loop_spend` — and `introspection.RunStats.cost_usd` has the same shape. Three packages consume
    that contract, so changing it needs an owner ruling and is NOT this unit's work. What IS this
    unit's work is refusing to put a figure on this payload that would read "$0.00" for a
    loop-backed run's document. Absent, not zero: the field is not here, and this test is what keeps
    a later session from adding one without meeting the finding.
    """
    from personalclaw.workflows import service

    payload = service.run_deliverable(_run("goal-pursuit-open-ended"))
    flat = json.dumps(payload)
    for banned in ("cost_usd", "tokens", "spend", "price"):
        assert (
            banned not in flat
        ), f"{banned} reached the deliverable payload — see issue #2566 before adding money here"


def test_the_payload_carries_no_roi_axis_either(run_home):
    """The fidelity gap the atom records as a design question rather than a bug.

    The run controller ledgers only `verdict`/`status`/`evidence` and keeps the rich `JudgeVerdict`
    in the node OUTPUT, so `marginal_value` and `quality_score` — the axes the loop's `RoiRail`
    plots — are unreachable from a run's ledger. PR #2565's rails report them absent and NAME them,
    which is the correct scope. This surface's correct scope is to carry neither: a document read is
    not where an unreachable score should first appear as a blank.
    """
    from personalclaw.workflows import service

    flat = json.dumps(service.run_deliverable(_run("design-project")))
    for banned in ("marginal_value", "quality_score"):
        assert banned not in flat
