import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import type { ComposerProps } from './types'
import { MIC_TONE } from '../MicCaptureChip'

// ── Hands-free voice says how it works ──────────────────────────────────────────────────────
//
// The button beside the microphone keeps one name, Hands-free voice, and says on or off by its
// pressed state. Its description says what it does and names the phrase that sends; the
// microphone's says it never sends. While hands-free is on, a line under the orb names the phrase
// that sends and the one that clears, or says it is paused while a reply is read aloud. On takes
// the listening tone, not the grey a hover also uses. In the desktop app a press checks the
// microphone grant first, so a denied one names System Settings.

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {},
    removeListener: () => {}, dispatchEvent: () => false, onchange: null,
  }) as unknown as MediaQueryList,
})

/** The recorder the composer drives, as a test sets it. */
const mic = { state: 'idle' as 'idle' | 'recording' | 'transcribing', muted: false, toggle: vi.fn() }
vi.mock('./useMicRecorder', () => ({
  useMicRecorder: () => ({ state: mic.state, toggle: mic.toggle, drain: () => {}, listening: false, muted: mic.muted }),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: true, optimize: false }

function composer(props: Partial<ComposerProps> = {}) {
  return (
    <Composer value="" onChange={() => {}} onSend={() => {}} onStop={() => {}} controls={CONTROLS}
      onTranscribe={async () => ''} onHandsFreeSubmit={() => {}}
      handsFree={{ confirmationPhrases: ['go ahead', 'do it'], exitPhrases: ['cancel', 'never mind'] }} {...props} />
  )
}

const handsFree = () => screen.getByRole('button', { name: 'Hands-free voice' })
const line = () => document.querySelector('[data-hands-free-line]')

afterEach(() => {
  cleanup()
  mic.state = 'idle'
  mic.muted = false
  mic.toggle.mockClear()
  delete (window as unknown as { pclawDesktop?: unknown }).pclawDesktop
})

describe('hands-free voice says how it works', () => {
  it('keeps one name in every state, and says on and off by its pressed state', async () => {
    const { rerender } = render(composer())
    expect(handsFree().getAttribute('aria-pressed')).toBe('false')
    await act(async () => { fireEvent.click(handsFree()) })
    expect(handsFree().getAttribute('aria-pressed')).toBe('true')
    mic.state = 'recording'
    rerender(composer())
    expect(handsFree().getAttribute('aria-pressed')).toBe('true')
    mic.muted = true
    rerender(composer())
    expect(handsFree().getAttribute('aria-pressed')).toBe('true')
  })

  it('says what it does and names the phrase that sends, then how to stop once it is on', async () => {
    render(composer())
    expect(handsFree()).toHaveAccessibleDescription('Hands-free voice: talk instead of typing, and end with “go ahead” to send.')
    await act(async () => { fireEvent.click(handsFree()) })
    expect(handsFree()).toHaveAccessibleDescription('Listening. Press to stop.')
  })

  it('says the microphone dictates and never sends', () => {
    render(composer())
    expect(screen.getByRole('button', { name: 'Voice input' })).toHaveAccessibleDescription('Voice input: dictate into the box. It never sends.')
  })

  it('names the phrases that send and clear while it is on, and says when it is paused', async () => {
    const { rerender } = render(composer())
    // Already there while off, empty, so a screen reader speaks the line the moment it fills.
    expect(line()?.getAttribute('role')).toBe('status')
    expect(line()?.textContent).toBe('')
    await act(async () => { fireEvent.click(handsFree()) })
    mic.state = 'recording'
    rerender(composer())
    expect(line()?.getAttribute('role')).toBe('status')
    expect(line()?.textContent).toBe('End with “go ahead” to send, or say “cancel” to clear.')
    mic.state = 'idle'
    mic.muted = true
    rerender(composer())
    expect(line()?.textContent).toBe('Paused while the reply is read aloud.')
  })

  it('takes the listening tone while on, and the microphone takes it while it records', async () => {
    const { rerender } = render(composer())
    await act(async () => { fireEvent.click(handsFree()) })
    expect(handsFree().getAttribute('style')).toContain(MIC_TONE)
    expect(handsFree().className).not.toContain('bg-surface-high')
    await act(async () => { fireEvent.click(handsFree()) })
    mic.state = 'recording'
    rerender(composer())
    const recording = screen.getByRole('button', { name: 'Stop recording' })
    expect(recording.getAttribute('style')).toContain(MIC_TONE)
    expect(recording.className).not.toContain('bg-surface-high')
  })

  it('in the desktop app, a press with the microphone turned off names System Settings and starts nothing', async () => {
    ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = {
      capabilities: {
        names: () => ['audio_capture'],
        probe: vi.fn(async () => ({ available: true, granted: 'denied', requestable: false, reason: '' })),
        snapshot: vi.fn(async () => ({})),
        request: vi.fn(async () => ({ granted: false, state: 'denied', prompted: false, reason: '' })),
        on: vi.fn(() => () => {}),
      },
      pushToTalk: {
        bind: vi.fn(async (chord: string) => ({ ok: true, chord, conflict: false, reason: '' })),
        setCapturing: vi.fn(async () => true),
        on: () => () => {},
      },
    }
    const errors: string[] = []
    render(composer({ onMicError: (m) => errors.push(m) }))
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(errors).toHaveLength(1))
    expect(errors[0]).toMatch(/System Settings/)
    expect(handsFree().getAttribute('aria-pressed')).toBe('false')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Voice input' })) })
    await waitFor(() => expect(errors).toHaveLength(2))
    expect(errors[1]).toMatch(/System Settings/)
    expect(mic.toggle).not.toHaveBeenCalled()
  })
})

describe('a press acts at once in a browser tab, and once in the desktop app', () => {
  it('starts the microphone at once in a browser tab', () => {
    render(composer())
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Voice input' })) })
    expect(mic.toggle).toHaveBeenCalledTimes(1)
  })

  it('turns hands-free on at once in a browser tab', () => {
    render(composer())
    act(() => { fireEvent.click(handsFree()) })
    expect(handsFree().getAttribute('aria-pressed')).toBe('true')
  })

  it('tells the host as hands-free turns on, so it clears the notice from when it last stopped', () => {
    const onHandsFreeStart = vi.fn()
    render(composer({ onHandsFreeStart }))
    act(() => { fireEvent.click(handsFree()) })
    expect(onHandsFreeStart).toHaveBeenCalledTimes(1)
    act(() => { fireEvent.click(handsFree()) })
    expect(handsFree().getAttribute('aria-pressed')).toBe('false')
    expect(onHandsFreeStart).toHaveBeenCalledTimes(1)
  })

  it('in the desktop app, a second press while the grant is being checked does nothing more', async () => {
    const pending: ((v: unknown) => void)[] = []
    const answer = (v: unknown) => { for (const resolve of pending.splice(0)) resolve(v) }
    ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = {
      capabilities: {
        names: () => ['audio_capture'],
        probe: vi.fn(() => new Promise((resolve) => { pending.push(resolve) })),
        snapshot: vi.fn(async () => ({})),
        request: vi.fn(async () => ({ granted: true, state: 'granted', prompted: false, reason: '' })),
        on: vi.fn(() => () => {}),
      },
      pushToTalk: {
        bind: vi.fn(async (chord: string) => ({ ok: true, chord, conflict: false, reason: '' })),
        setCapturing: vi.fn(async () => true),
        on: () => () => {},
      },
    }
    render(composer())
    const voice = screen.getByRole('button', { name: 'Voice input' })
    // Hands-free waits on the same check: its press does nothing more either.
    act(() => { fireEvent.click(voice); fireEvent.click(voice); fireEvent.click(handsFree()) })
    await act(async () => { answer({ available: true, granted: 'granted', requestable: false, reason: '' }) })
    await waitFor(() => expect(mic.toggle).toHaveBeenCalledTimes(1))
    await act(async () => {})
    expect(mic.toggle).toHaveBeenCalledTimes(1)
    expect(handsFree().getAttribute('aria-pressed')).toBe('false')
  })
})
