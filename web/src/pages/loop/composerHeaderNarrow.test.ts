import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The composer header at phone width ─────────────────────────────────────────────────
//
// Every control in this header is `shrink-0` on purpose — they must keep their natural size — so
// the row can only fit a narrow viewport if each WIDE control carries its own collapse strategy.
// Exactly one of the three did. Measured at 390×844 before this change:
//
//   clipped text            691 > 390
//   "Unattended"            1.48:1   (it ran under the frosted shell-corner chrome)
//   axe [serious]           target-size on the Granularity dial, 2 nodes
//
// Adding `collapse="menu"` to the Mode and Project-kind dials, and dropping the Scratch
// qualifier below `lg`, took that to: contrast failure GONE, clip 691 → 495, target-size 2 → 1.
//
// 🪤 THE REMAINING 105px IS NOT A FLAG, IT IS A LAYOUT DECISION — and it is recorded as an owner
// call rather than guessed at. At 390px the floating shell corner occupies x=211..390 with
// `pointer-events: auto`, so the usable header is ~211px while these four controls need ~495px
// even fully collapsed. A hit-test at the Granularity pill's centre returns
// `BUTTON[aria-label="2 degraded"]` — the corner is ON TOP of the control and steals the click,
// which is what axe means by "partially obscured (smallest space is 19px by 32px)". No amount of
// collapsing fixes that; the knobs have to move, or shed a member, at narrow widths.
//
// ⚠️ The accessible name is DELIBERATELY not responsive. The visible qualifier hides below `lg`,
// but the checkbox carries the full sentence as an explicit `aria-label` at every viewport —
// "Scratch" and "Scratch (auto-clean when done)" are different controls to anyone searching by
// name, so a name that changes with screen width is worse than a long one.

const SRC = readFileSync(join(process.cwd(), 'src/pages/loop/LoopComposer.tsx'), 'utf8')

/** The `headerControls` block ONLY — the row that has to share ~211px with the floating shell
 *  corner. Scoped deliberately: the page also renders a "Loop kind" Segmented in the BODY, which
 *  has the full column width and fits at 390px with all five labels readable, so requiring a
 *  collapse strategy there would flag a control that has room. A rail that reaches past what was
 *  measured invents work. */
const HEADER = SRC.slice(SRC.indexOf('const headerControls ='), SRC.indexOf('return (', SRC.indexOf('const headerControls =')))

/** Complete `<Segmented …>` tags, brace-depth tracked so a `>` inside an attribute cannot
 *  truncate the match. */
const segmentedTags = () => {
  const out: string[] = []
  for (const m of HEADER.matchAll(/<Segmented\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < HEADER.length; i++) {
      const ch = HEADER[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { out.push(HEADER.slice(m.index!, i + 1)); break }
    }
  }
  return out
}

describe('every dial in the composer header can collapse', () => {
  const tags = segmentedTags()

  it('finds the header dials (not vacuously green)', () => {
    // Granularity, Project kind, Mode.
    expect(tags.length, 'the matcher must find the Segmented dials').toBeGreaterThanOrEqual(3)
  })

  it('gives each one a collapse strategy', () => {
    const mute = tags.filter((t) => !/collapse=/.test(t)).map((t) => /ariaLabel="([^"]+)"/.exec(t)?.[1] ?? t.slice(0, 40))
    expect(
      mute,
      `dial(s) with no collapse strategy — they cannot shrink and push the row under the shell corner: ${mute.join(', ')}`,
    ).toEqual([])
  })

  it.each(['Granularity', 'Mode', 'Project kind'])('%s collapses to a menu', (label) => {
    const tag = tags.find((t) => t.includes(`ariaLabel="${label}"`))
    expect(tag, `${label} dial must exist`).toBeTruthy()
    expect(tag!).toMatch(/collapse="menu"/)
  })
})

describe('the scratch toggle keeps ONE name at every width', () => {
  it('names itself explicitly rather than relying on visible text that hides', () => {
    expect(SRC).toMatch(/aria-label="Scratch \(auto-clean when done\)"/)
  })

  it('hides only the QUALIFIER, and only below lg', () => {
    expect(SRC).toMatch(/Scratch<span className="hidden lg:inline"> \(auto-clean when done\)<\/span>/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(SRC).toMatch(/const headerControls =/)
    expect(SRC.length).toBeGreaterThan(4000)
  })
})
