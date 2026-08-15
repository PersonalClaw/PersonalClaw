import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A settings Row labels ONE control ───────────────────────────────────────────────────────────
//
// `Row` renders its `label` + `hint` on the left and its children together in one `shrink-0` div on
// the right. So two switches in a single Row share one visible caption — and `Toggle` renders no
// visible text of its own (its `label` becomes the accessible name only). The result is two
// identical-looking switches under one label, where flipping the wrong one is a silent mistake.
//
// HC-4 added "Offer 'Check this work'" as a second Toggle inside the "Follow-up suggestions" Row,
// whose hint describes suggested next messages — not verification. Measured on a live gateway:
//
//     the row rendered  label "Follow-up suggestions"  +  2 × role=switch  +  0 visible switch text
//     accessible names  "Follow-up suggestions" | "Offer 'Check this work'"   (SR users could tell)
//     sweep of 28 settings panels: 39 rows contain a switch · exactly 1 held TWO — this one
//
// So it was an outlier, not a house pattern, and the fix is the form the other 38 rows already use:
// its own Row, its own label, its own hint. This rail pins the shape and the count.

const SRC = join(process.cwd(), 'src')
const chatPanel = () => readFileSync(join(SRC, 'pages/settings/ChatPanel.tsx'), 'utf8')

/** The JSX body of every `<Row …>…</Row>` in a file, non-greedy so rows do not swallow each other. */
function rowBodies(src: string): string[] {
  return [...src.matchAll(/<Row\b[\s\S]*?<\/Row>/g)].map((m) => m[0])
}

describe('a settings Row labels exactly one control', () => {
  it('reads the real file (not vacuously green)', () => {
    const rows = rowBodies(chatPanel())
    expect(rows.length, 'ChatPanel must have Rows to check').toBeGreaterThan(5)
  })

  it('no Row in ChatPanel holds two Toggles', () => {
    const offenders = rowBodies(chatPanel())
      .filter((r) => (r.match(/<Toggle\b/g) || []).length > 1)
      .map((r) => (r.match(/label="([^"]+)"/) || [])[1] ?? '(unlabelled)')
    expect(offenders, `these Rows share one caption across two switches: ${offenders.join(', ')}`).toEqual([])
  })

  it('the check-work toggle has its own Row, label and hint', () => {
    const src = chatPanel()
    const row = rowBodies(src).find((r) => /offer_check_work/.test(r)) ?? ''
    expect(row, 'the check-work toggle must live in a Row').toContain('<Row')
    expect(row, 'with a visible label naming it').toMatch(/label="Offer “Check this work”"/)
    expect(row, 'and a hint about verification, not suggestions').toMatch(/hint="[^"]*re-runs the checks[^"]*"/)
    expect((row.match(/<Toggle\b/g) || []).length, 'exactly one control').toBe(1)
  })

  it("the follow-up Row keeps its own hint and only its own switch", () => {
    const row = rowBodies(chatPanel()).find((r) => /followup_chips/.test(r)) ?? ''
    expect(row).toMatch(/label="Follow-up suggestions"/)
    expect(row).toMatch(/suggested next messages/)
    expect(/offer_check_work/.test(row), 'the two features must not share a row').toBe(false)
  })

  it('Row still renders one label and puts children on the right — the reason this matters', () => {
    // Vacuity guard: the whole argument rests on Row's shape. If Row ever labels each child, this
    // rail's premise changes and it should be re-derived rather than left asserting a stale story.
    const ui = readFileSync(join(SRC, 'pages/settings/settingsUI.tsx'), 'utf8')
    const row = ui.slice(ui.indexOf('export function Row'), ui.indexOf('export function Row') + 520)
    expect(row).toMatch(/\{label\}/)
    expect(row).toMatch(/shrink-0">\{children\}/)
  })
})
