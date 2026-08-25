import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { refinePillLabel, TRIGGER_LABEL, SkillProposals } from './SkillProposals'
import { diffLineColor, UnifiedDiff } from '../../ui/UnifiedDiff'
import { api } from '../../lib/api'
import type { SkillProposal, SkillProposalDetail } from '../../lib/api'

// ── LV-5: a refine proposal is a CHANGE, so the surface shows the change ──────────────────────────
//
// Three claims are rail-worthy here, and each one was a real gap before:
//
//  1. The refine pill says WHY, from a MAP — an unknown trigger must render as plain 'refine'
//     rather than leak an enum into the UI.
//  2. The expanded row renders the DIFF (a `+` line reads as added, a `-` line as removed) and
//     the version accepting would create, so the user can see what they are approving.
//  3. The accept confirmation names the VERSION. Without it, refining a skill that already had
//     refinements is indistinguishable from refining one for the first time.
//
// The diff is untrusted text — it is derived from a turn's own transcript. `renders the patch as
// TEXT` is the rail for that: markup inside the patch must arrive as characters, never as DOM.

const proposal = (over: Partial<SkillProposal> = {}): SkillProposal => ({
  id: 'release-flow-abc123',
  slug: 'release-flow',
  description: 'Refined after you corrected this turn',
  triggers: '',
  kind: 'refine',
  refine_target: 'release-flow',
  trigger: 'correction',
  session_key: 'sess:1',
  created_at: '2026-08-25T12:00:00+00:00',
  status: 'pending',
  procedure_preview: 'When this skill applies, honor the correction…',
  ...over,
})

const DIFF = [
  '--- release-flow/SKILL.md',
  '+++ release-flow/SKILL.md',
  '@@ -4,3 +4,9 @@',
  ' Run `pip install`.',
  '-old line to drop',
  '+',
  '+## Refinement v2 (2026-08-25, from a correction)',
  '+',
  '+> No, use uv instead of pip.',
].join('\n')

const detail = (over: Partial<SkillProposalDetail> = {}): SkillProposalDetail => ({
  ...proposal(),
  procedure_md: 'When this skill applies, honor the correction the user gave.',
  source_excerpt: '',
  diff: DIFF,
  version: 2,
  ...over,
})

describe('refine pill label', () => {
  it('names the stumble for every trigger the backend can emit', () => {
    // Kept in step with `after_turn_review.STUMBLE_TRIGGERS`. A trigger with no label reads as a
    // bare 'Refine' — the user loses the reason, so this is where the omission gets noticed.
    for (const t of ['correction', 'failure_retry', 'rejection']) {
      expect(TRIGGER_LABEL[t], t).toBeTruthy()
      expect(refinePillLabel(t)).toBe(`Refine · ${TRIGGER_LABEL[t]}`)
    }
  })

  it('falls back to plain refine for an absent or unknown trigger', () => {
    expect(refinePillLabel(undefined)).toBe('Refine')
    expect(refinePillLabel('')).toBe('Refine')
    expect(refinePillLabel('some_future_trigger')).toBe('Refine')
  })

  it('produces DISTINCT labels per trigger', () => {
    // The vacuity floor: every assertion above passes against a helper returning one constant.
    const labels = new Set(['correction', 'failure_retry', 'rejection'].map(refinePillLabel))
    expect(labels.size).toBe(3)
  })
})

describe('unified diff renderer', () => {
  it('maps added, removed, hunk and header lines to distinct tokens', () => {
    const added = diffLineColor('+new')
    const removed = diffLineColor('-old')
    const hunk = diffLineColor('@@ -1,2 +1,3 @@')
    expect(new Set([added, removed, hunk]).size).toBe(3)
    // The file headers must NOT read as an add/remove — '+++' and '---' lead every patch.
    expect(diffLineColor('+++ a/SKILL.md')).not.toBe(added)
    expect(diffLineColor('--- a/SKILL.md')).not.toBe(removed)
    // Ordinary context has no color at all, so the marked lines are what stands out.
    expect(diffLineColor(' context')).toBeUndefined()
  })

  it('renders the patch as TEXT, so markup inside it cannot become DOM', () => {
    const hostile = '+<img src=x onerror=alert(1)>\n+**not bold**'
    const { container } = render(<UnifiedDiff patch={hostile} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(container.textContent).toContain('**not bold**')
  })

  it('keeps a line box for a blank patch line', () => {
    // A blank context line that collapsed would shift every line after it out of alignment with
    // the patch it is being read against.
    const { container } = render(<UnifiedDiff patch={'a\n\nb'} />)
    const rows = container.querySelectorAll('pre > div')
    expect(rows.length).toBe(3)
    expect(rows[1].textContent).not.toBe('')
  })
})

describe('SkillProposals refine row', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the diff, the version it would create, and the reason on the pill', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'skillProposalDetail').mockResolvedValue(detail())

    render(<SkillProposals />)
    const row = await screen.findByText('release-flow')
    expect(screen.getByText('Refine · you corrected it')).toBeTruthy()

    await userEvent.click(row)
    // The heading names the target AND the version accepting would create.
    const heading = await waitFor(() => screen.getByText(/^Change to release-flow/))
    expect(heading.textContent).toMatch(/refinement v2/i)
    // The patch itself is on the surface, not just its summary.
    expect(screen.getByText('+## Refinement v2 (2026-08-25, from a correction)')).toBeTruthy()
    // …and the raw procedure body is NOT also dumped: the diff's + lines already carry it.
    expect(screen.queryByText('Procedure')).toBeNull()
  })

  it('says plainly when a refine has NO diff, instead of showing an empty change', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'skillProposalDetail').mockResolvedValue(detail({ diff: '', version: 0 }))

    render(<SkillProposals />)
    await userEvent.click(await screen.findByText('release-flow'))
    await waitFor(() => expect(screen.getByText(/no longer installed/i)).toBeTruthy())
    // The fallback still shows the body, so the row is never a dead end.
    expect(screen.getByText('Procedure')).toBeTruthy()
  })

  it('names the VERSION in the accept confirmation', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'acceptSkillProposal').mockResolvedValue({ ok: true, name: 'release-flow', version: 2 })

    render(<SkillProposals />)
    await screen.findByText('release-flow')
    await userEvent.click(screen.getByRole('button', { name: /accept/i }))
    await waitFor(() => expect(screen.getByText(/refinement v2/i)).toBeTruthy())
  })

  it('omits the version for a kind=new accept, which creates rather than versions', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({
      proposals: [proposal({ kind: 'new', trigger: '', refine_target: '' })],
      lastReview: null,
    })
    vi.spyOn(api, 'acceptSkillProposal').mockResolvedValue({ ok: true, name: 'auto/release-flow', version: 0 })

    render(<SkillProposals />)
    await screen.findByText('release-flow')
    await userEvent.click(screen.getByRole('button', { name: /accept/i }))
    await waitFor(() => expect(screen.getByText(/Accepted → auto\/release-flow/)).toBeTruthy())
    expect(screen.queryByText(/refinement v/i)).toBeNull()
  })
})
