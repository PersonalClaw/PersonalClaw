import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import type { ChannelRuntime, NotificationRuleRow } from '../../lib/api'

// ── "Send approvals to" (`agent.approval_channel`) ──────────────────────────────────────────────
//
// An approval asked on the first paired chat channel in name order — Discord, then Email, Slack,
// Telegram — and nothing in the product could change that, so an owner with four channels paired
// had their approvals asked on Discord. This is the control: it lists the channels that know you,
// keeps the old order as the default, writes the allowlisted path, and its description says what
// happens under the current choice. Driven through the real Notifications panel.
//
// Ledger 283: an approval for a chat that STARTED on a channel is asked in that chat first, since
// the person asking is there; the setting decides everything else (a chat here, an unattended run,
// a trigger). The sentence under the control says exactly that, under every choice.

const patchConfig = vi.fn()

const channel = (name: string, display_name: string, owner = ''): ChannelRuntime => ({
  name, display_name, connected: true, health: { state: 'ready' }, app: `${name}-channel`,
  owner: { id: owner, source: owner ? 'channel' : '' },
})

const CHANNELS: ChannelRuntime[] = [
  { name: 'webui', display_name: 'Web UI', connected: true, health: { state: 'ready' }, app: '' },
  channel('telegram', 'Telegram', '6041337201'),
  channel('email', 'Email'), // configured, but it does not know who you are
  channel('discord', 'Discord', '1052983746291048451'),
]

const approvalRow: NotificationRuleRow = {
  key: 'approval/requested', source: 'approval', kind: 'requested', label: 'Approval needed', severity: 2,
  mode: 'immediate', default_mode: 'immediate', configured: true,
  targets: ['dashboard', 'channel_dm'], conditions: { keywords: [], name_mention: false }, sound: null,
}

function mockApi(stored: string) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      notificationSettings: async () => ({
        mute_all: false, min_severity: 'info', quiet_hours_enabled: false,
        quiet_hours_start: '22:00', quiet_hours_end: '07:00',
      }),
      notificationRules: async () => ({
        rules: [approvalRow], digest: { schedule: '0 8 * * *' },
        targets: ['dashboard', 'channel_dm', 'push', 'native'],
      }),
      personalclawConfig: async () => ({ agent: { approval_channel: stored } }),
      channels: async () => CHANNELS,
      patchConfig: (...a: unknown[]) => patchConfig(...a),
    },
  }))
}

async function mount(stored = '') {
  mockApi(stored)
  const { NotificationsPanel } = await import('./NotificationsPanel')
  render(<NotificationsPanel />)
  return (await screen.findByRole('combobox', { name: 'Send approvals to' })) as HTMLSelectElement
}

const options = (select: HTMLSelectElement) => Array.from(select.options).map((o) => o.textContent)

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  patchConfig.mockReset().mockResolvedValue({ ok: true })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.doUnmock('../../lib/api') })

describe('Send approvals to', () => {
  it('🔑 lists the channels that know you, keeps name order as the default, and says so', async () => {
    const select = await mount()
    expect(select.value).toBe('')
    expect(options(select)).toEqual(['The first connected channel that knows you', 'Discord', 'Telegram'])
    expect(screen.getByText(
      'A chat that started on a chat channel is asked in that chat. Everything else (a chat here, an unattended run, a trigger) asks the first connected channel that knows you, in name order: Discord, Telegram. Choose one to be asked only there.',
    )).toBeInTheDocument()
  })

  it('🔑 choosing a channel saves agent.approval_channel and says only it asks', async () => {
    const select = await mount()
    fireEvent.change(select, { target: { value: 'telegram' } })
    await waitFor(() => expect(patchConfig).toHaveBeenCalled())
    expect(patchConfig.mock.calls.at(-1)?.slice(0, 2)).toEqual(['agent.approval_channel', 'telegram'])
    expect(await screen.findByText(
      "A chat that started on a chat channel is asked in that chat. Everything else (a chat here, an unattended run, a trigger) asks only on Telegram. When it can't reach you, the approval waits here in PersonalClaw, and no other channel is asked.",
    )).toBeInTheDocument()
  })

  it('shows a stored choice that no longer knows you as what it is, not as the default', async () => {
    const select = await mount('email')
    expect(select.value).toBe('email')
    expect(options(select)).toContain("Email (doesn't know you)")
    expect(screen.getByText(
      /waits here in PersonalClaw, because Email doesn't know who you are yet, and no other channel asks/,
    )).toBeInTheDocument()
  })

  it('🔑 says a chat that started on a channel is asked there, whatever is chosen', async () => {
    for (const stored of ['', 'telegram', 'email', 'slack']) {
      cleanup()
      vi.resetModules()
      const select = await mount(stored)
      const id = select.getAttribute('aria-describedby') || ''
      const sentence = document.getElementById(id.split(' ').find((i) => document.getElementById(i)) || '')?.textContent || ''
      expect(sentence, `under ${stored || 'the default'}`).toMatch(
        /^A chat that started on a chat channel is asked in that chat\. Everything else \(a chat here, an unattended run, a trigger\) /,
      )
    }
  })

  it('rolls back and says so when the save is refused', async () => {
    patchConfig.mockRejectedValue(new Error("'telegram' is not a chat channel here"))
    const select = await mount()
    fireEvent.change(select, { target: { value: 'telegram' } })
    await waitFor(() => expect(select.value).toBe(''))
  })

  it('the approval row names where its Channel DM goes', async () => {
    await mount()
    fireEvent.click(await screen.findByRole('button', { name: /delivery detail for Approval needed/i }))
    const row = screen.getByRole('checkbox', { name: /Deliver Approval needed to Channel DM/ })
    expect(row.getAttribute('aria-label')).toBe(
      'Deliver Approval needed to Channel DM (asks where the chat started, else on the channel under Send approvals to, above)',
    )
    expect(within(row.closest('label') as HTMLElement).getByText(/Send approvals to/)).toBeInTheDocument()
  })
})
