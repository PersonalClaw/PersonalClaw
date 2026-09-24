import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ApiError } from '../../lib/api'
import { classifyFailure, CLASSIFY_OPAQUE_FALLBACK } from './classifyFailure'

// ── #3470 The loop launcher relays the server's pointer instead of asking a question ──────
//
// The defect was not a swallow: the failure was terminal and visible and nothing was
// fabricated. It was that the composer overwrote a BETTER sentence with a worse one. The
// backend answers `POST /api/loops/classify` with 409 `model_unresolved` and the remediation
// spelled out, and `submit()` replaced it with "is a model configured?" — a question whose
// answer the server had just supplied, minus the `Settings → Models` pointer.
//
// So this file asserts two independent things, because either alone is satisfiable by the
// defect. §1 is the DECISION (whose sentence wins, and when the written one still must),
// and §2 is the CALL SITE — a correct decision function that nothing calls changes nothing
// a user sees, which is exactly the shape the issue describes.

/** The 409 body `loop_routes.py` actually sends, verbatim from the handler. Hard-coded
 *  rather than imported so a backend reword reds this file instead of silently agreeing
 *  with itself — the same reason `test_no_provider_first_run_rail.py` ports the substrings
 *  into Python. */
const SERVER_SENTENCE
  = "No model provider resolves for use case 'background'. "
  + 'Connect a model in Settings → Models to analyze tasks.'

const SRC = join(__dirname, '..', '..')
const composer = () => readFileSync(join(SRC, 'pages/loop/LoopComposer.tsx'), 'utf8')

describe('§1 a classify refusal keeps the sentence the server authored', () => {
  it('relays the 409 remediation verbatim, pointer included', () => {
    const got = classifyFailure(new ApiError(SERVER_SENTENCE, 409, 'model_unresolved'))
    expect(got).toBe(SERVER_SENTENCE)
    // Named separately from the equality above: the pointer is the whole remediation, and a
    // future "tidy the sentence" change that drops it would still satisfy a looser check.
    expect(got, 'the remediation pointer is the point').toContain('Settings → Models')
  })

  it('does NOT ask the user a question the server already answered', () => {
    const got = classifyFailure(new ApiError(SERVER_SENTENCE, 409, 'model_unresolved'))
    expect(got).not.toContain('is a model configured?')
  })

  it('keeps the written sentence when the rejection says nothing actionable', () => {
    // The closed opaque set in `lib/errText` — the request never reached a backend that
    // could author anything, so there is no server sentence to prefer. Relaying `''` here
    // would paint an empty alert box.
    for (const opaque of ['Failed to fetch', 'Load failed', 'HTTP 500']) {
      expect(classifyFailure(new Error(opaque)), opaque).toBe(CLASSIFY_OPAQUE_FALLBACK)
    }
    expect(classifyFailure(new ApiError('HTTP 409', 409, 'model_unresolved')))
      .toBe(CLASSIFY_OPAQUE_FALLBACK)
  })

  it('never returns an empty string, whatever it is handed', () => {
    for (const e of [null, undefined, '', new Error(''), {}, 0]) {
      expect(classifyFailure(e), String(e)).not.toBe('')
    }
  })
})

describe('§2 the composer actually uses it', () => {
  it('the classify rejection is not discarded', () => {
    // The defect, as source: an argument-less arrow throws the `ApiError` away before the
    // error branch can read it. This is the assertion that reds on `origin/main`.
    //
    // 🪤 `[^)]*` between the two calls reads a FALSE PASS — it cannot cross the `)` in
    // `task.trim()`, so it never reached the `.catch` on main's one-line call and the
    // assertion passed against the very code it was written to catch. Measured: 6 passed
    // where 5 should have. `[^\n]*` keeps it to the one statement without that hole.
    expect(
      composer(),
      'the classify call discards its rejection, so no server sentence can reach the alert',
    ).not.toMatch(/api\.classifyULoop\([^\n]*\.catch\(\(\s*\)\s*=>/)
  })

  it('and hands it to classifyFailure', () => {
    expect(composer()).toMatch(/classifyFailure\(/)
  })

  it('and does not re-type the fallback sentence', () => {
    // How the server's sentence got overwritten in the first place: the copy lived at the
    // call site, so relaying became a choice each branch made separately.
    expect(
      composer(),
      'the fallback belongs to classifyFailure, not to the page',
    ).not.toContain(CLASSIFY_OPAQUE_FALLBACK)
  })

  it('and the call it guards still exists (the vacuity floor)', () => {
    // Without this, §2 goes green the day someone deletes the classify step — the two
    // assertions above are both satisfied by a page that no longer classifies anything.
    const src = composer()
    expect(src, 'nothing left to relay a rejection from').toMatch(/api\.classifyULoop\(/)
    expect(src, 'the error branch still renders somewhere').toMatch(/role="alert"/)
  })
})
