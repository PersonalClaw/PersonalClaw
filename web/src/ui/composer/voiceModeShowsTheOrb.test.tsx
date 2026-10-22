import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import type { ComposerProps } from './types'
import { voiceOrbPhase } from './voiceOrb'

// ── Voice mode shows the 64 px orb, listening while it hears you (#4963) ─────────────────────
//
// `docs/design/agent-orb.md` puts the 64 px orb in three places; voice mode is the composer's.
// While hands-free is on, the orb is `listening` while the microphone hears the owner, and the
// turn's own phase the rest of the time: muted while a reply is spoken, transcribing, or between
// segments. With hands-free off there is no 64 px orb. Under reduced motion it is one still frame.

let REDUCE = false
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    get matches() { return query.includes('prefers-reduced-motion') ? REDUCE : false },
    media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {},
    removeListener: () => {}, dispatchEvent: () => false, onchange: null,
  }) as unknown as MediaQueryList,
})

/** The recorder the composer drives, as a test sets it: recording, muted for a reply, or idle. */
const mic = { state: 'idle' as 'idle' | 'recording' | 'transcribing', muted: false }
vi.mock('./useMicRecorder', () => ({
  useMicRecorder: () => ({ state: mic.state, toggle: () => {}, drain: () => {}, listening: false, muted: mic.muted }),
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
      handsFree={{ confirmationPhrases: ['go ahead'], exitPhrases: ['stop listening'] }} {...props} />
  )
}

/** Hands-free's loop opens the microphone once it is on: the recorder starts recording. */
function hears(rerender: (ui: React.ReactElement) => void, ui: React.ReactElement) {
  mic.state = 'recording'
  rerender(ui)
}

const voiceOrb = () => document.querySelector('[data-voice-orb] [data-agent-orb]') as HTMLElement | null
const handsFree = () => screen.getByRole('button', { name: /^Hands-free voice/ })

afterEach(() => {
  cleanup()
  REDUCE = false
  mic.state = 'idle'
  mic.muted = false
})

describe('the phase voice mode shows', () => {
  it('is listening while it hears you, and the turn’s own phase the rest of the time', () => {
    expect(voiceOrbPhase({ heard: true, phase: null })).toBe('listening')
    expect(voiceOrbPhase({ heard: true, phase: 'streaming' })).toBe('listening')
    expect(voiceOrbPhase({ heard: false, phase: 'thinking' })).toBe('thinking')
    expect(voiceOrbPhase({ heard: false, phase: 'approval' })).toBe('approval')
    expect(voiceOrbPhase({ heard: false, phase: null })).toBe('idle')
  })
})

describe('voice mode', () => {
  it('shows no 64 px orb until hands-free is on', () => {
    render(composer())
    expect(voiceOrb()).toBeNull()
  })

  it('shows the 64 px orb, listening while the microphone hears you', () => {
    const { rerender } = render(composer())
    fireEvent.click(handsFree())
    hears(rerender, composer())
    const orb = voiceOrb()!
    expect(orb).toBeTruthy()
    expect(orb.getAttribute('data-orb-size')).toBe('64')
    expect(orb.getAttribute('data-orb-phase')).toBe('listening')
    expect(screen.getByRole('img', { name: 'Listening' })).toBeTruthy()
  })

  it('shows the turn’s own phase while it is muted for a spoken reply', () => {
    const { rerender } = render(composer({ phase: 'streaming', streaming: true }))
    fireEvent.click(handsFree())
    mic.muted = true
    hears(rerender, composer({ phase: 'streaming', streaming: true }))
    expect(voiceOrb()!.getAttribute('data-orb-phase')).toBe('streaming')
  })

  it('shows the turn’s own phase between segments, and idle with no turn', () => {
    const { rerender } = render(composer({ phase: 'thinking', processing: true }))
    fireEvent.click(handsFree())
    expect(voiceOrb()!.getAttribute('data-orb-phase')).toBe('thinking')
    rerender(composer({ phase: null }))
    expect(voiceOrb()!.getAttribute('data-orb-phase')).toBe('idle')
  })

  it('goes when hands-free goes off', () => {
    render(composer())
    fireEvent.click(handsFree())
    expect(voiceOrb()).toBeTruthy()
    fireEvent.click(handsFree())
    expect(voiceOrb()).toBeNull()
  })

  it('is one still frame under reduced motion', () => {
    REDUCE = true
    const { rerender } = render(composer())
    fireEvent.click(handsFree())
    hears(rerender, composer())
    // `listening` moves; under reduced motion the orb draws its still instant and never runs.
    expect(voiceOrb()!.getAttribute('data-agent-orb')).toBe('instant')
  })

  it('moves while it listens, with motion allowed', () => {
    const { rerender } = render(composer())
    fireEvent.click(handsFree())
    hears(rerender, composer())
    expect(voiceOrb()!.getAttribute('data-agent-orb')).toBe('animated')
  })
})
