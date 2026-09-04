"""A server-generated redaction mask must never be persisted over real content.

Measured (issue 440): `GET /api/prompts/{name}` redacts credential-shaped strings, the edit
form seeds its draft from that response and sends it back verbatim, and the save path had no
inverse — so saving a prompt after ANY edit, even a title-only one, replaced the stored body
with `[REDACTED: credential]`. Prompts keep no version history, so the original was gone:

    on disk       ->  key: sk-ant-api03-AAAA…LLLL
    GET returns   ->  key: [REDACTED: credential]
    PUT it back   ->  {"ok": true}
    on disk NOW   ->  [REDACTED: credential]

The fix is an inverse on the WRITE path, not a raw read: the plaintext moves store -> store and
is never lifted onto the wire, so the read path keeps masking unconditionally.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import (
    api_prompt_detail,
    api_prompt_save,
    api_snippet_detail,
    api_snippet_save,
)
from personalclaw.security import _mask_pairs, redact_for_display, restore_masked_spans

# A credential-shaped literal the redactor recognises. Not a real key.
SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_PROMPT_SEED", "1")


@pytest.fixture(autouse=True)
def _mock_sel(monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: MagicMock())


def _req(name=None, body=None):
    r = MagicMock()
    if name is not None:
        r.match_info = {"name": name}
    r.query = {}

    async def _json():
        if body is None:
            raise ValueError("no body")
        return body

    r.json = _json
    return r


def _payload(resp):
    return json.loads(resp.body.decode())


# ── the inverse itself ─────────────────────────────────────────────────────────────────────


def test_the_mask_is_recognised_and_the_secret_restored():
    stored = f"key: {SECRET}\nrest of the body"
    masked = redact_for_display(stored)
    assert MASK in masked and SECRET not in masked, "precondition: the redactor masks this"
    assert restore_masked_spans(masked, stored) == stored


def test_an_edit_elsewhere_in_the_body_still_lands():
    # The whole point: the user's real edit must survive alongside the restore.
    stored = f"key: {SECRET}\nold line"
    submitted = redact_for_display(stored).replace("old line", "new line")
    assert restore_masked_spans(submitted, stored) == f"key: {SECRET}\nnew line"


def test_every_mask_in_a_multi_secret_body_is_restored_in_order():
    first, second = SECRET, SECRET.replace("A", "D")
    stored = f"a: {first}\nb: {second}\ntail"
    restored = restore_masked_spans(redact_for_display(stored), stored)
    assert restored == stored
    assert restored.index(first) < restored.index(second)


def test_a_mask_at_the_very_end_of_the_body_is_restored():
    # The span has no following literal to bound it — the walk must run to the end instead.
    stored = f"token={SECRET}"
    assert restore_masked_spans(redact_for_display(stored), stored) == stored


def test_content_with_no_mask_is_returned_untouched():
    # Vacuity guard: an ordinary prompt must not be rewritten by this path at all.
    assert restore_masked_spans("plain body", "plain body") == "plain body"
    assert restore_masked_spans("edited body", "plain body") == "edited body"


def test_a_user_typed_marker_is_not_replaced_with_a_secret():
    # Nothing in the stored value is masked, so mask-looking text is the user's own writing.
    stored = "an ordinary prompt"
    submitted = f"an ordinary prompt mentioning {MASK} literally"
    assert restore_masked_spans(submitted, stored) == submitted


def test_deleting_the_masked_line_deletes_the_secret():
    # Removing the mask is a real instruction: the secret must NOT be resurrected.
    stored = f"key: {SECRET}\nkeep me"
    submitted = "keep me"
    assert restore_masked_spans(submitted, stored) == "keep me"


def test_an_extra_marker_beyond_the_stored_secrets_is_left_alone():
    stored = f"key: {SECRET}"
    submitted = f"key: {MASK}\nand another {MASK}"
    restored = restore_masked_spans(submitted, stored)
    assert restored == f"key: {SECRET}\nand another {MASK}"


def test_a_marker_kept_in_a_rewritten_body_still_carries_its_value():
    # A mask is a PLACEHOLDER for the n-th hidden value, so keeping the marker keeps the value
    # even when everything around it changed. Refusing here would be the data-loss branch
    # wearing a different hat: the user asked to keep it.
    stored = f"key: {SECRET}\ntail"
    assert (
        restore_masked_spans(f"totally rewritten {MASK}", stored) == f"totally rewritten {SECRET}"
    )


def test_the_walk_gives_up_instead_of_guessing_when_the_store_does_not_line_up():
    # The fail-closed seam: if the literal segments around a mask are not present in the stored
    # value, nothing can be said about what the mask replaced.
    assert _mask_pairs(f"prefix {MASK} tail", "a completely different body") is None
    assert _mask_pairs(f"key: {MASK}\ntail", f"key: {SECRET}\ntail") == [(MASK, SECRET)]


# ── the endpoints ──────────────────────────────────────────────────────────────────────────


def _seed(provider, name, content, snippet=False):
    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate

    if snippet:
        provider.create_snippet(PromptSnippet.from_dict({"name": name, "content": content}))
    else:
        provider.create_prompt(
            PromptTemplate.from_dict(
                {"name": name, "kind": "user", "title": "T", "content": content}
            )
        )


def _provider():
    from personalclaw.dashboard.handlers.prompts import _get_default_prompt_provider

    return _get_default_prompt_provider()


def test_prompt_round_trip_does_not_destroy_the_stored_secret():
    prov = _provider()
    stored = f"key: {SECRET}\nbody"
    _seed(prov, "zz-redact", stored)

    got = _payload(asyncio.run(api_prompt_detail(_req(name="zz-redact"))))
    assert MASK in got["content"] and SECRET not in got["content"], "the read still masks"

    # The form sends the redacted draft back with only the title changed.
    resp = asyncio.run(
        api_prompt_save(
            _req(
                name="zz-redact",
                body={
                    "name": "zz-redact",
                    "kind": "user",
                    "title": "New",
                    "content": got["content"],
                },
            )
        )
    )
    assert resp.status == 200
    assert prov.get_prompt("zz-redact").content == stored, "one save must not rewrite the body"
    assert prov.get_prompt("zz-redact").title == "New", "and the real edit still lands"


def test_snippet_round_trip_does_not_destroy_the_stored_secret():
    prov = _provider()
    stored = f"key: {SECRET}\nsnippet body"
    _seed(prov, "zz-snip", stored, snippet=True)

    got = _payload(asyncio.run(api_snippet_detail(_req(name="zz-snip"))))
    assert MASK in got["content"]

    resp = asyncio.run(
        api_snippet_save(_req(name="zz-snip", body={"name": "zz-snip", "content": got["content"]}))
    )
    assert resp.status == 200
    assert prov.get_snippet("zz-snip").content == stored


def test_a_genuine_content_edit_is_still_persisted():
    # Vacuity guard at the endpoint: editing a prompt with no secret in it works as before.
    prov = _provider()
    _seed(prov, "zz-plain", "original body")
    asyncio.run(
        api_prompt_save(
            _req(
                name="zz-plain",
                body={
                    "name": "zz-plain",
                    "kind": "user",
                    "title": "T",
                    "content": "rewritten body",
                },
            )
        )
    )
    assert prov.get_prompt("zz-plain").content == "rewritten body"


def test_an_unrecoverable_mask_is_refused_instead_of_persisting_the_mask(monkeypatch):
    # The handler's fail-closed wiring, with the unrecoverable verdict injected: reaching it for
    # real needs a stored value the redactor's own output cannot be walked back to.
    prov = _provider()
    stored = f"key: {SECRET}\ntail"
    _seed(prov, "zz-conflict", stored)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.prompts.restore_masked_spans",
        lambda submitted, stored_content: None,
    )
    resp = asyncio.run(
        api_prompt_save(
            _req(
                name="zz-conflict",
                body={
                    "name": "zz-conflict",
                    "kind": "user",
                    "title": "T",
                    "content": f"rewritten {MASK}",
                },
            )
        )
    )
    assert resp.status == 409
    assert "cannot be recovered" in _payload(resp)["error"]
    assert prov.get_prompt("zz-conflict").content == stored, "the store is untouched by a refusal"


def test_saving_an_unknown_prompt_still_reports_not_found():
    # The restore must not swallow the 404 branch by reading a missing prompt first.
    resp = asyncio.run(
        api_prompt_save(
            _req(name="nope", body={"name": "nope", "kind": "user", "title": "T", "content": "x"})
        )
    )
    assert resp.status == 404
