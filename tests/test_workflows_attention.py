"""Projecting a waiting run into the attention surfaces (WF2-R7, Slice 8c).

The gap this closes: before it, a run parking on `needs_input` appeared in exactly ONE place —
an SSE frame. So a gate only reached a human who already had the run view open, and a scheduled
run firing at 3am would park and never be mentioned anywhere. That is not a UI polish problem;
it is an unattended run silently waiting forever.

What the tests pin:

* a waiting gate raises a durable inbox row AND one notification, through `emit_attention_item`
  (the pairing seam) rather than two separate calls that could drift;
* **one row per question, not per poll** — the watchdog re-polls a waiting run every few
  seconds, so the dedup key is the load-bearing part;
* a rewind re-asking the same question is a NEW row, because it genuinely is a new ask;
* answering closes the row, and a run ENDING closes whatever it left open — a row that
  outlives its gate is unanswerable, and one unanswerable row teaches a user to ignore the
  inbox;
* nothing here can break a run. A gate is what a user is waiting on; losing the run to a
  bookkeeping failure would be strictly worse than losing the row.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows import attention


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


class TestDedupKey:
    def test_the_key_is_scoped_to_run_path_and_epoch(self) -> None:
        assert attention.dedup_key("r1", "root.body#0", 0) == "workflow:r1:root.body#0:0"

    def test_two_polls_of_one_gate_share_a_key(self) -> None:
        """The load-bearing property. The watchdog re-observes a waiting run every few
        seconds; without this a single gate would stack a row per poll, each carrying a
        separately-valid resume token."""
        a = attention.dedup_key("r1", "root.gate", 0)
        b = attention.dedup_key("r1", "root.gate", 0)
        assert a == b

    def test_a_rewind_re_asking_is_a_NEW_key(self) -> None:
        """Epoch-scoped, not token-scoped: a rewind genuinely re-asks the question, and
        suppressing the second ask would leave the run waiting on a row marked handled."""
        before = attention.dedup_key("r1", "root.gate", 0)
        after = attention.dedup_key("r1", "root.gate", 1)
        assert before != after

    def test_two_concurrent_gates_have_different_keys(self) -> None:
        a = attention.dedup_key("r1", "root.children[0]", 0)
        b = attention.dedup_key("r1", "root.children[1]", 0)
        assert a != b


class TestTitleAndBody:
    def test_the_asks_own_prompt_is_the_title(self) -> None:
        """The actual question, not a generic label: "workflow needs input" forces the user to
        open the row before learning anything at all."""
        title = attention.ask_title("deploy", "confirm", {"prompt": "Ship v2.3 to production?"})
        assert title == "Ship v2.3 to production?"

    def test_a_long_prompt_is_clipped(self) -> None:
        """An inbox row is a glance, and a model-authored prompt can be a paragraph."""
        title = attention.ask_title("w", "n", {"prompt": "x" * 400})
        assert len(title) <= 120
        assert title.endswith("…")

    def test_a_promptless_ask_still_says_what_and_where(self) -> None:
        assert attention.ask_title("deploy", "confirm", {}) == "deploy: confirm needs your input"
        assert attention.ask_title("deploy", "", None) == "deploy: a step needs your input"

    def test_the_promptless_FALLBACK_IS_REACHABLE_from_a_real_gate(self) -> None:
        """🪤 The test above proves the fallback WORKS. Nothing proved it ever RAN.

        `_ask_payload` used to manufacture a prompt — `or "Approval needed"` — so `prompt` was
        always truthy and this fallback was dead in production. Measured on the shipped templates:
        **7 of the 19 gate/approval nodes author neither `prompt` nor `message`**, so `code-project`
        alone raised three inbox rows all titled "Approval needed", identifying neither the run nor
        the step. The same default also killed `needs_input._blocker_text`'s ladder and
        `WorkflowAsk.tsx`'s own "This run needs your input." — three written fallbacks, one default.

        So this asserts the CALL SITE, not the helper: the ask a real promptless gate produces must
        leave `prompt` empty, and the title composed from it must name the run and the step.
        """
        from personalclaw.workflows.engine import _ask_payload

        class _Node:
            id = "init_gate"

        ask = _ask_payload(_Node(), {})  # a bundled gate: no prompt, no message

        assert ask["prompt"] == "", f"a prompt was manufactured again: {ask['prompt']!r}"
        title = attention.ask_title("code-project", "init_gate", ask)
        assert title == "code-project: init_gate needs your input", title
        # The generic literal must not come back by any route.
        assert "Approval needed" not in title

    def test_an_AUTHORED_prompt_still_wins_over_the_fallback(self) -> None:
        """The fix must not cost the question. A gate that authors a prompt — 12 of the 19 do —
        keeps it as the headline, and `message` is still accepted as its alias."""
        from personalclaw.workflows.engine import _ask_payload

        class _Node:
            id = "approve"

        for cfg in ({"prompt": "Ship the release?"}, {"message": "Ship the release?"}):
            ask = _ask_payload(_Node(), cfg)
            assert ask["prompt"] == "Ship the release?", cfg
            assert attention.ask_title("release-check", "approve", ask) == "Ship the release?"

    def test_the_SHIPPED_TEMPLATES_still_contain_promptless_gates(self) -> None:
        """The vacuity floor. If every bundled gate ever authored a prompt, the change above would
        be unreachable and the two tests before it would prove nothing about the product."""
        import glob
        import json

        def walk(node: object, out: list) -> None:
            if isinstance(node, dict):
                if "kind" in node:
                    out.append(node)
                for value in node.values():
                    walk(value, out)
            elif isinstance(node, list):
                for value in node:
                    walk(value, out)

        files = sorted(glob.glob("src/personalclaw/packs/bundled/*/templates/*.json")) + sorted(
            glob.glob("src/personalclaw/workflows/bundled/**/*.json", recursive=True)
        )
        assert len(files) > 10, f"the template corpus must be discoverable, found {len(files)}"
        gates, promptless = 0, 0
        for path in files:
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except Exception:  # noqa: BLE001 — a malformed fixture is not this test's subject
                continue
            nodes: list = []
            walk(doc.get("root") or doc, nodes)
            for node in nodes:
                if str(node.get("kind")) not in ("gate", "approval"):
                    continue
                gates += 1
                cfg = node.get("config") or {}
                if not (cfg.get("prompt") or cfg.get("message")):
                    promptless += 1
        assert gates >= 10, f"only {gates} gates found — the walk is missing nodes"
        assert promptless >= 1, "no promptless gate ships, so the fallback is unreachable again"

    def test_the_body_names_the_kind_of_answer_wanted(self) -> None:
        for kind, expected in (
            ("approval", "approval"),
            ("choice", "choose"),
            ("text", "written"),
            ("form", "form"),
        ):
            assert expected in attention.ask_body({"kind": kind}, None).lower(), kind

    def test_an_unknown_ask_kind_still_produces_a_sentence(self) -> None:
        """A future ask kind must not render an empty body — that reads as a broken row."""
        assert attention.ask_body({"kind": "signature"}, None)

    def test_the_body_carries_the_outstanding_count(self) -> None:
        """The decision often depends on it: "is this the last step, or are eight waiting on
        me?" changes how urgently a user acts."""
        body = attention.ask_body({"kind": "approval"}, {"outstanding": ["a", "b", "c"]})
        assert "3 other step(s)" in body


class TestRaise:
    def test_a_gate_raises_one_row_and_one_notification(self, monkeypatch) -> None:
        calls: list[dict] = []

        def fake_emit(state, **kw):
            calls.append(kw)
            return "item-1"

        monkeypatch.setattr("personalclaw.inbox.emit_attention_item", fake_emit)
        item_id = attention.raise_gate_item(
            object(),
            run_id="r1",
            workflow="deploy",
            node_id="confirm",
            instance_path="root.gate",
            epoch=0,
            resume_token="tok-abc",
            ask={"kind": "approval", "prompt": "Ship it?"},
        )
        assert item_id == "item-1"
        assert len(calls) == 1
        kw = calls[0]
        assert kw["source"] == attention.SOURCE and kw["kind"] == attention.KIND
        assert kw["dedup_key"] == "workflow:r1:root.gate:0"

    def test_the_row_carries_what_makes_it_actionable(self, monkeypatch) -> None:
        """The run id AND the resume token: without the token a surface can only deep-link,
        which turns the inbox into a notification with extra steps."""
        captured: dict = {}
        monkeypatch.setattr(
            "personalclaw.inbox.emit_attention_item",
            lambda state, **kw: captured.update(kw) or "i",
        )
        attention.raise_gate_item(
            object(),
            run_id="r1",
            workflow="deploy",
            node_id="confirm",
            instance_path="root.gate",
            epoch=2,
            resume_token="tok-xyz",
        )
        refs = captured["refs"]
        assert refs["workflow"] == "r1"
        assert refs["resume_token"] == "tok-xyz"
        assert refs["workflow_node"] == "confirm"

    def test_it_rides_the_EXISTING_needs_input_pair(self) -> None:
        """`loop/needs_input` is registered, carries `attention=True`, and is what a user's
        "always interrupt me for needs_input" rule keys on. A second pair would make a
        workflow gate need its own separate configuration for the same behaviour."""
        from personalclaw.notification_kinds import resolve_kind

        kind = resolve_kind(attention.SOURCE, attention.KIND)
        assert (kind.source, kind.kind) == (
            attention.SOURCE,
            attention.KIND,
        ), "the pair fell through to the generic fallback — it is not registered"
        assert kind.attention is True

    def test_no_state_is_a_silent_no_op(self) -> None:
        """A controller driven in a test has no gateway. Raising there must not explode."""
        assert (
            attention.raise_gate_item(
                None,
                run_id="r1",
                workflow="w",
                node_id="n",
                instance_path="p",
                epoch=0,
                resume_token="t",
            )
            == ""
        )

    def test_a_broken_inbox_never_propagates(self, monkeypatch) -> None:
        """Best-effort by contract: the gate is what the user is waiting on, and losing the RUN
        to a bookkeeping failure is strictly worse than losing the row."""

        def boom(state, **kw):
            raise RuntimeError("inbox is on fire")

        monkeypatch.setattr("personalclaw.inbox.emit_attention_item", boom)
        assert (
            attention.raise_gate_item(
                object(),
                run_id="r1",
                workflow="w",
                node_id="n",
                instance_path="p",
                epoch=0,
                resume_token="t",
            )
            == ""
        )


class TestResolve:
    def _seed(self, run_id: str = "r1", node_id: str = "confirm", status: str = "pending"):
        from personalclaw.inbox import InboxItem, InboxStore, ItemKind

        store = InboxStore()
        store.load()
        item = InboxItem(
            id=f"needs_input-{run_id}-{node_id}",
            channel="",
            channel_name="",
            thread_ts=None,
            message="Ship it?",
            sender_id="",
            sender_name="",
            status=status,
            item_kind=ItemKind.NEEDS_INPUT.value,
            refs={"workflow": run_id, "workflow_node": node_id},
        )
        store.add(item)
        store.save()
        return item.id

    def test_answering_closes_the_row(self) -> None:
        from personalclaw.inbox import InboxStore, ItemStatus

        item_id = self._seed()
        assert attention.resolve_gate_item(None, "r1", "confirm") == 1
        store = InboxStore()
        store.load()
        assert store.items[item_id].status == ItemStatus.HANDLED.value

    def test_it_is_HANDLED_not_DISMISSED(self) -> None:
        """Different facts. Dismissed reads as "the user ignored it" and feeds the engagement
        signals accordingly; this was actually answered."""
        from personalclaw.inbox import InboxStore, ItemStatus

        item_id = self._seed()
        attention.resolve_gate_item(None, "r1", "confirm")
        store = InboxStore()
        store.load()
        assert store.items[item_id].status != ItemStatus.DISMISSED.value

    def test_answering_one_gate_leaves_a_CONCURRENT_gate_open(self) -> None:
        """A run with two gates has two rows. Closing both on one answer would hide a question
        the run is still genuinely waiting on."""
        from personalclaw.inbox import InboxStore

        a = self._seed(node_id="confirm")
        b = self._seed(node_id="review")
        assert attention.resolve_gate_item(None, "r1", "confirm") == 1
        store = InboxStore()
        store.load()
        assert store.items[b].status == "pending"
        assert store.items[a].status != "pending"

    def test_a_run_ending_closes_everything_it_left_open(self) -> None:
        """Cancel a run mid-gate and the question would otherwise survive the run — a
        permanently unanswerable row."""
        self._seed(node_id="confirm")
        self._seed(node_id="review")
        assert attention.resolve_run_items(None, "r1") == 2

    def test_another_runs_rows_are_untouched(self) -> None:
        from personalclaw.inbox import InboxStore

        mine = self._seed(run_id="r1")
        theirs = self._seed(run_id="r2")
        attention.resolve_run_items(None, "r1")
        store = InboxStore()
        store.load()
        assert store.items[theirs].status == "pending"
        assert store.items[mine].status != "pending"

    def test_a_dismissed_row_is_not_rewritten(self) -> None:
        """The user's own action wins: they already decided about this row, and silently
        flipping it to handled would overwrite that."""
        from personalclaw.inbox import InboxStore

        item_id = self._seed(status="dismissed")
        assert attention.resolve_gate_item(None, "r1", "confirm") == 0
        store = InboxStore()
        store.load()
        assert store.items[item_id].status == "dismissed"

    def test_resolving_with_no_rows_is_a_no_op(self) -> None:
        assert attention.resolve_run_items(None, "never-existed") == 0


class TestEmitSeamRegressions:
    """Two bugs in `emit_attention_item` itself, both found by driving a REAL workflow gate
    through a live gateway and then looking at what the inbox actually showed.

    Neither is workflow-specific: they affected every attention kind (loop gates, skill
    proposals, mirrored approvals). Pinned here because this is the session that found them.
    """

    def test_the_row_writes_to_the_RUNNING_services_store(self) -> None:
        """The service holds its items in MEMORY and never re-reads the file. A fresh
        `InboxStore()` here wrote a row that `/api/inbox` (which serves the service's instance)
        could not see — and that the service's next save would silently overwrite. The row hit
        disk and the inbox stayed empty.
        """
        from personalclaw.inbox import InboxStore, emit_attention_item

        live = InboxStore()
        live.load()

        class _Svc:
            inbox = live

        class _State:
            _inbox_svc = _Svc()

            def notify(self, *a, **kw):
                pass

        item_id = emit_attention_item(
            _State(), source="loop", kind="needs_input", title="Ship it?", body="Waiting."
        )
        assert item_id in live.items, "the row went to a detached store the API cannot serve"

    def test_a_mock_state_does_not_capture_the_write(self) -> None:
        """The type check matters: a `MagicMock()` state answers every getattr, so an
        attribute-only check would route real writes into a mock and the row would vanish.
        """
        from unittest.mock import MagicMock

        from personalclaw.inbox import InboxStore, emit_attention_item

        item_id = emit_attention_item(
            MagicMock(), source="loop", kind="needs_input", title="Ship it?", body="Waiting."
        )
        on_disk = InboxStore()
        on_disk.load()
        assert item_id in on_disk.items

    def test_the_title_is_not_discarded_by_the_body(self) -> None:
        """`message=body or title` dropped the title whenever a body existed. A workflow gate's
        row therefore read "Waiting for your approval." and LOST the actual question — the one
        thing a user needs in order to decide from the list.
        """
        from personalclaw.inbox import InboxStore, emit_attention_item

        item_id = emit_attention_item(
            None,
            source="loop",
            kind="needs_input",
            title="Ship the release to production?",
            body="Waiting for your approval.",
        )
        store = InboxStore()
        store.load()
        message = store.items[item_id].message
        assert "Ship the release to production?" in message
        assert "Waiting for your approval." in message

    def test_a_titleless_or_bodyless_item_has_no_stray_blank_lines(self) -> None:
        from personalclaw.inbox import InboxStore, emit_attention_item

        store = InboxStore()
        only_title = emit_attention_item(None, source="loop", kind="needs_input", title="Just this")
        store.load()
        assert store.items[only_title].message == "Just this"

    def test_resolving_writes_through_the_LIVE_store_too(self) -> None:
        """The raise side was fixed first, and the resolve side had the SAME bug: closing a row
        in a fresh `InboxStore()` mutated a detached copy the running service then overwrote, so
        an answered gate's row stayed open. Found by approving a real gate in a real browser and
        watching the row survive it.
        """
        from personalclaw.inbox import InboxItem, InboxStore, ItemKind, ItemStatus

        live = InboxStore()
        live.load()
        live.add(
            InboxItem(
                id="needs_input-live",
                channel="",
                channel_name="",
                thread_ts=None,
                message="Ship?",
                sender_id="",
                sender_name="",
                item_kind=ItemKind.NEEDS_INPUT.value,
                refs={"workflow": "rLIVE", "workflow_node": "confirm"},
            )
        )
        live.save()

        class _Svc:
            inbox = live

        class _State:
            _inbox_svc = _Svc()

        assert attention.resolve_gate_item(_State(), "rLIVE", "confirm") == 1
        # The LIVE instance is what the API serves, so that is where the close must land.
        assert live.items["needs_input-live"].status == ItemStatus.HANDLED.value
