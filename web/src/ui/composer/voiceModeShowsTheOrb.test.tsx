import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import type { ComposerProps } from './types'
import { voiceOrbPhase } from './voiceOrb'
import { MIN_HOLD_MS } from '../turnEnding'

// ── Voice mode shows the 64 px orb, listening while it hears you (#4963) ─────────────────────
//
// `docs/design/agent-orb.md` puts the 64 px orb in three places; voice mode is the composer's.
// While hands-free is on, the orb is `listening` while the microphone hears the owner, and the
// turn's own phase the rest of the time: muted while a reply is spoken, transcribing, or between
// segments. With hands-free off there is no 64 px orb. Listening is the claw ring's ready form, so it
// holds still while the owner speaks and the words say Listening (#6117); a turn's own phase moves
// only while it thinks or works, and under reduced motion that is one still frame.

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

/** The voice orb as it is, recording whether it may bloom (jsdom draws no canvas, so the bloom
 *  itself is watched in `ui/theLiveLineShowsHowTheTurnEnded.test.tsx`). */
const blooms = vi.hoisted(() => [] as boolean[])
vi.mock('../AgentOrb', async (orig) => {
  const real = await orig<typeof import('../AgentOrb')>()
  const Recorded = (p: Parameters<typeof real.AgentOrb>[0]) => { if (p.size === 64) blooms.push(p.bloom ?? true); return <real.AgentOrb {...p} /> }
  return { ...real, AgentOrb: Recorded }
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

  it('shows how the turn ended while that is held, unless it hears you', () => {
    const ending = { phase: 'done' as const, line: 'Done in 3 seconds', outcome: 'complete' as const }
    expect(voiceOrbPhase({ heard: false, phase: null, ending })).toBe('done')
    expect(voiceOrbPhase({ heard: false, phase: null, ending: { phase: 'error', line: '', outcome: 'error' } })).toBe('error')
    expect(voiceOrbPhase({ heard: true, phase: null, ending })).toBe('listening')
    expect(voiceOrbPhase({ heard: false, phase: 'thinking', ending })).toBe('thinking')
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

  it('shows the turn’s ending once it ends, holds it, and then is idle', () => {
    vi.useFakeTimers()
    try {
      const { rerender } = render(composer({ phase: 'streaming', streaming: true }))
      fireEvent.click(handsFree())
      expect(voiceOrb()!.getAttribute('data-orb-phase'), 'the control: the turn’s phase').toBe('streaming')
      rerender(composer({ phase: null, ending: { phase: 'done', line: 'Done in 3 seconds', outcome: 'complete' } }))
      expect(voiceOrb()!.getAttribute('data-orb-phase'), 'the ending is not shown').toBe('done')
      act(() => { vi.advanceTimersByTime(MIN_HOLD_MS - 50) })
      expect(voiceOrb()!.getAttribute('data-orb-phase'), 'the ending was not held').toBe('done')
      act(() => { vi.advanceTimersByTime(100) })
      expect(voiceOrb()!.getAttribute('data-orb-phase'), 'the ending stayed').toBe('idle')
    } finally { vi.useRealTimers() }
  })

  it.each([
    ['complete', 'Done in 3 seconds', true],
    ['stopped', 'Stopped', false],
    ['error', 'Claude stopped after you denied shell.', false],
  ] as const)('a turn that ended %s (“%s”) gives the voice orb leave to bloom: %s', (outcome, words, may) => {
    blooms.length = 0
    const { rerender } = render(composer({ phase: 'streaming', streaming: true }))
    fireEvent.click(handsFree())
    expect(blooms.at(-1), 'the control: a running turn’s orb has its own rule').toBe(true)
    rerender(composer({ phase: null, ending: { phase: 'done', line: words, outcome } }))
    expect(voiceOrb()!.getAttribute('data-orb-phase')).toBe('done')
    expect(blooms.at(-1)).toBe(may)
  })

  it('goes when hands-free goes off', () => {
    render(composer())
    fireEvent.click(handsFree())
    expect(voiceOrb()).toBeTruthy()
    fireEvent.click(handsFree())
    expect(voiceOrb()).toBeNull()
  })

  it.each([false, true])('holds still while it listens (reduced motion: %s)', (reduce) => {
    REDUCE = reduce
    const { rerender } = render(composer())
    fireEvent.click(handsFree())
    hears(rerender, composer())
    expect(voiceOrb()!.getAttribute('data-orb-state')).toBe('ready')
    expect(voiceOrb()!.getAttribute('data-agent-orb')).toBe('still')
  })

  it('a turn thinking between segments moves, and is one still frame under reduced motion', () => {
    const { rerender } = render(composer({ phase: 'thinking', processing: true }))
    fireEvent.click(handsFree())
    expect(voiceOrb()!.getAttribute('data-agent-orb')).toBe('animated')
    REDUCE = true
    rerender(composer({ phase: 'planning', processing: true }))
    expect(voiceOrb()!.getAttribute('data-agent-orb')).toBe('instant')
  })
})
