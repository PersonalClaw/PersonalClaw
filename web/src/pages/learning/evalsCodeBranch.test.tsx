import { describe, expect, it, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, ApiError, hasApiCode } from '../../lib/api'
import { errEnvelope, errText } from '../../lib/errText'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { StudiesPanel } from './StudiesPanel'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { AblationPanel } from './AblationPanel'

// ── The typed error code the funnel was throwing away, and the six branches that waited ──────
//
// `lib/errText.ts` parsed `{"error": {"code", "message"}}` and kept only the sentence. `ApiError`
// carried only `.status`. So four learning panels matched their code against the human copy
// (`error.message.includes('evals_disabled')`) — and no message ever contains its own code.
//
// DRIVEN on a real gateway (`--seed demo-home`, port 10784, viewport 1440×1000 and 390×844),
// `#/learning`, with the four `/api/evals/*` responses read off the wire:
//
//   evals.enabled = false  → all four answer 404 `evals_disabled`
//     BEFORE  4 × red role="alert" "Couldn't load your judge benchmark / studies / retrieval
//             benchmark / ablation report", each with a Retry that cannot ever succeed
//     AFTER   4 × "The eval substrate is off, so no <x> can run. Turn it on with
//             `personalclaw config set evals.enabled true`." — no Retry
//
//   evals.enabled = true, nothing run yet → 404 `judge_bench_absent` / `retrieval_absent` /
//   `ablation_absent` (studies answers 200 `{"studies": []}`)
//     BEFORE  3 × the same red "Couldn't load your …" + dead Retry
//     AFTER   3 × the panel's own run command as guidance
//
// 🪤 THE FIXTURE IS THE DEFECT. Every existing test for these branches passed by rejecting with
// `new Error('ablation_absent')` — a "message" that IS the code, which nothing on the wire ever
// sends. `includes()` matched the test's own invention. So every body below is verbatim from
// `curl`, and each case asserts the vacuity floor: the message does NOT contain the code, so the
// old prose predicate is proven to have been false for exactly these responses.
//
// 🔑 AND THE ONE COPY THAT EXISTED FOR THIS STATE WAS WRONG. `AblationPanel`'s `evals_disabled`
// branch said "Turn on `evals.enabled` in Settings" and linked `#/settings`. `evals.enabled` is
// in no `_EDITABLE_CONFIG` entry and on no settings surface — the link led to a page with no such
// control. Being unreachable is what kept that invisible.

/** Verbatim `curl http://127.0.0.1:10784/api/evals/…` bodies. Do not paraphrase these. */
const WIRE = {
  judge_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish benchmark results."}}',
  studies_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish study results."}}',
  retrieval_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish retrieval ablation reports."}}',
  ablation_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish ablation reports."}}',
  judge_absent: '{"error": {"code": "judge_bench_absent", "message": "No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one."}}',
  retrieval_absent: '{"error": {"code": "retrieval_absent", "message": "No retrieval benchmark has run yet. Run `personalclaw retrieval-eval` to score both stores."}}',
  ablation_absent: '{"error": {"code": "ablation_absent", "message": "No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `personalclaw ablation --force`."}}',
} as const

const res = (body: string, status = 404) =>
  new Response(body, { status, headers: { 'Content-Type': 'application/json' } })

describe('errEnvelope keeps BOTH halves of the platform envelope', () => {
  it('lifts the code off every real /api/evals body', async () => {
    for (const [name, body] of Object.entries(WIRE)) {
      const { code, message } = await errEnvelope(res(body))
      expect(code, `${name} must carry a code`).toMatch(/^[a-z][a-z0-9_]*$/)
      // THE VACUITY FLOOR, and the whole defect: the sentence never contains the code, so
      // `message.includes(code)` — what all four panels used — was false for every response.
      expect(message.includes(code), `${name}: prose must not contain its own code`).toBe(false)
    }
  })

  it('leaves the human sentence byte-identical to errText', async () => {
    // This ADDS a field. Nothing a user reads, or a screen reader speaks, may move.
    for (const body of Object.values(WIRE)) {
      const { message } = await errEnvelope(res(body))
      expect(message).toBe(await errText(res(body)))
    }
    // Including every shape `errText.test.ts` pins, so the funnel's other rules are untouched.
    for (const [body, want] of [
      ['{"error": "name is required"}', 'name is required'],
      ['{"detail": "theme store is read-only"}', 'theme store is read-only'],
      ['{"error": {"code": "x"}}', 'HTTP 404'],
      ['{"error": {"code": 7}}', 'HTTP 404'],
      ['', 'HTTP 404'],
    ] as const) {
      expect(await errEnvelope(res(body)).then((e) => e.message)).toBe(want)
      expect(await errText(res(body))).toBe(want)
    }
  })

  it('still lifts a code even when the message is unusable', async () => {
    // A code-only envelope is not a sentence for a user (the status stands), but it IS a fact
    // for a caller. Dropping it here would resurrect the bug for any route that stops writing
    // a message.
    const { code, message } = await errEnvelope(res('{"error": {"code": "evals_disabled"}}'))
    expect(code).toBe('evals_disabled')
    expect(message).toBe('HTTP 404')
  })

  it('reports no code for the 239 bare-string sites', async () => {
    expect((await errEnvelope(res('{"error": "disk full"}'))).code).toBe('')
    expect((await errEnvelope(res('nginx is down', 502))).code).toBe('')
  })
})

describe('the api client hands the code to its callers', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('populates ApiError.code from the envelope, through the real request helper', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res(WIRE.judge_disabled)))
    const err = await api.judgeBench().then(() => null, (e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('evals_disabled')
    expect((err as ApiError).status).toBe(404)
    // `.message` is the contract 200+ `catch(e => e.message)` call sites depend on.
    expect((err as ApiError).message)
      .toBe('The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.')
  })

  it('populates it on the DELETE helper too, which throws its own ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res('{"error": {"code": "pack_not_installed", "message": "No such installed pack."}}')))
    const err = await api.deleteSnippet('x').then(() => null, (e: unknown) => e)
    expect(hasApiCode(err, 'pack_not_installed')).toBe(true)
  })

  it('leaves .code empty rather than guessing when the body carries none', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res('{"error": "the gateway sentence"}', 500)))
    const err = await api.judgeBench().then(() => null, (e: unknown) => e)
    expect((err as ApiError).code).toBe('')
    // And `hasApiCode` must not treat "" as a wildcard.
    expect(hasApiCode(err, 'evals_disabled')).toBe(false)
  })

  it('hasApiCode refuses a bare Error and a look-alike message', () => {
    expect(hasApiCode(new Error('evals_disabled'), 'evals_disabled')).toBe(false)
    expect(hasApiCode(new ApiError('evals_disabled', 404), 'evals_disabled')).toBe(false)
    expect(hasApiCode(new ApiError('off', 404, 'evals_disabled'), 'evals_disabled')).toBe(true)
    expect(hasApiCode(undefined, 'evals_disabled')).toBe(false)
  })
})

/** Build the rejection the panel really receives: the wire body, through the real funnel. */
async function wireError(body: string): Promise<ApiError> {
  const { message, code } = await errEnvelope(res(body))
  return new ApiError(message, 404, code)
}

describe('all four eval panels say "the substrate is off" — and say it alike', () => {
  const CASES = [
    { name: 'judge tiers', body: WIRE.judge_disabled, what: 'judge benchmark', el: (e: unknown) => <JudgeBenchPanel bench={undefined} error={e} onRetry={() => {}} /> },
    { name: 'template studies', body: WIRE.studies_disabled, what: 'study', el: (e: unknown) => <StudiesPanel studies={undefined} error={e} onRetry={() => {}} /> },
    { name: 'retrieval', body: WIRE.retrieval_disabled, what: 'retrieval benchmark', el: (e: unknown) => <RetrievalBenchPanel bench={undefined} error={e} onRetry={() => {}} /> },
    { name: 'ablation', body: WIRE.ablation_disabled, what: 'ablation', el: (e: unknown) => <AblationPanel view={undefined} error={e} onRetry={() => {}} /> },
  ] as const

  for (const c of CASES) {
    it(`${c.name}: guidance, one instruction, and no Retry`, async () => {
      render(c.el(await wireError(c.body)))
      expect(screen.getByText(new RegExp(`no ${c.what} can run`))).toBeTruthy()
      expect(screen.getByText('personalclaw config set evals.enabled true')).toBeTruthy()
      // A switch that is off does not flip because the fetch is repeated.
      expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
      // Nor is this a failure: `LoadError`'s role="alert" would announce a decided answer as
      // unrequested bad news.
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.queryByText(/Couldn't load your/)).toBeNull()
    })
  }

  it('does NOT send the user to Settings, which has no such control', async () => {
    // The sentence that used to ship here. `evals.enabled` is in no `_EDITABLE_CONFIG` entry
    // and on no settings surface, so the link was a dead end.
    for (const c of CASES) {
      const { unmount } = render(c.el(await wireError(c.body)))
      expect(screen.queryByRole('link', { name: /Settings/ })).toBeNull()
      expect(document.querySelector('a[href="#/settings"]')).toBeNull()
      unmount()
    }
  })

  it('keeps its LABELLED section, so the panel does not vanish from the page', async () => {
    // Asked of the accessibility tree rather than of the copy: the section must still exist and
    // its `aria-labelledby` must still resolve to a name. `LoadError` replaces the whole section
    // — including the heading — so before this a user scanning #/learning could not tell which
    // four things had failed apart from the sentence inside each red block.
    for (const [c, id] of [
      [CASES[0], 'judge-bench-heading'], [CASES[1], 'studies-heading'],
      [CASES[2], 'retrieval-bench-heading'], [CASES[3], 'ablation-heading'],
    ] as const) {
      const { unmount } = render(c.el(await wireError(c.body)))
      expect(document.querySelector(`section[aria-labelledby="${id}"]`), `${c.name} keeps its section`).not.toBeNull()
      expect(document.getElementById(id)?.textContent?.trim(), `${c.name} keeps its name`).toBeTruthy()
      unmount()
    }
  })
})

describe('and the "nothing has run yet" branches, which were inert for the same reason', () => {
  it('judge tiers names its run command', async () => {
    render(<JudgeBenchPanel bench={undefined} error={await wireError(WIRE.judge_absent)} onRetry={() => {}} />)
    expect(screen.getByText('personalclaw judge-bench')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('retrieval names its run command AND still offers the label card', async () => {
    render(<RetrievalBenchPanel bench={undefined} error={await wireError(WIRE.retrieval_absent)} onRetry={() => {}} />)
    expect(screen.getByText('personalclaw retrieval-eval')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('ablation points at the registry — the place the OFF state must not mention', async () => {
    render(<AblationPanel view={undefined} error={await wireError(WIRE.ablation_absent)} onRetry={() => {}} />)
    expect(screen.getByText('evals/ablation_registry.json')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('the OFF state does not leak any of those three next steps', async () => {
    const { unmount } = render(<AblationPanel view={undefined} error={await wireError(WIRE.ablation_disabled)} onRetry={() => {}} />)
    expect(screen.queryByText(/ablation_registry/)).toBeNull()
    unmount()
    render(<JudgeBenchPanel bench={undefined} error={await wireError(WIRE.judge_disabled)} onRetry={() => {}} />)
    expect(screen.queryByText(/personalclaw judge-bench/)).toBeNull()
  })
})

describe('a real failure is still a failure', () => {
  const GENERIC = new ApiError('The benchmark artifacts could not be read.', 500, 'judge_bench_unreadable')

  it('an unrecognised code renders LoadError, with the Retry that belongs there', () => {
    render(<JudgeBenchPanel bench={undefined} error={GENERIC} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
    expect(screen.getByText(/Couldn't load your judge benchmark/)).toBeTruthy()
    // The confusion the panels exist to refuse: a broken read reading as an empty one.
    expect(screen.queryByText(/personalclaw judge-bench/)).toBeNull()
    expect(screen.queryByText(/substrate is off/)).toBeNull()
  })

  it('a codeless network blip does too', () => {
    render(<AblationPanel view={undefined} error={new Error('Failed to fetch')} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })

  it('an EMPTY study register is a 200, not this branch', () => {
    // 🪤 `study_absent` used to be OR'd into `StudiesPanel`'s predicate. `api_evals_studies`
    // cannot return it — it belongs to `/api/evals/studies/{id}` — so that arm was unreachable
    // twice over. The empty register arrives as `{"studies": []}` and renders the register's own
    // empty state, which must NOT be what the OFF state says.
    render(<StudiesPanel studies={[]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No study has been registered/)).toBeTruthy()
    expect(screen.queryByText(/substrate is off/)).toBeNull()
  })
})

describe('one owner for the predicate', () => {
  it('no panel re-derives a code from prose', async () => {
    const { readFileSync, readdirSync } = await import('node:fs')
    const { join } = await import('node:path')
    const dir = join(process.cwd(), 'src', 'pages', 'learning')
    const offenders = readdirSync(dir)
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
      .filter((f) => /\.message\b[^\n]*\.includes\(|includes\(['"](?:evals_disabled|[a-z_]+_absent)['"]\)/
        .test(readFileSync(join(dir, f), 'utf8')))
    // It was FOUR files with a private copy of the same wrong predicate. `hasApiCode` is the
    // one home; a fifth copy would reintroduce the bug in a place a fix would not reach.
    expect(offenders, 'match on ApiError.code via hasApiCode, never on the message').toEqual([])
  })
})
