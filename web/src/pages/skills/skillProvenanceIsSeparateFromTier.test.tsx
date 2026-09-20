import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── #576: a taught skill must be identifiable as taught, WITHOUT losing its tier ──────────────────
//
// Promotion writes `source: taught` into the skill's frontmatter and the auto-extractor writes
// `source: auto`, but the listing's `source` field is derived purely from the DIRECTORY the skill
// sits in, so both markers were write-only: a skill the user explicitly taught the agent rendered
// the same `local` chip as one dropped into that directory by hand, on every surface.
//
// 🔑 THE TWO FACTS STAY SEPARATE. The fix adds `provenance` beside `source` rather than folding
// taught/auto into the source chip's vocabulary, because `source` is what decides whether the
// inspector offers Edit and Delete — a taught skill is an ordinary editable `local` skill. So every
// case here asserts BOTH: the new marker appears AND the tier chip is unchanged. A test that only
// looked for "taught" would pass just as happily if the fix had overwritten the tier.
//
// 🔑 RENDERED, NOT INSPECTED. These mount the real `SkillsPage` and read the accessibility tree, so
// a `provenanceMeta` helper that returned the right label while no surface called it would fail
// here — which is the exact shape of the defect being fixed.
//
// 🪤 The marker is deliberately ABSENT for a hand-authored skill (most skills), so the third case is
// the vacuity control: without it, a renderer that printed a marker unconditionally would pass.

const ROW = {
  path: '/x',
  always: false,
  loaded_by_agents: [] as string[],
  type: 'installed',
}

const SKILLS = [
  { ...ROW, key: 'termbase', name: 'termbase', description: 'check the termbase', source: 'local', provenance: 'taught' },
  { ...ROW, key: 'release', name: 'release', description: 'cut a release', source: 'local', provenance: 'auto' },
  { ...ROW, key: 'byhand', name: 'byhand', description: 'placed by hand', source: 'local', provenance: '' },
]

function mockApi(skills: unknown[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      skills: () => Promise.resolve(skills),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      learningSummary: () => Promise.resolve(null),
    },
  }))
}

async function mountSkillsPage() {
  const { SkillsPage } = await import('./SkillsPage')
  render(<SkillsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('check the termbase')).toBeInTheDocument())
}

/** The row element for a named skill.
 *
 *  `ListRow` puts the row's accessible name on a zero-content overlay `<button>` that is a
 *  SIBLING of the row content (`ui/ListScaffold.tsx` explains why), so the row is that
 *  button's parent — going through the role means these read the same tree AT does. */
function row(name: string): HTMLElement {
  const el = screen.getByRole('button', { name }).parentElement
  expect(el, `no row found for "${name}"`).toBeTruthy()
  return el as HTMLElement
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/skills names how each skill came to exist', () => {
  it('marks a taught skill "taught" and STILL shows its local tier', async () => {
    mockApi(SKILLS)
    await mountSkillsPage()

    const taught = row('termbase')
    expect(taught.textContent).toMatch(/taught/)
    // The tier survived. This is the assertion that fails if provenance ever overrides `source`.
    expect(taught.textContent).toMatch(/local/)
  })

  it('marks an auto-extracted skill "auto"', async () => {
    mockApi(SKILLS)
    await mountSkillsPage()

    const auto = row('release')
    expect(auto.textContent).toMatch(/auto/)
    expect(auto.textContent).toMatch(/local/)
  })

  it('says NOTHING about provenance for a hand-authored skill', async () => {
    mockApi(SKILLS)
    await mountSkillsPage()

    // Vacuity control: the common row must be unchanged, or the marker is noise rather than a fact.
    const byHand = row('byhand')
    expect(byHand.textContent).not.toMatch(/taught/)
    expect(byHand.textContent).not.toMatch(/auto/)
    expect(byHand.textContent).toMatch(/local/)
  })

  it('explains the marker on hover — "taught" alone does not say what taught it', async () => {
    mockApi(SKILLS)
    await mountSkillsPage()

    const marker = screen.getByTitle(/Taught in a session/i)
    expect(marker.textContent).toMatch(/taught/)
    expect(screen.getByTitle(/Extracted automatically/i).textContent).toMatch(/auto/)
  })

  it('tolerates a backend that has not sent the field at all', async () => {
    // `provenance` is optional on the wire, so an older gateway (or a surface that builds a
    // SkillItem by hand) must render the row rather than a blank marker.
    mockApi([{ ...ROW, key: 'k', name: 'k', description: 'no provenance key', source: 'local' }])
    const { SkillsPage } = await import('./SkillsPage')
    render(<SkillsPage query={{}} setQuery={() => {}} />)
    await waitFor(() => expect(screen.getByText('no provenance key')).toBeInTheDocument())
    expect(row('k').textContent).not.toMatch(/taught/)
  })
})
