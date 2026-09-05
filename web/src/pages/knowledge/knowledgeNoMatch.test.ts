import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The library's narrowed-to-nothing state names its narrower (AUD-NZ10) ─────────────
//
// Source pins (the page shell is too heavy to render for a copy contract). The old single
// state — "No matching items / Try a different search or filter." — blamed both controls at
// whoever touched either, offered no escape, and covered a third case it never mentioned:
// a shelf (collection) emptied by navigation. The search here is SERVER-side (`submitted`
// shapes the fetch), so unlike the tasks list there is no honest library total in hand under
// a query — the hints teach the way out instead of counting. That deviation is deliberate;
// a count sourced from the already-narrowed fetch would describe the wrong population.

const src = readFileSync(join(process.cwd(), 'src', 'pages', 'knowledge', 'KnowledgeListPage.tsx'), 'utf8')

describe('knowledge library no-match split', () => {
  it('the blame-everything sentence is gone', () => {
    expect(src).not.toContain('Try a different search or filter.')
  })

  it('a search miss names the query and offers Clear search (clearing BOTH query states)', () => {
    // `q` mirrors the input; `submitted` is the value the fetch used. Clearing only one
    // leaves the box and the list disagreeing about whether a search is active.
    expect(src).toMatch(/No items match “\$\{submitted\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => \{ setQ\(''\); setSubmitted\(''\) \}/)
  })

  it('the view escape resets every client-side narrower', () => {
    expect(src).toMatch(
      /label: 'View all items', onClick: \(\) => \{ setTypeFilter\(''\); setProviderFilter\(''\); setTagFilter\(''\); setCurationFilter\(''\) \}/,
    )
  })

  it('the empty shelf keeps a neutral sentence — navigation has no in-page escape', () => {
    expect(src).toMatch(/This shelf has no items yet\./)
  })

  it('the genuinely-empty library state is untouched', () => {
    expect(src).toContain('Knowledge base is empty')
  })
})
