import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError, type InboxItem, type SkillProposalDetail } from '../../lib/api'
import { InboxDetail } from './InboxDetail'

// ── Issue 283: installing a skill from an inbox proposal must SAY it installed one ────────────
//
// The accept path modelled only `busy` and `err`. Three consequences, all of them here:
//
//  1. NO CONFIRMATION. The endpoint's `{ok, name, version}` was discarded, so a successful
//     install looked exactly like having clicked nothing.
//  2. THE BUTTONS STAYED. They were gated on `!!busy` alone, so Install was clickable again the
//     instant the request settled — and the first accept has CONSUMED the proposal, so the
//     second call 409s and the server's raw `no proposal '<id>'` landed in the error slot.
//  3. Which is why the rail below is "the buttons are GONE", not "a second click is handled":
//     the re-click has to be unreachable, not merely survivable.
//
// The third defect the issue names — the inbox item keeping its status — is the SERVER's, and it
// already does it: `skills/proposals.py:_resolve_inbox_item` moves the item to a terminal status
// from inside `accept()`/`reject()`. What this file pins is that the panel re-reads the item
// afterwards (`onChanged`), which is what makes that transition visible.
//
// The api module is mocked at the boundary with the REAL `ApiError` kept: `act()` branches on
// `instanceof ApiError` to tell "answered somewhere else" from a genuine failure, and a fake class
// would make that discrimination fall through to the raw-message path this issue is about.

const acceptSkillProposal = vi.fn<(id: string) => Promise<{ ok: boolean; name: string; version: number }>>()
const rejectSkillProposal = vi.fn<(id: string) => Promise<void>>()
const skillProposalDetail = vi.fn<(id: string) => Promise<SkillProposalDetail>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      acceptSkillProposal: (id: string) => acceptSkillProposal(id),
      rejectSkillProposal: (id: string) => rejectSkillProposal(id),
      skillProposalDetail: (id: string) => skillProposalDetail(id),
    },
  }
})
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

const PID = 'release-flow-abc123'

function detail(over: Partial<SkillProposalDetail> = {}): SkillProposalDetail {
  return {
    id: PID,
    slug: 'release-flow',
    description: 'Refined after you corrected this turn',
    triggers: '',
    kind: 'refine',
    refine_target: 'release-flow',
    trigger: 'correction',
    session_key: 'sess:1',
    created_at: '2026-08-25T12:00:00+00:00',
    status: 'pending',
    procedure_preview: 'When this skill applies…',
    procedure_md: 'When this skill applies, honor the correction the user gave.',
    source_excerpt: '',
    version: 2,
    ...over,
  }
}

function proposalItem(): InboxItem {
  return {
    id: 'prop_1.000',
    message: 'A skill was proposed from your session.',
    sender_id: 'skills',
    sender_name: 'Skill reviewer',
    item_kind: 'proposal',
    classification: 'fyi',
    confidence: 'high',
    status: 'seen',
    refs: { skill_proposal: PID },
  } as unknown as InboxItem
}

/** Render the panel and wait until the proposal body (and its Install button) is on screen. */
async function openPanel(onChanged = () => {}) {
  render(<InboxDetail item={proposalItem()} onChanged={onChanged} navigate={() => {}} />)
  return waitFor(() => screen.getByRole('button', { name: /install skill/i }))
}

describe('inbox proposal decision confirms itself (issue 283)', () => {
  beforeEach(() => {
    acceptSkillProposal.mockReset()
    rejectSkillProposal.mockReset()
    skillProposalDetail.mockReset()
    skillProposalDetail.mockResolvedValue(detail())
  })

  it('names the installed skill AND the refinement version after a successful install', async () => {
    acceptSkillProposal.mockResolvedValue({ ok: true, name: 'release-flow', version: 2 })

    await userEvent.click(await openPanel())

    // The confirmation is the accept response, not a generic "done" — same sentence the Skills
    // page shows for the same decision, because both read it from `acceptedLabel`.
    const ok = await waitFor(() => screen.getByText(/Accepted → release-flow/))
    expect(ok.textContent).toMatch(/refinement v2/i)
  })

  it('omits the version for a kind=new accept, which creates rather than versions', async () => {
    skillProposalDetail.mockResolvedValue(detail({ kind: 'new', refine_target: '', version: 0 }))
    acceptSkillProposal.mockResolvedValue({ ok: true, name: 'auto/release-flow', version: 0 })

    await userEvent.click(await openPanel())

    await waitFor(() => expect(screen.getByText(/Accepted → auto\/release-flow/)).toBeTruthy())
    // The vacuity floor for the assertion above: it must be reading the payload, not a constant.
    expect(screen.queryByText(/refinement v/i)).toBeNull()
  })

  it('REMOVES the decision buttons once installed, so the 409 re-click is unreachable', async () => {
    acceptSkillProposal.mockResolvedValue({ ok: true, name: 'release-flow', version: 2 })

    await userEvent.click(await openPanel())
    await waitFor(() => expect(screen.getByText(/Accepted → release-flow/)).toBeTruthy())

    // All three offers are gone — not disabled. A disabled Install still describes an action that
    // has already happened, and Reject/Edit-first would both act on a consumed proposal.
    expect(screen.queryByRole('button', { name: /install skill/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^reject$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /edit first/i })).toBeNull()
    // …and the endpoint was reached exactly once, which is the defect measured end to end.
    expect(acceptSkillProposal).toHaveBeenCalledTimes(1)
  })

  it('re-reads the item after the decision, so the server-side status transition shows', async () => {
    acceptSkillProposal.mockResolvedValue({ ok: true, name: 'release-flow', version: 2 })
    const onChanged = vi.fn()

    await userEvent.click(await openPanel(onChanged))

    // `accept()` resolves the inbox item server-side; without this re-read the row would keep
    // claiming attention for a proposal that no longer needs deciding.
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('confirms a reject too — the other decision was equally silent', async () => {
    rejectSkillProposal.mockResolvedValue(undefined)

    await openPanel()
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }))

    await waitFor(() => expect(screen.getByText('Rejected')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /install skill/i })).toBeNull()
  })

  it('reads a 409 as ALREADY ANSWERED, never as the server sentence', async () => {
    // The cross-surface race that survives removing the buttons: answered on the Skills page (or
    // another tab) between this panel's load and the click.
    acceptSkillProposal.mockRejectedValue(new ApiError(`no proposal '${PID}'`, 409))

    await userEvent.click(await openPanel())

    await waitFor(() => expect(screen.getByText(/already answered/i)).toBeTruthy())
    expect(screen.queryByText(new RegExp(PID))).toBeNull()
  })

  it('still surfaces a GENUINE failure rather than swallowing it as answered', async () => {
    // The gate above must discriminate on status, not on "an error happened" — a 500 is a real
    // failure and the proposal is still pending.
    acceptSkillProposal.mockRejectedValue(new ApiError('overlay write failed', 500))

    await userEvent.click(await openPanel())

    await waitFor(() => expect(screen.getByText('overlay write failed')).toBeTruthy())
    expect(screen.queryByText(/already answered/i)).toBeNull()
    // The decision is still offered, because it did not happen.
    expect(screen.getByRole('button', { name: /install skill/i })).toBeTruthy()
  })
})
