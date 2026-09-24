"""The Doctor fix seam is two-step-armed AND SEL-audited (PLATFORM-RESILIENCE §2, PR2-4).

PR2-4's done-when requires the ``resilience/fixes.py`` registry to be "SEL-audited behind a
two-step confirm (400 without confirm)". The behaviour existed in code
(``dashboard/handlers/doctor.py:138`` returns 400 when ``confirm`` is absent;
``resilience/fixes.py:apply_fix`` emits a ``doctor_fix:<id>`` tool-invocation to the SEL) but
neither half was pinned by a test on THIS seam — the confirm helper was only test-proven on
three sibling endpoints, and no test asserted the SEL emit on a fix apply. These two tests close
that gap so the security gate cannot silently regress:

* A bare POST (no ``{confirm: true}``) is refused 400 *before* any fix is imported or run.
* A successful ``apply_fix`` emits exactly one SEL ``doctor_fix:<id>`` maintenance entry.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp.test_utils import make_mocked_request

from personalclaw.resilience import fixes


def test_the_fix_endpoint_refuses_a_bare_apply_with_400(monkeypatch):
    """POST /api/doctor/fix/{fix_id} without ``{confirm: true}`` is refused 400 and the fix
    never runs — the two-step armed pattern, so a stray/replayed POST cannot mutate."""
    from personalclaw.dashboard.handlers import doctor as H

    # Doctor is guard-gated; force it ON so we exercise the confirm gate, not the 404.
    monkeypatch.setattr(H, "_resilience_cfg", lambda: SimpleNamespace(doctor_enabled=True))
    # Spy: a bare POST must never reach apply_fix (the 400 returns before it is even imported).
    ran: list[str] = []
    monkeypatch.setattr(
        "personalclaw.resilience.fixes.apply_fix",
        lambda fix_id, **kw: ran.append(fix_id) or {"ok": True, "fix_id": fix_id},
    )

    req = make_mocked_request("POST", "/api/doctor/fix/serving-fs.symlink-repair")  # no JSON body
    resp = asyncio.run(H.api_doctor_fix_apply(req))

    assert resp.status == 400
    assert b"confirm_required" in resp.body
    assert ran == [], "a bare (unconfirmed) POST must not run the fix"


def test_apply_fix_emits_a_sel_audit_entry(monkeypatch):
    """Every fix application is SEL-audited: ``apply_fix`` emits exactly one
    ``doctor_fix:<id>`` maintenance tool-invocation, even for a no-op fix."""
    captured: list[dict] = []

    class _SEL:
        def log_tool_invocation(self, **kw):
            captured.append(kw)

    monkeypatch.setattr("personalclaw.sel.sel", lambda: _SEL())

    fid = "test-only.noop-fix"
    fixes.register_fix(
        fixes.Fix(
            id=fid,
            title="noop",
            impact="none",
            dry_preview=lambda: "would do nothing",
            apply=lambda: "did nothing",
        )
    )
    try:
        result = fixes.apply_fix(fid)
    finally:
        fixes._FIXES.pop(fid, None)  # keep the global registry clean for other tests

    assert result["ok"] is True and result["result"] == "did nothing"
    assert len(captured) == 1, "exactly one SEL entry per fix application"
    entry = captured[0]
    assert entry["tool_name"] == f"doctor_fix:{fid}"
    assert entry["outcome"] == "ok"
    assert entry["tool_kind"] == "maintenance"
