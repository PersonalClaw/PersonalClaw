import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── A polled source that cannot be read says why, in the Inbox ───────────────────────────────────────
//
// The gateway polled the drop folder alone, so an installed Mail Inbox did nothing, and a source whose
// poll failed read exactly like a quiet one: the banner said "Also polling mail-inbox" over an inbox
// that nothing would ever reach. Now `/api/inbox/status` carries each polled source's label and its
// last poll's sentence, and the banner gives a failing one its own line.

const REFUSED =
  'the IMAP server imap.fv-mail.test:18941 refused the login for noor@fv-mail.test (AUTHENTICATIONFAILED)'

function mockApi(sources: unknown[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      inboxStatus: () => Promise.resolve({
        enabled: false, native_source_active: true, health: { last_poll_at: 0 },
        sources: [{ name: 'native', active: true, kind: 'push', can_reply: true }, ...sources],
      }),
      inbox: () => Promise.resolve([]),
      proactiveDigest: () => Promise.resolve({ installed: false }),
    },
  }))
}

async function renderInbox() {
  const { InboxPage } = await import('./InboxPage')
  render(<InboxPage query={{}} setQuery={() => {}} navigate={vi.fn()} />)
}

const DROP_FOLDER_OFF = { name: 'filesystem', label: 'Drop folder', active: false, kind: 'poll', can_reply: false }

describe('the Inbox names every polled source, and a failing one says why', () => {
  beforeEach(() => { vi.resetModules() })

  it('shows the sentence the source raised, under the source it belongs to', async () => {
    mockApi([
      { name: 'mail-inbox', label: 'Mail Inbox', active: true, kind: 'poll', can_reply: true, ok: false, error: REFUSED },
      DROP_FOLDER_OFF,
    ])
    await renderInbox()
    await waitFor(() => expect(screen.getByText(/Also polling Mail Inbox\./)).toBeInTheDocument())
    expect(screen.getByText(`Mail Inbox can't be read: ${REFUSED}. It tries again at the next poll.`))
      .toBeInTheDocument()
    expect(screen.queryByText(/Drop folder/), 'a source that is not polled is not listed as polled').toBeNull()
  })

  it('says nothing more for a source that read', async () => {
    mockApi([{ name: 'mail-inbox', label: 'Mail Inbox', active: true, kind: 'poll', can_reply: true, ok: true, error: '' }])
    await renderInbox()
    await waitFor(() => expect(screen.getByText(/Also polling Mail Inbox\./)).toBeInTheDocument())
    expect(screen.queryByText(/can't be read/)).toBeNull()
  })

  it('points at the inbox apps when nothing is polled', async () => {
    mockApi([DROP_FOLDER_OFF])
    await renderInbox()
    await waitFor(() => expect(
      screen.getByText(/Install an inbox app \(Mail Inbox, Slack\) to collect your mail and messages here\./),
    ).toBeInTheDocument())
  })
})
