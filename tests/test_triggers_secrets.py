"""`{{secret:KEY}}` in a trigger's action config (§7 item 6 / decision 11 — S115).

🔴 THE DEFECT. Workflows have carried this form since WF2-R14 — the validator REJECTS an inline
credential and tells the author to use `{{secret:KEY}}`, and three surfaces say so in their error
text. A TRIGGER action did not resolve it. Driven before a line was written:

    bash action, command "echo tok={{secret:MY_KEY}}"
      → stdout: tok={{secret:MY_KEY}}       # the literal placeholder reached the shell

So a user following the documented pattern got a broken command, and the only way to make a trigger
authenticate was to paste the credential into `triggers.json` — a file snapshotted (S113), echoed
into run records, and rendered in the UI. The guidance and the mechanism disagreed, and the
mechanism won.

Every test injects its resolver, so none of them touches the real credential store.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from personalclaw.triggers import secrets as S

_FAKE = {"MY_KEY": "sk-abc123", "TOK": "t0k"}


def _resolver(key: str) -> str:
    return _FAKE.get(key, "")


# ── finding the references ──


def test_it_finds_a_reference_in_a_plain_string():
    assert S.references("echo {{secret:MY_KEY}}") == ["MY_KEY"]


def test_it_walks_nested_config():
    """An action config NESTS — `{"headers": {"Authorization": "Bearer {{secret:X}}"}}` is the
    shape a webhook action actually has, so a top-level-only scan would miss the common case."""
    config = {
        "headers": {"Authorization": "Bearer {{secret:TOK}}"},
        "args": ["--key", "{{secret:MY_KEY}}"],
        "retries": 3,
    }
    assert S.references(config) == ["TOK", "MY_KEY"]


def test_the_order_is_stable_and_deduplicated():
    """Stable so an error message and a doctor finding name keys the same way twice."""
    assert S.references(["{{secret:B}}", "{{secret:A}}", "{{secret:B}}"]) == ["B", "A"]


def test_no_reference_is_an_empty_list():
    assert S.references({"command": "echo hi", "n": 5}) == []


def test_a_non_string_leaf_is_not_scanned():
    assert S.references({"timeout": 600, "flag": True, "nothing": None}) == []


# ── resolving ──


def test_a_whole_string_reference_yields_the_raw_value():
    """A field that IS a token — the value must not be wrapped or stringified."""
    assert S.resolve("{{secret:MY_KEY}}", resolver=_resolver) == "sk-abc123"


def test_an_embedded_reference_is_substituted_in_place():
    """The other half of the contract: building a header, not carrying a field."""
    assert S.resolve("Bearer {{secret:TOK}}", resolver=_resolver) == "Bearer t0k"


def test_padded_braces_resolve():
    """A user who pads the braces means the same thing; failing on whitespace is the kind of silent
    near-miss that sends someone back to pasting the credential inline."""
    assert S.resolve("{{ secret:MY_KEY }}", resolver=_resolver) == "sk-abc123"


def test_nested_config_resolves_throughout():
    resolved = S.resolve(
        {"headers": {"Authorization": "Bearer {{secret:TOK}}"}, "args": ["{{secret:MY_KEY}}"]},
        resolver=_resolver,
    )
    assert resolved == {"headers": {"Authorization": "Bearer t0k"}, "args": ["sk-abc123"]}


def test_two_references_in_one_string_both_resolve():
    assert S.resolve("{{secret:MY_KEY}}:{{secret:TOK}}", resolver=_resolver) == "sk-abc123:t0k"


def test_a_config_with_no_references_is_returned_UNCHANGED():
    """Same object, not a copy: the overwhelmingly common case must cost one regex scan."""
    config = {"command": "echo hi"}
    assert S.resolve(config, resolver=_resolver) is config


def test_non_string_leaves_survive_resolution():
    resolved = S.resolve(
        {"timeout": 600, "flag": True, "key": "{{secret:TOK}}", "none": None},
        resolver=_resolver,
    )
    assert resolved == {"timeout": 600, "flag": True, "key": "t0k", "none": None}


# ── the refusal ──


def test_an_unresolved_key_RAISES_rather_than_substituting_empty():
    """🔴 THE CONTROL. Substituting "" would run `curl -H "Authorization: Bearer "` — a request
    that fails remotely with a 401 the user cannot trace to a missing credential."""
    with pytest.raises(S.UnresolvedSecret) as ei:
        S.resolve({"command": "echo {{secret:NOPE}}"}, resolver=_resolver)
    assert ei.value.key == "NOPE"


def test_the_refusal_NAMES_the_key():
    """A bare "a secret is missing" leaves the user checking every credential they have."""
    with pytest.raises(S.UnresolvedSecret) as ei:
        S.resolve("{{secret:STRIPE_KEY}}", resolver=_resolver)
    assert "STRIPE_KEY" in str(ei.value)
    assert "personalclaw auth" in str(ei.value), "and says how to fix it"


def test_one_missing_key_refuses_the_WHOLE_config():
    """A partially-resolved config would dispatch an action with a live token in one field and a
    placeholder in another — worse than not firing, because it half-works."""
    with pytest.raises(S.UnresolvedSecret):
        S.resolve(
            {"a": "{{secret:MY_KEY}}", "b": "{{secret:MISSING}}"},
            resolver=_resolver,
        )


def test_a_resolver_returning_empty_is_treated_as_missing():
    """The store returns "" for a descriptor with no value configured, which is indistinguishable
    from absent at the point of use — both mean "cannot authenticate"."""
    with pytest.raises(S.UnresolvedSecret):
        S.resolve("{{secret:MY_KEY}}", resolver=lambda k: "")


# ── the default resolver's contract ──


def test_the_default_resolver_returns_empty_for_an_unknown_key(tmp_path, monkeypatch):
    """Mirrors `workflows.controller._secret_resolver`: the same store and the same
    empty-on-missing contract, so a key resolves identically for a workflow and a trigger."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    assert S.default_resolver("DEFINITELY_NOT_SET") == ""


def test_an_unreadable_store_is_a_missing_secret_not_a_crash(tmp_path, monkeypatch):
    """A fire must not die on a corrupt credential file — it must refuse legibly, which the
    `UnresolvedSecret` path above does."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    with patch("personalclaw.llm.credentials.CredentialStore", side_effect=OSError("unreadable")):
        assert S.default_resolver("ANY") == ""


# ── the fire path ──


def _orch():
    from personalclaw.gateway import GatewayOrchestrator

    return object.__new__(GatewayOrchestrator)


def _trigger(command: str, tid: str = "clock:x"):
    return types.SimpleNamespace(
        id=tid,
        workflow={"inline": {"provider": "bash", "config": {"command": command}}},
    )


class _Recorder:
    def __init__(self) -> None:
        self.seen: dict = {}

    async def execute(self, config, ctx, timeout=30):
        self.seen = dict(config)
        return types.SimpleNamespace(success=True)


def test_a_fired_trigger_receives_the_RESOLVED_config():
    """🔴 The end-to-end contract. Driven through the real `_fire_store_trigger`."""
    import asyncio

    import personalclaw.action_providers as AP

    rec = _Recorder()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: rec
        with patch.object(S, "default_resolver", lambda k: "sk-RESOLVED"):
            asyncio.run(
                _orch()._fire_store_trigger(
                    _trigger("echo {{secret:MY_KEY}}"), {"trigger_id": "clock:x"}
                )
            )
    finally:
        AP.get_action_provider = real
    assert rec.seen["command"] == "echo sk-RESOLVED"


def test_an_unresolved_secret_means_the_provider_is_NEVER_CALLED():
    """Refusing before dispatch is the point: a provider that received a literal `{{secret:...}}`
    would send it to a shell, an HTTP header, or a model prompt."""
    import asyncio

    import personalclaw.action_providers as AP

    rec = _Recorder()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: rec
        with patch.object(S, "default_resolver", lambda k: ""):
            asyncio.run(
                _orch()._fire_store_trigger(
                    _trigger("echo {{secret:MISSING}}"), {"trigger_id": "clock:y"}
                )
            )
    finally:
        AP.get_action_provider = real
    assert rec.seen == {}, "the action must not run with an unresolved credential"


def test_the_stored_config_is_never_mutated():
    """🔴 Resolution is at DISPATCH, never at save — that is what keeps the secret out of
    `triggers.json`, out of every snapshot (S113), and out of the run record."""
    import asyncio

    import personalclaw.action_providers as AP

    trigger = _trigger("echo {{secret:MY_KEY}}")
    rec = _Recorder()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: rec
        with patch.object(S, "default_resolver", lambda k: "sk-RESOLVED"):
            asyncio.run(_orch()._fire_store_trigger(trigger, {"trigger_id": "clock:x"}))
    finally:
        AP.get_action_provider = real
    stored = trigger.workflow["inline"]["config"]["command"]
    assert stored == "echo {{secret:MY_KEY}}", "the placeholder must survive on the trigger"


def test_the_workflow_engine_and_the_trigger_path_share_the_credential_store():
    """One key must mean one thing product-wide. Asserted on the source, because the property is
    that both call `CredentialStore(config_dir()).resolve(...)` — not that they share a function."""
    import inspect

    from personalclaw.workflows import controller

    wf = inspect.getsource(controller._secret_resolver)
    tr = inspect.getsource(S.default_resolver)
    for needle in ("CredentialStore", "config_dir()", "cred.secret or"):
        assert needle in wf and needle in tr, needle


# ── the inline-credential lint on the store (S115) ──


def _row(tmp_path, config, *, tid="clock:x"):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"inline": {"provider": "bash", "config": config}},
        )
    )
    return store.get(tid)


def test_a_credential_shaped_literal_is_FLAGGED(tmp_path):
    """🔴 Measured: the workflow lint flags this exact string as an inline secret, and a TRIGGER
    stored it with `ok: True` and zero issues. The two surfaces disagreed about the same mistake, so
    the advice the workflow validator gives was unenforced for the automation half."""
    row = _row(
        tmp_path, {"command": "curl -H 'Authorization: Bearer sk-ant-api03-REALTOKEN123456'"}
    )
    assert [i.message for i in row.warnings], "a pasted token must be visible on the row"
    assert "secret:KEY" in row.warnings[0].message, "and must name the fix"


def test_a_secret_named_field_holding_a_literal_is_FLAGGED(tmp_path):
    """The second independent signal: a secret-NAMED key with a literal value, which no credential
    regex would catch on its own."""
    row = _row(tmp_path, {"api_key": "literal-value-here"})
    assert any("api_key" in i.message for i in row.warnings)


def test_the_SANCTIONED_form_is_not_flagged(tmp_path):
    """The fix for a finding must never trip the finding again."""
    row = _row(tmp_path, {"command": "curl -H 'Authorization: Bearer {{secret:MY_KEY}}'"})
    assert not row.warnings


def test_a_clean_action_is_not_flagged(tmp_path):
    row = _row(tmp_path, {"command": "echo hello"})
    assert not row.warnings


def test_the_flag_is_a_WARNING_so_the_trigger_still_FIRES(tmp_path):
    """Refusing would break every automation a user already has with a token pasted in — exactly the
    population that most needs to keep working while they migrate."""
    row = _row(
        tmp_path, {"command": "curl -H 'Authorization: Bearer sk-ant-api03-REALTOKEN123456'"}
    )
    assert row.ok is True
    assert row.trigger.enabled is True
    assert not row.errors


def test_the_lint_never_breaks_a_parse(tmp_path):
    """A lint that cannot import, or that raises, must not fail the row it was inspecting — a
    validation helper taking a store offline would be worse than the leak it looks for."""
    from personalclaw.triggers import models as M

    with patch(
        "personalclaw.workflows.secrets.find_inline_secrets", side_effect=RuntimeError("boom")
    ):
        assert M._inline_credential_issues({"inline": {"config": {"api_key": "x"}}}) == []


def test_a_non_dict_workflow_is_ignored(tmp_path):
    from personalclaw.triggers import models as M

    assert M._inline_credential_issues(None) == []
    assert M._inline_credential_issues("not a dict") == []


# ── the webhook token_ref lint (decision 12 — S119) ──


def _webhook(tmp_path, token_ref, *, tid="webhook:deploy"):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="webhook",
            spec={"token_ref": token_ref},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid)


def test_a_VERBATIM_webhook_token_is_flagged(tmp_path):
    """🔴 MEASURED. Decision 12 says webhook bearer tokens are "SHA-256-hashed at rest" and R14 says
    "never verbatim in triggers.json". The store wrote `sk-LITERAL-SECRET-abc123` straight to disk
    with `ok: True` and ZERO warnings.

    S115's lint would have caught that string — but it scans the `workflow` only, and a webhook's
    token lives in `spec`. So the one field on the one kind whose entire purpose is authentication
    was the field with no credential lint.
    """
    row = _webhook(tmp_path, "sk-LITERAL-SECRET-abc123")
    assert [i.message for i in row.warnings], "a pasted token must be visible on the row"
    assert "{{secret:KEY}}" in row.warnings[0].message, "and must name the fix"


def test_the_token_is_still_ON_DISK_so_the_fix_says_ROTATE(tmp_path):
    """The lint cannot un-leak it. A warning that only says "use a reference next time" would leave
    the user believing the exposure was handled, so the doctor's fix says to rotate."""
    from personalclaw.triggers.calendar import diagnose

    _webhook(tmp_path, "sk-LITERAL-SECRET-abc123")
    rows = [{"id": "schedule:webhook:deploy", "spec": {"token_ref": "sk-LITERAL-SECRET-abc123"}}]
    finding = next(
        f
        for f in diagnose(rows, known_workflows=None).findings
        if f.code == "verbatim_webhook_token"
    )
    assert "rotate" in finding.fix


def test_the_SANCTIONED_reference_is_not_flagged(tmp_path):
    """The fix for a finding must never trip the finding again."""
    assert not _webhook(tmp_path, "{{secret:DEPLOY_TOKEN}}").warnings


def test_a_padded_reference_is_not_flagged(tmp_path):
    """`{{ secret:X }}` means the same thing — matching `resolve()`'s own tolerance, or the lint
    would send someone back to pasting the token."""
    assert not _webhook(tmp_path, "{{ secret:DEPLOY_TOKEN }}").warnings


def test_the_flag_is_a_WARNING_so_the_webhook_still_LOADS(tmp_path):
    """Refusing would break every webhook a user has already authored — the population that most
    needs to keep working while they migrate."""
    row = _webhook(tmp_path, "sk-LITERAL-SECRET-abc123")
    assert row.ok is True
    assert not row.errors


def test_a_MISSING_token_ref_is_still_an_ERROR_not_a_warning(tmp_path):
    """The pre-existing control must survive: an unauthenticated fire endpoint is a different and
    worse thing than a badly-stored token, so it errors rather than warns."""
    row = _webhook(tmp_path, "", tid="webhook:open")
    assert [i.path for i in row.errors] == ["spec.token_ref"]


def test_a_non_webhook_kind_is_untouched(tmp_path):
    """`token_ref` is only meaningful on `webhook`; scanning every spec would flag unrelated fields
    named like secrets on kinds that have no token at all."""
    from personalclaw.triggers.models import _token_ref_issues

    assert _token_ref_issues({"kind": "interval", "interval_secs": 60}) == []
    assert _token_ref_issues(None) == []
