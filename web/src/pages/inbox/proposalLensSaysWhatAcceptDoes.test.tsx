import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { ProposalsLens } from './ProposalsLens'
import { acceptOf, canBatchApprove, refusalText } from './proposalLens'
import type { InboxItem, InboxProposal } from '../../lib/api'

// The Proposals lens says what each learning proposal's Accept does, as its card says it
// (`learning.review.accept_for`, carried on the row as `refs.card.accept`): a template saves a
// template, a lesson records that you agree, a plan shape can't be accepted here. Every learning
// row used to read "· Installs a skill", whatever it was, because all of them carry the one apply
// case `skill_promotion`; and the lens offered Approve on a plan shape, whose Accept refuses.
//
// A refusal shows the gateway's sentence once: no class name, no double stop, and "it still
// waits" said once.

const applyInboxProposal = vi.fn((_id: string, _edited?: InboxProposal) =>
  Promise.resolve({ ok: true } as { ok: boolean; error?: string }),
)

vi.mock('../../lib/api', () => ({
  api: {
    applyInboxProposal: (id: string, edited?: InboxProposal) => applyInboxProposal(id, edited),
  },
}))

type Accept = { effect: 'installs' | 'applies' | 'records' | 'none'; verb: string; sentence: string; confirm: boolean }

function learningRow(id: string, title: string, accept: Accept | null, variant = ''): InboxItem {
  return {
    id,
    channel: '',
    channel_name: '',
    message: title,
    sender_id: '',
    sender_name: '',
    item_kind: 'proposal',
    status: 'pending',
    refs: {
      learning_proposal: `lp-${id}`,
      ...(accept ? { card: { headline: title, detail: '', name: '', label: 'Template', variant, accept } } : {}),
      proposal: {
        title,
        preview: '',
        preview_kind: 'text',
        provenance: 'learning',
        editable: false,
        apply: { skill_promotion: { pid: `lp-${id}` } },
      },
    },
  } as unknown as InboxItem
}

const template = learningRow('t1', 'Template: nightly-digest', {
  effect: 'installs', verb: 'Save template', sentence: 'Saves the template nightly-digest to Workflows', confirm: false,
}, 'template.named')
const prompt = learningRow('p1', 'Prompt: release notes', {
  effect: 'installs', verb: 'Save prompt', sentence: 'Saves the prompt release-notes', confirm: false,
}, 'prompt.card')
const skill = learningRow('s1', 'Skill: restore-drill', {
  effect: 'installs', verb: 'Install skill', sentence: 'Installs restore-drill with this procedure', confirm: false,
}, 'skill.promoted')
const change = learningRow('c1', 'Fix the weekly report', {
  effect: 'applies', verb: 'Apply changes', sentence: 'Saves weekly-report as a new version with these 2 changes', confirm: false,
}, 'template_diff')
const lesson = learningRow('l1', 'You prefer short answers', {
  effect: 'records', verb: 'Accept', sentence: 'Accepting records that you agree. It changes nothing the agent reads yet.', confirm: false,
}, 'lesson.inferred')
const PLAN_SHAPE =
  'This names a repeated pattern. It has no steps to save, so it can\'t be accepted. Open the latest run to make a template from it, or reject this. Nothing changed, and it is still pending.'
const planShape = learningRow('ps1', 'A plan you repeat: check the drive, then the backup', {
  effect: 'none', verb: '', sentence: PLAN_SHAPE, confirm: false,
}, 'template.plan_shape')
const workflowRow = {
  ...template,
  id: 'w1',
  message: 'Run nightly',
  refs: {
    proposal: {
      title: 'Run nightly',
      preview: '',
      preview_kind: 'text',
      provenance: 'agent',
      editable: false,
      apply: { workflow: { ref: 'nightly' } },
    },
  },
} as InboxItem

function row(title: RegExp): HTMLElement {
  const el = screen.getByText(title).closest('.rounded-lg')
  if (!el) throw new Error(`no row for ${title}`)
  return el as HTMLElement
}

describe('what a learning proposal says its Accept does', () => {
  it('reads the card the gateway filed with the row', () => {
    expect(acceptOf(template)).toEqual({ effect: 'installs', sentence: 'Saves the template nightly-digest to Workflows' })
    expect(acceptOf(planShape)?.effect).toBe('none')
    expect(acceptOf(workflowRow)).toBeNull()
  })

  it.each([
    [template, 'Saves the template nightly-digest to Workflows'],
    [prompt, 'Saves the prompt release-notes'],
    [skill, 'Installs restore-drill with this procedure'],
    [change, 'Saves weekly-report as a new version with these 2 changes'],
    [lesson, 'Accepting records that you agree. It changes nothing the agent reads yet.'],
  ])('labels %s by its Accept and offers Approve', (it_) => {
    render(<ProposalsLens items={[it_ as InboxItem]} onChanged={() => {}} />)
    const shown = row(new RegExp(String((it_ as InboxItem).message).replace(/[.*+?^${}()|[\]\\:]/g, '\\$&')))
    const card = ((it_ as InboxItem).refs as { card: { accept: Accept } }).card
    expect(shown.textContent).toContain(card.accept.sentence)
    expect(shown.textContent).not.toMatch(/Installs a skill/)
    expect(within(shown).getByRole('button', { name: /^Approve$/ })).toBeTruthy()
  })

  it('a proposal whose Accept can’t be done here says why and offers no Approve', () => {
    render(<ProposalsLens items={[planShape, template]} onChanged={() => {}} />)
    const shown = row(/A plan you repeat/)
    expect(shown.textContent).toContain(PLAN_SHAPE)
    expect(shown.textContent).not.toMatch(/Installs a skill/)
    expect(within(shown).queryByRole('button', { name: /Approve/ })).toBeNull()
    expect(within(shown).queryByRole('checkbox')).toBeNull()
    // The other row still offers its own.
    expect(within(row(/Template: nightly-digest/)).getByRole('button', { name: /^Approve$/ })).toBeTruthy()
  })

  it('a proposal whose Accept can’t be done here is never in a batch', () => {
    // Rows batch by their card's variant too (`groupKey`): two templates do.
    const another = learningRow('t2', 'Template: weekly-report', {
      effect: 'installs', verb: 'Save template', sentence: 'Saves the template weekly-report to Workflows', confirm: false,
    }, 'template.named')
    expect(canBatchApprove([template, another])).toBe(true)
    expect(canBatchApprove([template, planShape])).toBe(false)
    expect(canBatchApprove([planShape])).toBe(false)
  })

  it('a learning row with no card says only that Accept accepts it', () => {
    const bare = learningRow('b1', 'An older proposal', null)
    render(<ProposalsLens items={[bare]} onChanged={() => {}} />)
    const shown = row(/An older proposal/)
    expect(shown.textContent).not.toMatch(/Installs a skill/)
    expect(shown.textContent).toMatch(/Accepts the proposal/)
  })

  it('a row of another kind keeps its own label', () => {
    render(<ProposalsLens items={[workflowRow]} onChanged={() => {}} />)
    expect(row(/Run nightly/).textContent).toMatch(/Starts a workflow/)
  })
})

describe('a refusal shows the gateway’s sentence once', () => {
  beforeEach(() => applyInboxProposal.mockClear())

  const TAKEN = 'A workflow named nightly-digest already exists, so nothing was saved and the proposal still waits.'

  it.each([
    [TAKEN, true, TAKEN],
    [PLAN_SHAPE, true, PLAN_SHAPE],
    ['dispatcher refused', true, 'Not applied: dispatcher refused. It still waits.'],
    ['dispatcher refused', false, 'Not applied: dispatcher refused. It still waits.'],
    ['proposal expired.', false, 'Not applied: proposal expired. It still waits.'],
    // An app's or a provider's own words keep the frame, whatever they say.
    [TAKEN, false, `Not applied: ${TAKEN} It still waits.`],
    ['Saved, still waiting to sync', false, 'Not applied: Saved, still waiting to sync. It still waits.'],
    ['', true, 'Not applied. It still waits.'],
    [undefined, false, 'Not applied. It still waits.'],
  ])('%j (learning %s) reads %j', (error, learning, said) => {
    expect(refusalText(error, learning)).toBe(said)
  })

  it('never doubles a stop or names a class', () => {
    for (const error of [TAKEN, PLAN_SHAPE, 'dispatcher refused', 'proposal expired.']) {
      const said = refusalText(error, true)
      expect(said).not.toMatch(/\.\./)
      expect(said).not.toMatch(/\b[A-Z]\w*Error:/)
      expect(said.match(/still (waits|pending)/gi)?.length).toBe(1)
    }
  })

  it('an app’s refusal that says it “still waits” still says it wasn’t applied', async () => {
    const app = {
      ...workflowRow,
      id: 'a1',
      message: 'Send the reply?',
      refs: {
        proposal: {
          title: 'Send the reply?',
          preview: '',
          preview_kind: 'text',
          provenance: 'app:mail',
          editable: false,
          apply: { app_callback: { app: 'mail', route: 'send' } },
        },
      },
    } as unknown as InboxItem
    applyInboxProposal.mockResolvedValueOnce({ ok: false, error: 'Queued: it still waits for the mail server' })
    render(<ProposalsLens items={[app]} onChanged={() => {}} />)
    fireEvent.click(within(row(/Send the reply/)).getByRole('button', { name: /^Approve$/ }))
    await waitFor(() => expect(within(row(/Send the reply/)).getByRole('status').textContent).toBe(
      'Not applied: Queued: it still waits for the mail server. It still waits.',
    ))
  })

  it('the row says the refusal the gateway answered', async () => {
    applyInboxProposal.mockResolvedValueOnce({ ok: false, error: TAKEN })
    render(<ProposalsLens items={[template]} onChanged={() => {}} />)
    fireEvent.click(within(row(/Template: nightly-digest/)).getByRole('button', { name: /^Approve$/ }))
    await waitFor(() => expect(within(row(/Template: nightly-digest/)).getByRole('status').textContent).toBe(TAKEN))
  })
})
