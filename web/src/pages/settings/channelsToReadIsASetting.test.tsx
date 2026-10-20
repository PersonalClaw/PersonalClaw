import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import type { InboxProvider } from '../../lib/api'

// ── "Channels to read" (`inbox.watched_channels`) ───────────────────────────────────────────────
//
// Slack's inbox source reads the channels in `inbox.watched_channels`, and that list had no write
// path and no control: the source polled nothing unless someone edited config.json by hand. Both
// Inbox settings panels now list it, while a polled source says it reads it (`watches_channels`),
// named by that source. An edit is one id in or out (`api.saveListEdits`), and a refused id keeps
// the list and says why.

const saveListEdits = vi.fn()
const notify = vi.fn()

const SLACK: InboxProvider = { name: 'slack', display_name: 'Slack', source_name: 'slack', polled: true, watches_channels: true }
const MAIL: InboxProvider = { name: 'mail-inbox', display_name: 'Mail Inbox', source_name: 'mail-inbox', polled: true, watches_channels: false }

function mockApi(providers: InboxProvider[] | Error, stored: string[]) {
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify: (...a: unknown[]) => notify(...a) }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        inboxSettings: () => Promise.resolve({ auto_cleanup_enabled: false, retention_days: 90 }),
        personalclawConfig: () => Promise.resolve({ inbox: { enabled: false, engagement_ranking_enabled: false, watched_channels: stored }, proactive: {} }),
        inboxProviders: () => (providers instanceof Error ? Promise.reject(providers) : Promise.resolve(providers)),
        approvalRules: () => Promise.resolve({ rules: [], unreadable: [] }),
        proactiveStatus: () => Promise.resolve({}),
        saveListEdits: (...a: unknown[]) => saveListEdits(...a),
      },
    }
  })
}

const COPIES: Array<[string, () => Promise<{ InboxSettingsPanel: ComponentType }>]> = [
  ['Settings → Inbox', () => import('./InboxSettingsPanel')],
  ['the Inbox drawer', () => import('../inbox/InboxSettingsPanel')],
]

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  saveListEdits.mockReset()
  notify.mockReset()
})
afterEach(() => { cleanup(); vi.doUnmock('../../lib/api'); vi.doUnmock('../../app/appSdk') })

describe.each(COPIES)('%s', (_name, load) => {
  it('🔑 lists the channels Slack reads, and adds one by its id', async () => {
    mockApi([SLACK, MAIL], ['C0AAAA1111'])
    saveListEdits.mockResolvedValue(['C0AAAA1111', 'C0BBBB2222'])
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    expect(await screen.findByText('Channels to read')).toBeInTheDocument()
    expect(screen.getByText(/^Slack reads these channels into your Inbox\. Add each by its id/)).toBeInTheDocument()
    expect(screen.getByText('C0AAAA1111')).toBeInTheDocument()

    const add = screen.getByRole('textbox', { name: 'Add to channels to read' })
    fireEvent.change(add, { target: { value: 'C0BBBB2222' } })
    fireEvent.keyDown(add, { key: 'Enter' })

    await waitFor(() => expect(saveListEdits).toHaveBeenCalledWith(
      'inbox.watched_channels', ['C0AAAA1111'], ['C0AAAA1111', 'C0BBBB2222'],
    ))
    expect(await screen.findByText('C0BBBB2222')).toBeInTheDocument()
  })

  it('a refused id keeps the list as it was and says why', async () => {
    mockApi([SLACK], [])
    saveListEdits.mockRejectedValue(new Error("'general chat' is not a channel id: add each channel by its id alone, one word (for example C0123456789)"))
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    const add = await screen.findByRole('textbox', { name: 'Add to channels to read' })
    fireEvent.change(add, { target: { value: 'general chat' } })
    fireEvent.keyDown(add, { key: 'Enter' })

    await waitFor(() => expect(notify).toHaveBeenCalledWith(
      "Couldn't change the channels to read: 'general chat' is not a channel id: add each channel by its id alone, one word (for example C0123456789)",
      'error',
    ))
    expect(screen.queryByText('general chat')).not.toBeInTheDocument()
  })

  it('a failed read of the sources says so, rather than hiding the list as if none read it', async () => {
    mockApi(new Error('the gateway answered 503'), ['C0AAAA1111'])
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    expect(await screen.findByText(
      "Couldn't read the inbox sources, so the channels they read can't be shown: the gateway answered 503",
    )).toBeInTheDocument()
    expect(screen.queryByText('Channels to read')).not.toBeInTheDocument()
  })

  it('is not offered while no polled source reads channels', async () => {
    mockApi([MAIL], ['C0AAAA1111'])
    const { InboxSettingsPanel } = await load()
    render(<InboxSettingsPanel />)

    expect(await screen.findByText('Engagement ranking')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Engagement ranking' })).not.toBeDisabled())
    expect(screen.queryByText('Channels to read')).not.toBeInTheDocument()
  })
})
