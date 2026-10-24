import { describe, expect, it } from 'vitest'
import { normalizeTree } from '../../e2e/surfaceChecks'

// ── The surface matrix's tree baseline does not hang on the weekday or the machine's first sample ──
//
// Two lines the run's date and the gateway's timing decide, not the surface:
//  · Learning's capture strip labels its last seven days by weekday, so one baseline read
//    "Tue — not started … Mon — not started" on a Monday and "Wed — … Tue — …" the next day;
//  · the Settings hub's System tile reads "CPU —" until the gateway's first CPU sample lands, then
//    "CPU 12%", and Settings › system's health strip "— cpu", then "12% cpu", so each line depended
//    on whether the sample was in when the tree was read. The strip's gateway uptime is "3m 12s"
//    for the first hour and "1h 2m" after it.

describe("the matrix's accessibility tree folds a day strip's weekdays", () => {
  it('reads the strip on a Monday and on the Tuesday after as the same line', () => {
    const days = (order: string[]) => `  - text: Capture, last 7 days no capture pass has run yet ${order.map((d) => `${d} — not started`).join(' ')}`
    const monday = days(['Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Mon'])
    const tuesday = days(['Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Mon', 'Tue'])
    expect(normalizeTree(tuesday)).toBe(normalizeTree(monday))
    expect(normalizeTree(monday)).toContain('<day> — not started')
  })

  it('leaves a weekday that names a column or a date alone', () => {
    // Control: the week view runs under a fixed clock, and its days name its own columns.
    expect(normalizeTree('        - columnheader "Mon 2"')).toBe('        - columnheader "Mon #"')
    expect(normalizeTree("        - 'button \"Tue, Nov 3 09:00 — 2 fires\"': \"2\"")).toContain('Tue, Nov #')
  })
})

describe("the matrix's accessibility tree folds the machine's CPU reading and the gateway's uptime", () => {
  it('reads the tile before and after the first sample as the same line', () => {
    expect(normalizeTree('  - text: System CPU 12% Memory 9.6/16GB')).toBe(normalizeTree('  - text: System CPU — Memory 9.6/16GB'))
  })

  it('still names that a CPU reading is there', () => {
    expect(normalizeTree('  - text: System CPU 4% Memory 9.6/16GB')).toBe('  - text: <theme> CPU <cpu> Memory #.#/#GB')
  })

  it("reads Settings › system's strip before the first sample and an hour on as the same line", () => {
    const early = '  - text: 3m 12s uptime v0.2.3 darwin — cpu 9.6/16GB mem 120/460GB disk 2.10 load · 10cpu 6 triggers 0 subagents'
    const later = '  - text: 1h 2m uptime v0.2.3 darwin 7% cpu 9.6/16GB mem 120/460GB disk 2.10 load · 10cpu 6 triggers 0 subagents'
    expect(normalizeTree(later)).toBe(normalizeTree(early))
    expect(normalizeTree(early)).toMatch(/^ {2}- text: <uptime> uptime v#\.#\.# <platform> <cpu> cpu /)
  })
})

describe("the matrix's accessibility tree folds a live percentage's precision", () => {
  it("reads Settings › models' memory reading at 12.5% and at 12% as the same line", () => {
    // One CI attempt read `#.#%` and its retry `#%` (#3905): the reading landed on a whole number.
    const at = (pct: string) => `  - text: 9.6 GiB of 16.0 GiB in use · ${pct} No models are loaded right now. One loads on its first use.`
    expect(normalizeTree(at('12%'))).toBe(normalizeTree(at('12.5%')))
    expect(normalizeTree(at('12.5%'))).toContain('in use · #% No models')
  })

  it('leaves a decimal that is not a percentage alone', () => {
    expect(normalizeTree('  - text: 9.6 GiB of 16.0 GiB in use')).toBe('  - text: #.# GiB of #.# GiB in use')
  })
})
