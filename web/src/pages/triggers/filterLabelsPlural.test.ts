import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The triggers filter chips are all plural categories — one had drifted singular ─────────────
//
// `#/triggers`' filter row deliberately labels each chip as the PLURAL of its kind — it names "a
// CATEGORY OF ROWS you are filtering to, not the kind of the one thing you are creating" (the file's
// own comment). Schedules · Data events · Automations followed that; `lifecycle` read the singular
// "Lifecycle", breaking the row's own rule. It now reads "Lifecycle events" — the plural of the
// canonical "Lifecycle event" kind, parallel to "Data events".
//
// Verified live (the chips live in a closed-by-default FilterMenu, so they are invisible to the
// default snapshot — this is why the visual baseline did not need regenerating): opening "Filter &
// sort" on `#/triggers` shows Schedules · Lifecycle events · Data events · Automations, no bare
// "Lifecycle".

const SRC = join(process.cwd(), 'src')
const page = () => readFileSync(join(SRC, 'pages/triggers/TriggersListPage.tsx'), 'utf8')

describe('the triggers filter labels are plural categories', () => {
  it('every non-"All" filter chip is plural — none is a bare singular kind', () => {
    const src = page()
    // Pull the FILTERS array's labels.
    const block = src.match(/const FILTERS[\s\S]*?\]\n/)?.[0] ?? ''
    const labels = [...block.matchAll(/label: '([^']+)'/g)].map((m) => m[1])
    expect(labels, 'the FILTERS census must not go empty').toEqual(
      ['All', 'Schedules', 'Lifecycle events', 'Data events', 'Automations'],
    )
    // The specific drift this closed: no chip may be the singular "Lifecycle".
    expect(labels, 'the lifecycle chip must be the plural category').not.toContain('Lifecycle')
  })

  it('the plural convention is documented at the source, so a future edit knows the rule', () => {
    expect(page()).toMatch(/PLURAL of its kind/)
  })
})
