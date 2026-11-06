import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Skills › Browse: the search's match counts stay out of the filter's label ────────────────────
//
// The Marketplace section's rows read "All marketplaces (12)", and the header filter's label is
// its chosen row's words. The cluster's probes measure that label, so every search reply changed
// the row's width and could re-tier it, taking the search field away while it was being typed in.
// The counts are each row's `count` now, drawn beside its words, and the label stays the words.

const src = readFileSync(join(process.cwd(), 'src/pages/skills/SkillsPage.tsx'), 'utf8')

describe("Browse's Marketplace section", () => {
  it('names each row by its marketplace alone, and counts in `count`', () => {
    const section = src.slice(src.indexOf("title: 'Marketplace'"), src.indexOf('}]} />', src.indexOf("title: 'Marketplace'")))
    expect(section).toContain("{ key: '', label: 'All marketplaces', count: totalMatches || undefined }")
    expect(section).toContain('label: m.name, count: counts[m.name] || undefined')
    expect(section, 'no count in any label').not.toMatch(/label: [^,]*\(\$\{/)
  })
})
