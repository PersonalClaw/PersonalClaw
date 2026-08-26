"""BA-3 — the browse loop, the BrowseActionProvider contract, and the provider-fidelity wiring.

Every class here pins one clause of the atom's contract, and each guard is paired with a
CONTROL leg that proves it can fail:

  * ``TestTheProviderContract`` — the ABC + ``ALLOWED_HOOK_PROVIDERS`` + the rung declaration.
    Control: a name that is NOT allowlisted is rejected by the same create-path validation.
  * ``TestTheWorkflowActionNode`` — the real ``dispatch_action`` seam resolves ``browse``
    through the real registry. Control: with the registry entry gone the same node fails
    "unknown action provider", so the assertion is about the resolution, not the fake.
  * ``TestTheLoop`` — a multi-step task completes UNDER ``max_steps`` (the control leg for the
    ceiling) and every page is fenced. Control for the ceiling: the same script with
    ``max_steps=2`` parks. Control for the fence: the fence assertion counts pages, so one
    unfenced page out of four fails it.
  * ``TestSubmitVerification`` — §7.1. Control: a submit that changes nothing is reported
    FAILED *without* asking the model, so the "verified" path cannot be a rubber stamp.
  * ``TestParking`` — step and budget exhaustion park with notes intact, at the loop, at the
    provider, at the engine's action node and at the controller's run status.

The SEL row a park writes lands in ``PERSONALCLAW_HOME``, so every test here runs under an
isolated home — never the operator's real one.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from personalclaw.browse.extraction import extract_page
from personalclaw.browse.loop import (
    MAX_STEPS_DEFAULT,
    PARK_BUDGET_EXHAUSTED,
    PARK_NAVIGATION_BLOCKED,
    PARK_STEP_EXHAUSTED,
    PARK_STUCK,
    VERDICT_FORM_FAILED,
    VERDICT_FORM_OK,
    run_browse_loop,
)
from personalclaw.security import is_fenced

INDEX_URL = "https://example.test/docs"
CHANGELOG_URL = "https://example.test/changelog"

INDEX_HTML = """<html><body>
<h1>Documentation index</h1>
<p>The release notes for every version live behind the changelog link.</p>
<a href="/changelog">Changelog</a>
<a href="/install">Install guide</a>
</body></html>"""

CHANGELOG_HTML = """<html><body>
<h1>Changelog</h1>
<p>Version 2.0 shipped on Tuesday with the new scheduler.</p>
<form id="feedback">
  <input name="email" type="email">
  <button type="submit">Send</button>
</form>
</body></html>"""

CHANGELOG_AFTER_SUBMIT = """<html><body>
<h1>Thanks</h1><p>We recorded your address.</p>
</body></html>"""


def _run(coro):
    return asyncio.run(coro)


async def _no_settle() -> None:
    """The SUBMIT verification's real settle is 5 polls x 2s (a form post is a round trip). Tests
    inject this instead of shortening `_SETTLE_SECONDS`, so production keeps its real wait and the
    suite does not spend 10s per submit."""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME`, not a patched ``config_dir``: it is read per call and cached
    nowhere, so it also redirects the import-bound stores a ``setattr`` would miss."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


# ── harness ───────────────────────────────────────────────────────────────────


class _FakePage:
    """A ``PageDriver`` over a dict of URL → HTML. Records every actuation."""

    def __init__(self, pages: dict[str, str], *, url: str) -> None:
        self._pages = dict(pages)
        self.url = url
        self.actions: list[tuple] = []
        self.shot = ""
        self.on_submit = None

    async def html(self) -> str:
        return self._pages.get(self.url, "<html><body>nothing here</body></html>")

    async def current_url(self) -> str:
        return self.url

    async def click(self, ref) -> None:
        self.actions.append(("click", ref.ref))

    async def fill(self, ref, value) -> None:
        self.actions.append(("fill", ref.ref, value))

    async def submit(self) -> None:
        self.actions.append(("submit",))
        if self.on_submit is not None:
            self.on_submit(self)

    async def scroll(self, direction) -> None:
        self.actions.append(("scroll", direction))

    async def go_back(self) -> None:
        self.actions.append(("back",))

    async def screenshot(self) -> str:
        return self.shot

    def replace(self, url: str, html: str) -> None:
        self._pages[url] = html


class _FakeSession:
    """A stand-in for ``GatedCdpSession``: records navigations, denies a configured set."""

    def __init__(self, page: _FakePage, *, deny: tuple[str, ...] = ()) -> None:
        self._page = page
        self.deny = set(deny)
        self.navigations: list[str] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def navigate(self, url: str):
        self.navigations.append(url)
        if url in self.deny:
            return SimpleNamespace(
                ok=False, allowed=False, url=url, reason="denied by the BROWSE policy", error=""
            )
        self._page.url = url
        return SimpleNamespace(ok=True, allowed=True, url=url, reason="", error="")


class _Decide:
    """A scripted model. Records every prompt; falls back to a harmless NOTES forever."""

    def __init__(self, *replies: str, fallback: str = "NOTES still looking") -> None:
        self.replies = list(replies)
        self.fallback = fallback
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else self.fallback

    @property
    def page_prompts(self) -> list[str]:
        """The prompts that carried a PAGE — one per perceive step, excluding verifications."""
        return [p for p in self.prompts if "CURRENT PAGE:" in p]


def _ref_of(html: str, url: str, label: str) -> str:
    extraction = extract_page(html, url=url)
    for element in extraction.links:
        if element.label.strip().lower() == label.lower():
            return element.ref
    for form in extraction.forms:
        for field in form.fields:
            if field.label.strip().lower() == label.lower():
                return field.ref
    raise AssertionError(f"no element labelled {label!r} in the extraction")


def _fresh_pair(*, url: str = INDEX_URL, deny: tuple[str, ...] = ()):
    page = _FakePage({INDEX_URL: INDEX_HTML, CHANGELOG_URL: CHANGELOG_HTML}, url=url)
    return _FakeSession(page, deny=deny), page


# ── clause 1: the ActionProvider contract + the allowlist ─────────────────────


class TestTheProviderContract:
    def test_browse_implements_the_action_provider_abc_and_is_resolvable_by_name(self):
        """Not `issubclass` on the class the test imported — the object the DISPATCH SEAMS get.

        `get_action_provider("browse")` is the one lookup every seam performs, so resolving it
        here is what proves the provider is reachable rather than merely written.
        """
        from personalclaw.action_providers.base import ActionProvider
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        provider = get_action_provider("browse")
        assert provider is not None, "browse is not in the action-provider registry"
        assert isinstance(provider, ActionProvider)
        assert provider.name == "browse"
        assert provider.display_name
        # The ABC's abstract surface is satisfied without a shim: instantiating an incomplete
        # subclass raises TypeError, so a successful construction IS the assertion.
        assert type(provider)() is not provider

    def test_browse_is_in_allowed_hook_providers_and_the_create_path_accepts_it(self):
        """The clause's own line: 'browse' is added to ALLOWED_HOOK_PROVIDERS.

        Asserted through `HOOK_CREATE_SCHEMA`, the validation a hook/trigger create actually
        runs, not just against the frozenset — the frozenset is the mechanism, the accepted
        create is the use.
        """
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS, HOOK_CREATE_SCHEMA

        assert "browse" in ALLOWED_HOOK_PROVIDERS

        field = next(f for f in HOOK_CREATE_SCHEMA.fields if f.name == "provider")
        assert "browse" in field.allowed
        # CONTROL: the same allowed-set is what rejects an unknown name, so the assertion
        # above is not vacuously true of every string.
        assert "browse-but-typoed" not in field.allowed

    def test_browse_carries_an_autonomy_declaration_so_the_seams_can_govern_it(self):
        """A registered provider with no declaration is, at a seam, indistinguishable from an
        ungoverned action. Browse declares `one_tap` at both ends because a SUBMIT is an
        irreversible external write it cannot undo."""
        from personalclaw.guardrails import autonomy as au
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        spec = au.action_type_for_provider("browse")
        assert spec is not None, "browse has no ActionTypeSpec"
        assert spec.key == "action.browse"
        assert spec.leaves_machine is True
        assert spec.floor == spec.ceiling == "one_tap"

    def test_browse_is_classified_write_capable_by_the_trigger_capability_fence(self):
        """The SECOND inventory table a new provider has to appear in.

        `test_triggers_capability_fence` only asserts a provider is in ONE of the two sets; this
        pins WHICH. Write-capable is not a close call for a SUBMIT, and even a read-only browse
        spends a model call per step over attacker-controlled text — so it needs the opt-in.
        """
        from personalclaw.triggers.screen import (
            READ_ONLY_PROVIDERS,
            WRITE_CAPABLE_PROVIDERS,
            provider_is_read_only,
        )

        assert "browse" in WRITE_CAPABLE_PROVIDERS
        assert "browse" not in READ_ONLY_PROVIDERS
        assert provider_is_read_only("browse") is False
        # CONTROL: the predicate does return True for something, so the assertion above is not
        # just the fail-closed default answering for every string.
        assert provider_is_read_only("notify") is True

    def test_the_denylist_screens_a_browse_dispatch_at_the_real_hooks_seam(self, monkeypatch):
        """THE call site for the 'inheriting denylist' clause.

        The seam is driven for real; `enforce_action` is spied at its definition site. Delete
        the `enforce_action` call from `hooks.py` and this goes red — which is the point, because
        browse contributes nothing to that screen and inherits all of it.
        """
        from personalclaw.guardrails import denylist as dl

        seen: list[tuple[str, dict]] = []

        def _spy(provider_name, action_config, ctx=None, session_key=""):
            seen.append((provider_name, dict(action_config or {})))
            return dl.DenyDecision(
                blocked=True, verdict="block", reason="spied", matched="test:spy"
            )

        monkeypatch.setattr(dl, "enforce_action", _spy)

        from personalclaw.hooks import ScriptHook, run_script_hook

        hook = ScriptHook(
            id="h1",
            name="nightly browse",
            event="SessionStart",
            provider="browse",
            provider_config={"goal": "read the changelog", "start_url": INDEX_URL},
        )
        result = _run(run_script_hook(hook, "", {"event": "SessionStart"}))

        assert seen, "the hooks seam dispatched browse without screening it"
        assert seen[0][0] == "browse"
        assert seen[0][1]["start_url"] == INDEX_URL, "the seam screened an empty config"
        assert "denylist" in (result.error or ""), result.error
        assert hook.last_status == "blocked"


# ── clause 2: the workflow action node ────────────────────────────────────────


class TestTheWorkflowActionNode:
    def test_a_workflow_definition_naming_browse_is_accepted(self):
        """`validate_spec` is the authoring-time acceptance path. A browse action node with its
        arguments under `config.with` validates clean — no warnings either, because a flat
        argument beside `provider` reaches the provider as an empty config."""
        from personalclaw.workflows.validator import validate_spec

        res = validate_spec(
            {
                "name": "read-the-changelog",
                "root": {
                    "kind": "action",
                    "id": "browse-docs",
                    "config": {
                        "provider": "browse",
                        "with": {"goal": "find the latest version", "start_url": INDEX_URL},
                    },
                },
            }
        )
        assert res.ok, [i.code for i in res.issues]
        assert not [i for i in res.issues if i.code.startswith("WF_")], res.issues

    def test_the_action_node_dispatches_browse_through_the_REAL_registry(self, monkeypatch):
        """THE call site. `get_provider=None` means `dispatch_action` resolves the name itself
        through `action_providers.registry.get_action_provider` — the seam BA-3 had to land in,
        not a parallel path.

        The provider's own plumbing is faked (no browser, no model); the RESOLUTION is real.
        """
        from personalclaw.action_providers import browse_provider as bp
        from personalclaw.action_providers.registry import _ensure_default_providers_registered
        from personalclaw.workflows.engine import dispatch_action
        from personalclaw.workflows.models import InstanceState, Node

        _ensure_default_providers_registered()

        session, page = _fresh_pair()
        decide = _Decide("NOTES version 2.0 shipped on Tuesday", "DONE")
        monkeypatch.setattr(bp, "_decide", decide)
        monkeypatch.setattr(
            bp.BrowseActionProvider,
            "_open",
            lambda self, cfg, ctx: _done((session, page, None)),
        )

        node = Node.from_dict(
            {
                "kind": "action",
                "id": "browse-docs",
                "config": {
                    "provider": "browse",
                    "with": {"goal": "find the latest version", "start_url": INDEX_URL},
                },
            }
        )
        result = _run(dispatch_action(node, _binding_ctx(), run_id="run-1"))

        assert result.state is InstanceState.DONE, result.failure
        # The engine parses the provider's JSON stdout into the node's output, so a downstream
        # binding reads `{{nodes.browse-docs.notes}}` with nothing in between.
        assert result.output["notes"] == ["version 2.0 shipped on Tuesday"]
        assert session.navigations == [INDEX_URL], "the node did not drive the gated session"

    def test_the_same_node_fails_when_browse_is_not_registered(self, monkeypatch):
        """CONTROL for the test above. With the registry entry removed the identical node fails
        'unknown action provider' — so the green above is about the real resolution, not about
        the fake plumbing being reachable some other way."""
        from personalclaw.action_providers import registry
        from personalclaw.workflows.engine import dispatch_action
        from personalclaw.workflows.models import InstanceState, Node

        registry._ensure_default_providers_registered()
        monkeypatch.setattr(registry, "get_action_provider", lambda _name: None)

        node = Node.from_dict(
            {"kind": "action", "id": "b", "config": {"provider": "browse", "with": {}}}
        )
        result = _run(dispatch_action(node, _binding_ctx()))
        assert result.state is InstanceState.FAILED
        assert "unknown action provider" in (result.failure.cause_plain or "")


# ── clause 3: the loop, its ceiling, and per-page fencing ─────────────────────


class TestTheLoop:
    def test_a_multi_step_task_completes_within_max_steps_with_every_page_fenced(self):
        """The CONTROL LEG for `max_steps` and the fencing clause in one run.

        Four steps under a ceiling of twenty: a navigate, a click, a note and a DONE. Without
        this leg "parks at exhaustion" measures nothing — a loop that parked on step one would
        satisfy the exhaustion test too.
        """
        session, page = _fresh_pair()
        changelog_ref = _ref_of(INDEX_HTML, INDEX_URL, "Changelog")
        decide = _Decide(
            f"CLICK {changelog_ref}",
            f"NAVIGATE {CHANGELOG_URL}",
            "NOTES version 2.0 shipped on Tuesday",
            "DONE",
        )

        result = _run(
            run_browse_loop(
                goal="find the latest version",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=MAX_STEPS_DEFAULT,
            )
        )

        assert result.ok and not result.parked, result
        assert result.step_count == 4 <= MAX_STEPS_DEFAULT
        assert result.notes == ("version 2.0 shipped on Tuesday",)
        assert ("click", changelog_ref) in page.actions
        assert session.navigations == [INDEX_URL, CHANGELOG_URL]

        # PER PAGE, not once for the run: four perceive steps, four fenced pages.
        assert len(decide.page_prompts) == 4
        unfenced = [i for i, p in enumerate(decide.page_prompts) if not is_fenced(p)]
        assert unfenced == [], f"page(s) reached the model unfenced: {unfenced}"
        # And the fence carries the page's provenance, so a reader can tell WHICH page.
        assert f"source={CHANGELOG_URL} source_type=web_page" in decide.page_prompts[-1]

    def test_the_default_ceiling_is_twenty_steps(self):
        """§7.2's number, asserted by COUNTING the model calls a never-finishing task makes —
        not by reading the constant back, which would pin the constant to itself.

        The script ALTERNATES scroll directions so stuck detection never fires — otherwise this
        would measure `STUCK_REPEAT_LIMIT` and report it as the step ceiling.
        """
        session, page = _fresh_pair()
        decide = _Decide(*(["SCROLL down", "SCROLL up"] * 15), fallback="DONE")
        result = _run(
            run_browse_loop(
                goal="scroll forever",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
            )
        )
        assert MAX_STEPS_DEFAULT == 20
        assert result.step_count == 20
        assert len(decide.page_prompts) == 20
        assert result.parked and result.park_reason == PARK_STEP_EXHAUSTED

    def test_a_stuck_model_is_warned_once_and_then_the_run_ends(self):
        """§7.2 stuck detection. The warning has to reach the NEXT prompt, and a model that
        ignores it has to stop the run — otherwise the guard is a slower infinite loop."""
        session, page = _fresh_pair()
        decide = _Decide(fallback="SCROLL down")
        result = _run(
            run_browse_loop(
                goal="go nowhere",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=20,
            )
        )
        assert result.parked and result.park_reason == PARK_STUCK
        assert result.step_count == 4, "the run should end one repeat after the warning"
        assert any("You appear stuck" in p for p in decide.page_prompts)

    def test_revisiting_a_page_warns_the_model(self):
        """Visited-URL dedup (§7.2): a warning, not a refusal — revisiting is sometimes right."""
        session, page = _fresh_pair()
        decide = _Decide(f"NAVIGATE {CHANGELOG_URL}", f"NAVIGATE {CHANGELOG_URL}", "DONE")
        _run(
            run_browse_loop(
                goal="loop between two pages",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=6,
            )
        )
        assert any(f"already visited {CHANGELOG_URL}" in p for p in decide.page_prompts)

    def test_a_denied_first_navigation_is_a_failure_not_a_park(self):
        """A run that never loaded a page produced nothing to keep, so it fails rather than
        parking — parking a run with no notes asks a human to look at an empty result."""
        session, page = _fresh_pair(deny=(INDEX_URL,))
        decide = _Decide("DONE")
        result = _run(
            run_browse_loop(
                goal="read a blocked host",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
            )
        )
        assert result.ok is False
        assert result.parked and result.park_reason == PARK_NAVIGATION_BLOCKED
        assert result.blocked_urls == (INDEX_URL,)
        assert decide.prompts == [], "the model was consulted about a page that never loaded"


# ── clause 4: SUBMIT outcome verification (§7.1) ───────────────────────────────


class TestSubmitVerification:
    def test_submit_triggers_a_second_model_call_that_judges_the_outcome(self):
        session, page = _fresh_pair(url=CHANGELOG_URL)
        page.on_submit = lambda p: p.replace(CHANGELOG_URL, CHANGELOG_AFTER_SUBMIT)
        email_ref = _ref_of(CHANGELOG_HTML, CHANGELOG_URL, "email")
        decide = _Decide(
            f"TYPE {email_ref}(me@example.test)",
            "SUBMIT",
            "FORM_OK",
            "DONE",
        )

        result = _run(
            run_browse_loop(
                goal="subscribe",
                start_url=CHANGELOG_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=8,
                settle=_no_settle,
            )
        )

        assert ("submit",) in page.actions
        submit_step = next(s for s in result.steps if s.action == "SUBMIT")
        assert submit_step.verification == VERDICT_FORM_OK, result.steps
        assert any("submission verified" in n for n in result.notes), result.notes
        # The verification is its OWN call, with the post-submit page fenced in it too.
        verify_prompts = [p for p in decide.prompts if "FORM_OK" in p and "THE PAGE NOW" in p]
        assert len(verify_prompts) == 1, decide.prompts
        assert is_fenced(verify_prompts[0])
        assert "Thanks" in verify_prompts[0]

    def test_a_submit_that_changed_nothing_is_failed_without_asking_the_model(self):
        """CONTROL for the verification. A page that did not move means the post never reached
        the server, and asking a model to judge an identical page invites a hallucinated
        success — so the verdict is reached WITHOUT a model call."""
        session, page = _fresh_pair(url=CHANGELOG_URL)  # on_submit stays None: nothing changes
        decide = _Decide("SUBMIT", "DONE")

        result = _run(
            run_browse_loop(
                goal="subscribe",
                start_url=CHANGELOG_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=4,
                settle=_no_settle,
            )
        )

        submit_step = next(s for s in result.steps if s.action == "SUBMIT")
        assert submit_step.verification == VERDICT_FORM_FAILED
        assert any("did not change after SUBMIT" in n for n in result.notes), result.notes
        assert not [p for p in decide.prompts if "FORM_OK" in p], "the model was asked anyway"

    def test_a_form_failed_verdict_is_preserved_as_a_note_the_agent_can_act_on(self):
        session, page = _fresh_pair(url=CHANGELOG_URL)
        page.on_submit = lambda p: p.replace(
            CHANGELOG_URL, "<html><body><p>That address is not valid.</p></body></html>"
        )
        decide = _Decide("SUBMIT", "FORM_FAILED the address was rejected", "DONE")

        result = _run(
            run_browse_loop(
                goal="subscribe",
                start_url=CHANGELOG_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=4,
                settle=_no_settle,
            )
        )
        assert any("the address was rejected" in n for n in result.notes), result.notes
        assert result.ok and not result.parked


# ── clause 5: parking with notes preserved ────────────────────────────────────


class TestParking:
    def test_step_exhaustion_parks_with_every_note_preserved(self):
        session, page = _fresh_pair()
        decide = _Decide(
            "NOTES the index lists two guides",
            "NOTES the changelog is linked from here",
            fallback="SCROLL down",
        )
        result = _run(
            run_browse_loop(
                goal="summarise the docs",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=3,
            )
        )
        assert result.parked and result.park_reason == PARK_STEP_EXHAUSTED
        assert result.ok is True, "a park is not a failure — its notes are the deliverable"
        assert result.notes == (
            "the index lists two guides",
            "the changelog is linked from here",
        )

    def test_budget_exhaustion_parks_before_it_spends_the_next_call(self):
        """The guard sits where the model call happens. It is consulted BEFORE the call, so an
        exceeded budget costs zero further tokens."""
        session, page = _fresh_pair()
        decide = _Decide("NOTES one useful fact", fallback="SCROLL down")
        verdicts = ["ok", "ok", "exceeded"]

        def _budget():
            return (verdicts.pop(0) if verdicts else "exceeded"), "day token budget exceeded"

        result = _run(
            run_browse_loop(
                goal="summarise the docs",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=MAX_STEPS_DEFAULT,
                budget_check=_budget,
            )
        )
        assert result.parked and result.park_reason == PARK_BUDGET_EXHAUSTED
        assert "budget exceeded" in result.park_detail
        assert result.notes == ("one useful fact",)
        assert len(decide.page_prompts) == 2, "the exceeded verdict did not stop the next call"

    def test_the_same_task_completes_when_the_budget_is_fine(self):
        """CONTROL for the budget guard: with an OK verdict the identical script finishes, so
        the park above is the budget's doing and not the script running out."""
        session, page = _fresh_pair()
        decide = _Decide("NOTES one useful fact", "DONE")
        result = _run(
            run_browse_loop(
                goal="summarise the docs",
                start_url=INDEX_URL,
                session=session,
                page=page,
                decide=decide,
                max_steps=MAX_STEPS_DEFAULT,
                budget_check=lambda: ("ok", ""),
            )
        )
        assert not result.parked and result.ok
        assert result.notes == ("one useful fact",)

    def test_the_provider_reports_a_park_as_needs_input_with_the_notes_on_stdout(self, monkeypatch):
        from personalclaw.action_providers import browse_provider as bp
        from personalclaw.action_providers.base import ActionContext

        session, page = _fresh_pair()
        decide = _Decide("NOTES the index lists two guides", fallback="SCROLL down")
        monkeypatch.setattr(bp, "_decide", decide)
        monkeypatch.setattr(
            bp.BrowseActionProvider, "_open", lambda self, cfg, ctx: _done((session, page, None))
        )

        result = _run(
            bp.BrowseActionProvider().execute(
                {"goal": "summarise the docs", "start_url": INDEX_URL, "max_steps": 2},
                ActionContext(event="workflow_node"),
            )
        )
        assert result.success is True
        assert result.outcome == "needs_input"
        payload = json.loads(result.stdout)
        assert payload["parked"] is True
        assert payload["park_reason"] == PARK_STEP_EXHAUSTED
        assert payload["notes"] == ["the index lists two guides"]
        # The user-facing sentence: what stopped it, and that nothing was lost.
        assert "note(s) kept" in result.stderr and "2 steps" in result.stderr

    def test_the_action_node_parks_a_needs_input_result_into_waiting(self):
        """The engine half of the contract. WAITING with no `wake_at` is what the controller
        reads as 'nothing will wake this run' — see the run-status test below."""
        from personalclaw.action_providers.base import ActionResult
        from personalclaw.workflows.engine import dispatch_action
        from personalclaw.workflows.models import InstanceState, Node

        class _Parker:
            name = "parker"
            display_name = "parker"

            async def execute(self, cfg, ctx, timeout=30):
                return ActionResult(
                    success=True,
                    stdout=json.dumps({"notes": ["kept"]}),
                    outcome="needs_input",
                    stderr="Browse stopped after 2 steps; 1 note(s) kept.",
                )

        node = Node.from_dict({"kind": "action", "id": "b", "config": {"provider": "parker"}})
        result = _run(dispatch_action(node, _binding_ctx(), get_provider=lambda _n: _Parker()))

        assert result.state is InstanceState.WAITING
        assert result.wake_at in (0, 0.0, None), "a park must not schedule its own wake-up"
        assert result.output["notes"] == ["kept"], "the park lost the notes"
        assert "note(s) kept" in (result.degraded_reason or "")

    def test_a_parked_action_node_finishes_the_run_as_needs_input(self, tmp_path, monkeypatch):
        """THE call site one layer up: a REAL `RunController`. Without this the WAITING mapping
        could be right and the run could still end COMPLETE — the engine honouring a state the
        controller never surfaces is what 'parks cleanly' has to rule out."""
        from personalclaw.action_providers.base import ActionResult
        from personalclaw.workflows import store
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import RunStatus, WorkflowRun

        home = tmp_path / "wf-home"
        home.mkdir()
        monkeypatch.setattr(store, "config_dir", lambda: home)

        class _Parker:
            async def execute(self, cfg, ctx, timeout=30):
                return ActionResult(
                    success=True,
                    stdout=json.dumps({"notes": ["kept across the park"]}),
                    outcome="needs_input",
                    stderr="Browse stopped after 2 steps; 1 note(s) kept.",
                )

        spec = {
            "name": "browse-parks",
            "root": {
                "kind": "sequence",
                "id": "root",
                "children": [
                    {
                        "kind": "action",
                        "id": "browse-docs",
                        "config": {"provider": "browse", "with": {"goal": "g", "start_url": "u"}},
                    }
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="browse-parks"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(get_provider=lambda _name: _Parker())
        )
        status = _run(controller.run_to_completion(timeout=20))

        assert status == RunStatus.NEEDS_INPUT, status

    def test_the_trigger_ledger_records_a_park_as_deferred(self):
        """The other reader of the outcome vocabulary. A status this map does not recognise is
        recorded FAILED, which would turn every park into a red row in the runs surface."""
        from personalclaw.triggers.executor import STATUS_TO_OUTCOME
        from personalclaw.triggers.models import Outcome

        assert STATUS_TO_OUTCOME["needs_input"] == Outcome.DEFERRED.value


# ── provider-level refusals ───────────────────────────────────────────────────


class TestProviderRefusals:
    def test_a_config_without_a_goal_or_a_url_is_refused_with_a_typed_envelope(self):
        from personalclaw.action_providers.base import ActionContext
        from personalclaw.action_providers.browse_provider import BrowseActionProvider

        result = _run(
            BrowseActionProvider().execute({"start_url": INDEX_URL}, ActionContext(event="e"))
        )
        assert result.success is False
        assert result.agent_error is not None
        assert result.agent_error.code == "ERR_BROWSE_CONFIG"

    def test_no_browser_target_is_a_typed_refusal_not_a_silent_success(self):
        """An action that reports success while browsing nothing is indistinguishable, to a
        workflow, from one that did the work."""
        from personalclaw.action_providers.base import ActionContext
        from personalclaw.action_providers.browse_provider import BrowseActionProvider

        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read the docs", "start_url": INDEX_URL}, ActionContext(event="e")
            )
        )
        assert result.success is False
        assert result.agent_error.code == "ERR_BROWSE_NO_TARGET"
        assert "cdp_url" in result.agent_error.fix

    def test_incident_mode_refuses_before_a_browser_is_touched(self, monkeypatch):
        from personalclaw.action_providers.base import ActionContext
        from personalclaw.action_providers.browse_provider import BrowseActionProvider

        opened: list[int] = []
        monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
        monkeypatch.setattr(
            BrowseActionProvider,
            "_open",
            lambda self, cfg, ctx: opened.append(1) or _done((None, None, None)),
        )

        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read the docs", "start_url": INDEX_URL, "cdp_url": "ws://x"},
                ActionContext(event="e"),
            )
        )
        assert result.success is False
        assert result.agent_error.code == "ERR_BROWSE_INCIDENT_ACTIVE"
        assert opened == [], "incident mode was checked after the browser was opened"


# ── the CDP page driver's wire ─────────────────────────────────────────────────


class TestCdpPageDriver:
    def test_the_driver_addresses_an_element_by_its_identity_over_the_real_wire(self):
        """The transport is faked; the CDP methods and the substituted identity are real. A
        label the page controls goes in through `json.dumps`, in ONE substitution pass."""
        from personalclaw.browse.extraction import ElementRef
        from personalclaw.browse.page import CdpPageDriver

        sent: list[tuple[str, dict]] = []

        class _T:
            async def send(self, method, params=None):
                sent.append((method, dict(params or {})))
                return {"result": {"value": "ok"}}

        driver = CdpPageDriver(_T())
        ref = ElementRef(ref="abc123", role="link", label='Sign ") in', target="/login")
        _run(driver.click(ref))

        assert sent[0][0] == "Runtime.evaluate"
        expression = sent[0][1]["expression"]
        assert json.dumps('Sign ") in') in expression, "the label was not JSON-escaped"
        assert '"/login"' in expression
        assert "hit.click()" in expression

    def test_a_missing_element_raises_instead_of_reporting_a_click_that_never_happened(self):
        from personalclaw.browse.extraction import ElementRef
        from personalclaw.browse.page import NOT_FOUND, CdpPageDriver, PageActionError

        class _T:
            async def send(self, method, params=None):
                return {"result": {"value": NOT_FOUND}}

        driver = CdpPageDriver(_T())
        with pytest.raises(PageActionError):
            _run(driver.click(ElementRef(ref="x", role="link", label="gone")))

    def test_a_screenshot_is_written_to_a_path_and_never_returned_as_base64(self, tmp_path):
        import base64

        from personalclaw.browse.page import CdpPageDriver

        png = base64.b64encode(b"\x89PNG fake").decode()

        class _T:
            async def send(self, method, params=None):
                return {"data": png}

        driver = CdpPageDriver(_T(), screenshot_dir=tmp_path / "shots")
        path = _run(driver.screenshot())
        assert path.endswith(".png")
        assert "base64" not in path
        from pathlib import Path

        assert Path(path).read_bytes().startswith(b"\x89PNG")

    def test_no_screenshot_dir_yields_no_capture_rather_than_an_inline_payload(self):
        from personalclaw.browse.page import CdpPageDriver

        class _T:
            async def send(self, method, params=None):  # pragma: no cover - must not be called
                raise AssertionError("captureScreenshot was sent with nowhere to write")

        assert _run(CdpPageDriver(_T()).screenshot()) == ""


# ── helpers ───────────────────────────────────────────────────────────────────


def _done(value):
    """An already-resolved awaitable, so a lambda can stand in for an async method."""
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(value)
    return fut


def _binding_ctx():
    """The minimal `BindingContext` `resolve_config` needs for a literal-only config."""
    from personalclaw.workflows.bindings import BindingContext

    return BindingContext(inputs={}, node_outputs={})
