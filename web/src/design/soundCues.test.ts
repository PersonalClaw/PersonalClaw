/**
 * SOUND CUES — the silence contract (PERSONALITY-THEMES §S2, T2.1).
 *
 * An audio feature in a tool people leave open all day is only acceptable if
 * "quiet" is provable rather than promised, so the assertions below are built
 * around one discipline:
 *
 * 🔑 EACH SUPPRESSOR IS TESTED ALONE. There are three ways a cue must be
 * silenced — the master toggle, `prefers-reduced-motion`, and a hidden tab — and a
 * single test with all three switched on would pass with any two of the three
 * checks deleted from the source. So every suppressor test starts from the ONE
 * audible baseline, flips exactly one condition, and then ASSERTS the other two are
 * still clear. Deleting any single check from `playCue` turns exactly one of these
 * red, which is the property that makes them worth having.
 *
 * The second cluster is the AudioContext's lifecycle: exactly one, and never built
 * outside a user gesture. Browsers permanently suspend a context constructed
 * without user activation, and a second context is a leaked audio thread — both
 * failures are invisible at the call site, so they are pinned here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ── The fake Web Audio graph ────────────────────────────────────────────────
// jsdom implements none of Web Audio, so this is the whole instrument. It counts
// CONSTRUCTIONS (the single-context claim) and records the scheduled tone
// sequence (the "did anything actually sound?" claim). A cue that returns early
// leaves an empty log, which is exactly what silence looks like from here.

interface Tone { wave: string; freq: number; start: number }

let constructed = 0
let tones: Tone[] = []
let ramps: { value: number; at: number }[] = []
let resumeCalls = 0
let instances: FakeAudioContext[] = []

class FakeAudioContext {
  state: 'running' | 'suspended' = 'running'
  currentTime = 10
  destination = { id: 'destination' }

  constructor() {
    constructed++
    instances.push(this)
  }

  resume(): Promise<void> {
    resumeCalls++
    this.state = 'running'
    return Promise.resolve()
  }

  createOscillator() {
    const osc = {
      type: 'sine' as string,
      frequency: { value: 0 },
      connect: () => {},
      start: (t: number) => tones.push({ wave: osc.type, freq: osc.frequency.value, start: t }),
      stop: () => {},
    }
    return osc
  }

  createGain() {
    return {
      gain: {
        setValueAtTime: () => {},
        linearRampToValueAtTime: (value: number, at: number) => ramps.push({ value, at }),
        exponentialRampToValueAtTime: () => {},
      },
      connect: () => {},
    }
  }
}

const ORIGINAL_MATCH_MEDIA = window.matchMedia

/** jsdom has no `matchMedia` at all, so this is an assignment rather than a spy —
 *  spying on an absent property leaves a function returning `undefined` behind. */
function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

function setTabHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', { configurable: true, value: hidden })
}

/** A fresh module instance. The context, the armed flag and the enabled cache are
 *  MODULE state, so a shared import would let one test's primed context make the
 *  next test's "no context yet" assertion vacuously true. */
async function load() {
  vi.resetModules()
  return import('./soundCues')
}

beforeEach(() => {
  constructed = 0
  tones = []
  ramps = []
  resumeCalls = 0
  instances = []
  localStorage.clear()
  setReducedMotion(false)
  setTabHidden(false)
  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    writable: true,
    value: FakeAudioContext as unknown as typeof AudioContext,
  })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA,
  })
  setTabHidden(false)
  localStorage.clear()
})

/** The ONE audible arrangement: cues on, no reduced motion, tab visible, and a
 *  context primed by a real gesture. Every suppressor test below starts here and
 *  breaks it in exactly one place. */
async function audible() {
  const m = await load()
  localStorage.setItem(m.SOUND_CUES_KEY, 'on')
  m.armCueAudio()
  window.dispatchEvent(new Event('pointerdown'))
  expect(constructed, 'the baseline must have a real context, or every test below passes vacuously').toBe(1)
  return m
}

describe('the master toggle defaults OFF', () => {
  it('an absent preference is off', async () => {
    const m = await load()
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBeNull()
    expect(m.soundCuesEnabled()).toBe(false)
  })

  it("only the literal 'on' enables sound — every other value is off", async () => {
    const m = await load()
    // Default-off is a property of the COMPARISON, not of a `?? false`: there is no
    // truthy value that turns cues on by accident (a stale `'1'`, a `'true'` written
    // by some future writer, a half-written value).
    for (const bad of ['', 'off', '1', 'true', 'yes', 'ON', 'on ', '{}']) {
      localStorage.setItem(m.SOUND_CUES_KEY, bad)
      expect(m.soundCuesEnabled(), `'${bad}' must not enable sound`).toBe(false)
    }
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    expect(m.soundCuesEnabled()).toBe(true)
  })

  it('turning it on persists it and turning it off removes the key', async () => {
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBe('on')
    m.setSoundCuesEnabled(false)
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBeNull()
  })
})

describe('a cue plays when nothing suppresses it', () => {
  it('schedules the recipe: one tone per frequency, in order, on the declared wave', async () => {
    const m = await audible()
    m.playCue('turn_complete')
    const recipe = m.CUES.turn_complete
    expect(tones.map((t) => t.freq)).toEqual(recipe.freqs)
    expect(tones.every((t) => t.wave === recipe.wave)).toBe(true)
    // Scheduled ahead on the context timeline, in sequence — not fired by a timer.
    expect(tones[0].start).toBeGreaterThanOrEqual(10)
    expect(tones[1].start).toBeGreaterThan(tones[0].start)
  })

  it('plays each of the three closed cues', async () => {
    const m = await audible()
    for (const name of ['turn_complete', 'approval_needed', 'error'] as const) {
      tones = []
      m.playCue(name)
      expect(tones.length, name).toBe(m.CUES[name].freqs.length)
    }
  })

  it('resumes a context the browser suspended while the tab was in the background', async () => {
    const m = await audible()
    resumeCalls = 0
    // A tab restored from the background comes back with a SUSPENDED context. A cue
    // that only schedules onto it would be silent for the rest of the session — the
    // first blur would permanently break the feature, with nothing to see in a log.
    instances[0].state = 'suspended'
    m.playCue('error')
    expect(resumeCalls, 'a suspended context must be resumed, not scheduled onto').toBe(1)
    expect(tones.length).toBeGreaterThan(0)
  })
})

// ── The three suppressors, one at a time ────────────────────────────────────

describe('the master toggle alone silences a cue', () => {
  it('is silent with cues off while reduced-motion is CLEAR and the tab is VISIBLE', async () => {
    const m = await audible()
    localStorage.removeItem(m.SOUND_CUES_KEY)
    // The other two suppressors are asserted inactive, so this test can only pass
    // because of the toggle check.
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(false)
    expect(document.hidden).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })
})

describe('prefers-reduced-motion alone silences a cue', () => {
  it('is silent under reduced motion while cues are ON and the tab is VISIBLE', async () => {
    const m = await audible()
    setReducedMotion(true)
    expect(m.soundCuesEnabled(), 'the toggle must stay ON or this proves nothing').toBe(true)
    expect(document.hidden).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })

  it('and goes audible again the moment the query clears — read live, never cached', async () => {
    const m = await audible()
    setReducedMotion(true)
    m.playCue('error')
    expect(tones).toEqual([])
    setReducedMotion(false)
    m.playCue('error')
    expect(tones.length).toBeGreaterThan(0)
  })
})

describe('a hidden tab alone silences a cue', () => {
  it('is silent while hidden with cues ON and reduced-motion CLEAR', async () => {
    const m = await audible()
    setTabHidden(true)
    expect(m.soundCuesEnabled(), 'the toggle must stay ON or this proves nothing').toBe(true)
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })
})

// ── The context's lifecycle ─────────────────────────────────────────────────

describe('exactly one AudioContext, and only from a user gesture', () => {
  it('importing the module constructs nothing', async () => {
    await load()
    expect(constructed).toBe(0)
  })

  it('playCue NEVER constructs a context — no gesture, no sound, no leak', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    // Every suppressor clear: the ONLY reason this stays silent is that nothing has
    // primed a context yet. A browser refuses (or permanently suspends) a context
    // built without user activation, so building one here would be worse than quiet.
    m.playCue('turn_complete')
    expect(constructed, 'a context built outside a gesture is a dead context').toBe(0)
    expect(tones).toEqual([])
  })

  it('the armed primer builds the context on the first gesture, then stops listening', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    m.armCueAudio()
    expect(constructed, 'arming alone must not construct — the gesture does').toBe(0)
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed).toBe(1)
    // Further gestures (and a redundant re-arm) reuse the one context.
    window.dispatchEvent(new Event('pointerdown'))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    m.armCueAudio()
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'a second context is a leaked audio thread').toBe(1)
  })

  it('a keypress counts as the gesture too — keyboard-only users get cues', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    m.armCueAudio()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(constructed).toBe(1)
  })

  it('keeps listening through gestures made while cues are off, then primes once enabled', async () => {
    const m = await load()
    m.armCueAudio()
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'nothing to prime while the toggle is off').toBe(0)
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'the primer must not have given up on the first miss').toBe(1)
  })

  it('turning the toggle ON primes inside that click, so the next cue is audible', async () => {
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(constructed, 'the toggle click IS the user activation — use it').toBe(1)
    m.playCue('error')
    expect(tones.length).toBeGreaterThan(0)
  })

  it('resumes a context that starts suspended even inside the toggle gesture', async () => {
    // Safari has historically handed back a suspended context from a gesture anyway,
    // which is why ChatPage's Speak path resumes too. Without this, enabling cues
    // would appear to work and then be silent for the whole session.
    class StartsSuspended extends FakeAudioContext {
      constructor() {
        super()
        this.state = 'suspended'
      }
    }
    Object.defineProperty(window, 'AudioContext', {
      configurable: true, writable: true, value: StartsSuspended as unknown as typeof AudioContext,
    })
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(resumeCalls, 'a suspended context must be resumed at the gesture').toBe(1)
  })

  it('survives a browser with no Web Audio at all, and does not arm a listener for nothing', async () => {
    Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: undefined })
    Object.defineProperty(window, 'webkitAudioContext', { configurable: true, writable: true, value: undefined })
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    expect(() => {
      m.setSoundCuesEnabled(true)
      m.armCueAudio()
      window.dispatchEvent(new Event('pointerdown'))
      m.playCue('error')
    }).not.toThrow()
    expect(tones).toEqual([])
    // 🪤 THIS ASSERTION FOUND A REAL (SMALL) DEFECT. The primer used to arm before
    // asking whether the platform HAS Web Audio, so on such a browser it kept a
    // pointerdown+keydown listener alive for the life of the page, retrying a
    // construction that could never succeed. It is measured here rather than reviewed
    // because the symptom — silence — is the correct outcome either way. It surfaced
    // as cross-test interference: the stale listener survived `vi.resetModules()` and
    // built a SECOND context in the next test.
    Object.defineProperty(window, 'AudioContext', {
      configurable: true, writable: true, value: FakeAudioContext as unknown as typeof AudioContext,
    })
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'nothing should have been armed, so this gesture builds nothing').toBe(0)
  })

  it('a cue never throws into the surface that fired it', async () => {
    const m = await audible()
    // An error toast calls this while rendering. A throw here would replace a
    // recoverable failure notice with a blank boundary.
    const broken = FakeAudioContext.prototype.createOscillator
    FakeAudioContext.prototype.createOscillator = () => {
      throw new Error('no output device')
    }
    try {
      expect(() => m.playCue('error')).not.toThrow()
    } finally {
      FakeAudioContext.prototype.createOscillator = broken
    }
  })
})

// ── The cue set is closed, and quiet ────────────────────────────────────────

describe('the cue set is closed', () => {
  it('is exactly the three cue points the plan names', async () => {
    const m = await load()
    // A caller cannot invent `playCue('ka-ching')` — the name is a closed union, so
    // that is a type error; this pins the runtime side of the same claim.
    expect(Object.keys(m.CUES).sort()).toEqual(['approval_needed', 'error', 'turn_complete'])
  })

  it('every recipe is playable: a real wave, at least one positive frequency, real duration', async () => {
    const m = await load()
    const WAVES = new Set(['sine', 'square', 'sawtooth', 'triangle'])
    for (const [name, r] of Object.entries(m.CUES)) {
      expect(WAVES.has(r.wave), `${name}.wave=${r.wave}`).toBe(true)
      expect(r.freqs.length, name).toBeGreaterThan(0)
      expect(r.freqs.every((f) => f > 0 && f < 20000), name).toBe(true)
      expect(r.durMs, name).toBeGreaterThan(0)
      expect(r.durMs, `${name} — a cue is a blip, not a jingle`).toBeLessThanOrEqual(400)
    }
  })

  it('no shipped recipe exceeds the loudness ceiling', async () => {
    const m = await load()
    for (const [name, r] of Object.entries(m.CUES)) {
      expect(r.gain, name).toBeGreaterThan(0)
      expect(r.gain, `${name} must stay under MAX_GAIN`).toBeLessThanOrEqual(m.MAX_GAIN)
    }
  })

  it('clamps a recipe that asks to be louder than the ceiling', async () => {
    const m = await audible()
    // The ceiling has to hold against the recipe, not just against today's authors:
    // a future cue tuned by ear on quiet speakers is how someone gets startled.
    m.CUES.error.gain = 5
    m.playCue('error')
    expect(ramps.length).toBeGreaterThan(0)
    expect(Math.max(...ramps.map((r) => r.value))).toBe(m.MAX_GAIN)
  })

  it('clamps a negative gain to silence rather than inverting the wave', async () => {
    const m = await audible()
    m.CUES.error.gain = -0.5
    m.playCue('error')
    expect(ramps.length).toBeGreaterThan(0)
    expect(Math.max(...ramps.map((r) => r.value))).toBe(0)
  })
})
