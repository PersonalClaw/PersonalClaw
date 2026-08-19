import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Toggle } from './Toggle'

// ── Four switches a keyboard user could not reach, and a census that first said two ────────
//
// Cycle 109 gave `Button` a `disabledReason`, and the same question was never asked of the app's
// other interactive primitives. `Toggle`'s natively-disabled sites triage into three buckets:
//
//   4  A PRECONDITION THE USER CAN FIX     the two `#/settings/account` security switches ·
//                                          MemoryPanel's volunteer-memory (needs the entity graph) ·
//                                          VoicePanel's enable (needs a bound model)
//   5  still loading (`=== null`, `!state`)  the two Inbox panels ×2, Guardrails' incident mode
//   5  an action IN FLIGHT (`busy`)          bento, ScheduleDetail, ToolGroupsTile, StoreTriggerDetail,
//                                            LifecycleDetail
//
// 🪤 THE FIRST CENSUS SAID **TWO**, and shipping that would have fragmented the family — converging 2
// of 4 is worse than leaving all 4. The probe matched `<Toggle\b[^>]{0,400}`, which stops at the first
// `>`; the `>` inside `onChange={(v) => …}` truncated the tag before its `disabled=`, hiding MemoryPanel
// and VoicePanel. The rail below matches non-greedily to `/>` instead, and disagreeing with the probe
// is how the miss surfaced. **A JSX matcher cannot scan to the first `>`.**
//
// Only the first bucket changes. Measured on `#/settings/account`, before → after:
//
//                                before                              after
//   native disabled              true                                **false**
//   aria-disabled                null                                **"true"**
//   title                        null                                **the precondition**
//   focusable                    **false**                           **true**
//   switches a keyboard reaches  **0 of 2**                          **2 of 2**
//                                (the two on that route; the other two live on memory / voice)
//   computed opacity / cursor    0.4 / not-allowed                   0.4 / not-allowed
//
// 🔑 THE PRECONDITION WAS ALREADY ON SCREEN — in the row's hint ("Set a password first…") — which is
// exactly what made this easy to miss. A sighted user reads it; a keyboard user tabs past a SECURITY
// control and never learns it exists. Same trade `Button` makes: the native attribute is stronger
// protection, but a control that cannot explain itself is the worse failure.
//
// 🪤 THE DIMMING HAD TO MOVE WITH THE SEMANTICS. `disabled:opacity-40` cannot match an
// `aria-disabled` element, so naming only the native selector would leave a soft-off switch looking
// fully enabled while refusing to toggle (cycle 111 hit this on `Button`). Both are named, and the
// browser confirms it: **opacity 0.4 and cursor not-allowed after the change**, not merely intended.
//
// 🔑 WHY THE OTHER TEN STAY NATIVE. An in-flight toggle must not be re-clickable, and "still
// loading" is not a state the user can act on — a reason there would announce noise a moment before
// it stops being true. `disabledReason` is for a precondition with a fix.

describe('a Toggle with a reason stays reachable', () => {
  const props = { on: false, label: 'Require a 2FA code', disabled: true }

  it('drops the native attribute so the tab stop survives', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="Enroll an authenticator first" />)
    const el = screen.getByRole('switch') as HTMLButtonElement
    expect(el.disabled, 'a natively disabled switch leaves the tab order').toBe(false)
    expect(el.getAttribute('aria-disabled')).toBe('true')
  })

  it('announces the reason', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="Enroll an authenticator first" />)
    expect(screen.getByRole('switch').getAttribute('title')).toBe('Enroll an authenticator first')
  })

  it('still refuses to toggle', () => {
    // Reachable is not the same as usable: `aria-disabled` is a promise the handler has to keep.
    const onChange = vi.fn()
    render(<Toggle {...props} onChange={onChange} disabledReason="Enroll an authenticator first" />)
    screen.getByRole('switch').click()
    expect(onChange, 'aria-disabled without a suppressed handler is a lie').not.toHaveBeenCalled()
  })

  it('keeps its role, state and name while unavailable', () => {
    render(<Toggle on onChange={vi.fn()} label="Require a 2FA code" disabled disabledReason="x" />)
    const el = screen.getByRole('switch', { name: 'Require a 2FA code' })
    expect(el.getAttribute('aria-checked')).toBe('true')
  })

  it('carries BOTH dimming selectors, because one of them cannot match', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="x" />)
    const cls = screen.getByRole('switch').className
    expect(cls).toMatch(/\baria-disabled:opacity-40\b/)
    expect(cls, 'the native selector still serves the ten sites that keep it').toMatch(/\bdisabled:opacity-40\b/)
  })

  it('changes nothing when no reason is given', () => {
    // The omit-and-nothing-changes half: transient unavailability keeps the stronger attribute.
    render(<Toggle {...props} onChange={vi.fn()} />)
    const el = screen.getByRole('switch') as HTMLButtonElement
    expect(el.disabled).toBe(true)
    expect(el.getAttribute('aria-disabled')).toBeNull()
    expect(el.getAttribute('title')).toBeNull()
  })
})

describe('the triage, pinned per site', () => {
  const SRC = join(process.cwd(), 'src')
  const account = readFileSync(join(SRC, 'pages/settings/AccountPanel.tsx'), 'utf8')

  it('all four precondition switches name what unlocks them', () => {
    // Per TAG, not per file: an earlier rail in this session stayed green while one of two Saves in
    // one file lost its contract.
    const PRECONDITION: [string, RegExp][] = [
      ['pages/settings/AccountPanel.tsx', /(?<!aria-)disabled=\{!state\.credential_configured\}/],
      ['pages/settings/AccountPanel.tsx', /(?<!aria-)disabled=\{!state\.totp_enabled\}/],
      ['pages/settings/MemoryPanel.tsx', /(?<!aria-)disabled=\{s\.graph_enabled === false\}/],
      ['pages/settings/VoicePanel.tsx', /(?<!aria-)disabled=\{!bound\}/],
    ]
    for (const [rel, gate] of PRECONDITION) {
      const src = rel.endsWith('AccountPanel.tsx') ? account : readFileSync(join(SRC, rel), 'utf8')
      const tag = [...src.matchAll(/<Toggle\b[\s\S]{0,400}?\/>/g)].map((m) => m[0]).find((t) => gate.test(t))
      expect(tag, `${rel}: the switch gated by ${gate} must still exist`).toBeTruthy()
      expect(tag!, `${rel}: a precondition switch must say what unlocks it`).toMatch(/disabledReason="/)
    }
  })

  it('an in-flight toggle keeps the native attribute', () => {
    // Five sites gate on `busy`. Re-clicking an in-flight switch is the failure this prevents, so a
    // reason here would be a regression, not an improvement.
    for (const rel of [
      'pages/schedule/ScheduleDetail.tsx',
      'pages/tools/ToolGroupsTile.tsx',
      'pages/triggers/StoreTriggerDetail.tsx',
      // WS-9's pause switch on a watched source: the only reason it is ever unavailable is an
      // in-flight PATCH, so it belongs in this class rather than the precondition one.
      'pages/knowledge/SourcesPage.tsx',
      // WF2KNO-12's pause switch on a scheduled report: same shape as the watched-source one
      // above — the only thing that ever disables it is its own in-flight PUT.
      'pages/knowledge/ReportsPage.tsx',
    ]) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const tag = src.match(/<Toggle\b[\s\S]{0,300}?\/>/)?.[0] ?? ''
      expect(tag, `${rel} must still gate on busy`).toMatch(/(?<!aria-)disabled=\{busy\}/)
      expect(tag, `${rel} must NOT soften an in-flight action`).not.toMatch(/disabledReason/)
    }
  })

  it('the census is reproducible — 16 disabled Toggle sites, not vacuously zero', () => {
    // If this count drops, a site was converted or deleted; if it climbs, a new one arrived
    // un-triaged. Either way it should be a deliberate line in a PR, not a silent drift.
    const walk = (d: string): string[] =>
      readFileSync !== undefined
        ? require('node:fs').readdirSync(d, { withFileTypes: true }).flatMap((e: { name: string; isDirectory(): boolean }) =>
          e.isDirectory() ? walk(join(d, e.name)) : (/\.tsx$/.test(e.name) && !/\.(test|doc)\.tsx$/.test(e.name) ? [join(d, e.name)] : []))
        : []
    const sites = walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/<Toggle\b[\s\S]{0,400}?\/>/g)]
        .filter((m) => /(?<!aria-)disabled=/.test(m[0])))
    // 🔺 15 → 16 (MGAV-9): Memory → Settings gained the topology-orientation switch, which is
    // a PRECONDITION switch (the map is built from entity-graph links), so it carries a reason
    // naming what unlocks it rather than going dark unexplained. Reasoned sites 4 → 5.
    // 🔺 16 → 17 (WF2KNO-12): the scheduled-report pause switch. An IN-FLIGHT switch (the only
      // thing that disables it is its own PUT), so it stays native and the reasoned count holds at 5.
      expect(sites.length).toBe(17)
    expect(sites.filter((m) => /disabledReason/.test(m[0])).length, 'five carry a reason; eleven stay native').toBe(5)
  })
})
