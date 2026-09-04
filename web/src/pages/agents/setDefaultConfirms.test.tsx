import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── #666: Set default confirms before rewriting the GLOBAL default agent ─────────────────────────
//
// "Set default" rewrote `default_agent` — the agent every new chat starts with — on one
// unconfirmed click, while Delete three lines away in the same detail component both confirms
// AND refuses to touch the default (evidence the state is understood to be load-bearing). The
// previous default's name appeared nowhere in the interaction, so there was no way back short
// of an out-of-band PUT. The write now goes through the house `confirm` dialog, naming BOTH
// agents so the user can see what is being replaced.
//
// The failure-reporting half of the issue landed separately (`reportingWrite` replaced the
// `.catch(() => {})`); these rails pin the confirm half: gated, named, dismiss writes nothing,
// confirm writes exactly once.

const catalog = {
  agents: [
    { name: 'scout', model: 'x' },
    { name: 'probe', model: 'y' },
  ],
  default_agent: 'scout',
}

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(catalog),
      agentProviders: () => Promise.resolve([]),
      syncAgents: () => Promise.resolve({ ok: true }),
      setDefaultAgent: setDefaultSpy,
      ...over,
    },
  }))
}

let setDefaultSpy = vi.fn()

async function mountOpenProbe() {
  const { AgentsListPage } = await import('./AgentsListPage')
  const { DialogHost } = await import('../../ui/dialog/DialogHost')
  render(
    <>
      <AgentsListPage query={{ open: 'native:probe' }} setQuery={() => {}} onCreate={() => {}} />
      <DialogHost />
    </>,
  )
  // The detail panel for the non-default agent carries the Set default action.
  return await screen.findByRole('button', { name: /Set default/i })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  setDefaultSpy = vi.fn().mockResolvedValue({ ok: true, default_agent: 'probe' })
})

afterEach(async () => {
  // Drain any dialog a test left open, or it leaks into the next one.
  const { subscribeDialogs, closeDialog } = await import('../../ui/dialog/dialogStore')
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list as { id: number }[] })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('Set default confirms before rewriting the global default (#666)', () => {
  it('the dialog names BOTH agents — the replacement and the one being replaced', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('probe')
    // The previous default's name appears nowhere else in the interaction —
    // this dialog is the only record of the way back.
    expect(dialog.textContent).toContain('scout')
    expect(setDefaultSpy).not.toHaveBeenCalled()
  })

  it('dismissing writes nothing', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(setDefaultSpy).not.toHaveBeenCalled()
  })

  it('confirming writes exactly once', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    const dialog = await screen.findByRole('dialog')
    // The detail panel's own "Set default" button shares the name — scope to the dialog.
    const { within } = await import('@testing-library/react')
    await userEvent.click(within(dialog).getByRole('button', { name: /^Set default$/i }))
    await waitFor(() => expect(setDefaultSpy).toHaveBeenCalledTimes(1))
    expect(setDefaultSpy).toHaveBeenCalledWith('probe')
  })
})
