// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { SkillProposal } from '../../lib/api'
import { NeedsYouHarness, waitingList } from '../../test/needsYouHarness'

// ── A skill proposal's panel shows the change ───────────────────────────────────────────────────
//
// A refinement's panel is the change itself: the block it appends to its skill, as a diff that
// wraps (nothing scrolls inside the pane, so it takes no tab stop) and draws ink only, its hunk
// headers quiet. A new skill's is its procedure and the words it fires on. The decide bar says where
// Accept lands, and your words are where it came from.

const waiting = vi.fn()
const skillProposals = vi.fn()
const skillProposalDetail = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => skillProposals(),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    skillProposalDetail: (...a: unknown[]) => skillProposalDetail(...a),
    chatSessionDetail: () => Promise.resolve({ key: 'c', title: '', messages: [], run_state: 'idle' }),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

const DIFF = [
  '--- a/incident-writeup/SKILL.md', '+++ b/incident-writeup/SKILL.md', '@@ -40,0 +41,4 @@',
  '+## Refinement v3 (2026-11-11, from a correction)', '+_Refined after you corrected this turn_', '+', '+> Always link the metrics dashboard',
].join('\n')

const REFINE = {
  id: 'sp-1', slug: 'auto/incident-writeup', description: 'Refined after you corrected this turn', triggers: '', kind: 'refine',
  refine_target: 'incident-writeup', session_key: 'dashboard:chat-3', created_at: '2026-11-15T10:00:00Z', status: 'pending', procedure_preview: '',
  card: { headline: '“Always link the metrics dashboard”', detail: 'Refinement v3 · you corrected it', name: 'incident-writeup', label: 'Refine a skill',
    accept: { verb: 'Add refinement', sentence: 'Adds refinement v3 to incident-writeup. Its own files stay as they are.' } },
  accept_target: 'incident-writeup', target_resolves: true,
} as unknown as SkillProposal

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList())
})

describe('a refinement’s panel is its change', () => {
  it('the appended block as a wrapping diff, named for its skill, ink only and no tab stop', async () => {
    skillProposals.mockResolvedValue({ proposals: [REFINE], lastReview: null })
    skillProposalDetail.mockResolvedValue({ ...REFINE, procedure_md: '', source_excerpt: 'Always link the metrics dashboard', diff: DIFF, version: 3 })
    render(<NeedsYouHarness query={{ open: 'proposal:sp-1' }} />)
    const pane = await screen.findByRole('region', { name: /Always link the metrics dashboard/ })
    expect(await within(pane).findByRole('heading', { level: 3, name: 'Change to incident-writeup, refinement v3' })).toBeInTheDocument()
    const diff = within(pane).getByLabelText('The change to incident-writeup')
    expect(diff.getAttribute('tabindex')).toBeNull()
    expect(diff.className).toContain('whitespace-pre-wrap')
    const hunk = [...diff.children].find((l) => l.textContent?.startsWith('@@')) as HTMLElement
    expect(hunk.style.color).toBe('var(--color-on-surface-low)')
    expect(within(pane).getByText('Always link the metrics dashboard', { selector: 'blockquote' })).toBeInTheDocument()
    const bar = pane.querySelector('[data-decide-bar]') as HTMLElement
    expect(within(bar).getByText('Adds refinement v3 to incident-writeup. Its own files stay as they are.')).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: /^Add refinement: / })).toBeInTheDocument()
  })

  it('a new skill’s panel is its procedure and the words it fires on', async () => {
    const created = { ...REFINE, id: 'sp-2', kind: 'new', slug: 'csv-dedupe', refine_target: '', accept_target: '', target_resolves: false,
      card: { ...REFINE.card!, headline: 'Remove duplicate rows from a CSV', accept: { verb: 'Install skill', sentence: 'Installs auto/csv-dedupe' } } }
    skillProposals.mockResolvedValue({ proposals: [created], lastReview: null })
    skillProposalDetail.mockResolvedValue({ ...created, triggers: 'dedupe, duplicate rows, csv', procedure_md: '1. Read the CSV\n2. Drop rows whose key was seen', source_excerpt: '' })
    render(<NeedsYouHarness query={{ open: 'proposal:sp-2' }} />)
    const pane = await screen.findByRole('region', { name: /Remove duplicate rows/ })
    expect(await within(pane).findByRole('heading', { level: 3, name: 'The procedure' })).toBeInTheDocument()
    expect(within(pane).getByText('Read the CSV')).toBeInTheDocument()
    const fires = within(pane).getByRole('heading', { level: 3, name: 'It fires on' }).closest('section')!
    expect([...fires.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['dedupe', 'duplicate rows', 'csv'])
    expect(within(pane.querySelector('[data-decide-bar]') as HTMLElement).getByText('Installs auto/csv-dedupe')).toBeInTheDocument()
  })
})
