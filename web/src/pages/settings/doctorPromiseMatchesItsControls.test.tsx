import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── issue 537: the Doctor promised it changes nothing, and it changes things ─────────────────────
//
// The panel header said *"Nothing here changes anything on your machine."* while the same file
// rendered two controls that do:
//
//   · a failed probe's **Fix** — `api.doctorFixApply`, repairs symlinks / stale locks / stale
//     bindings. Confirm-gated and SEL-audited server-side.
//   · Maintenance → **Run now** — `api.doctorRemediationRun`, runs embedding re-index, orphan
//     prune, skill aging. Confirm-gated too, with its dry-run plan rendered above the button.
//
// A UI-composed sentence IS a user-facing surface, so the two options were "make the promise true"
// or "correct the sentence". Making it true means deleting PLATFORM-RESILIENCE §2's remediation
// engine — a shipped feature with a dry-run preview rendered directly above its own button — so the
// sentence is what was wrong. `DiagnosticsPanel` in this same file already had the shape to copy:
// *"Nothing here runs an action, spends a token, or changes any state"*, scoped to a section where
// it is true.
//
// 🪤 A COPY FIX GOES STALE THE NEXT TIME A CONTROL LANDS, AND THAT IS NOT HYPOTHETICAL HERE — an
// earlier draft of this very fix asserted that Run now does not confirm, which was true when it was
// written and is false now. So the rail is deliberately NOT "the sentence contains the right
// words": it is two-sided. The copy must name each mutating control, AND the panel must still
// render them, AND the census below reads the api methods the FILE calls rather than trusting a
// hand-written list — so a third mutation added later reds this until the header names it too.

const PANEL = join(process.cwd(), 'src/pages/settings/DoctorPanel.tsx')

/** The api methods on this panel that WRITE. Kept as a list, not a boolean, so the assertion can
 *  say which one is unaccounted for. */
const MUTATING_CALLS = ['doctorFixApply', 'doctorRemediationRun']

const report = {
  ok: false,
  capabilities: {
    'serving-fs': {
      ok: false, tier: 1,
      probes: [{
        id: 'spa-symlink', title: 'SPA symlink', detail: 'static/dist is a real directory',
        ok: false, tier: 1, fix_id: 'relink-spa', evidence: {},
      }],
    },
  },
  skipped_capabilities: [],
}

const snapshot = { score: 70, target_score: 90, deficits: [], plan: [], recent_runs: [] }

async function mountPanel() {
  vi.resetModules()
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api,
        doctor: () => Promise.resolve(report),
        doctorRemediation: () => Promise.resolve(snapshot),
        doctorSimulateSurfacing: () => Promise.resolve({ candidates: [] }),
        doctorSimulateAutomation: () => Promise.resolve({ steps: [] }),
        triggers: () => Promise.resolve({ triggers: [] }),
      },
    }
  })
  const { DoctorPanel } = await import('./DoctorPanel')
  await act(async () => {
    render(<DoctorPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { vi.resetModules() })

describe('the Doctor panel says what it can change', () => {
  it('makes no unqualified no-mutation claim', async () => {
    await mountPanel()
    const text = screen.getByRole('heading', { level: 1 }).parentElement?.textContent ?? ''
    // The exact sentence the issue quoted, and the family it belongs to. Absence is the assertion:
    // a blanket promise on a panel that repairs state is a false statement, not loose wording.
    expect(text).not.toContain('Nothing here changes anything on your machine')
    expect(text).not.toMatch(/Nothing here changes anything/i)
  })

  it('names BOTH mutating controls in the header, scoped like the Diagnostics sibling', async () => {
    await mountPanel()
    const hint = screen.getByRole('heading', { level: 1 }).parentElement?.textContent ?? ''
    // Each exception named where a reader meets it, by the label on the control itself.
    expect(hint).toMatch(/\bFix\b/)
    expect(hint).toMatch(/Run now/)
    // The read-only claim SURVIVES, scoped to the probing the panel actually does. Dropping it
    // entirely would lose a true and load-bearing reassurance.
    expect(hint).toMatch(/probe|probing/i)
  })

  it('still renders the controls the sentence names', async () => {
    // VACUITY FLOOR. Every assertion above is about copy, and copy can describe controls that are
    // not there. If a later change removes the Fix affordance, the honest sentence becomes a
    // different lie and this goes red.
    await mountPanel()
    expect(screen.getByRole('button', { name: /Fix/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Run now/ })).toBeInTheDocument()
  })

  it('accounts for every write call the file makes', async () => {
    // The half that does not go stale: the census is read from the source, so a THIRD mutating
    // call added later fails here until the header names its control too.
    const code = readFileSync(PANEL, 'utf8')
    const writes = MUTATING_CALLS.filter((m) => code.includes(`api.${m}(`))
    // VACUITY FLOOR: the census must have found the calls it is about. A rename would otherwise
    // make the loop below trivially empty and this test would pass while measuring nothing.
    expect(writes).toEqual(MUTATING_CALLS)

    const hint = code.match(/hint="([^"]*)"/)?.[1] ?? ''
    expect(hint, 'the PanelHeader hint must be findable — its first `hint=` is the panel-level one').toBeTruthy()
    // `doctorFixApply` sits behind the button labelled Fix; `doctorRemediationRun` behind Run now.
    for (const [call, label] of [['doctorFixApply', 'Fix'], ['doctorRemediationRun', 'Run now']]) {
      expect(writes).toContain(call)
      expect(hint, `${call} mutates state and the header never mentions its control (${label})`).toContain(label)
    }
  })

  it('describes each mutating control the way the code actually gates it', async () => {
    // 🔑 THE ANTI-STALENESS HALF, and the reason this file exists rather than a copy edit. Naming a
    // control is not enough — the hint makes a claim ABOUT each one, and a claim is exactly what
    // rotted the first time. Both controls call `confirm(` today, so the hint may say both confirm;
    // if a later change drops either confirm, the sentence becomes the same class of lie the issue
    // reported and this goes red.
    const code = readFileSync(PANEL, 'utf8')
    const confirms = (code.match(/await confirm\(\{/g) ?? []).length
    expect(confirms, 'FixButton and RemediationSection each gate on a confirm').toBe(2)

    const hint = code.match(/hint="([^"]*)"/)?.[1] ?? ''
    expect(hint).toMatch(/confirms/)
    // …and it is said of BOTH, not just the one that happened to be checked first.
    expect(hint).toMatch(/confirms first[\s\S]*also confirms/)
  })

  it('carries the correction on the HUB TILE too, not just in the panel', async () => {
    // 🔑 TWO READERS, ONE CLAIM — and fixing one is how this defect survives a fix. The Settings hub
    // tile (`settingsWidgets.tsx`'s `doctor` entry) described the panel as "Read-only health probes",
    // which is the surface a user reads BEFORE opening it, so the expectation was set by a sentence
    // the panel's own header no longer makes. Asserted against the source because the tile's
    // `description` is static metadata on the registry entry, not rendered by `DoctorPanel`.
    const widgets = readFileSync(join(process.cwd(), 'src/pages/settings/settingsWidgets.tsx'), 'utf8')
    const tile = widgets.slice(widgets.indexOf("id: 'doctor'"))
    const description = tile.match(/description: '([^']*)'/)?.[1] ?? ''
    // VACUITY FLOOR: the slice must actually have found the doctor tile's description.
    expect(description, "the doctor tile's description must be findable").toMatch(/health probes/i)
    expect(description).not.toMatch(/read-only/i)
  })

  it('does not weaken the Simulators section, which really is read-only', async () => {
    // The floor on the other side: this fix must not turn a TRUE no-mutation promise into a hedge.
    // The simulators re-run a deterministic scorer and walk a dry fire — nothing executes.
    const code = readFileSync(PANEL, 'utf8')
    expect(code).toContain('Nothing here runs an action, spends a token, or changes any state.')
  })
})
