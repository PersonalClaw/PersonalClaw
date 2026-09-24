---
name: check-work
description: Verify what you just claimed, with real tool calls — reconstruct the claims from this session, derive 2-4 executable checks from those specific claims, run every one, and report pass/fail with quoted evidence. A check that cannot be executed is reported unverifiable, never assumed passing.
always: false
triggers: check your work, check this work, verify that, verify this, did that actually work, are you sure, prove it, double check, confirm it works, did it work, is that actually done, self check
---

# Check Work

You just told the user something was done. This skill answers a different question:
**is it?** Not by re-reading your own message — by observing the artifacts your
message claimed exist.

**The one rule:** every check is a tool call or it is `unverifiable`. There is no
third option. You may never report a check as passing because it "should" pass, or
because a related command passed earlier in the session. Ground truth over
self-report — the same doctrine the loop judge runs on.

## Step 1 — Reconstruct the claims (don't re-derive the task)

Read back over **this session's** recent turns and tool calls and write down what was
*claimed complete*, in the claimer's own words. Quote them.

- "Added `derive_checks()` to `src/personalclaw/check_work.py`" → a claim about a file's contents.
- "`make lint` is clean" → a claim about a command's exit status.
- "The endpoint returns the new field" → a claim about a running surface.
- "I'll add tests next" → **not a claim.** Intent isn't a claim; skip it.

Claims come from the transcript, not from the task description. If the task said
five things and the session claimed two, you check the two that were claimed.

## Step 2 — Derive 2-4 checks from *those* claims

Each check must be a real command the user could run and watch pass or fail, and it
must be traceable to a specific claim. **Not a generic checklist** — a checklist that
would read the same for any session is a tell that you skipped step 1.

| Claim shape | Check shape |
|---|---|
| a file was written / edited | `test -e <path>`, then `grep -n '<the thing claimed>' <path>` |
| a command passes | re-run that exact command (`make lint`, `python -m pytest -n 0 --no-cov <file>`) |
| an endpoint answers | call it (`curl -s localhost:<port>/api/...`) and read the body |
| something was deleted / renamed | `grep -rn '<old name>' <tree>` returns nothing |
| an artifact renders | fetch/open it and confirm the expected content is present |

Prefer the **content** form over the existence form: `grep` for the symbol the claim
named beats `test -e`, because an empty file passes `test -e`.

Two is the floor (one check is not a cross-check); four is the ceiling (past that
you're writing a test suite, and the user asked for a check).

Only commands that exist in this repo. If you cannot name a real command for a claim,
that claim is `unverifiable` — say so. Do not invent a script.

## Step 3 — Run them. Actually run them.

Execute each check with a real tool call and keep the output. Then classify:

- **pass** — it ran and the observed output matches the claim. Evidence = the output
  line (`src/personalclaw/check_work.py:196: def derive_checks(`), not "confirmed".
- **fail** — it ran and the output contradicts the claim. Evidence = the contradiction
  (`no such file`, `1 failed`, exit 1, an empty grep).
- **unverifiable** — it could not run here (no dev gateway up, no credentials, needs a
  browser, the path is outside the work root). Evidence = *why*, plus what would make
  it verifiable.

A fail is the useful outcome. Report it plainly and immediately; do not soften it,
re-run it until it passes, or quietly fix the artifact and then report a pass — if you
fix something, say that the first run failed and show both runs.

## Step 4 — Report

```
**Check-work: FAIL**

- [PASS] `src/personalclaw/check_work.py` contains `derive_checks` — `grep -n 'derive_checks' src/personalclaw/check_work.py`
  - evidence: src/personalclaw/check_work.py:196: def derive_checks(claims, ...)
- [FAIL] `tests/test_check_work.py` exists — `test -e tests/test_check_work.py`
  - evidence: no such path (the turn claimed tests were added)
- [UNVERIFIABLE] `curl -s localhost:10000/api/config` — no gateway is running here
  - evidence: connection refused; start `make serve` first and re-run
```

Verdict rules: any fail → **FAIL**. No fail and at least one executed pass → **PASS**.
Nothing executed → **UNVERIFIABLE** — an empty report is never a pass.

If fewer than two checks were derivable, say that in one line ("only one claim in this
session was checkable: …") instead of padding the report.

## Scope — light and immediate

This is the **light** half of verification: seconds, current session, the claims that
were just made. It does not read a spec, walk a whole feature, or replay a run. That
depth belongs to the deep QA companion (see `references/qa-boundary.md`); when a
check-work run keeps failing or the user wants whole-feature assurance, hand off there
rather than growing this skill into it.

Unattended SDLC loops run this same derivation automatically after a stage gate passes
when `loops.check_work_stages` is on — same module, same rules, no self-report.
