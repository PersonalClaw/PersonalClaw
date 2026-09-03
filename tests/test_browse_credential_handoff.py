"""BA-4 — the browser-session credential handoff, and above all its ONE invariant.

    "the credentials-never-transit-the-agent invariant holds
     (the LLM never sees password / 2FA / token)"

That clause is the reason this file is organised the way it is. The other three clauses (park on
needs-input, resume with the session, a later run reuses the profile) are ordinary behaviour with
ordinary tests. The invariant is a NEGATIVE claim about every string in the system, and a negative
claim needs a different shape of test: not "does the redactor work" but "sweep every surface a
credential could reach and prove it is not there".

So ``TestTheInvariant`` runs full loops with a real credential planted on the real path and then
sweeps SIX surfaces at once — every prompt the model saw, the step ledger, the notes, the warnings
(which become the NEXT prompt), the run payload, and the log records. A test that checked only the
prompt would have passed against three of the four leaks found while writing this.

**Each guard has a control leg that proves it can fail.** They are named ``…_control`` and they are
not decoration: the invariant sweep asserts a string is ABSENT, and an absence assertion passes
trivially when the harness never put the string anywhere. Every control leg therefore drives the
SAME harness with a NON-credential field and asserts the value IS present on the same surfaces —
so a sweep that has stopped observing anything fails its control instead of quietly staying green.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.browse_provider import (
    OUTCOME_NEEDS_INPUT,
    BrowseActionProvider,
)
from personalclaw.browse.credentials import (
    WITHHELD,
    is_credential_input,
    screen_action_render,
    screen_url,
)
from personalclaw.browse.extraction import extract_page, render_links_dsl
from personalclaw.browse.handoff import (
    AUTH_STATE_ACTIVE,
    AUTH_STATE_EXPIRED,
    PARK_LOGIN_REQUIRED,
    SESSION_ABSENT,
    SESSION_EXPIRED,
    SESSION_FRESH,
    ensure_profile_key,
    expired_sites,
    has_profile_key,
    load_meta,
    looks_like_login_url,
    mark_expired,
    profile_dir,
    profile_key_name,
    profiles_root,
    record_login,
    request_login,
    session_state,
    site_slug,
)
from personalclaw.browse.loop import run_browse_loop

# ── the planted credentials ───────────────────────────────────────────────────
#
# Distinctive literals, so a sweep that finds one has found THAT value and not a substring of
# something innocent. Each stands for a different clause of the invariant: a password, a 2FA code,
# an OAuth authorization code and an implicit-flow bearer token.
PASSWORD = "hunter2-CORRECTHORSE"
OTP_CODE = "418290-ONETIMECODE"
OAUTH_CODE = "AUTHZ-4f9d2e-CODEVALUE"
BEARER = "eyJBEARERTOKENVALUE"

#: The control value: an ordinary field's contents. Present on exactly the surfaces the credentials
#: must be absent from, which is what makes the absence assertions non-vacuous.
ORDINARY = "alice-ORDINARYVALUE"

LOGIN_URL = "https://bank.test/login"
HOME_URL = "https://bank.test/dashboard"
CALLBACK_URL = f"https://bank.test/callback?code={OAUTH_CODE}&country=US"
IMPLICIT_URL = f"https://bank.test/done#access_token={BEARER}&token_type=bearer"

LOGIN_HTML = f"""<html><body>
<h1>Sign in</h1>
<form name="signin">
  <input name="username" type="text" value="{ORDINARY}">
  <input name="password" type="password" value="{PASSWORD}">
  <input name="code" type="text" autocomplete="one-time-code" value="{OTP_CODE}">
  <input type="submit" value="Sign in">
</form>
<a href="/help">Help</a>
</body></html>"""

DASHBOARD_HTML = """<html><body>
<h1>Your accounts</h1><p>Balance is fine.</p>
</body></html>"""


def _run(coro):
    return asyncio.run(coro)


async def _no_settle() -> None:
    """The SUBMIT verification's real settle is 5x2s. Injected so the suite does not pay it."""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME`, not a patched ``config_dir``: it is read per call and cached nowhere,
    so it also redirects the import-bound stores a ``setattr`` would miss. The profile directories
    and the park's SEL row both land under the home — never the operator's real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


# ── harness ───────────────────────────────────────────────────────────────────


class _FakePage:
    """A ``PageDriver`` over URL → HTML. Records every actuation, including refused ones."""

    def __init__(self, pages: dict[str, str], *, url: str) -> None:
        self._pages = dict(pages)
        self.url = url
        self.actions: list[tuple] = []
        self.shot = ""

    async def html(self) -> str:
        return self._pages.get(self.url, "<html><body>nothing</body></html>")

    async def current_url(self) -> str:
        return self.url

    async def click(self, ref) -> None:
        self.actions.append(("click", ref.ref))

    async def fill(self, ref, value) -> None:
        self.actions.append(("fill", ref.ref, value))

    async def submit(self) -> None:
        self.actions.append(("submit",))

    async def scroll(self, direction) -> None:
        self.actions.append(("scroll", direction))

    async def go_back(self) -> None:
        self.actions.append(("back",))

    async def screenshot(self) -> str:
        return self.shot

    @property
    def fills(self) -> list[tuple]:
        return [a for a in self.actions if a[0] == "fill"]


class _FakeSession:
    """Stand-in for ``GatedCdpSession``. Records navigations; can move the page."""

    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.navigations: list[str] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def navigate(self, url: str):
        self.navigations.append(url)
        self._page.url = url
        return SimpleNamespace(ok=True, allowed=True, url=url, reason="", error="")


class _Decide:
    """A scripted model that RECORDS every prompt it was given."""

    def __init__(self, *replies: str, fallback: str = "DONE") -> None:
        self.replies = list(replies)
        self.fallback = fallback
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else self.fallback


def _ref_of(html: str, url: str, label: str) -> str:
    extraction = extract_page(html, url=url)
    for form in extraction.forms:
        for field in form.fields:
            if field.label.strip().lower() == label.lower():
                return field.ref
    for element in extraction.links:
        if element.label.strip().lower() == label.lower():
            return element.ref
    raise AssertionError(f"no element labelled {label!r} in the extraction")


def _surfaces(decide: _Decide, result, caplog) -> dict[str, str]:
    """EVERY string a credential could have reached, as one named map.

    Named rather than concatenated so a failure says WHICH surface leaked — "it is in the step
    ledger" and "it is in a log line" have different fixes, and a single blob would report neither.

    The six:
      * ``prompts``     — what the model actually received. The invariant's literal subject.
      * ``steps``       — the recorded action lines, which are re-composed into later prompts.
      * ``notes``       — persisted, and shown to the user.
      * ``payload``     — the ActionResult stdout: what a workflow, the run detail and the API see.
      * ``park``        — the park reason/detail, which reaches the user-facing sentence.
      * ``logs``        — every log record emitted during the run.
    """
    payload = result.to_payload() if hasattr(result, "to_payload") else result
    return {
        "prompts": "\n".join(decide.prompts),
        "steps": "\n".join(f"{s.action} {s.note} {s.verification}" for s in result.steps),
        "notes": "\n".join(result.notes),
        "payload": json.dumps(payload),
        "park": f"{result.park_reason} {result.park_detail}",
        "logs": "\n".join(r.getMessage() for r in caplog.records),
    }


def _assert_absent(surfaces: dict[str, str], secret: str) -> None:
    leaked = sorted(name for name, blob in surfaces.items() if secret in blob)
    assert not leaked, f"{secret!r} reached {leaked}"


def _assert_present(surfaces: dict[str, str], value: str, *, on: str) -> None:
    """The control assertion. Proves the sweep observes the surface it claims to observe."""
    assert (
        value in surfaces[on]
    ), f"{value!r} is NOT on the {on!r} surface, so an absence assertion over it proves nothing"


def _login_run(decide: _Decide, *, start: str = LOGIN_URL, pages: dict[str, str] | None = None):
    page = _FakePage(pages or {LOGIN_URL: LOGIN_HTML, HOME_URL: DASHBOARD_HTML}, url=start)
    session = _FakeSession(page)
    result = _run(
        run_browse_loop(
            goal="sign in and read the balance",
            start_url=start,
            session=session,
            page=page,
            decide=decide,
            max_steps=6,
            settle=_no_settle,
        )
    )
    return result, page, session


# ══════════════════════════════════════════════════════════════════════════════
# CLAUSE 4 (the one that matters most): credentials never transit the agent
# ══════════════════════════════════════════════════════════════════════════════


class TestTheInvariant:
    """The LLM never sees a password, a 2FA code, or a token — on ANY surface."""

    def test_a_password_field_value_never_reaches_any_surface(self, caplog):
        """A password sitting in the DOM as `value="…"` does not enter the run at all.

        The strongest form of the invariant: the value is never READ, so there is no
        representation of it anywhere to redact, log or compose.
        """
        caplog.set_level(logging.DEBUG)
        decide = _Decide("NOTES the sign-in form is here", "DONE")
        result, _page, _session = _login_run(decide)
        surfaces = _surfaces(decide, result, caplog)
        _assert_absent(surfaces, PASSWORD)

    def test_a_password_field_value_never_reaches_any_surface_control(self, caplog):
        """CONTROL for the test above. The ORDINARY field's value IS in the prompt, from the same
        HTML, on the same run. So the sweep is reading a page that really carries field values —
        the password's absence is the screen working, not the harness being empty."""
        caplog.set_level(logging.DEBUG)
        decide = _Decide("NOTES the sign-in form is here", "DONE")
        result, _page, _session = _login_run(decide)
        surfaces = _surfaces(decide, result, caplog)
        _assert_present(surfaces, ORDINARY, on="prompts")

    def test_a_2fa_code_field_is_screened_even_though_its_type_is_text(self, caplog):
        """A one-time-code box is `type=text autocomplete=one-time-code` — masking is not wanted on
        it — so a type-only rule would miss every 2FA field the invariant names."""
        caplog.set_level(logging.DEBUG)
        decide = _Decide("DONE")
        result, _page, _session = _login_run(decide)
        _assert_absent(_surfaces(decide, result, caplog), OTP_CODE)

    def test_the_agent_cannot_type_into_a_credential_field(self, caplog):
        """The second half of the invariant: the agent cannot WRITE a credential either.

        The model emits a TYPE carrying a password. Three things must hold at once: `page.fill` is
        never called (so nothing reaches the DOM or the site), the value appears on no surface (so
        the refusal does not leak by explaining itself), and the run PARKS for a human.
        """
        caplog.set_level(logging.DEBUG)
        ref = _ref_of(LOGIN_HTML, LOGIN_URL, "password")
        decide = _Decide(f"TYPE {ref}({PASSWORD})")
        result, page, _session = _login_run(decide)

        assert page.fills == [], "browse typed into a credential field"
        assert result.parked and result.park_reason == PARK_LOGIN_REQUIRED
        _assert_absent(_surfaces(decide, result, caplog), PASSWORD)

    def test_the_agent_cannot_type_into_a_credential_field_control(self, caplog):
        """CONTROL. The SAME model reply shape against the ORDINARY field DOES fill and DOES record
        its value — so the refusal above is about the field being a credential, not about TYPE
        being broken or the harness dropping every fill."""
        caplog.set_level(logging.DEBUG)
        ref = _ref_of(LOGIN_HTML, LOGIN_URL, "username")
        decide = _Decide(f"TYPE {ref}({ORDINARY})", "DONE")
        result, page, _session = _login_run(decide)

        assert page.fills and page.fills[0][2] == ORDINARY
        assert not (result.parked and result.park_reason == PARK_LOGIN_REQUIRED)
        _assert_present(_surfaces(decide, result, caplog), ORDINARY, on="steps")

    def test_an_oauth_authorization_code_in_the_url_never_reaches_any_surface(self, caplog):
        """The post-login redirect is where a real token lives. The URL reaches the model three
        ways (the outline header, the fence's source, and a link's target) and the user three more
        (the payload, the park sentence, the SEL row) — one screen at the read site covers all six.
        """
        caplog.set_level(logging.DEBUG)
        decide = _Decide("NOTES landed on the callback", "DONE")
        result, _page, _session = _login_run(
            decide,
            start=CALLBACK_URL,
            pages={CALLBACK_URL: DASHBOARD_HTML},
        )
        surfaces = _surfaces(decide, result, caplog)
        _assert_absent(surfaces, OAUTH_CODE)
        # …and the URL is still DIAGNOSABLE: the key survives, and so does the innocent parameter.
        assert "code=" in surfaces["prompts"] and "country=US" in surfaces["prompts"]

    def test_an_implicit_flow_bearer_token_in_the_fragment_never_reaches_any_surface(self, caplog):
        """The fragment matters MORE, not less: it never reaches a server, so it is the one place a
        token is guaranteed to be sitting in the URL of the page the agent just read."""
        caplog.set_level(logging.DEBUG)
        decide = _Decide("DONE")
        result, _page, _session = _login_run(
            decide, start=IMPLICIT_URL, pages={IMPLICIT_URL: DASHBOARD_HTML}
        )
        _assert_absent(_surfaces(decide, result, caplog), BEARER)

    def test_a_credential_in_a_link_FRAGMENT_never_reaches_the_model(self):
        """The live half of the link screen, measured rather than assumed.

        `_clean_link` reduces a link's QUERY to an allowlist, so no token survives there — but it
        passes the FRAGMENT through untouched, and `#access_token=…` is exactly what the OAuth
        implicit flow returns. Driven before this test existed: the token reached the rendered
        target verbatim.
        """
        html = f'<html><body><a href="/r#access_token={BEARER}">Resume</a></body></html>'
        extraction = extract_page(html, url=HOME_URL)
        rendered = render_links_dsl(extraction.links)
        assert BEARER not in rendered
        assert WITHHELD in rendered
        # The REF keeps the real href, which is what the CDP locator matches on first.
        assert BEARER in extraction.links[0].target

    def test_a_credential_in_a_link_QUERY_is_closed_by_the_keep_params_allowlist(self):
        """The other half — and a RAIL on it. `_KEEP_PARAMS` is an allowlist, which is a stronger
        control than this module's denylist, so BA-4 pins the property instead of duplicating it. A
        later session that widens `_clean_link` to preserve query strings breaks THIS test by name
        rather than silently reopening the path."""
        html = f'<html><body><a href="/r?access_token={BEARER}&q=hi">Resume</a></body></html>'
        extraction = extract_page(html, url=HOME_URL)
        assert BEARER not in extraction.links[0].target
        assert "q=hi" in extraction.links[0].target, "the allowlist must still keep what it allows"

    def test_the_refusal_warning_names_the_field_and_never_the_value(self, caplog):
        """A refusal that echoed the value back would defeat itself by explaining itself. The
        warning becomes the NEXT prompt's WARNINGS block, so this is a model-visible surface."""
        caplog.set_level(logging.DEBUG)
        ref = _ref_of(LOGIN_HTML, LOGIN_URL, "password")
        decide = _Decide(f"TYPE {ref}({PASSWORD})")
        result, _page, _session = _login_run(decide)
        recorded = "\n".join(s.action for s in result.steps)
        assert ref in recorded, "the refusal must still name the field, or it is not legible"
        assert PASSWORD not in recorded
        assert WITHHELD in recorded


class TestTheScreenItself:
    """Unit-level properties of the screen, so a failure localises to the mechanism."""

    @pytest.mark.parametrize(
        "itype,name,autocomplete",
        [
            ("password", "pw", ""),
            ("text", "user_password", ""),
            ("text", "otp", ""),
            ("text", "x", "one-time-code"),
            ("text", "x", "current-password"),
            ("text", "verification-code", ""),
            ("textarea", "api_token", ""),
        ],
    )
    def test_credential_inputs_are_recognised(self, itype, name, autocomplete):
        assert is_credential_input(itype, name=name, autocomplete=autocomplete)

    @pytest.mark.parametrize(
        "itype,name",
        [("text", "username"), ("email", "email"), ("text", "postal_code"), ("text", "zip")],
    )
    def test_ordinary_inputs_are_not(self, itype, name):
        """The other direction. `postal_code` is why `code` alone is not a name token: screening it
        would blind the agent to fields it legitimately fills while protecting nothing."""
        assert not is_credential_input(itype, name=name)

    def test_screen_url_is_idempotent(self):
        once = screen_url(CALLBACK_URL)
        assert screen_url(once) == once, "a second pass must not corrupt its own output"

    def test_screen_url_leaves_a_credential_free_url_byte_identical(self):
        plain = "https://x.test/a/b?page=2&sort=asc#top"
        assert screen_url(plain) == plain

    def test_screen_url_keeps_the_csrf_state_parameter(self):
        """`state` is a CSRF nonce, not a credential, and it is the one parameter that makes a
        broken OAuth round trip diagnosable."""
        assert "state=xyz" in screen_url("https://x.test/cb?code=abc&state=xyz")

    def test_screen_action_render_moves_only_the_value(self):
        assert screen_action_render("TYPE ab12cd34(sekrit)", credential=True) == (
            f"TYPE ab12cd34({WITHHELD})"
        )
        assert screen_action_render("CLICK ab12cd34", credential=True) == "CLICK ab12cd34"
        assert screen_action_render("TYPE ab12cd34(x)", credential=False) == "TYPE ab12cd34(x)"


# ══════════════════════════════════════════════════════════════════════════════
# CLAUSE 1: the run PARKS on needs-input
# ══════════════════════════════════════════════════════════════════════════════


class TestThePark:
    def test_the_loop_parks_rather_than_sailing_past_the_login_wall(self):
        ref = _ref_of(LOGIN_HTML, LOGIN_URL, "password")
        decide = _Decide(f"TYPE {ref}({PASSWORD})")
        result, _page, _session = _login_run(decide)
        assert result.parked is True
        assert result.park_reason == PARK_LOGIN_REQUIRED
        assert result.ok is True, "a login wall is not a failure — the notes are the deliverable"

    def test_the_loop_parks_rather_than_sailing_past_the_login_wall_control(self):
        """CONTROL: the same harness, the same page, a NON-credential TYPE. It does NOT park, so
        the park above is caused by the credential field and not by the fixture."""
        ref = _ref_of(LOGIN_HTML, LOGIN_URL, "username")
        decide = _Decide(f"TYPE {ref}({ORDINARY})", "DONE")
        result, _page, _session = _login_run(decide)
        assert result.park_reason != PARK_LOGIN_REQUIRED

    def test_the_provider_projects_the_park_onto_the_shipped_needs_input_gate(self, monkeypatch):
        """`outcome="needs_input"` — the value the engine's action-node dispatch maps to WAITING and
        `workflows/attention.py` projects into the inbox. BA-4 adds a reason, not a second gate."""
        result = _run(
            BrowseActionProvider().execute(
                {"goal": "sign in", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        assert result.success is True
        assert result.outcome == OUTCOME_NEEDS_INPUT

    def test_the_pre_run_check_parks_before_a_single_model_call(self, monkeypatch):
        """§5.3's whole value. A stale session parks at step ZERO — parking on step 14 wastes the
        thirteen steps the user already paid for."""
        calls: list[str] = []

        async def _never(prompt: str) -> str:  # pragma: no cover - must not run
            calls.append(prompt)
            return "DONE"

        import personalclaw.action_providers.browse_provider as bp

        monkeypatch.setattr(bp, "_decide", _never)
        record_login(HOME_URL)
        mark_expired(HOME_URL)

        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read the balance", "start_url": HOME_URL}, ActionContext(event="e")
            )
        )
        assert result.outcome == OUTCOME_NEEDS_INPUT
        assert calls == [], "the pre-run check must park before spending a model call"

    def test_the_park_sentence_is_a_product_surface_not_a_reason_code(self):
        result = _run(
            BrowseActionProvider().execute(
                {"goal": "sign in", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        assert "bank.test" in result.stderr
        assert PARK_LOGIN_REQUIRED not in result.stderr, "a reason code is not a sentence"
        assert "never sees what you type" in result.stderr

    def test_the_needs_input_card_carries_no_field_a_credential_could_occupy(self):
        handoff = request_login(CALLBACK_URL, reason="credential_field", run_id="r1")
        blob = json.dumps(handoff.to_payload())
        assert OAUTH_CODE not in blob
        assert handoff.item["block_kind"]
        assert handoff.item["choices"], "a card with no choices is not answerable"


# ══════════════════════════════════════════════════════════════════════════════
# CLAUSES 2 + 3: the session is persisted, and a later run reuses it
# ══════════════════════════════════════════════════════════════════════════════


class TestProfilePersistenceAndReuse:
    def test_a_fresh_site_has_no_profile(self):
        assert session_state(HOME_URL) == SESSION_ABSENT

    def test_a_recorded_login_makes_the_session_fresh_and_persists_it(self):
        record_login(HOME_URL)
        assert session_state(HOME_URL) == SESSION_FRESH
        meta = load_meta(HOME_URL)
        assert meta is not None and meta.auth_state == AUTH_STATE_ACTIVE
        assert (profile_dir(HOME_URL) / ".meta.json").is_file()

    def test_a_second_run_reuses_the_persisted_profile_without_re_auth(self, monkeypatch):
        """The atom's third clause, end to end at the provider.

        Run 1 hits a sign-in page and parks. The human authenticates (simulated by the same
        `record_login` the provider calls on a completed run — see the module docstring in
        `handoff.py`). Run 2 reads the SAME on-disk profile, finds it fresh, and does NOT park.
        """
        first = _run(
            BrowseActionProvider().execute(
                {"goal": "sign in", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        assert first.outcome == OUTCOME_NEEDS_INPUT, "run 1 must ask for the handoff"

        record_login(LOGIN_URL)  # the human authenticated in the headful window

        # Run 2: no browser configured, so it stops at ERR_BROWSE_NO_TARGET — which is the POINT.
        # Reaching the missing-target error proves it got PAST the pre-run session check, i.e. it
        # reused the profile instead of asking the human again.
        second = _run(
            BrowseActionProvider().execute(
                {"goal": "read the balance", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        assert (
            second.outcome != OUTCOME_NEEDS_INPUT
        ), "run 2 re-authenticated despite a fresh profile"
        assert second.agent_error is not None
        assert second.agent_error.code == "ERR_BROWSE_NO_TARGET"

    def test_a_second_run_re_authenticates_when_persistence_is_broken(self):
        """The falsification partner of the test above, as a permanent test rather than a one-off
        mutation: DELETE the persisted profile between the two runs and the second run parks again.
        Without this, "run 2 did not park" is equally consistent with "the check never runs"."""
        _run(
            BrowseActionProvider().execute(
                {"goal": "sign in", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        record_login(LOGIN_URL)
        (profile_dir(LOGIN_URL) / ".meta.json").unlink()

        again = _run(
            BrowseActionProvider().execute(
                {"goal": "read the balance", "start_url": LOGIN_URL}, ActionContext(event="e")
            )
        )
        assert again.outcome == OUTCOME_NEEDS_INPUT

    def test_an_expired_session_is_recorded_as_such_for_the_banner(self):
        record_login(HOME_URL)
        mark_expired(HOME_URL)
        meta = load_meta(HOME_URL)
        assert meta is not None and meta.auth_state == AUTH_STATE_EXPIRED
        assert session_state(HOME_URL) == SESSION_EXPIRED

    def test_the_ttl_elapsing_expires_the_session(self):
        record_login(HOME_URL, now=1000.0, ttl_secs=60.0)
        assert session_state(HOME_URL, now=1030.0) == SESSION_FRESH
        assert session_state(HOME_URL, now=1090.0) == SESSION_EXPIRED

    def test_a_corrupt_meta_fails_toward_asking_the_human(self):
        record_login(HOME_URL)
        (profile_dir(HOME_URL) / ".meta.json").write_text("{ not json", encoding="utf-8")
        assert session_state(HOME_URL) == SESSION_EXPIRED

    def test_the_meta_file_holds_no_credential_fields(self):
        record_login(f"https://alice:{PASSWORD}@bank.test/x")
        blob = json.dumps(load_meta("https://bank.test/x").to_dict())
        assert PASSWORD not in blob
        assert set(json.loads(blob)) == {
            "site",
            "created_at",
            "last_login_at",
            "session_valid_until",
            "auth_state",
        }

    def test_a_completed_run_records_the_login_it_observed(self, monkeypatch):
        """§5.2's own definition of "authenticated": the run completed, so the session works.

        This is `record_login`'s production caller — without it the mechanism would ship inert and
        the user would be re-prompted on every single run.
        """
        import personalclaw.action_providers.browse_provider as bp
        from personalclaw.browse.loop import BrowseLoopResult

        async def _fake_loop(**kwargs):
            return BrowseLoopResult(ok=True, goal="g", final_url=HOME_URL)

        monkeypatch.setattr(bp, "run_browse_loop", _fake_loop)
        monkeypatch.setattr(
            BrowseActionProvider,
            "_open",
            lambda self, cfg, ctx, cdp_url: _done((object(), object(), None)),
        )
        assert session_state(HOME_URL) == SESSION_ABSENT
        result = _run(
            BrowseActionProvider().execute(
                {"goal": "read the balance", "start_url": HOME_URL, "cdp_url": "ws://x"},
                ActionContext(event="e"),
            )
        )
        assert result.success and result.outcome == ""
        assert session_state(HOME_URL) == SESSION_FRESH, "record_login has no production caller"


def _done(value):
    """A resolved awaitable, for monkeypatching an `async def` with a lambda."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    future.set_result(value)
    return future


class TestTheProfileDirectory:
    def test_the_slug_comes_from_the_host_only(self):
        assert site_slug("https://bank.test/login?x=1") == site_slug("https://bank.test/other")
        assert site_slug("https://bank.test/a") == "bank.test"

    @pytest.mark.parametrize(
        "hostile",
        [
            "https://../../etc/passwd",
            "../../../../etc/passwd",
            "https://bank.test/../../..",
            "file:///etc/shadow",
            "https://%2e%2e%2f%2e%2e/x",
            "",
            "not a url at all",
        ],
    )
    def test_no_url_can_escape_the_profiles_root(self, hostile):
        """Traversal-proof by CONSTRUCTION: every character outside `[a-z0-9.-]` collapses, so
        there is no input for which this resolves outside the root — a stronger statement than
        "the bad ones are rejected"."""
        resolved = profile_dir(hostile).resolve()
        root = profiles_root().resolve()
        assert root in resolved.parents or resolved == root
        assert resolved != root, "an empty slug would share one profile across every bad URL"

    def test_userinfo_never_lands_in_a_directory_name(self):
        assert PASSWORD not in str(profile_dir(f"https://alice:{PASSWORD}@bank.test/x"))

    def test_different_sites_get_different_profiles(self):
        assert profile_dir("https://a.test/") != profile_dir("https://b.test/")

    def test_the_handoff_binds_the_headful_window_to_the_same_profile(self):
        from personalclaw.browse.handoff import chrome_launch_args

        args = chrome_launch_args(LOGIN_URL, headful=True)
        assert f"--user-data-dir={profile_dir(LOGIN_URL)}" in args
        assert not any("headless" in a for a in args), "the human must be able to see the window"
        assert "--disable-blink-features=AutomationControlled" in args

    def test_the_unattended_form_is_headless(self):
        from personalclaw.browse.handoff import chrome_launch_args

        assert "--headless=new" in chrome_launch_args(LOGIN_URL, headful=False)

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://x.test/login", True),
            ("https://x.test/users/sign_in", True),
            ("https://x.test/oauth/authorize", True),
            ("https://x.test/dashboard", False),
            ("https://x.test/search?q=login", False),
        ],
    )
    def test_login_url_detection_reads_the_path_not_the_query(self, url, expected):
        assert looks_like_login_url(url) is expected


class TestTheProfileNeverTravels:
    """§5.1: "never backed up by snapshot/portability (credentials), never exported"."""

    def test_the_profile_root_is_claimed_by_the_state_inventory(self, tmp_path):
        """A path under the home that nobody claims fails `audit_home`, so an unclaimed profile
        directory would report as unmanaged drift the first time anyone browsed."""
        from personalclaw.durability import inventory as inv

        # A dedicated directory, NOT `tmp_path`: the autouse `_isolated_home` fixture already put
        # `home/` there, and auditing it would report that unrelated directory as the unclaimed one
        # — a red that says nothing about the profile path this test is actually about.
        fake_home = tmp_path / "audit-home"
        (fake_home / "browse" / "profiles" / "bank.test").mkdir(parents=True)
        assert inv.audit_home(fake_home).ok

    def test_the_audit_still_catches_an_undeclared_directory(self, tmp_path):
        """CONTROL for the test above. The same audit over the same shape with an UNDECLARED
        directory fails — so the pass above is the `browse` claim working, not `audit_home` being
        vacuous on a tiny tree."""
        from personalclaw.durability import inventory as inv

        fake_home = tmp_path / "audit-home-2"
        (fake_home / "not-a-real-store").mkdir(parents=True)
        assert not inv.audit_home(fake_home).ok

    def test_the_profile_is_ignored_rather_than_a_secret_entry(self):
        """The distinction is load-bearing and easy to get wrong. A `secret=True` entry is excluded
        from EXPORTS but CAPTURED by snapshots on purpose (so a backup can restore the credential
        store). A browser profile holds the cookies that ARE the authentication, and a snapshot is
        restored onto another machine — so it must not travel at all."""
        from personalclaw.durability import inventory as inv

        assert inv.is_ignored("browse")
        assert not any(e.path.split("/")[0] == "browse" for e in inv.INVENTORY)
        # `backup_entries` is the SNAPSHOT projection and `export_entries` the portable one. Both,
        # because the point is that a profile is in NEITHER — a `secret=True` entry would be absent
        # from the second and present in the first.
        assert not any(e.path.startswith("browse") for e in inv.backup_entries())
        assert not any(e.path.startswith("browse") for e in inv.export_entries())
        assert "browse" not in inv.secret_paths()

    def test_an_export_carries_no_profile_bytes(self, _isolated_home):
        """Driven rather than reasoned: build a real export zip over a home holding a profile with
        a recognisable cookie, and assert no member came from it."""
        from personalclaw import portability

        record_login(LOGIN_URL)
        (profile_dir(LOGIN_URL) / "Default").mkdir(parents=True, exist_ok=True)
        (profile_dir(LOGIN_URL) / "Default" / "Cookies").write_text(
            f"session={BEARER}", encoding="utf-8"
        )
        _zip_bytes, manifest = portability.create_export_zip()
        members = [str(m["path"]) for m in manifest["members"]]
        assert not [m for m in members if m.startswith("browse")], members
        assert not [m for m in members if "Cookies" in m]

    def test_an_export_carries_no_profile_bytes_control(self, _isolated_home):
        """CONTROL for the test above: the SAME export DOES carry an ordinary declared store, so
        the empty result is the exclusion working rather than the export being empty."""
        from personalclaw import portability

        (_isolated_home / "config.json").write_text('{"x": 1}', encoding="utf-8")
        _zip_bytes, manifest = portability.create_export_zip()
        members = [str(m["path"]) for m in manifest["members"]]
        assert "config.json" in members, members


# ══════════════════════════════════════════════════════════════════════════════
# BA-5 — the per-site profile-encryption key lives in the credential store,
#        NEVER in the profile dir, and is hidden from the user's vault.
# ══════════════════════════════════════════════════════════════════════════════


class TestTheProfileEncryptionKey:
    def test_a_recorded_login_puts_the_profile_key_in_the_credential_store(self):
        from personalclaw.config.credentials import credential_names

        assert not has_profile_key(HOME_URL)
        record_login(HOME_URL)
        assert has_profile_key(HOME_URL)
        assert profile_key_name(HOME_URL) in credential_names()
        assert profile_key_name(HOME_URL) == "BROWSE_PROFILE_KEY_bank.test"

    def test_the_key_is_generated_once_and_not_rotated(self):
        """Idempotent: a re-login must not rotate the key out from under a profile it encrypts."""
        first = ensure_profile_key(HOME_URL)
        second = ensure_profile_key(HOME_URL)
        assert first and first == second

    def test_the_key_value_is_never_written_into_the_profile_dir(self):
        """The whole point of §5.1: a key that sat beside the cookies it protects protects nothing.
        Build a real profile with a session file, then sweep every byte under the profile dir."""
        key = ensure_profile_key(HOME_URL)
        pdir = profile_dir(HOME_URL)
        (pdir / "Default").mkdir(parents=True, exist_ok=True)
        (pdir / "Default" / "Cookies").write_text("session=abc", encoding="utf-8")
        for path in pdir.rglob("*"):
            if path.is_file():
                assert key not in path.read_text(
                    encoding="utf-8", errors="ignore"
                ), f"the profile key leaked into {path}"

    def test_the_profile_key_is_hidden_from_the_users_secrets_vault(self):
        """It is machine-managed key material, not a secret the user typed — so it must never
        appear as a vault row they could see or DELETE (which would break the profile)."""
        from personalclaw.secrets_vault import is_reserved_key, list_presence

        record_login(HOME_URL)
        assert is_reserved_key(profile_key_name(HOME_URL))
        rows = list_presence()
        assert not any(r.name.startswith("BROWSE_PROFILE_KEY_") for r in rows)

    def test_an_ordinary_secret_still_appears_in_the_vault(self):
        """CONTROL for the test above: the SAME vault read DOES surface an ordinary user secret, so
        the profile key's absence is the exclusion working, not `list_presence` being empty."""
        from personalclaw.config.credentials import save_credential
        from personalclaw.secrets_vault import list_presence

        record_login(HOME_URL)  # also writes the (hidden) profile key
        save_credential("MY_API_TOKEN", "value")
        names = {r.name for r in list_presence()}
        assert "MY_API_TOKEN" in names
        assert not any(n.startswith("BROWSE_PROFILE_KEY_") for n in names)

    def test_expired_sites_reports_key_presence(self):
        record_login(HOME_URL)
        mark_expired(HOME_URL)
        rows = expired_sites()
        row = next((r for r in rows if r["site"] == "bank.test"), None)
        assert row is not None and row["key_present"] is True


# ══════════════════════════════════════════════════════════════════════════════
# BA-5 — an expired session surfaces a persistent banner + a needs_input inbox
#        item, and produces zero failed ticks (the tick stays success=True).
# ══════════════════════════════════════════════════════════════════════════════


class _FakeDashboardState:
    """Records WS frames + notifications; carries no `_inbox_svc`, so `emit_attention_item` falls
    back to the on-disk InboxStore under the isolated home the test then reads."""

    def __init__(self) -> None:
        self.ws: list[tuple] = []

    def broadcast_ws(self, msg_type, data) -> None:
        self.ws.append((msg_type, data))

    def notify(self, kind, title, body, *, meta=None) -> None:
        pass


class TestTheExpiredSurfacing:
    def test_the_expired_park_surfaces_a_banner_and_a_needs_input_item(self):
        """At the auth_state=expired write, BA-5 raises the banner (a `browse_auth_expired` frame)
        and a durable needs_input inbox row — independent of the engine's own attention path, so a
        schedule/hook/manual run surfaces it too. The tick stays success=True (no failed tick)."""
        from personalclaw.inbox import InboxStore
        from personalclaw.inbox_providers.native_source import set_dashboard_state

        record_login(HOME_URL)
        mark_expired(HOME_URL)
        assert session_state(HOME_URL) == SESSION_EXPIRED

        fake = _FakeDashboardState()
        set_dashboard_state(fake)
        try:
            result = _run(
                BrowseActionProvider().execute(
                    {"goal": "read the balance", "start_url": HOME_URL}, ActionContext(event="e")
                )
            )
        finally:
            set_dashboard_state(None)

        # Zero failed ticks: an expired session is needs_input, never a failure.
        assert result.success is True
        assert result.outcome == OUTCOME_NEEDS_INPUT
        # The persistent banner.
        assert any(t == "browse_auth_expired" for t, _ in fake.ws), "no banner broadcast"
        # The durable inbox row.
        store = InboxStore()
        store.load()
        rows = [i for i in store.items.values() if i.refs.get("browse_auth") == "expired"]
        assert rows, "no needs_input inbox row for the expired session"
        assert rows[0].item_kind == "needs_input"

    def test_a_fresh_session_surfaces_nothing(self):
        """CONTROL: a fresh session does NOT hit the expired path, so no banner and no row — the
        surfacing above is the expiry, not something every run does."""
        from personalclaw.inbox_providers.native_source import set_dashboard_state

        record_login(HOME_URL)  # fresh, not expired
        fake = _FakeDashboardState()
        set_dashboard_state(fake)
        try:
            # No CDP target, so it stops at ERR_BROWSE_NO_TARGET — the point is it got PAST the
            # session check without parking on auth, so nothing was surfaced.
            _run(
                BrowseActionProvider().execute(
                    {"goal": "read", "start_url": HOME_URL}, ActionContext(event="e")
                )
            )
        finally:
            set_dashboard_state(None)
        assert not any(t == "browse_auth_expired" for t, _ in fake.ws)
