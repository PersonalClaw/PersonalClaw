import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── On a health surface, silence is a claim ──────────────────────────────────────────────────────
//
// The dashboard's SystemHealth strip surfaces its Doctor row ONLY when something is wrong — its own
// comment says so: *"surfaces only when something needs attention; a healthy system stays quiet"*.
// That is a fine rule, and it is exactly why `catch(() => {})` on the probe was not: `doctor` was
// `null` both when nothing had been polled yet AND when the probe failed, so **an unreachable health
// check rendered identically to a clean bill of health**.
//
// `#/settings/doctor` already got this right, and its comment states the doctrine this cycle
// converges the summary surfaces onto:
//
//   > `role="alert"`: on a HEALTH surface, "we could not probe" is unrequested bad news that
//   > changes what the screen means — the same reason `LoadError` announces.
//
// Three readers of `api.doctor()`; the owning panel spoke, the two summaries went quiet. Same for
// `api.incident()`, whose settings card is the one place that says whether unattended work is
// suspended — it rendered a BLANK card body on a failed read, because the swallowed rejection
// resolved the fetcher and `loading` went false with nothing to show.
//
// 🪤 Tone is deliberately NOT danger on the unknown row: we do not know that anything is wrong, only
// that we could not look. Claiming a fault we have not measured is the mirror image of this defect.

const boom = () => Promise.reject(new Error('probe unreachable'))
const okReport = { ok: true, core_ok: true, worst: '', capabilities: {} }
const sickReport = { ok: false, core_ok: true, worst: 'memory', capabilities: { memory: { ok: false } } }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve(okReport),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ tips: [] }),
      // Every slice DashboardLive polls on mount — an unmocked one throws before the widget renders.
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ entries: [] }),
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the dashboard health strip cannot mistake "could not probe" for "healthy"', () => {
  async function mountStrip() {
    const { DashboardLiveProvider } = await import('./DashboardLive')
    const { SystemHealth } = await import('./widgets/SystemHealth')
    render(
      <DashboardLiveProvider>
        <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
      </DashboardLiveProvider>,
    )
  }

  it('says the health is unknown when the probe cannot be read', async () => {
    mockApi({ doctor: boom })
    await mountStrip()
    const row = await waitFor(() => screen.getByTitle(/health probe could not be read/i))
    expect(row.textContent, 'names the state, not a fault we did not measure').toMatch(/Health unknown/)
    expect(screen.queryByText(/degraded|Core failing/), 'no invented fault').toBeNull()
  })

  it('stays quiet when the probe says everything is healthy', async () => {
    mockApi({})
    await mountStrip()
    // The strip's rule is preserved: healthy is quiet. Asserted so the fix cannot become noise.
    await waitFor(() => expect(screen.queryByText(/Health unknown/)).toBeNull())
    expect(screen.queryByText(/degraded/)).toBeNull()
  })

  it('still reports a real degradation as a fault', async () => {
    mockApi({ doctor: () => Promise.resolve(sickReport) })
    await mountStrip()
    await waitFor(() => expect(screen.getByText(/degraded/)).toBeInTheDocument())
    expect(screen.queryByText(/Health unknown/), 'a measured fault is not "unknown"').toBeNull()
  })
})

describe('the settings health and safety cards say when they could not check', () => {
  const src = () => readFileSync(join(process.cwd(), 'src/pages/settings/settingsWidgets.tsx'), 'utf8')
  const code = () => src().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  /** The whole `useQuery(<key>, …)` call, paren-matched — not a prefix. */
  const registration = (c: string, key: string) => {
    const at = c.indexOf(`useQuery('${key}'`)
    expect(at, `${key} must be registered`).toBeGreaterThan(-1)
    let i = c.indexOf('(', at) + 1
    let depth = 1
    while (i < c.length && depth > 0) {
      if (c[i] === '(') depth++
      else if (c[i] === ')') depth--
      i++
    }
    return c.slice(at, i)
  }

  it('neither fetcher maps its rejection to a value that reads as "loaded"', () => {
    // 🪤 The shape: `.catch(() => null)` RESOLVES the fetcher, so `loading` (`data === undefined`)
    // goes false and the card renders its `{data && …}` body as nothing. A blank health card.
    //
    // 🪤 MUTATION-FOUND: the first version asserted `… => api.doctor()` as a PREFIX, so re-appending
    // `.catch(() => null)` still matched and the mutation passed. Take the whole paren-matched call
    // and assert the absence — a prefix says nothing about what follows it.
    for (const key of ['settings:doctor', 'settings:incident']) {
      expect(registration(code(), key), `${key} must not swallow its rejection`).not.toMatch(/\.catch\(/)
    }
  })

  /** One `SETTINGS_WIDGETS` entry, brace-matched from its `id:` line.
   *
   *  🪤 This used to be a FILE-WIDE count pinned at 2, which made the rail measure the wrong
   *  thing: it passed only while exactly two cards in the whole file used this copy. CA-2's
   *  Devices card adopted the same good form — a failed read must not read as "no devices" on a
   *  security surface — and the count went to 3, reporting a regression where there was none,
   *  while still saying nothing about whether THESE two cards kept the pattern. Scoped per card
   *  it is strictly stronger: an unrelated adopter is invisible, and either named card dropping
   *  the state fails by name. */
  const cardBlock = (c: string, id: string) => {
    const at = c.indexOf(`id: '${id}', group:`)
    expect(at, `the ${id} card must exist`).toBeGreaterThan(-1)
    const open = c.lastIndexOf('{', at)
    let i = open
    let depth = 0
    do {
      if (c[i] === '{') depth++
      else if (c[i] === '}') depth--
      i++
    } while (i < c.length && depth > 0)
    return c.slice(open, i)
  }

  it('both cards render a "could not check" state and keep their loading flag honest', () => {
    const c = code()
    // Two cards, one form. `loading` must not stay true forever on a rejection either.
    for (const id of ['doctor', 'guardrails']) {
      const block = cardBlock(c, id)
      expect(block, `the ${id} card must offer a could-not-check state`).toMatch(/Could ?n['’]t check/)
      expect(block, `the ${id} card's loading must exclude the failed case`)
        .toMatch(/=== undefined && !\w*Err/)
    }
  })

  it('the savings tile keeps its catch — a missing number really is "no data"', () => {
    // The distinction deliberately KEPT: not every swallowed read is a defect. A savings metric that
    // cannot be computed has nothing to assert; a health card that cannot probe does.
    expect(code()).toMatch(/useToolsSavings[\s\S]{0,120}?\.catch\(\(\) => null/)
  })
})

describe('the probe failure reaches the consumers at all', () => {
  it('DashboardLive publishes it instead of dropping it', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/dashboard/DashboardLive.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    // 🪤 Without this the "Health unknown" row is an INERT control — `doctorErr` would be permanently
    // null and every DOM test above would pass against a branch that can never fire.
    expect(code, 'the loader keeps the rejection').toMatch(/\.catch\(\(e\) => guard\(setDoctorErr\)\(e\)\)/)
    expect(code, 'and the context carries it').toMatch(/doctor, doctorErr,/)
    expect(code, 'the old silent catch is gone').not.toMatch(/api\.doctor\(\)[\s\S]{0,80}?catch\(\(\) => \{\}\)/)
  })
})
