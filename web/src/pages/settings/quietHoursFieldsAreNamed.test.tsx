import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

// ── A settings control a screen reader can name ────────────────────────────────────────────────
//
// Measured on Settings › Notifications with quiet hours on: "Window · Start and end (24-hour, server
// time)." and then two `<input type="time">` with no aria-label, aria-labelledby, id or `<label>` — a
// screen reader heard "time, edit" twice. A settings `Row` names nothing; its control has to. Driven
// through the real panel.

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      notificationSettings: async () => ({
        mute_all: false, min_severity: 'info', quiet_hours_enabled: true,
        quiet_hours_start: '23:00', quiet_hours_end: '06:00',
      }),
      notificationRules: async () => ({ rules: [], digest: { schedule: '0 8 * * *' }, targets: ['dashboard'] }),
      personalclawConfig: async () => ({ agent: { approval_channel: '' } }),
      channels: async () => [],
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.doUnmock('../../lib/api') })

describe('the quiet-hours window', () => {
  it('names each of its two times, and describes them with the row hint', async () => {
    mockApi()
    const { NotificationsPanel } = await import('./NotificationsPanel')
    render(<NotificationsPanel />)
    // 🔴 Before: neither field could be found by any name.
    const start = (await screen.findByLabelText('Quiet hours window start')) as HTMLInputElement
    const end = screen.getByLabelText('Quiet hours window end') as HTMLInputElement
    expect([start.type, start.value, end.type, end.value]).toEqual(['time', '23:00', 'time', '06:00'])
    for (const field of [start, end]) {
      const described = document.getElementById(field.getAttribute('aria-describedby') ?? '')
      expect(described?.textContent).toBe('Start and end (24-hour, server time).')
    }
  })
})

describe('the text-to-speech speaking speed', () => {
  it('is a slider named for the label beside it, and reads its value as shown', async () => {
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        ...(await orig<{ api: Record<string, unknown> }>()).api,
        useCaseSettings: async (useCase: string) =>
          ({ value: useCase === 'tts' ? { enabled: true, speed: 1.2 } : { enabled: false }, revision: 'rev-1' }),
        modelsActive: async () => ({ stt: ['whisper:base'], tts: ['piper:en'] }),
        personalclawConfig: async () => ({}),
        voiceLoopConfig: async () => ({}),
        voiceProfiles: async () => ({ profiles: [], bindings: {} }),
        voiceResolve: async () => ({ surface: '', resolved: true, level: 'built-in' }),
        lexiconTerms: async () => ({ terms: [], total: 0 }),
        lexiconCorrections: async () => ({ corrections: [] }),
      },
    }))
    const { VoicePanel } = await import('./VoicePanel')
    render(<VoicePanel go={() => {}} />)
    // 🔴 Before: a raw range input inside the Field, named by nothing.
    const slider = await screen.findByRole('slider', { name: 'Speaking speed' })
    expect(slider.getAttribute('aria-valuetext')).toBe('1.20×')
  })
})
