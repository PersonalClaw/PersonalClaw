import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ScheduleForm, emptyDraft, toDraft, draftToPayload, type ScheduleDraft } from './ScheduleForm'
import { channelLabel, splitChannel } from './notifyChannel'
import type { ScheduleJob } from '../../lib/api'

// ── A schedule's "Notify channel" names a chat channel, and a chat on it ────────────────────────
//
// The field was a free-text "Optional Slack channel ID", and the server refused any id not shaped
// like one, so no other channel's chats could be named. It is a picker over your chat channels now,
// then "You, in a direct message" or "A chat or channel" with that channel's id. The channel checks
// the id when the schedule is saved; its sentence is the save error.

const h = vi.hoisted(() => ({
  channels: vi.fn(),
  triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return { ...actual, api: { ...actual.api, channels: h.channels, triggerHistory: h.triggerHistory } }
})

vi.mock('../../lib/agents', () => ({
  useAgentCatalog: () => ({ options: [] }),
  useModelCatalog: () => ({ options: [] }),
}))

const CHANNELS = [
  { name: 'webui', display_name: 'Web UI', connected: true, health: { state: 'ready', detail: '' } },
  { name: 'numchat', display_name: 'NumChat', connected: true, health: { state: 'ready', detail: '' } },
  { name: 'codechat', display_name: 'CodeChat', connected: false, health: { state: 'offline', detail: '' } },
]

beforeEach(async () => {
  h.channels.mockReset().mockResolvedValue(CHANNELS)
  const { invalidateKeys } = await import('../../lib/data')
  invalidateKeys('settings:channels', true)
})

function mount(over: Partial<ScheduleDraft> = {}) {
  const onChange = vi.fn()
  const draft: ScheduleDraft = { ...emptyDraft(), ...over }
  const view = render(<ScheduleForm draft={draft} onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  return { onChange, view, draft }
}

const picker = () => screen.getByRole('combobox', { name: 'Chat channel for results' }) as HTMLSelectElement

describe('the Notify channel picker', () => {
  it('lists your chat channels by name, and not the dashboard', async () => {
    mount()
    await waitFor(() => expect(within(picker()).getByRole('option', { name: 'NumChat' })).toBeTruthy())
    const names = within(picker()).getAllByRole('option').map((o) => o.textContent)
    expect(names).toEqual(["Don't send results to a chat channel", 'NumChat', 'CodeChat (not connected)'])
    expect(screen.queryByPlaceholderText('C0123456789')).toBeNull()
  })

  it('picking a channel sends results to your DMs there', async () => {
    const { onChange } = mount()
    await waitFor(() => expect(within(picker()).getByRole('option', { name: 'NumChat' })).toBeTruthy())
    fireEvent.change(picker(), { target: { value: 'numchat' } })
    expect(onChange.mock.calls.at(-1)?.[0].channel).toBe('numchat')
  })

  it('a chat on it takes that channel’s id', async () => {
    const { onChange } = mount({ channel: 'numchat' })
    await waitFor(() => expect(picker().value).toBe('numchat'))
    expect(screen.getByRole('radio', { name: 'You, in a direct message' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', { name: 'A chat or channel' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Chat or channel id' }), { target: { value: '-100123' } })
    expect(onChange.mock.calls.at(-1)?.[0].channel).toBe('numchat:-100123')
  })

  it('opens on the chat a saved schedule names', async () => {
    const draft = toDraft({ id: 'j', name: 'n', message: 'm', enabled: true, schedule: '', channel: 'numchat:4242' } as ScheduleJob)
    expect(draftToPayload(draft).channel).toBe('numchat:4242')
    mount(draft)
    await waitFor(() => expect(picker().value).toBe('numchat'))
    expect(screen.getByRole('radio', { name: 'A chat or channel' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('textbox', { name: 'Chat or channel id' })).toHaveValue('4242')
  })

  it('keeps a saved channel that is not set up here visible instead of blanking it', async () => {
    mount({ channel: 'C0EXAMPLE01' })
    await waitFor(() => expect(within(picker()).getByRole('option', { name: 'C0EXAMPLE01 (not set up here)' })).toBeTruthy())
    expect(picker().value).toBe('C0EXAMPLE01')
  })

  it("says it couldn't load your chat channels, even after Settings → Providers cached none", async () => {
    // Settings → Providers reads the same list with a catch and caches `[]` when it fails. Under
    // that key, this read's failure looked like "you have no chat channels".
    const { writeQuery } = await import('../../lib/data')
    writeQuery('settings:channels', [])
    h.channels.mockReset().mockRejectedValue(new Error('HTTP 500'))
    mount({ channel: 'numchat' })
    expect(await screen.findByText(/Couldn't load your chat channels/)).toBeTruthy()
    // And it doesn't claim the saved channel isn't set up: it couldn't check.
    expect(within(picker()).getAllByRole('option').map((o) => o.textContent))
      .toEqual(["Don't send results to a chat channel", 'numchat'])
  })
})

describe('the schedule panel', () => {
  it("keeps the route on the channel chip and says the channels' names couldn't be read", async () => {
    h.channels.mockReset().mockRejectedValue(new Error('HTTP 500'))
    const { ScheduleDetail } = await import('./ScheduleDetail')
    const job = { id: 'clock:j', name: 'Nightly', message: 'm', enabled: true, schedule: 'every 60s', channel: 'numchat',
      last_run_ts: null, last_status: null, run_count: 0, next_run_ts: null, is_running: false, warnings: [], broken: [] }
    render(<ScheduleDetail job={job as unknown as ScheduleJob} providers={[]} onSaved={vi.fn()} onDeleted={vi.fn()}
      onChanged={vi.fn()} editing={false} onEditingChange={vi.fn()} />)
    expect(await screen.findByText("Couldn't load your chat channels.")).toBeTruthy()
    expect(screen.getByText('↳ numchat')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('how a schedule row names its channel', () => {
  it('uses the channel’s own name, and says which chat', () => {
    expect(channelLabel('numchat', CHANNELS as never)).toBe('NumChat, your DMs')
    expect(channelLabel('numchat:-100123', CHANNELS as never)).toBe('NumChat · -100123')
    expect(channelLabel('C0EXAMPLE01', CHANNELS as never)).toBe('C0EXAMPLE01')
    expect(splitChannel('mail:a:b')).toEqual({ name: 'mail', target: 'a:b' })
  })
})
