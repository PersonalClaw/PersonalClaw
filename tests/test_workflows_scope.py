"""Write-scope enforcement (WF2-R19) and secrets hygiene (WF2-R14).

The failure class write-scope guards is not hypothetical: this platform's
destructive-test-isolation incident deleted the user's real bound model. The load-bearing
claims here:

* a write inside the declared scope is clean; one outside is a violation;
* **a symlink escape is caught** — the check resolves links, because a link inside the
  workspace pointing at `~/.ssh` is exactly the escape it exists to find;
* `..` cannot smuggle a path in, since both sides normalize before comparison;
* `warn` records and preserves the outcome; `reject` flips the node to `scope_violation`;
* a truncated snapshot reports `incomplete` — never a false clean pass;
* secrets: values strip to `_has*` flags on read, re-inject BY NODE ID on write (so a
  moved node keeps its credentials), and `{{secret:KEY}}` survives a round-trip intact.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import scope, secrets, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


# ── path matching ────────────────────────────────────────────────────────────


class TestInScope:
    def test_a_directory_covers_its_whole_subtree(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        (ws / "deep" / "nested").mkdir(parents=True)
        target = ws / "deep" / "nested" / "f.txt"
        target.write_text("x")
        assert scope.in_scope(str(target), [str(ws)])

    def test_a_sibling_directory_is_out_of_scope(self, tmp_path) -> None:
        (tmp_path / "ws").mkdir()
        other = tmp_path / "other"
        other.mkdir()
        f = other / "f.txt"
        f.write_text("x")
        assert not scope.in_scope(str(f), [str(tmp_path / "ws")])

    def test_dotdot_cannot_smuggle_a_path_in(self, tmp_path) -> None:
        """Both sides normalize, so `ws/../secrets` resolves out of scope."""
        ws = tmp_path / "ws"
        ws.mkdir()
        escaped = tmp_path / "secrets.env"
        escaped.write_text("x")
        assert not scope.in_scope(str(ws / ".." / "secrets.env"), [str(ws)])

    def test_a_symlink_escape_is_caught(self, tmp_path) -> None:
        """The whole reason links are resolved: a link inside the workspace pointing
        outside it must not read as in-scope."""
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "id_rsa"
        secret.write_text("KEY")
        link = ws / "innocent.txt"
        link.symlink_to(secret)
        assert not scope.in_scope(str(link), [str(ws)])

    def test_a_glob_pattern_matches(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        (ws / "out").mkdir(parents=True)
        f = ws / "out" / "report.md"
        f.write_text("x")
        assert scope.in_scope(str(f), [str(ws / "out" / "*.md")])
        assert scope.in_scope(str(f), [str(ws / "**")])

    def test_an_empty_allowlist_permits_nothing(self, tmp_path) -> None:
        """An empty declaration is 'the workspace only', resolved by the caller — it is
        never 'allow everything'."""
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert not scope.in_scope(str(f), [])


class TestAllowedWritePaths:
    def test_workspace_is_always_included(self) -> None:
        assert scope.allowed_write_paths({}, "/tmp/ws") == ["/tmp/ws"]

    def test_declared_paths_come_first_then_workspace(self) -> None:
        got = scope.allowed_write_paths({"allowed_write_paths": ["/a", "/b"]}, "/tmp/ws")
        assert got == ["/a", "/b", "/tmp/ws"]

    def test_a_bare_string_is_accepted(self) -> None:
        assert scope.allowed_write_paths({"allowed_write_paths": "/a"}, "") == ["/a"]

    def test_scope_checking_is_opt_in(self) -> None:
        """Snapshotting costs a tree walk; a fan-out of fast transforms must not each pay
        for one."""
        assert not scope.enforces_scope({})
        assert scope.enforces_scope({"allowed_write_paths": ["/a"]})
        assert scope.enforces_scope({"write_scope_mode": "reject"})

    def test_mode_defaults_to_warn_and_rejects_garbage(self) -> None:
        assert scope.scope_mode({}) == scope.ScopeMode.WARN
        assert scope.scope_mode({"write_scope_mode": "reject"}) == scope.ScopeMode.REJECT
        assert scope.scope_mode({"write_scope_mode": "nonsense"}) == scope.ScopeMode.WARN


# ── snapshot + diff ──────────────────────────────────────────────────────────


class TestSnapshotDiff:
    def test_a_created_file_inside_scope_is_clean(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        before = scope.snapshot([str(ws)])
        (ws / "new.txt").write_text("hello")
        report = scope.diff(before, scope.snapshot([str(ws)]), [str(ws)])
        assert len(report.created) == 1
        assert report.clean

    def test_a_created_file_outside_scope_violates(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        roots = [str(tmp_path)]  # watch wider than we allow
        before = scope.snapshot(roots)
        (tmp_path / "escaped.txt").write_text("oops")
        report = scope.diff(before, scope.snapshot(roots), [str(ws)])
        assert not report.clean
        assert report.violations and report.violations[0].endswith("escaped.txt")

    def test_a_deletion_is_detected(self, tmp_path) -> None:
        """Deletion matters as much as creation — the incident this guards deleted a
        file, it did not create one."""
        victim = tmp_path / "important.txt"
        victim.write_text("x")
        ws = tmp_path / "ws"
        ws.mkdir()
        before = scope.snapshot([str(tmp_path)])
        victim.unlink()
        report = scope.diff(before, scope.snapshot([str(tmp_path)]), [str(ws)])
        assert report.deleted and report.violations

    def test_a_modification_is_detected_by_size_or_mtime(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("short")
        before = scope.snapshot([str(tmp_path)])
        f.write_text("a much longer body than before")
        report = scope.diff(before, scope.snapshot([str(tmp_path)]), [str(tmp_path)])
        assert report.modified and report.clean

    def test_noise_directories_are_pruned(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        (ws / "node_modules" / "pkg").mkdir(parents=True)
        (ws / "node_modules" / "pkg" / "index.js").write_text("x")
        (ws / "real.txt").write_text("x")
        snap = scope.snapshot([str(ws)])
        assert any(p.endswith("real.txt") for p in snap.entries)
        assert not any("node_modules" in p for p in snap.entries)

    def test_a_truncated_snapshot_reports_incomplete(self, tmp_path, monkeypatch) -> None:
        """A capped walk must degrade to 'could not verify', never to a false pass."""
        monkeypatch.setattr(scope, "MAX_SNAPSHOT_ENTRIES", 2)
        ws = tmp_path / "ws"
        ws.mkdir()
        for i in range(5):
            (ws / f"f{i}.txt").write_text("x")
        snap = scope.snapshot([str(ws)])
        assert snap.truncated
        report = scope.diff(snap, snap, [str(ws)])
        assert report.incomplete

    def test_a_missing_root_is_skipped_not_fatal(self, tmp_path) -> None:
        snap = scope.snapshot([str(tmp_path / "does-not-exist")])
        assert len(snap) == 0


# ── controller integration ───────────────────────────────────────────────────


def _stage_spec(config: dict) -> dict:
    return {
        "name": "scoped",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "w", "config": config}],
        },
    }


def _writer_provider(target: str):
    """An action provider that writes a file — the escape under test."""

    class P:
        async def execute(self, cfg, ctx, timeout=30):
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("written")

            class R:
                success = True
                stdout = '{"ok": true}'
                outcome = ""
                error = ""
                exit_code = 0
                stderr = ""
                agent_error = None

            return R()

    return lambda name: P()


class TestControllerScopeEnforcement:
    async def test_warn_mode_records_the_violation_but_keeps_the_outcome(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        # The escape lands OUTSIDE the workspace. `cwd` is the workspace, and the
        # workspace is always in-scope — so the target must sit above it to be a violation.
        escape = tmp_path / "escaped.txt"
        spec = _stage_spec(
            {
                "provider": "writer",
                "allowed_write_paths": [str(ws)],
                "write_scope_mode": "warn",
            }
        )
        run = store.create(WorkflowRun(id="", workflow_name="scoped"))
        store.write_spec(run.id, spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_writer_provider(str(escape)), cwd=str(ws)),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        inst = store.read_state(run.id)["root.children[0]"]
        assert inst.state == InstanceState.DONE  # warn preserves the outcome
        from personalclaw.workflows.journal import STEP_SCOPE, ledger

        scope_events = [e for e in ledger(run.id) if e.get("kind") == STEP_SCOPE]
        assert len(scope_events) == 1
        assert any("escaped.txt" in v for v in scope_events[0]["violations"])

    async def test_reject_mode_flips_the_node_to_scope_violation(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        escape = tmp_path / "escaped.txt"
        spec = _stage_spec(
            {
                "provider": "writer",
                "allowed_write_paths": [str(ws)],
                "write_scope_mode": "reject",
            }
        )
        run = store.create(WorkflowRun(id="", workflow_name="scoped"))
        store.write_spec(run.id, spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_writer_provider(str(escape)), cwd=str(ws)),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        inst = store.read_state(run.id)["root.children[0]"]
        assert inst.state == InstanceState.SCOPE_VIOLATION
        assert inst.failure.terminal_reason == "scope_violation"

    async def test_an_in_scope_write_passes_clean(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        inside = ws / "output.txt"
        spec = _stage_spec(
            {
                "provider": "writer",
                "allowed_write_paths": [str(ws)],
                "write_scope_mode": "reject",
            }
        )
        run = store.create(WorkflowRun(id="", workflow_name="scoped"))
        store.write_spec(run.id, spec)
        c = RunController(
            run,
            spec,
            services=EngineServices(get_provider=_writer_provider(str(inside)), cwd=str(tmp_path)),
        )
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        from personalclaw.workflows.journal import STEP_SCOPE, ledger

        assert [e for e in ledger(run.id) if e.get("kind") == STEP_SCOPE] == []

    async def test_a_node_without_a_declaration_never_snapshots(self, tmp_path) -> None:
        """Opt-in: no declaration means no tree walk at all."""
        calls = {"n": 0}
        real = scope.snapshot

        def counting(roots):
            calls["n"] += 1
            return real(roots)

        import personalclaw.workflows.controller as ctrl

        original = ctrl.scope_snapshot
        ctrl.scope_snapshot = counting
        try:
            spec = _stage_spec({"provider": "writer"})
            run = store.create(WorkflowRun(id="", workflow_name="scoped"))
            store.write_spec(run.id, spec)
            c = RunController(
                run,
                spec,
                services=EngineServices(
                    get_provider=_writer_provider(str(tmp_path / "x.txt")),
                    cwd=str(tmp_path),
                ),
            )
            assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        finally:
            ctrl.scope_snapshot = original
        assert calls["n"] == 0


# ── secrets hygiene (WF2-R14) ────────────────────────────────────────────────


class TestSecretDetection:
    def test_secret_named_keys_are_recognized(self) -> None:
        for key in ("api_key", "apiKey", "OPENAI_API_KEY", "token", "password", "secret"):
            assert secrets.is_secret_key(key), key

    def test_reference_keys_are_not_treated_as_values(self) -> None:
        """`credential_ref` holds the indirection that keeps secrets OUT of the spec —
        stripping it would destroy the mechanism."""
        for key in ("credential_ref", "secret_ref", "auth_mode", "auth_type"):
            assert not secrets.is_secret_key(key), key

    def test_the_secret_binding_is_recognized(self) -> None:
        assert secrets.is_secret_binding("{{secret:OPENAI_KEY}}")
        assert secrets.is_secret_binding("Bearer {{ secret:TOKEN }}")
        assert not secrets.is_secret_binding("sk-plainvalue")

    def test_referenced_secret_names_are_collected_for_preflight(self) -> None:
        spec = {
            "root": {
                "children": [
                    {"config": {"header": "Bearer {{secret:GH_TOKEN}}"}},
                    {"config": {"key": "{{secret:OPENAI_KEY}}"}},
                ]
            }
        }
        assert secrets.secret_keys_referenced(spec) == ["GH_TOKEN", "OPENAI_KEY"]


class TestStripAndReinject:
    def test_a_secret_value_strips_to_a_presence_flag(self) -> None:
        spec = {"id": "n", "config": {"api_key": "sk-real-value", "url": "https://x"}}
        stripped = secrets.strip_secrets(spec)
        assert stripped["config"]["_has_api_key"] is True
        assert "api_key" not in stripped["config"]
        assert stripped["config"]["url"] == "https://x"  # non-secrets untouched

    def test_a_secret_binding_survives_stripping_intact(self) -> None:
        """It holds no value; stripping it would make the round-trip lossy and quietly
        break a working template."""
        spec = {"id": "n", "config": {"api_key": "{{secret:KEY}}"}}
        assert secrets.strip_secrets(spec)["config"]["api_key"] == "{{secret:KEY}}"

    def test_reinject_restores_by_node_id(self) -> None:
        stored = {"root": {"children": [{"id": "a", "config": {"api_key": "sk-stored"}}]}}
        incoming = {"root": {"children": [{"id": "a", "config": {"_has_api_key": True}}]}}
        merged = secrets.reinject_secrets(incoming, stored)
        assert merged["root"]["children"][0]["config"]["api_key"] == "sk-stored"
        assert "_has_api_key" not in merged["root"]["children"][0]["config"]

    def test_reinject_survives_a_node_moving_in_the_tree(self) -> None:
        """The reason the map is keyed by ID, not path: a mutation that moves a node must
        not drop its credentials."""
        stored = {
            "root": {
                "children": [
                    {"id": "first", "config": {"token": "t-1"}},
                    {"id": "second", "config": {"token": "t-2"}},
                ]
            }
        }
        # The same two nodes, order swapped — every path changed.
        incoming = {
            "root": {
                "children": [
                    {"id": "second", "config": {"_has_token": True}},
                    {"id": "first", "config": {"_has_token": True}},
                ]
            }
        }
        merged = secrets.reinject_secrets(incoming, stored)
        assert merged["root"]["children"][0]["config"]["token"] == "t-2"
        assert merged["root"]["children"][1]["config"]["token"] == "t-1"

    def test_an_explicit_new_value_wins_over_the_stored_one(self) -> None:
        stored = {"root": {"children": [{"id": "a", "config": {"api_key": "sk-old"}}]}}
        incoming = {
            "root": {
                "children": [{"id": "a", "config": {"api_key": "sk-new", "_has_api_key": True}}]
            }
        }
        merged = secrets.reinject_secrets(incoming, stored)
        assert merged["root"]["children"][0]["config"]["api_key"] == "sk-new"

    def test_a_false_flag_clears_the_credential(self) -> None:
        """How a user removes a credential without a separate endpoint."""
        stored = {"root": {"children": [{"id": "a", "config": {"api_key": "sk-old"}}]}}
        incoming = {"root": {"children": [{"id": "a", "config": {"_has_api_key": False}}]}}
        merged = secrets.reinject_secrets(incoming, stored)
        assert "api_key" not in merged["root"]["children"][0]["config"]

    def test_strip_then_reinject_round_trips(self) -> None:
        original = {
            "root": {
                "children": [
                    {"id": "a", "config": {"api_key": "sk-secret", "model": "opus"}},
                    {"id": "b", "config": {"prompt": "hello"}},
                ]
            }
        }
        merged = secrets.reinject_secrets(secrets.strip_secrets(original), original)
        assert merged == original


class TestInlineSecretLint:
    def test_a_secret_named_field_with_a_literal_is_flagged(self) -> None:
        found = secrets.find_inline_secrets(
            {"id": "n", "config": {"api_key": "some-literal-value"}}
        )
        assert len(found) == 1 and found[0].key == "api_key" and found[0].node_id == "n"

    def test_a_credential_shape_anywhere_is_flagged(self) -> None:
        """A token pasted into a prompt is just as leaked as one in an api_key field."""
        found = secrets.find_inline_secrets(
            {"id": "n", "config": {"prompt": "use sk-ant-abcdefghijklmnopqrstuvwxyz to auth"}}
        )
        assert len(found) == 1 and found[0].key == "prompt"

    @pytest.mark.parametrize(
        "value",
        [
            "ghp_abcdefghijklmnopqrstuvwxyz01",
            "AKIAIOSFODNN7EXAMPLE",
            "xoxb-1234567890-abcdefghij",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_known_credential_shapes_are_caught(self, value: str) -> None:
        assert secrets.find_inline_secrets({"config": {"note": value}})

    def test_the_sanctioned_binding_is_not_a_finding(self) -> None:
        assert secrets.find_inline_secrets({"id": "n", "config": {"api_key": "{{secret:K}}"}}) == []

    def test_ordinary_config_is_not_flagged(self) -> None:
        """A lint that cries wolf gets muted, and a muted lint protects nothing."""
        assert (
            secrets.find_inline_secrets(
                {
                    "id": "n",
                    "config": {
                        "prompt": "Summarize the quarterly report in three bullets.",
                        "model_tier": "reasoning",
                        "max_turns": 5,
                    },
                }
            )
            == []
        )

    def test_a_finding_never_carries_the_value_itself(self) -> None:
        """An error message that quotes the credential leaks it into the logs that render
        the message."""
        found = secrets.find_inline_secrets({"config": {"api_key": "sk-supersecret-value"}})
        assert found
        assert "supersecret" not in found[0].to_dict()["hint"]
        assert "supersecret" not in str(found[0].to_dict())
