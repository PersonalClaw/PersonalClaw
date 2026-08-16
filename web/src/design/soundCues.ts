/**
 * SOUND CUES — three synthesised earcons, off by default.
 *
 * PERSONALITY-THEMES §S2 (T2.1, contract C2). The app has three moments a user
 * genuinely wants to hear while looking somewhere else: a turn finished, an
 * approval is waiting, something failed. Everything else is chatter, so the set is
 * CLOSED at those three.
 *
 * Four rules make an audio feature safe to ship in a tool people leave open all day:
 *
 * 1. **Silence is the default and the fallback.** The only value that enables sound
 *    is the literal `'on'` in one localStorage key, so an absent, corrupt, or
 *    unreadable preference is off — not "off unless something is truthy". Three
 *    independent conditions each suppress sound on their own: the master toggle,
 *    `prefers-reduced-motion`, and a hidden tab. They are checked here, in the one
 *    function that makes sound, rather than at the three call sites — a gate a
 *    caller can forget to apply is not a gate.
 * 2. **Zero audio files.** Every cue is built from oscillator + gain nodes at play
 *    time. Nothing is fetched, nothing is decoded, and no sample ships in the
 *    bundle (`noAudioAssets.test.ts` is the rail). An HTMLAudioElement or a bundled
 *    sample would be bytes on every user's first load for a feature almost nobody
 *    turns on.
 * 3. **ONE AudioContext, created inside a user gesture.** Browsers refuse (or
 *    permanently suspend) a context constructed without user activation, and a
 *    second context is a leaked audio thread. So construction lives in exactly one
 *    private function with one caller-shaped contract: it runs from a real gesture
 *    — the toggle's own click, or the one-shot primer `armCueAudio()` arms at shell
 *    mount. `playCue` NEVER constructs one; if no context exists yet it stays
 *    silent, which is the correct outcome for "the user has not interacted".
 * 4. **A hard ceiling on loudness.** A recipe's gain is clamped to `MAX_GAIN`, so
 *    no future cue — however it is authored — can startle someone wearing
 *    headphones.
 */

import { prefersReducedMotion } from './motion'

/** The closed cue set. A caller cannot invent a cue: an unknown name is a type
 *  error, and `CUES` is a total `Record`, so adding a member without a recipe
 *  fails to compile. */
export type CueName = 'turn_complete' | 'approval_needed' | 'error'

/** A cue is a short sequence of tones. `freqs` play in order across `durMs`
 *  (one slice each), so a two-entry recipe is an interval and a one-entry recipe
 *  is a single blip. `gain` is a linear peak amplitude, clamped to `MAX_GAIN`. */
export interface CueRecipe {
  wave: OscillatorType
  freqs: number[]
  durMs: number
  gain: number
}

/** The loudest any cue may be. Cues are ambient background signals sitting on top
 *  of whatever the user is actually listening to, so the ceiling is deliberately
 *  low — closer to a keyboard click than to a notification chime. */
export const MAX_GAIN = 0.1

export const CUES: Record<CueName, CueRecipe> = {
  // A turn settled: a small rising major third — resolved, finished, unremarkable.
  turn_complete: { wave: 'sine', freqs: [659.25, 830.61], durMs: 130, gain: 0.05 },
  // An approval is waiting on you: two rising pulses, the shape of a question.
  approval_needed: { wave: 'triangle', freqs: [880, 1108.73, 880], durMs: 210, gain: 0.06 },
  // Something failed: a falling minor second on a harder wave — unmistakably not "done".
  error: { wave: 'square', freqs: [311.13, 233.08], durMs: 170, gain: 0.04 },
}

/** The one preference. `'on'` is the ONLY enabling value — see rule 1. */
export const SOUND_CUES_KEY = 'soundCues'
const ENABLED_VALUE = 'on'

let ctx: AudioContext | null = null
/** One-way latch: the primer is armed at most once per page load, so a second
 *  `armCueAudio()` is a no-op. It is never lowered — once a context exists, `ctx`
 *  itself is the terminal guard. */
let armed = false

export function soundCuesEnabled(): boolean {
  try {
    return localStorage.getItem(SOUND_CUES_KEY) === ENABLED_VALUE
  } catch {
    // Private mode / storage denied: no preference is readable, so no sound.
    return false
  }
}

/** The platform's AudioContext constructor, or `null` on a browser without Web
 *  Audio. Read through one helper so "this browser cannot make sound at all" is a
 *  single question with one answer. */
function audioCtor(): typeof AudioContext | null {
  if (typeof window === 'undefined') return null
  return (
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext ||
    null
  )
}

/** Construct the single AudioContext. MUST be called from inside a user gesture —
 *  both callers are (the toggle's click handler and the first-gesture primer).
 *  Idempotent: the second call returns the first context. */
function ensureContext(): AudioContext | null {
  if (ctx) return ctx
  const Ctor = audioCtor()
  if (!Ctor) return null
  try {
    ctx = new Ctor()
  } catch {
    // A browser that refuses construction (no activation, no output device) —
    // cues simply stay silent rather than breaking the surface that fired them.
    return null
  }
  return ctx
}

/** Arm a one-shot primer that creates the context on the user's next gesture.
 *
 *  Called once from the app shell. The listener persists until it actually builds
 *  a context, so a user who enables cues later still gets one on their next click
 *  without needing a reload. Cheap by construction: it does nothing at all while
 *  the toggle is off, and removes itself the moment a context exists. */
export function armCueAudio(): void {
  if (armed || ctx) return
  // No Web Audio at all (also covers a non-browser caller): there is nothing a
  // gesture could unlock, so don't hold a listener on every click for the life of
  // the page.
  if (!audioCtor()) return
  armed = true
  const onGesture = () => {
    if (!soundCuesEnabled()) return // keep listening — the toggle may come on later
    ensureContext()
    if (!ctx) return
    window.removeEventListener('pointerdown', onGesture)
    window.removeEventListener('keydown', onGesture)
  }
  window.addEventListener('pointerdown', onGesture)
  window.addEventListener('keydown', onGesture)
}

/** Persist the master toggle. Call from a click handler when turning cues ON: the
 *  context is primed inside that user gesture, so the very next cue is audible
 *  instead of waiting for another interaction. */
export function setSoundCuesEnabled(on: boolean): void {
  try {
    if (on) localStorage.setItem(SOUND_CUES_KEY, ENABLED_VALUE)
    else localStorage.removeItem(SOUND_CUES_KEY)
  } catch {
    /* storage denied — the preference just won't persist */
  }
  if (on) {
    const c = ensureContext()
    if (c && c.state === 'suspended') void c.resume().catch(() => {})
  }
}

/** Play a cue, or (far more often) don't.
 *
 *  Returns silently on any of: cues off, reduced motion, hidden tab, no context
 *  yet, no Web Audio. Never throws and never constructs a context — this runs from
 *  a toast render and an error path, where a throw would take the surface with it. */
export function playCue(name: CueName): void {
  if (!soundCuesEnabled()) return
  if (prefersReducedMotion()) return
  if (typeof document !== 'undefined' && document.hidden) return
  const c = ctx
  if (!c) return
  try {
    if (c.state === 'suspended') void c.resume().catch(() => {})
    synth(c, CUES[name])
  } catch {
    /* a node the browser refused to build — stay silent, never break the caller */
  }
}

/** Build the tone sequence on the context's own timeline.
 *
 *  Each frequency gets its own oscillator and gain envelope, scheduled ahead of
 *  `currentTime` rather than played by a timer, so the sequence keeps its shape
 *  even if the main thread is busy rendering the thing that triggered it. */
function synth(c: AudioContext, recipe: CueRecipe): void {
  const peak = Math.min(Math.max(recipe.gain, 0), MAX_GAIN)
  const slice = recipe.durMs / 1000 / Math.max(recipe.freqs.length, 1)
  const t0 = c.currentTime
  recipe.freqs.forEach((freq, i) => {
    const osc = c.createOscillator()
    const gain = c.createGain()
    osc.type = recipe.wave
    osc.frequency.value = freq
    const start = t0 + i * slice
    const end = start + slice
    // A short attack then an exponential decay: a click-free blip. Exponential
    // ramps cannot reach zero, hence the small floor.
    gain.gain.setValueAtTime(0, start)
    gain.gain.linearRampToValueAtTime(peak, start + Math.min(0.008, slice * 0.25))
    gain.gain.exponentialRampToValueAtTime(0.0001, end)
    osc.connect(gain)
    gain.connect(c.destination)
    osc.start(start)
    osc.stop(end)
  })
}
