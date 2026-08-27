import { afterEach, describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, ApiError } from '../../lib/api'
import { errEnvelope } from '../../lib/errText'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { StudiesPanel } from './StudiesPanel'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { AblationPanel } from './AblationPanel'

// ── SIX BRANCHES THAT COULD NEVER FIRE, AND THE FIELD THAT WAS PARSED AND DISCARDED ───────
//
// `errText` reaches into the platform's declared envelope, `{"error": {"code", "message"}}`, to
// lift the sentence out — and returned only the sentence. `ApiError` carried only `.status`. So
// four learning panels re-derived the code from the copy, e.g. `AblationPanel`:
//
//     // Matched on the backend's stable `code`, not on prose:
//     return text.includes(code)          ← text is error.MESSAGE
//
// The comment states the intention exactly, and the line does the opposite: it searches the
// human sentence for the machine code. Those two strings do not overlap.
//
// MEASURED on a live gateway (port 10784, PERSONALCLAW_HOME=/tmp/wave1-ux-blast-radius, seeded
// `demo-home`, evals off by default), driving `#/learning` at 1440×900:
//
//   $ curl /api/evals/judge-bench
//   404 {"error": {"code": "evals_disabled",
//                  "message": "The eval substrate is off. Turn on `evals.enabled` to publish
//                              benchmark results."}}
//
// The message contains `evals.enabled`; the code is `evals_disabled`. `.includes()` misses, and
// SIX branches — `evals_disabled` (×2 panels), `judge_bench_absent`, `study_absent`,
// `ablation_absent`, `retrieval_absent` — had never once run in production.
//
//   BEFORE (all four panels, evals off)       AFTER
//   ─────────────────────────────────────     ────────────────────────────────────────────────
//   "Couldn't load your judge benchmark"      "The eval substrate is off, so nothing here has
//   "Couldn't load your studies"               been measured. Turn it on with `personalclaw
//   "Couldn't load your retrieval benchmark"   config set evals.enabled true` to <what it
//   "Couldn't load your ablation report"       measures>."
//   + 4 Retry buttons, each re-fetching       + 0 Retry buttons — a config switch is not a
//     a 404 forever                            transient failure and re-asking cannot help
//
// 🪤 THE FIXTURES WERE THE REASON THIS LOOKED TESTED. `AblationPanel.test.tsx` already asserted
// the `evals_disabled` rendering — and passed, because its fixture was `new Error('evals_disabled')`,
// whose MESSAGE is the code. The test proved `.includes()` works on a string containing the
// needle; it never proved the branch fires on what the server sends. Fifteen such fixtures were
// converted to `ApiError` carrying the real sentence, so a regression to prose-matching now goes
// red instead of green.
//
// This file asserts the two halves that make the branches reachable: `errEnvelope` reports the
// code, and each panel keys on it while ignoring a message that merely mentions it.

const res = (body: string, status = 404) =>
  new Response(body, { status, headers: { 'Content-Type': 'application/json' } })

const envelope = (code: string, message: string) =>
  res(JSON.stringify({ error: { code, message } }))

/** The four codes, with the sentence the gateway actually pairs each one with. Copied from
 *  `handlers/evals.py` — note that no message contains its own code, which is the whole defect. */
const OFF = 'The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.'

describe('the envelope reports the code beside the sentence', () => {
  it('lifts BOTH fields out of the declared envelope', async () => {
    expect(await errEnvelope(envelope('evals_disabled', OFF)))
      .toEqual({ code: 'evals_disabled', message: OFF })
  })

  it('the message it returns does NOT contain the code — the defect, asserted', async () => {
    // If this ever becomes false the old `.includes()` match would start working by accident,
    // and the next reader would conclude prose-matching is fine. It is not: this is a coincidence
    // the copy is free to break at any time.
    const { message, code } = await errEnvelope(envelope('evals_disabled', OFF))
    expect(message.includes(code)).toBe(false)
  })

  it('reports a code even when the message is unusable, and still says HTTP <status>', async () => {
    // Two independent questions. A code-only body is not a sentence (unchanged behaviour: the
    // user hears the status), but a CALLER can still branch on it. Collapsing the two is what
    // made the field invisible in the first place.
    expect(await errEnvelope(res('{"error": {"code": "evals_disabled"}}', 502)))
      .toEqual({ code: 'evals_disabled', message: 'HTTP 502' })
  })

  it('is `code: ""` for every shape that carries none', async () => {
    for (const body of ['{"error": "disk full"}', '{"detail": "read-only"}', '', '<html>x</html>']) {
      expect((await errEnvelope(res(body, 500))).code, body).toBe('')
    }
    // A non-string code is not a code. `{"error": {"code": 7}}` must not become "7".
    expect((await errEnvelope(res('{"error": {"code": 7}}', 500))).code).toBe('')
  })

  it('errText is unchanged — it is the envelope minus the code', async () => {
    // The one-line contract of this change: it ADDS a field. Asserted structurally so a future
    // edit cannot quietly give `errText` a second body-reading pass (a `Response` body can only
    // be read once, so two readers is not a style question — the second gets '').
    const src = readFileSync(join(process.cwd(), 'src', 'lib', 'errText.ts'), 'utf8')
    expect(src).toMatch(/export async function errText\(r: Response\): Promise<string> \{\s*return \(await errEnvelope\(r\)\)\.message/)
    // `await r.text()`, not `r.text()` — the latter also matches the prose above the function,
    // and a scanner that counts comments measures the documentation, not the code.
    expect((src.match(/await r\.text\(\)/g) ?? []).length, 'exactly one body read').toBe(1)
  })
})

describe('every eval panel keys on ApiError.code, not on the sentence', () => {
  /** The message deliberately MENTIONS a different code than the one carried. A panel that reads
   *  the message picks the wrong branch; a panel that reads `.code` cannot. */
  const decoy = (code: string) =>
    new ApiError('nothing has run yet — see judge_bench_absent, study_absent, ablation_absent, retrieval_absent', 404, code)

  it('JudgeBenchPanel: off → the switch, absent → the command, neither offers Retry', () => {
    const { unmount } = render(
      <JudgeBenchPanel bench={undefined} error={decoy('evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw config set evals.enabled true/)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
    unmount()
    render(<JudgeBenchPanel bench={undefined} error={decoy('judge_bench_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw judge-bench/)).toBeTruthy()
    expect(screen.queryByText(/config set evals.enabled/)).toBeNull()
  })

  it('StudiesPanel: off is NOT empty — two 404s, two answers', () => {
    const { unmount } = render(
      <StudiesPanel studies={undefined} error={decoy('evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw config set evals.enabled true/)).toBeTruthy()
    // The regression this closes: the panel used to fold `evals_disabled` into the empty state
    // and tell a user with the substrate off that no study had been REGISTERED — true, and
    // useless, because registering one would not have helped.
    expect(screen.queryByText(/No study has been registered/)).toBeNull()
    unmount()
    render(<StudiesPanel studies={undefined} error={decoy('study_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/No study has been registered/)).toBeTruthy()
    expect(screen.queryByText(/config set evals.enabled/)).toBeNull()
  })

  it('RetrievalBenchPanel: off withholds the labelling card, absent offers it', () => {
    const { unmount } = render(
      <RetrievalBenchPanel bench={undefined} error={decoy('evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw config set evals.enabled true/)).toBeTruthy()
    // `POST /api/evals/retrieval/labels` is behind the SAME switch, so the one action the
    // absent-state offers would 404. An offer that cannot be taken is worse than none.
    expect(screen.queryByText(/personalclaw retrieval-eval/)).toBeNull()
    unmount()
    render(<RetrievalBenchPanel bench={undefined} error={decoy('retrieval_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw retrieval-eval/)).toBeTruthy()
  })

  it('AblationPanel: off → the switch, absent → the registry', () => {
    const { unmount } = render(
      <AblationPanel view={undefined} error={decoy('evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw config set evals.enabled true/)).toBeTruthy()
    expect(screen.queryByText(/ablation_registry.json/)).toBeNull()
    unmount()
    render(<AblationPanel view={undefined} error={decoy('ablation_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/ablation_registry.json/)).toBeTruthy()
  })

  it('a plain Error with the code as its MESSAGE no longer fires any branch', () => {
    // The precise false green that hid this for four panels: the old predicate matched a fixture
    // nobody's server sends. A bare Error carries no code, so it is now a real failure — which is
    // what it is — and `LoadError` (with its Retry) is the right rendering for it.
    render(<AblationPanel view={undefined} error={new Error('evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/Couldn't load your ablation report/)).toBeTruthy()
    expect(screen.queryByText(/config set evals.enabled/)).toBeNull()
  })

  it('an unknown code falls through to the honest failure, it does not match by prefix', () => {
    render(<AblationPanel view={undefined} error={new ApiError('boom', 500, 'ablation_unreadable')} onRetry={() => {}} />)
    expect(screen.getByText(/Couldn't load your ablation report/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })
})

describe('the code reaches the panels because every thrower populates it', () => {
  const API = readFileSync(join(process.cwd(), 'src', 'lib', 'api.ts'), 'utf8')

  afterEach(() => { vi.unstubAllGlobals() })

  /** The SEAM, driven end to end — the only assertion here that fails on behaviour rather than on
   *  text. Everything above proves `errEnvelope` reports a code and a panel branches on one; this
   *  proves the request helper carries it BETWEEN them, which is the link that did not exist. The
   *  body is the verbatim 404 measured from the live gateway.
   *
   *  A structural "one construction site" rail cannot cover this: dropping the third argument at
   *  that one site leaves a `code: ''` that every `e.code === '…'` guard reads as a clean "not that
   *  case", so the panels quietly return to their generic failure with no test able to tell. */
  it('a real `api` call rejects with the code the gateway sent', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res(JSON.stringify({
      error: { code: 'evals_disabled', message: OFF },
    }))))
    const err = await api.judgeBench().then(() => null, (e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('evals_disabled')
    expect((err as ApiError).status).toBe(404)
    // And the sentence is byte-identical to what `errText` returned before this change.
    expect((err as ApiError).message).toBe(OFF)
    // Which is exactly what the panel needs: the same rejection, rendered.
    render(<JudgeBenchPanel bench={undefined} error={err} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw config set evals.enabled true/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('no `new ApiError` outside the single constructor site', () => {
    // A helper that hand-rolls `new ApiError(await errText(r), r.status)` would ship a
    // permanently code-less error for its routes, and `e.code === '…'` reads that as a clean
    // "not that case" — a miss indistinguishable from a correct non-match. Measured before this
    // change: EIGHT such sites.
    const sites = (API.match(/new ApiError\(/g) ?? []).length
    expect(sites, 'only `apiError()` may construct one').toBe(1)
    // And that one site takes BOTH fields from the envelope. Asserted as three separate lines
    // rather than one multi-line regex: a `[\s\S]*` window is not a scope, and would keep
    // matching if the pieces drifted into unrelated functions.
    expect(API).toMatch(/async function apiError\(r: Response\): Promise<ApiError> \{/)
    expect(API).toMatch(/const \{ message, code \} = await errEnvelope\(r\)/)
    expect(API).toMatch(/return new ApiError\(message, r\.status, code\)/)
  })

  it('every !ok thrower goes through it', () => {
    expect((API.match(/throw await apiError\(r\)/g) ?? []).length).toBeGreaterThanOrEqual(8)
  })
})

describe('the codes this branches on are the ones the backend declares', () => {
  const PY = readFileSync(
    join(process.cwd(), '..', 'src', 'personalclaw', 'dashboard', 'handlers', 'evals.py'), 'utf8')

  it('all five codes exist in handlers/evals.py', () => {
    // A code renamed on the server silently re-inerts the branch. This is the cheap half of that
    // guard; the expensive half is that the panels now read a field the server actually sends.
    for (const code of ['evals_disabled', 'judge_bench_absent', 'study_absent',
      'ablation_absent', 'retrieval_absent']) {
      expect(PY, `${code} must still be minted`).toContain(`"${code}"`)
    }
  })

  it('and no eval message contains its own code — the reason prose-matching cannot work', () => {
    // The structural statement of the measured finding. `evals.enabled` (the config key, in the
    // copy) is not `evals_disabled` (the code), and one character of drift is all it takes.
    expect(PY).toContain('Turn on `evals.enabled`')
    expect(PY).not.toContain('`evals_disabled`')
  })
})
