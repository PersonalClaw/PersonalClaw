import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The written sentence lost to the browser's debug string ────────────────────────────────────────
//
// `reportingWrite` and `reportActionFailure` composed `` `Couldn't ${what}: ${e.message}` ``, and on a
// network failure `e.message` is whatever the browser's fetch layer says — Chrome "Failed to fetch",
// Safari "Load failed", Firefox "NetworkError when attempting to fetch resource.". None of those
// passes through `errEnvelope`, so the toast read:
//
//     Couldn't save that: Failed to fetch
//
// A sentence written for a person, a colon, and a developer console string. `HTTP 500` is the same
// shape from the other side: `errEnvelope`'s own deliberate output for a body it refused to show,
// which is honest under an input and useless after a headline that already named what failed.
//
// 🔑 THE FILTER ALREADY EXISTED, WITH ONE CONSUMER. `lib/errText.readableErrText` returns `''` for
// exactly that closed set and its doc describes this call shape: *"Callers with their own written
// fallback do `readableErrText(e) || 'their sentence'`"*. It shipped wired to `ui/ListScaffold` and
// nowhere else, so **63 call sites** routed through `app/reportingWrite` kept printing the raw text —
// and so did `app/ErrorBoundary`, the widest surface in the app.
//
// 🪤 THE COLON IS PART OF THE BUG. `Couldn't ${what}: ` promises a following clause, so suppressing
// the detail while keeping the colon trades a bad sentence for a broken one. Asserted below.
//
// 🪤 THIS DOES NOT TOUCH THE ~150 SITES THAT COMPOSE THEIR OWN `notify()` STRING. Measured: 155
// production uses of the `e instanceof Error ? e.message` idiom, of which these three were the shared
// ones. The rest each carry their own noun and their own voice, so converting them is a per-site copy
// decision, not a mechanical substitution — a different change with a different review. This PR takes
// the three that are shared infrastructure, where one edit is 63 call sites.

const notified: string[] = []
function mockNotify() {
  notified.length = 0
  vi.doMock('./appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (m: string) => { notified.push(m) },
  }))
}

/** Every message the closed set covers, plus the shape errEnvelope itself emits. */
const OPAQUE = ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.', 'HTTP 500']

beforeEach(() => { vi.resetModules() })

describe('a toast never ends in the browser’s debug string', () => {
  it('suppresses every opaque message and closes the sentence with a full stop', async () => {
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    for (const m of OPAQUE) {
      notified.length = 0
      const ok = await reportingWrite('save that', () => Promise.reject(new Error(m)))
      expect(ok, 'the write still reports failure').toBe(false)
      expect(notified).toEqual(["Couldn't save that."])
      expect(notified[0], 'no debug string').not.toContain(m)
      // 🪤 The colon promises a clause. Without a detail there must not be one.
      expect(notified[0], 'and no dangling colon').not.toMatch(/:\s*$/)
    }
  })

  it('an AUTHORED backend message still survives — or this rail is a mute button', async () => {
    // The half that matters most: `readableErrText` is a CLOSED SET precisely so that
    // "name is required" / "evals_disabled" reach the reader. A broad "looks technical" filter would
    // swallow the messages most worth showing.
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    await reportingWrite('save that', () => Promise.reject(new Error('name is required')))
    expect(notified).toEqual(["Couldn't save that: name is required"])
  })

  it('reportActionFailure behaves identically — both forms, one sentence', async () => {
    mockNotify()
    const { reportActionFailure } = await import('./reportingWrite')
    reportActionFailure('refresh this tile')(new Error('Failed to fetch'))
    reportActionFailure('refresh this tile')(new Error('quota exceeded'))
    expect(notified).toEqual(["Couldn't refresh this tile.", "Couldn't refresh this tile: quota exceeded"])
  })

  it('a non-Error rejection does not print "[object Object]" or an empty tail', async () => {
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    await reportingWrite('do that', () => Promise.reject({ weird: true }))
    expect(notified[0]).toBe("Couldn't do that.")
    expect(notified[0]).not.toContain('object')
  })
})

describe('the full-page boundary does not show a minified exception', () => {
  /** Renders the boundary around a child that throws `msg`. */
  async function boundaryWith(msg: string) {
    const { ErrorBoundary } = await import('./ErrorBoundary')
    const { PersonalityProvider } = await import('./personality')
    const Boom = () => { throw new Error(msg) }
    // React logs the caught error; silence it so the run stays readable.
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<PersonalityProvider><ErrorBoundary><Boom /></ErrorBoundary></PersonalityProvider>)
    err.mockRestore()
  }

  beforeEach(() => {
    vi.doMock('./appearance', () => ({ useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }) }))
  })

  it('falls back to its OWN written sentence for an opaque message', async () => {
    await boundaryWith('Failed to fetch')
    expect(screen.getByText('Something went wrong rendering this view.')).toBeInTheDocument()
    expect(screen.queryByText('Failed to fetch'), 'the debug string must not be the page').toBeNull()
  })

  it('still shows an authored message — including the frozen fixture’s', async () => {
    // `errorTreatmentSkin.test.tsx` freezes this surface's markup against a PRE-CHANGE capture whose
    // fixture message is `kaboom`. Authored text passes through `readableErrText` untouched, which is
    // why that rail still matches and did not need re-capturing — asserted here rather than assumed.
    await boundaryWith('kaboom')
    expect(screen.getByText('kaboom')).toBeInTheDocument()
    expect(screen.queryByText('Something went wrong rendering this view.')).toBeNull()
  })
})

describe('the sentence has ONE owner, and the filter is really adopted', () => {
  const SRC = join(process.cwd(), 'src')
  const strip = (s: string) => s
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^(\s*)\/\/.*$/gm, '$1')
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) ? [p] : []
  })
  const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

  it('both exports compose through the same helper, so they cannot drift', () => {
    // This module's own contract: "ONE module owns the sentence in both forms, so the two cannot
    // drift into different wording." Two copies of the new conditional would be that drift.
    const code = codeOf('app/reportingWrite.ts')
    expect([...code.matchAll(/notify\(failureSentence\(what, e\), 'error'\)/g)].length,
      'both exports call the one composer').toBe(2)
    expect([...code.matchAll(/function failureSentence/g)].length, 'defined exactly once').toBe(1)
    expect(code, 'and the raw idiom is gone from the shared path').not.toMatch(/instanceof Error \? \w+\.message/)
  })

  it('the shared error path routes through readableErrText', () => {
    for (const rel of ['app/reportingWrite.ts', 'app/ErrorBoundary.tsx']) {
      expect(codeOf(rel), `${rel} must use the filter`).toMatch(/readableErrText\(/)
    }
  })

  it('VACUITY: readableErrText has more than its original single consumer', () => {
    // It shipped wired to `ui/ListScaffold` alone, which is how 63 call sites kept the raw text.
    // If this ever drops back toward 1, the wiring has been reverted.
    const consumers = walk(SRC).filter((abs) => !/\.(test|doc)\.tsx?$/.test(abs))
      .filter((abs) => {
        const rel = abs.slice(SRC.length + 1)
        if (rel === 'lib/errText.ts') return false
        return /readableErrText\s*\(/.test(strip(readFileSync(abs, 'utf8')))
      })
    expect(consumers.length, 'production consumers of readableErrText').toBeGreaterThanOrEqual(3)
  })
})
