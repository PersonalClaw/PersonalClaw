import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { InboxItem, Trigger as WireTrigger } from '../../lib/api'

// ── A trigger held back by its folder says so, and the folder is trusted where that is said ─────
//
// An automation's agent only reads in a working folder its owner has not trusted, whatever write
// access its step asks for, and an agent on an agent CLI is given no files to change. The trigger's
// panel says which holds it back (the server's `held_back`), and when it is a folder in Preview it
// offers Trust there. The request a folder raises in the Inbox on its first such run asked "Trust
// this project folder?" and offered nothing to answer it with: it offers Trust too. Trust asks
// first, and sends the one folder the server named.

const trustProjectFolder = vi.fn()
const confirm = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      trustProjectFolder: (...a: unknown[]) => trustProjectFolder(...a),
      updateInboxItem: () => Promise.resolve({}),
      favoriteInboxItem: () => Promise.resolve({}),
      draftInboxReply: () => Promise.resolve({}),
      restoreInboxItem: () => Promise.resolve({}),
    },
  }
})
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../ui/dialog')>()),
  confirm: (...a: unknown[]) => confirm(...a),
}))
vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))

const FOLDER = '/home/user/Projects/site'
const PREVIEW_WHY = 'Its agent only reads: its working folder ~/Projects/site is in Preview until you trust it.'

const row = (over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: 'store:file:site-rebuild', raw_id: 'file:site-rebuild',
  name: 'Rebuild the site', enabled: true,
  action: { provider: 'run-prompt', config: { message: 'Rebuild the site.', cwd: '~/Projects/site', capability: 'mutating' } },
  store_kind: 'file', spec: { paths: ['~/Projects/site/**'] }, broken: [],
  held_back: { why: PREVIEW_WHY, folder: FOLDER },
  ...over,
})

async function panel(trigger: WireTrigger, onChanged = vi.fn()) {
  const { StoreTriggerDetail } = await import('./StoreTriggerDetail')
  render(<StoreTriggerDetail trigger={trigger} onChanged={onChanged} onDeleted={() => {}} />)
  return onChanged
}

beforeEach(() => {
  trustProjectFolder.mockReset().mockResolvedValue({ dir: FOLDER, trusted: true, decided_at: 'now' })
  confirm.mockReset()
})
afterEach(() => cleanup())

describe('a trigger its working folder holds back', () => {
  it('says why on its panel, and Trust asks first, then trusts that one folder', async () => {
    confirm.mockResolvedValue(true)
    const onChanged = await panel(row())

    expect(screen.getByText(PREVIEW_WHY)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Trust this folder' }))

    await waitFor(() => expect(trustProjectFolder).toHaveBeenCalledWith(FOLDER))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm.mock.calls[0][0].title).toBe(`Trust ${FOLDER}?`)
    expect(confirm.mock.calls[0][0].confirmLabel).toBe('Trust this folder')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('trusts nothing when the question is declined', async () => {
    confirm.mockResolvedValue(false)
    const onChanged = await panel(row())

    fireEvent.click(screen.getByRole('button', { name: 'Trust this folder' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(trustProjectFolder).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('offers no Trust when no folder holds it back: an agent CLI is not a folder to trust', async () => {
    const why = 'Its agent runs on example-cli, whose own file edits PersonalClaw can’t limit to ~/Notes/kitchen.md, so it may not change it.'
    await panel(row({ held_back: { why, folder: '' } }))

    expect(screen.getByText(why)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Trust this folder' })).toBeNull()
  })

  it('offers no Trust on someone else’s trigger', async () => {
    await panel(row({ author: 'alice', read_only: true }))

    expect(screen.getByText(PREVIEW_WHY)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Trust this folder' })).toBeNull()
  })

  it('says nothing when nothing holds it back', async () => {
    await panel(row({ held_back: null }))

    expect(screen.queryByText(/does less than its step asks/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Trust this folder' })).toBeNull()
  })
})

describe('the request a folder in Preview raises in the Inbox', () => {
  const request = (over: Partial<InboxItem> = {}): InboxItem => ({
    id: 'agent_request_1790000000', channel: 'system', channel_name: 'system',
    message: 'Trust this project folder?\n\nAn automation given write access works in ' + FOLDER + '.',
    sender_id: 'system', sender_name: 'system', classification: 'needs_reply', confidence: 'high',
    status: 'pending', source: 'system', can_reply: false, item_kind: 'agent_request',
    refs: { dir: FOLDER, guardrail: 'project_trust' }, created_at: 1790000000,
    ...over,
  } as InboxItem)

  async function open(item: InboxItem, onChanged = vi.fn()) {
    const { InboxDetail } = await import('../inbox/InboxDetail')
    render(<InboxDetail item={item} onChanged={onChanged} navigate={() => {}} />)
    return onChanged
  }

  it('is answered there: Trust asks first, then trusts the folder it names', async () => {
    confirm.mockResolvedValue(true)
    const onChanged = await open(request())

    fireEvent.click(screen.getByRole('button', { name: 'Trust this folder' }))

    await waitFor(() => expect(trustProjectFolder).toHaveBeenCalledWith(FOLDER))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('offers nothing once it is settled', async () => {
    await open(request({ status: 'handled' }))
    expect(screen.queryByRole('button', { name: 'Trust this folder' })).toBeNull()
  })
})
