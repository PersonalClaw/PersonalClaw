import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListControls } from './ListControls'

// ── Filtering a list changed the page and said nothing ───────────────────────────────
//
// Typing in a list filter rewrites the page under the user. The sighted cue is the list
// redrawing; for a screen-reader user there was no cue at all. Measured on six surfaces
// (knowledge, prompts, triggers, apps, skills, projects) — filtering 26 rows to 25, and
// filtering to a "No matching items" empty state — **zero live regions, every time**:
//
//   knowledge   noMatchShown=true   liveRegions=[]   NOT ANNOUNCED
//   prompts     noMatchShown=true   liveRegions=[]   NOT ANNOUNCED
//   …6 of 6
//
// axe reports nothing here, as in cycle 52: a missing announcement is not a rule violation.
//
// 🔑 THE CANONICAL FORM ALREADY EXISTED — `pages/chat/FindBar.tsx` announces its match count
// with `aria-live="polite"` on the `n/total` counter. This brings the 13 `ListControls`
// consumers onto that pattern instead of inventing one.
//
// Verified live after the change:
//
//   idle                  ""                      (region MOUNTED, empty)
//   no match              "No matching items"
//   25 of 26 rows         "25 items"
//   1 row                 "1 item"                (singularised)
//   query cleared         ""

const opts = { value: '', onChange: () => {}, options: [] }

describe('a filtered list announces its result count', () => {
  it('mounts the live region even when idle', () => {
    // A live region created at the same moment its content appears is not reliably observed,
    // so it must already be in the DOM, empty, waiting. (Same lesson as the toast host.)
    const { container } = render(<ListControls search={{ value: '', onChange: () => {} }} />)
    const region = container.querySelector('[role="status"][aria-live="polite"]')
    expect(region, 'the region must exist before a filter is typed').not.toBeNull()
    expect(region!.textContent).toBe('')
  })

  it('says nothing while no filter is active', () => {
    // An idle list announcing its own length on mount is noise, not information.
    const { container } = render(
      <ListControls search={{ value: '', onChange: () => {} }}
        results={{ count: 26, noun: 'items', active: false }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('')
  })

  it('announces the count while filtering', () => {
    const { container } = render(
      <ListControls search={{ value: 'zfs', onChange: () => {} }}
        results={{ count: 25, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('25 items')
  })

  it('singularises a single result', () => {
    // "1 items" reads as a bug to anyone listening to it.
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 1, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('1 item')
  })

  it('names the empty result rather than announcing "0"', () => {
    // "No matching items" is the same sentence the visible EmptyState uses; "0 items" would
    // be technically true and useless.
    const { container } = render(
      <ListControls search={{ value: 'zzz', onChange: () => {} }}
        results={{ count: 0, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('No matching items')
  })

  it('is polite, not assertive — a result count is an update, not an interruption', () => {
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    const region = container.querySelector('[role="status"]')!
    expect(region.getAttribute('aria-live')).toBe('polite')
  })

  it('keeps the announcement out of the visible bar', () => {
    // The count is already visible as the list itself; printing it would duplicate what a
    // sighted user can see and add a shifting element to the controls row.
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.className).toContain('sr-only')
  })

  it('still renders nothing at all when the bar has no controls', () => {
    // The early return must survive: an empty controls bar should not leave a stray region.
    const { container } = render(<ListControls />)
    expect(container.firstChild).toBeNull()
  })

  it('works on a filter-only bar (no search box)', () => {
    const { container } = render(
      <ListControls filter={{ ...opts, value: 'open' }}
        results={{ count: 4, noun: 'triggers', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('4 triggers')
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
// The primitive existing is not the fix — a surface has to pass `results`. This pins the four
// migrated ones and deliberately does NOT assert the other nine: each needs its own filtered
// array and noun, and guessing which local variable holds the post-filter rows is how a sweep
// announces the wrong number.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated list surfaces pass a result count', () => {
  const ADOPTERS = [
    'pages/projects/ProjectsSection.tsx',
    'pages/knowledge/KnowledgeListPage.tsx',
    'pages/skills/SkillsPage.tsx',
    'pages/prompts/PromptsListPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} passes results to ListControls`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must pass a results prop').toMatch(/results=\{\{/)
      // `active` must be derived from the query, not hardcoded true — otherwise the list
      // announces its length on every mount.
      expect(src, 'active must be conditional on the query').toMatch(/active:\s*!!/)
    })
  }

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
