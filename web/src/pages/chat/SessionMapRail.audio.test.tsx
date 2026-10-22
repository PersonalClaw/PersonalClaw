import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Profiler } from 'react'
import { act, render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import { registerAudioSource, resetAudioPresence, type AttachedSource } from '../../lib/audioPresence'
import { resetMediaPresence } from '../../lib/mediaPresence'
import type { ChatTurn } from './chatTypes'

// ── The rail's audio presence (#4776) ─────────────────────────────────────────────────────────
//
// Off by default. Turned on, the rail subscribes while it is mounted and the document visible: the
// reply being read aloud is its question's marker ("playing"), the microphone the owner opened
// listens at the newest marker ("listening"), each said in the marker's name and drawn thicker,
// with a level line moved by a CSS variable rather than a render. Under reduced motion it is told
// who alone: nothing is measured and no frame runs. Hidden, it lets go.
//
// VACUITY FLOOR. "off by default / hidden / reduced motion attach nothing" passes for a rail that
// never attaches; each sits beside the on case that DOES attach, counted on the same fake source.

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = Array.from({ length: 10 }, (_, i) => (i % 2 === 0
  ? { role: 'user', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `question ${i / 2 + 1}` }] }
  : { role: 'assistant', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `answer ${i}` }] }))
const entries = sessionMapEntries(turns)

let reduce = false
const realMatchMedia = window.matchMedia
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'setTimeout', 'clearTimeout'] })
  reduce = false
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('prefers-reduced-motion'), media: query,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
      dispatchEvent: () => false, onchange: null,
    }) as unknown as MediaQueryList,
  })
})
afterEach(() => {
  resetMediaPresence()
  resetAudioPresence()
  document.querySelectorAll('[data-turn-coord]').forEach((n) => n.remove())
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: realMatchMedia })
  vi.useRealTimers()
  vi.restoreAllMocks()
})

/** A live source of one kind, owned by a reply, at a fixed level, recording attaches. */
function source(kind: 'playback' | 'microphone', owner: number | null = null) {
  const calls = { attach: 0, detach: 0 }
  const handle = registerAudioSource(kind, (): AttachedSource => {
    calls.attach++
    return { read: () => 0.5, detach: () => { calls.detach++ } }
  })
  handle.setOwner(owner)
  handle.setLive(true)
  return { calls, handle }
}

function mount(audioPresence: boolean) {
  let renders = 0
  const view = render(
    <Profiler id="rail" onRender={() => { renders++ }}>
      <SessionMapRail entries={entries} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} audioPresence={audioPresence} />
    </Profiler>,
  )
  const marks = () => [...view.container.querySelectorAll('[data-session-mark]')] as HTMLElement[]
  const nav = () => view.container.querySelector('nav') as HTMLElement
  return { ...view, marks, nav, renders: () => renders }
}

describe('the rail’s audio presence', () => {
  it('🔑 is off by default: nothing is measured and no marker says it plays', () => {
    const src = source('playback', entries[2].visibleIndex + 1)
    const h = mount(false)
    act(() => { vi.advanceTimersByTime(200) })
    expect(src.calls.attach, 'the rail measured audio with the setting off').toBe(0)
    expect(h.marks().some((m) => m.hasAttribute('data-audio'))).toBe(false)
  })

  it('🔑 turned on, the reply being read aloud is its question’s marker, named and drawn so', () => {
    // The owner is the REPLY's coordinate: it belongs to the third question's exchange.
    const src = source('playback', entries[2].visibleIndex + 1)
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(50) })
    expect(src.calls.attach, 'turned on, the rail measured nothing').toBe(1)
    const m = h.marks()[2]
    expect(m.getAttribute('data-audio')).toBe('playing')
    expect(m.getAttribute('aria-label')).toBe('Message 3 of 5, playing: question 3')
    expect(m.querySelector('[data-session-map-mark-line]')!.className).toContain('h-1')
    const energy = m.querySelector('[data-session-map-energy]') as HTMLElement
    expect(energy.getAttribute('aria-hidden'), 'the level line is decoration').toBe('true')
    expect(energy.style.transform).toBe('scaleX(var(--map-play-level, 0))')
    expect(h.nav().style.getPropertyValue('--map-play-level')).toBe('0.5')
    // Only that marker.
    expect(h.marks().filter((x) => x.hasAttribute('data-audio'))).toHaveLength(1)
  })

  it('🔑 a level moves a CSS variable, not the rail: frames run and the rail does not re-render', () => {
    const src = source('playback', entries[1].visibleIndex)
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(50) })
    const renders = h.renders()
    const frames = vi.spyOn(window, 'requestAnimationFrame')
    act(() => { vi.advanceTimersByTime(500) })
    expect(frames.mock.calls.length, 'no frame ran, so "no render" would be vacuous').toBeGreaterThan(5)
    expect(h.renders(), 'the level re-rendered the rail').toBe(renders)
    // And the reading ending takes the marker back, at once.
    act(() => { src.handle.setLive(false) })
    expect(h.marks()[1].hasAttribute('data-audio')).toBe(false)
    expect(h.marks()[1].getAttribute('aria-label')).toBe('Message 2 of 5: question 2')
  })

  it('the microphone the owner opened listens at the newest marker', () => {
    source('microphone')
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(50) })
    expect(h.marks()[4].getAttribute('data-audio')).toBe('listening')
    expect(h.marks()[4].getAttribute('aria-label')).toBe('Message 5 of 5, listening: question 5')
    expect(h.marks()[4].querySelector('[data-session-map-energy]')!.getAttribute('style')).toContain('--map-mic-level')
  })

  it('🔑 under reduced motion: told who alone — nothing measured, no frame, the still mark and its name', () => {
    reduce = true
    const src = source('playback', entries[3].visibleIndex)
    const frames = vi.spyOn(window, 'requestAnimationFrame')
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(500) })
    expect(src.calls.attach, 'reduced motion measured the audio').toBe(0)
    expect(frames, 'reduced motion ran frames').not.toHaveBeenCalled()
    const m = h.marks()[3]
    expect(m.getAttribute('data-audio')).toBe('playing')
    expect(m.getAttribute('aria-label')).toMatch(/, playing: /)
    expect(m.querySelector('[data-session-map-mark-line]')!.className).toContain('h-1')
    expect(m.querySelector('[data-session-map-energy]'), 'a level line under reduced motion').toBeNull()
  })

  it('🔑 a hidden document lets go, and showing it again subscribes again', () => {
    const src = source('playback', entries[1].visibleIndex)
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(50) })
    expect(src.calls.attach).toBe(1)
    const vis = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(src.calls.detach, 'hidden, the rail kept measuring').toBe(1)
    expect(h.marks()[1].hasAttribute('data-audio')).toBe(false)
    vis.mockReturnValue('visible')
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(src.calls.attach).toBe(2)
  })

  it('the rail going takes its subscription with it', () => {
    const src = source('playback', entries[1].visibleIndex)
    const h = mount(true)
    act(() => { vi.advanceTimersByTime(50) })
    h.unmount()
    expect(src.calls.detach).toBe(1)
  })

  it('🔑 PersonalClaw’s own media playing in a chat turn plays on that turn’s marker, and only while watched', () => {
    const turn = document.createElement('div')
    turn.setAttribute('data-turn-coord', String(entries[3].visibleIndex + 1))
    const el = document.createElement('video')
    let paused = true
    Object.defineProperty(el, 'paused', { configurable: true, get: () => paused })
    turn.appendChild(el)
    document.body.appendChild(turn)
    const off = mount(false)
    paused = false
    act(() => { el.dispatchEvent(new Event('playing')) })
    expect(off.marks().some((m) => m.hasAttribute('data-audio')), 'the media was watched with the setting off').toBe(false)
    off.unmount()
    const h = mount(true)
    expect(h.marks()[3].getAttribute('data-audio')).toBe('playing')
    paused = true
    act(() => { el.dispatchEvent(new Event('pause')) })
    expect(h.marks()[3].hasAttribute('data-audio')).toBe(false)
  })
})
