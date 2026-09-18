"""A dismissed Discover tip has to be a real tip (issue 317).

``POST /api/legibility/discover/dismiss`` validated that ``id`` was *present* and never that
it was a tip. Measured against `origin/main` by calling the real write path:

    dismiss("chat")                     -> ['chat']
    dismiss("definitely-not-a-tip")     -> ['chat', 'definitely-not-a-tip']
    dismiss("<script>alert(1)</script>") -> stored verbatim
    dismiss("x" * 200_000)              -> a 200 KB entity_settings/legibility.json

Two things the filed report did not measure:

* **One request, not an accumulation.** The API app's ceiling is the single-POST threshold
  plus 16 MB, so a single dismissal could leave megabytes in that file — which
  :func:`load_dismissed` re-reads and re-parses on *every* Discover request.
* **A non-catalog id is inert.** With all of that junk dismissed, ``select_visible`` still
  returned the other nine tips: it only ever compares against catalog ids. So default-deny
  costs exactly nothing here — no id it refuses could ever have done anything.

ARCC was queried first (input validation is an explicit trigger domain). SAX-04 Outcome 1
is the direction taken: *"only explicitly allowed inputs should pass validation"*, with
*"using blacklist approaches instead of allowlist (default-deny) validation"* named as a
pitfall, and Outcome 2 adds validating at the earliest point and logging the failures. The
valid set here is closed, known and ten entries long, so the allowlist IS the catalog — a
format regex would be the weaker half of that guidance for no gain.

The 400 is safe as UX and is not asserted here: the frontend already wraps this call in
``reportingWrite('dismiss that tip', …)``, pinned by
``web/src/pages/chat/dismissalFailureReported.test.ts``, so a refusal surfaces as an error
rather than an X that appears to work — and it can never fire from the UI at all, whose ids
come from the GET payload, i.e. from the catalog. Re-asserting it here would red two files
for one reason.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import legibility as handler
from personalclaw.legibility import discover as dc

NOT_ENGAGED: dict[str, bool] = {}

JUNK = (
    "definitely-not-a-tip",
    "<script>alert(1)</script>",
    "x" * 200_000,
    "Chat",  # case matters; ids are stable slugs
    "../../etc/passwd",
    "chat\nchat",
)
#: Explicit ASCII ids, for the reason `NUL_IDS` in
#: ``tests/test_path_validators_refuse_rather_than_raise.py`` spells out (#2720). Without them
#: pytest derives each id from the param string, so the 200_000-char entry above becomes a
#: ~200 KB node id. MEASURED 2026-09-18: 200,043 chars on one console line under `--verbose`
#: (which ``pyproject.toml`` addopts sets) and 200,393 bytes in the `--junitxml` report, twice
#: over — once per decorator below. `-q` hides it on the sharded CI legs but NOT in the
#: `coverage` job (`.github/workflows/full.yml`), which runs the full suite without `-q`, nor in
#: any local run. An AST scan of all 947 parametrize decorators on main found this the only
#: remaining un-`ids=`'d case over 500 chars; the two already-fixed siblings are at 100k.
JUNK_IDS = (
    "not-a-tip",
    "script-tag",
    "200k-chars",
    "wrong-case",
    "path-traversal",
    "embedded-newline",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The established fixture for this file's store (see `test_discover.py`)."""
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    return tmp_path


def _stored(home: Path) -> list[str] | None:
    path = home / "entity_settings" / "legibility.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["dismissed_discover_tips"]


async def _post(body: object) -> tuple[int, dict]:
    """Drive the real handler, so the refusal is measured as the 400 a client sees."""
    req = make_mocked_request("POST", "/api/legibility/discover/dismiss")

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    resp = await handler.api_discover_dismiss(req)
    return resp.status, json.loads(resp.text or "{}")


# ── the refusal ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("junk", JUNK, ids=JUNK_IDS)
def test_an_id_outside_the_catalog_is_not_persisted(junk: str, home: Path):
    """🔑 The defect itself, at the layer that owns the write."""
    with pytest.raises(dc.UnknownTipError):
        dc.dismiss(junk)
    assert _stored(home) is None, "a refused dismissal still wrote the settings file"


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", JUNK, ids=JUNK_IDS)
async def test_the_endpoint_refuses_it_with_400_and_stores_nothing(junk: str, home: Path):
    """The issue's own ask: reject with 400, and leave the stored list unchanged."""
    dc.dismiss("tasks")  # a real prior dismissal, so "unchanged" means something
    before = _stored(home)

    status, body = await _post({"id": junk})

    assert status == 400
    assert body == {"error": "unknown tip id"}
    assert _stored(home) == before, "the refused id changed the stored list"


@pytest.mark.asyncio
async def test_surrounding_whitespace_is_stripped_rather_than_refused(home: Path):
    """The two layers deliberately differ, and this pins which does what.

    The handler strips before validating, so `" chat "` is the real id `chat` and is
    dismissed. `dismiss` itself does NOT strip — it takes the id as given and answers
    against the catalog, so the same string reaching the store directly is refused. Normalize
    at the door, compare exactly at the store; a store that quietly normalized would be a
    second place where "what counts as this id" is decided.
    """
    status, body = await _post({"id": " chat "})
    assert (status, body) == (200, {"ok": True, "dismissed": ["chat"]})
    with pytest.raises(dc.UnknownTipError):
        dc.dismiss(" chat ")


@pytest.mark.asyncio
async def test_a_real_tip_is_still_dismissed(home: Path):
    """Vacuity floor. A fix that refused everything would pass every test above."""
    status, body = await _post({"id": "chat"})
    assert status == 200
    assert body == {"ok": True, "dismissed": ["chat"]}
    assert _stored(home) == ["chat"]


@pytest.mark.asyncio
async def test_every_catalog_id_is_dismissible(home: Path):
    """Each of the ten, through the endpoint — the allowlist is derived from the catalog, so
    a tip added above must not need a second edit here to become dismissable."""
    for tip in dc.CATALOG:
        status, _ = await _post({"id": tip.id})
        assert status == 200, tip.id
    assert set(_stored(home) or []) == dc.TIP_IDS


@pytest.mark.asyncio
async def test_the_existing_shape_checks_still_answer_first(home: Path):
    """The pre-existing 400s are unchanged — an empty id is still "id is required", not
    "unknown tip id", because "you sent nothing" and "you sent a thing that isn't a tip"
    are different answers to the caller."""
    assert (await _post({"id": ""}))[1] == {"error": "id is required"}
    assert (await _post([1, 2]))[1] == {"error": "Body must be a JSON object"}
    assert _stored(home) is None


# ── the prune: junk already on disk ──────────────────────────────────────────────────────


def test_a_dismissal_prunes_junk_an_older_build_wrote(home: Path):
    """The half of the issue a 400 alone does not fix: *"grows unboundedly with junk that
    can never be cleared through the UI"*. Anyone who ran the reproduction — or any older
    build — already has junk on disk, and nothing removes it.

    An idempotent backfill on the write path, not a migration: the next dismissal cleans it.
    """
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(
        "legibility",
        {"dismissed_discover_tips": ["<script>alert(1)</script>", "chat", "junk", "x" * 5000]},
    )

    assert dc.dismiss("tasks") == {"chat", "tasks"}
    assert _stored(home) == ["chat", "tasks"], "junk survived a write"


def test_the_reader_does_not_carry_junk_even_before_a_write(home: Path):
    """`load_dismissed` narrows too, so a home with junk reads clean immediately rather than
    waiting for the user's next dismissal. Its contract is "the set of dismissed tip ids",
    and junk is not a tip id."""
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings("legibility", {"dismissed_discover_tips": ["junk", "chat"]})
    assert dc.load_dismissed() == {"chat"}


def test_a_non_list_field_still_reads_as_empty(home: Path):
    """Pre-existing tolerance for a hand-mangled file, preserved."""
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings("legibility", {"dismissed_discover_tips": "chat"})
    assert dc.load_dismissed() == set()


# ── the rails ────────────────────────────────────────────────────────────────────────────


def test_the_allowlist_is_the_catalog_not_a_copy_of_it():
    """🪤 The pair that would go stale. `TIP_IDS` must stay *derived* from `CATALOG`: a
    hand-written second list of these ten strings would reject a newly added tip — the
    inverse of this bug, and just as silent, since the UI would offer an X that 400s."""
    assert dc.TIP_IDS == {tip.id for tip in dc.CATALOG}
    assert len(dc.TIP_IDS) == len(dc.CATALOG), "two catalog entries share an id"


def test_no_id_the_writer_accepts_is_inert():
    """🪤 What stops this class returning: **a dismissal that cannot hide anything must not
    be persisted.**

    That is the property the bug violated, stated without reference to validation. Every id
    `dismiss` accepts removes exactly one tip from the visible set; anything else is junk in
    a file that is re-read on every Discover request and that no UI can clear. A future
    author who widens what the writer takes has to break this to do it.
    """
    baseline = {tip.id for tip in dc.select_visible(dismissed=set(), engaged=NOT_ENGAGED)}
    for tip_id in sorted(dc.TIP_IDS):
        after = {tip.id for tip in dc.select_visible(dismissed={tip_id}, engaged=NOT_ENGAGED)}
        assert baseline - after == {tip_id}, f"dismissing {tip_id!r} hid the wrong thing"


def test_running_it_twice_is_still_idempotent(home: Path):
    """Unchanged behaviour, asserted because the prune rewrites the list every time."""
    assert dc.dismiss("chat") == {"chat"}
    assert dc.dismiss("chat") == {"chat"}
    assert _stored(home) == ["chat"]


def test_every_async_test_in_this_file_carries_the_asyncio_marker():
    """🪤 A vacuity floor, because the failure it guards is invisible.

    pytest-asyncio runs in STRICT mode here (this repo sets no `asyncio_mode`), so an async
    test with no marker is not an error — it is skipped with a warning and counted as a pass.
    Every endpoint-level assertion in this file is async, so a dropped marker would silently
    delete the coverage of the actual 400.
    """
    module = sys.modules[__name__]
    unmarked = [
        name
        for name, fn in vars(module).items()
        if name.startswith("test_")
        and inspect.iscoroutinefunction(fn)
        and "asyncio" not in {m.name for m in getattr(fn, "pytestmark", [])}
    ]
    assert unmarked == [], f"async tests that would silently not run: {unmarked}"
