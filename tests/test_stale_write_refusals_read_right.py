"""A refused whole-document save says what it refused in a sentence, and the audit log keeps the
two refusals apart.

#3690 put every whole-document write behind `If-Match` (`personalclaw/stale_write.py`). Two of
its surfaces were wrong in ways a user and an operator could see:

* **The sentence.** The app settings route named its document ``the app {name!r}'s settings``,
  so the 409 read "This write replaces the app 'vector-store-qdrant''s settings, …" and the 428
  "read the app 'vector-store-qdrant''s settings and send its revision". A possessive after a
  quoted name is a doubled quote, wherever it is written.
* **The audit row.** A write that named no revision at all (``428 revision_required``) was
  recorded exactly like one that named a stale revision (``409 stale_write``): outcome
  ``denied``, reason "stale base". Nothing about a 428 is stale — it comes from a writer that does
  not name its base (an old client, a script, an app page that could not send the header) — and
  an operator reading "stale base" was told a concurrent edit happened when none did. Thirteen
  routes audit a refusal; each wrote its own words for it, and none could tell the two apart.

Each route below is driven the way its page drives it, and the rows are read back from the log.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import test_stale_write as _config_harness
import test_stale_write_settings_records as _settings_harness
from aiohttp.test_utils import TestClient, TestServer

# The two harnesses whose routes these tests drive, and their fixtures re-exported by name.
config_file = _config_harness.config_file
app_home = _settings_harness.app_home
chain_home = _settings_harness.chain_home
_APP = _settings_harness._APP
_app_settings_client = _settings_harness._app_settings_client
_read_app_config = _settings_harness._read_app_config
_save_app_config = _settings_harness._save_app_config
_models_client = _settings_harness._models_client
_read_chain = _settings_harness._read_chain
_save_chain = _settings_harness._save_chain

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

_APPS_ROUTE = f"/api/apps/{_APP}/config"
_PROVIDERS_ROUTE = f"/api/providers/{_APP}/config"


def _rows(operation: str) -> list[dict]:
    """The audit rows written for *operation*, oldest first."""
    from personalclaw.sel import sel

    return [r for r in reversed(sel().recent(200)) if r.get("operation") == operation]


# ── 233. The sentence ────────────────────────────────────────────────────────────────────────────


class TestTheAppSettingsRefusalsAreSentences:
    @pytest.mark.asyncio
    async def test_the_stale_refusal_names_the_settings_without_a_doubled_quote(
        self, app_home
    ) -> None:
        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, _APPS_ROUTE)
            assert (
                await _save_app_config(c, _APPS_ROUTE, {**config, "room": "a"}, base)
            ).status == 200
            stale = await _save_app_config(c, _APPS_ROUTE, {**config, "room": "b"}, base)
            assert stale.status == 409
            message = (await stale.json())["error"]["message"]
        assert message.startswith(f"This write replaces the settings of '{_APP}', which changed")
        assert "''s" not in message, message

    @pytest.mark.asyncio
    async def test_the_missing_revision_refusal_names_them_the_same_way(self, app_home) -> None:
        async with _app_settings_client() as c:
            resp = await _save_app_config(c, _APPS_ROUTE, {"room": "x", "greeting": "y"}, None)
            assert resp.status == 428
            message = (await resp.json())["error"]["message"]
        assert f"read the settings of '{_APP}' and send its revision" in message
        assert "''s" not in message, message

    @pytest.mark.asyncio
    async def test_both_routes_that_write_the_file_call_it_one_thing(self, app_home) -> None:
        """Apps → Configure and Settings → Providers save the same file, so the same refusal."""
        async with _app_settings_client() as c:
            said = []
            for route in (_APPS_ROUTE, _PROVIDERS_ROUTE):
                resp = await _save_app_config(c, route, {"room": "x", "greeting": "y"}, None)
                said.append((await resp.json())["error"]["message"])
        assert said[0] == said[1]


def test_no_sentence_puts_a_possessive_after_a_quoted_name() -> None:
    """``{name!r}'s`` renders ``'name''s``. Five sentences in the tree did it — the app settings
    refusal, two model-resolution fixes ("change 'Broken App''s type"), the artifact body conflict
    and a store-conformance clause — so the rule is checked over the tree, not one call site."""
    doubled = re.compile(r"!r\}['’]s\b")
    found = [
        f"{path.relative_to(_SRC)}:{n}"
        for path in sorted(_SRC.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if doubled.search(line)
    ]
    assert not found, f"a possessive after a quoted name reads as a doubled quote: {found}"
    # The scan reads the tree: it sees the f-strings it is looking through.
    assert any("!r}" in p.read_text(encoding="utf-8") for p in _SRC.rglob("*.py"))


# ── 234. The audit row ───────────────────────────────────────────────────────────────────────────


def test_the_two_refusals_have_two_outcomes_in_the_denied_family() -> None:
    from personalclaw.sel import AUDIT_OUTCOME_FAMILIES, _outcome_token_match
    from personalclaw.stale_write import OUTCOME_REVISION_REQUIRED, OUTCOME_STALE_WRITE

    assert OUTCOME_REVISION_REQUIRED != OUTCOME_STALE_WRITE
    denied = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "denied")
    for word in (OUTCOME_REVISION_REQUIRED, OUTCOME_STALE_WRITE):
        # The write was asked for and refused: the Denied pill must find both.
        assert any(_outcome_token_match(v, word) for v in denied["values"]), word


def _assert_told_apart(refused: list[dict]) -> None:
    """The 428's row, then the 409's: two outcomes, and nothing in the 428's calls it stale."""
    assert len(refused) == 2, refused
    no_base, stale = refused
    assert no_base["outcome"] != stale["outcome"], "a 428 and a 409 read the same in the log"
    assert "stale" not in no_base["outcome"], no_base
    assert "stale base" not in f"{no_base['resources']} {no_base['error']}", no_base
    from personalclaw.stale_write import OUTCOME_REVISION_REQUIRED, OUTCOME_STALE_WRITE

    assert (no_base["outcome"], stale["outcome"]) == (
        OUTCOME_REVISION_REQUIRED,
        OUTCOME_STALE_WRITE,
    )


class TestTheAuditLogTellsTheTwoRefusalsApart:
    @pytest.mark.asyncio
    async def test_an_app_settings_save(self, app_home) -> None:
        async with _app_settings_client() as c:
            config, base = await _read_app_config(c, _APPS_ROUTE)
            no_base = await _save_app_config(c, _APPS_ROUTE, {**config, "room": "x"}, None)
            assert no_base.status == 428
            assert (
                await _save_app_config(c, _APPS_ROUTE, {**config, "room": "a"}, base)
            ).status == 200
            assert (
                await _save_app_config(c, _APPS_ROUTE, {**config, "room": "b"}, base)
            ).status == 409
        refused = [r for r in _rows("apps.config") if r["outcome"] != "ok"]
        _assert_told_apart(refused)
        assert all(r["resources"] == _APP for r in refused)
        # The row says why in its outcome; its error field, which a reader treats as "something
        # broke", stays empty — it used to carry "stale base" for both.
        assert all(r.get("error", "") == "" for r in refused), refused

    @pytest.mark.asyncio
    async def test_a_config_list_save(self, config_file) -> None:
        path = "tools.projection_rules"
        async with TestClient(TestServer(_config_harness._app())) as c:
            rules, base = await _config_harness._read(c, path)
            assert (await _config_harness._save(c, path, rules, None)).status == 428
            assert (await _config_harness._save(c, path, [], base)).status == 200
            assert (await _config_harness._save(c, path, rules, base)).status == 409
        refused = [r for r in _rows("config.patch") if r["outcome"].startswith("denied")]
        _assert_told_apart(refused)
        assert all(r["resources"] == path for r in refused), refused

    @pytest.mark.asyncio
    async def test_a_model_chain_save(self, chain_home) -> None:
        async with _models_client() as c:
            chain, base = await _read_chain(c, "chat")
            assert (await _save_chain(c, "chat", ["p3:m3"], None)).status == 428
            assert (await _save_chain(c, "chat", [*chain, "p3:m3"], base)).status == 200
            assert (await _save_chain(c, "chat", chain[::-1], base)).status == 409
        refused = [r for r in _rows("models.active_set") if r["outcome"] != "ok"]
        _assert_told_apart(refused)
        assert all(r["resources"] == "chat" for r in refused), refused


def test_every_audited_refusal_records_which_refusal_it_was() -> None:
    """The thirteen routes that audit a refusal each wrote their own words for it; the rule is
    that each asks the refusal (`refusal_outcome`), so none can say "stale base" of a 428 again.

    Read from the tree: a block that runs only when a refusal was built — ``if <refusal> is not
    None:`` after ``<refusal> = stale_write_refusal(…)`` (or the artifact route's
    ``_source_file_refusal``), or the ``except ArtifactStaleWrite`` arm — and writes an audit row
    must pass ``refusal_outcome(<refusal>)`` to it."""
    writers = {"_sel_log", "_audit", "log_api_access", "log_tool_invocation"}
    builders = {"stale_write_refusal", "_source_file_refusal"}

    def called(node: ast.AST) -> str:
        f = node.func if isinstance(node, ast.Call) else None
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

    def audits(block: list[ast.stmt]) -> list[ast.AST]:
        found = []
        for stmt in block:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and called(node) in writers:
                    found.append(node)
                elif (
                    isinstance(node, ast.Call)
                    and called(node) == "RefusedInConfigTransaction"
                    and any(k.arg == "audit" for k in node.keywords)
                ):
                    found.append(node)
        return found

    blocks = 0
    wrong = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            refusals = {
                t.id
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign) and called(node.value) in builders
                for t in node.targets
                if isinstance(t, ast.Name)
            }
            guarded: list[tuple[str, list[ast.stmt]]] = []
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id in refusals
                    and isinstance(node.test.ops[0], ast.IsNot)
                ):
                    guarded.append((node.test.left.id, node.body))
                if isinstance(node, ast.ExceptHandler) and "ArtifactStaleWrite" in ast.unparse(
                    node.type or ast.Constant(None)
                ):
                    guarded.append(("", node.body))
            for name, body in guarded:
                for call in audits(body):
                    blocks += 1
                    src = ast.unparse(call)
                    if not re.search(rf"refusal_outcome\({name or r'\w+'}\)", src):
                        wrong.append(f"{path.relative_to(_SRC)}:{call.lineno}: {src[:90]}")
    assert not wrong, "an audited refusal names no refusal:\n" + "\n".join(wrong)
    # Vacuity floor: the scan found the audited refusals it is about.
    assert blocks >= 13, blocks
