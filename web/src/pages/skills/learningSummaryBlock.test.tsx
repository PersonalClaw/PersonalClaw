import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── LV-3: the learning summary block, asserted at its CALL SITE ──────────────────────────────────
//
// T2.3 asked for this block to be registered with plan 42's digest builder. That builder does not
// exist (no digest-section registry anywhere in `src/`), so the atom's sanctioned fallback applies:
// the same block renders on the skills page header. Which makes the skills page the surface under
// test — not the component in isolation. A test that mounted `<LearningSummaryBlock />` alone and
// checked it renders a count would pass just as happily if nothing on `#/skills` ever called it,
// which is exactly the correct-but-uncalled shape this repo keeps shipping.
//
// So every case below renders the REAL `SkillsPage` and reads the accessibility tree. The block's
// own `<section aria-label>` is what makes it addressable as `role=region`, so these assertions
// also pin that it stays named.

const SUMMARY = {
  window_days: 7,
  total: 4,
  new_skills: { count: 2, names: ['auto/release-flow', 'auto/triage'] },
  refined_skills: { count: 1, names: ['auto/deploy'] },
  pending_proposals: { count: 1, names: ['auto/deploy (refine)'] },
  facts: { count: 0, names: [] },
}

const SKILLS = [
  { key: 'auto/release-flow', name: 'auto/release-flow', description: 'cut a release', source: 'auto', path: '/x', dir: '/x', always: false, status: 'active', loaded_by_agents: [] },
]

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      skills: () => Promise.resolve(SKILLS),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      learningSummary: () => Promise.resolve(SUMMARY),
      ...over,
    },
  }))
}

async function mountSkillsPage() {
  const { SkillsPage } = await import('./SkillsPage')
  render(<SkillsPage query={{}} setQuery={() => {}} />)
  // The skills list is the page's own content — awaiting it means the page really mounted, so a
  // missing block below is the block's absence rather than an unresolved first paint.
  await waitFor(() => expect(screen.getByText('cut a release')).toBeInTheDocument())
}

const region = () => screen.queryByRole('region', { name: /Learned in the last 7 days/i })

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/skills renders the learning summary block with real counts and names', () => {
  it('mounts the block on the skills page with each group\'s count and names', async () => {
    mockApi({})
    await mountSkillsPage()

    const block = await waitFor(() => {
      const r = region()
      expect(r, 'the block must MOUNT on the page, not merely exist as a component').not.toBeNull()
      return r!
    })
    const text = block.textContent ?? ''
    expect(text).toMatch(/2 new/)
    expect(text).toMatch(/auto\/release-flow/)
    expect(text).toMatch(/auto\/triage/)
    expect(text).toMatch(/1 refined/)
    expect(text).toMatch(/auto\/deploy/)
    expect(text).toMatch(/1 pending/)
    // An empty group is omitted rather than rendered as a zero row: "0 facts" would be a claim
    // about the fact store that the group's absence does not make.
    expect(text).not.toMatch(/facts/)
  })

  it('shows the EXACT count with a "+N more" remainder, never names.length', async () => {
    // The backend caps `names` at 8 while keeping `count` exact. A renderer that showed
    // `names.length` would report 8 of 12 as the whole truth — silently, and only once a
    // group got busy enough to truncate.
    const capped = {
      ...SUMMARY,
      total: 12,
      new_skills: { count: 12, names: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] },
      refined_skills: { count: 0, names: [] },
      pending_proposals: { count: 0, names: [] },
    }
    mockApi({ learningSummary: () => Promise.resolve(capped) })
    await mountSkillsPage()

    const text = await waitFor(() => {
      const r = region()
      expect(r).not.toBeNull()
      return r!.textContent ?? ''
    })
    expect(text).toMatch(/12 new/)
    expect(text).toMatch(/\+4 more/)
    expect(text, 'the truncated sample size must never be presented as the count').not.toMatch(/8 new/)
  })

  it('is ABSENT when nothing was learned in the window, and the list still renders', async () => {
    // The vacuity half of the two cases above: if the block rendered unconditionally, this
    // would still find the region and the assertions above would prove nothing about the guard.
    mockApi({
      learningSummary: () => Promise.resolve({
        window_days: 7, total: 0,
        new_skills: { count: 0, names: [] }, refined_skills: { count: 0, names: [] },
        pending_proposals: { count: 0, names: [] }, facts: { count: 0, names: [] },
      }),
    })
    await mountSkillsPage()

    expect(region(), 'four zeros are noise, not information').toBeNull()
    expect(screen.getByText('cut a release'), 'the page itself is unaffected').toBeInTheDocument()
  })

  it('is ABSENT when the route 404s (learning disabled), and the list still renders', async () => {
    // `learning.enabled` off ⇒ the route 404s. Rendering "0 new, 0 refined" there would assert
    // that nothing was learned; the truthful answer is that nothing is being tracked. The skills
    // list keeps its own hard error surface, which is why swallowing HERE is not a swallow.
    mockApi({ learningSummary: () => Promise.reject(new Error('404 learning is disabled')) })
    await mountSkillsPage()

    expect(region()).toBeNull()
    expect(screen.getByText('cut a release')).toBeInTheDocument()
    expect(screen.queryByRole('alert'), 'and not an error banner over a supplementary block').toBeNull()
  })
})

describe('the call site is on the page, not only in the component', () => {
  it('SkillsPage renders <LearningSummaryBlock /> inside the content column', () => {
    // A static companion to the render tests: they prove the block appears, this names WHERE, so a
    // refactor that moved the block out of the content column (losing the list's measure) is
    // visible in the diff. The vacuity floor is the import — if the identifier vanished entirely,
    // both halves fail together rather than one silently matching nothing.
    const src = readFileSync(join(__dirname, 'SkillsPage.tsx'), 'utf8')
    expect(src).toContain("import { LearningSummaryBlock } from './LearningSummaryBlock'")
    expect(src).toContain('<LearningSummaryBlock />')
  })
})
