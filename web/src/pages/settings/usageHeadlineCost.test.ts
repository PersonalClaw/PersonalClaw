import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { headlineCost } from './UsagePanel'

// ── A spend surface that showed no spend ──────────────────────────────────────────────────────────
//
// Measured on `#/settings/usage` with a seeded ledger — 30 turns across four models, three of them
// priced. The endpoint returned:
//
//   { cost_usd: 11.3496, turns: 30, priced: false }
//
// and the headline rendered the word **"unpriced"** — while the "By model" table directly below it
// listed $6.02, $4.59 and $0.7398. The one unpriced model (a local `qwen3:8b`, correctly unpriced)
// erased eleven dollars of known, computed spend from the only figure a user reads.
//
// The backend is not at fault: `usage_ledger` says "a single unpriced constituent taints the total —
// it can never present as complete", and sets `priced: false`. That contract is right. The panel
// implemented "cannot present as complete" as "**do not present**", which is a different and worse
// claim on a page whose entire job is "what have I spent".
//
// 🔑 The fix uses the panel's OWN vocabulary rather than inventing one: 250 lines below the stat it
// already says "Floor — … Real spend is higher than **the figure above**" — copy that presupposes a
// figure is there. So the headline states the floor (`≥$11.35`) and the existing "Partial" marker
// carries the incompleteness.

describe('the headline cost states a floor rather than nothing', () => {
  it('shows the exact figure when every model is priced', () => {
    expect(headlineCost({ cost_usd: 11.3496, priced: true })).toBe('$11.35')
    // Sub-dollar keeps four decimals, matching the table's own formatter.
    expect(headlineCost({ cost_usd: 0.7398, priced: true })).toBe('$0.7398')
  })

  it('states a FLOOR when some of the spend is priced and some is not', () => {
    // The measured case, verbatim.
    expect(headlineCost({ cost_usd: 11.3496, priced: false })).toBe('≥$11.35')
    expect(headlineCost({ cost_usd: 1.89, priced: false })).toBe('≥$1.89')
    expect(headlineCost({ cost_usd: 0.0123, priced: false })).toBe('≥$0.0123')
  })

  it('falls back to the word only when there is no floor to state', () => {
    // Nothing priced at all: "≥$0.00" would be true and useless, and "$0.00" would be a lie the
    // ledger explicitly forbids ("a caller MUST render 'unpriced', never $0.00").
    expect(headlineCost({ cost_usd: 0, priced: false })).toBe('unpriced')
  })

  it('never prints a bare $0.00 for an unpriced period', () => {
    // The ledger's contract, asserted from the UI side. A regression here is the original defect's
    // mirror image: claiming free instead of claiming unknown.
    for (const cost of [0, 0.0]) {
      expect(headlineCost({ cost_usd: cost, priced: false })).not.toBe('$0.00')
    }
  })

  it('the stat renders through this function, not an inline branch', () => {
    // The defect lived in an inline ternary on the JSX. Keeping the decision named and exported is
    // what makes the three branches assertable at all — an inline version is only reachable by
    // mounting the whole panel with six mocked endpoints.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/UsagePanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src).toMatch(/<BigStat caption="cost" value=\{headlineCost\(t\)\} \/>/)
    expect(src, 'no inline unpriced ternary should come back').not.toMatch(/t\.priced \? fmtUsd/)
    // And the Partial marker must survive: the floor is only honest beside it.
    expect(src, 'the incompleteness marker carries what the floor does not say')
      .toMatch(/<span className="text-warning">Partial<\/span>/)
  })
})
