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

/** The closed set of MOMENTS a cue may fire at. Three, deliberately — see the
 *  header. `playCue` takes one of these and nothing else, so a caller cannot
 *  invent a moment (a type error) and cannot fire a voice directly either. */
export type CuePoint = 'turn_complete' | 'approval_needed' | 'error'

/** The closed set of registered VOICES. Every cue point plays the voice of its own
 *  name by default; a personality may re-voice a point with any other member (see
 *  `PersonalityBehavior.soundCues`).
 *
 *  Splitting voice from point is what keeps the feature from growing two different
 *  ways at once. A personality can change what a moment SOUNDS LIKE, because that
 *  is presentation — but it cannot add a moment (the point union is closed), cannot
 *  author a tone (it names a voice, it does not supply a recipe), and cannot make
 *  sound at all outside `playCue`, which owns all three suppressors. `CUES` is a
 *  total `Record`, so adding a member without a recipe fails to compile. */
export type CueName = CuePoint | 'coin_blip' | 'terminal_bell'

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
  // A cabinet swallowing a coin: a bright rising fifth on a square wave, the 8-bit
  // "credit accepted". claw-arcade re-voices `turn_complete` with it.
  coin_blip: { wave: 'square', freqs: [987.77, 1479.98], durMs: 110, gain: 0.05 },
  // The ASCII BEL a terminal rings when a job wants you: one flat, high, unadorned
  // tone. retro-terminal re-voices `approval_needed` with it.
  terminal_bell: { wave: 'triangle', freqs: [1760], durMs: 90, gain: 0.05 },
}

/** The active personality's cue-point → voice overrides.
 *
 *  A mutable module bridge, the `design/runtime.ts` pattern: `PersonalityProvider`
 *  writes it whenever the identity changes and `playCue` reads it. That keeps the
 *  three cue call sites unconditional one-liners with no policy and no knowledge of
 *  which identity is active — and it keeps this module free of any import from the
 *  app layer, so `playCue` still cannot be reached except through its own gates.
 *  Empty (the default identity, and every standard scheme) = every point plays its
 *  own voice. */
let voices: Partial<Record<CuePoint, CueName>> = {}

/** The three cue points at runtime. One declaration, read by `setCueVoices` below,
 *  so "which moments exist" cannot drift between the type and the validator. */
export const CUE_POINTS: readonly CuePoint[] = ['turn_complete', 'approval_needed', 'error']

/** Install the active personality's overrides, or clear them.
 *
 *  Validated at the BOUNDARY rather than at every read, and both sides are checked:
 *  a key is copied only if it is one of the three points, and a value only if it is a
 *  registered recipe. That matters because the map arrives from a registry the
 *  compiler may not have seen — a persisted override, the plan's forward-hooked
 *  app-contributed manifest. `Object.hasOwn`, not truthiness: a plain index reads the
 *  PROTOTYPE CHAIN, so a voice named `'constructor'` would otherwise resolve to
 *  `Object` and be handed to `synth` as a recipe (the same hole PT-3 measured live in
 *  both personality registries). Anything rejected leaves the point on its own voice.
 *
 *  Passing `undefined` is the restore path and must leave NOTHING behind — a
 *  personality's voice outliving the personality is residue you can only hear. */
export function setCueVoices(next: Partial<Record<CuePoint, CueName>> | undefined): void {
  const clean: Partial<Record<CuePoint, CueName>> = {}
  if (next) {
    for (const point of CUE_POINTS) {
      const v = next[point]
      if (v && Object.hasOwn(CUES, v)) clean[point] = v
    }
  }
  voices = clean
}

/** The voice a point will actually play — its override if one is installed, else the
 *  voice of its own name. Total: `setCueVoices` already dropped anything unplayable,
 *  so this can never hand `synth` a missing recipe. */
export function cueVoice(point: CuePoint): CueName {
  return voices[point] ?? point
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

/** Play the cue for a moment, or (far more often) don't.
 *
 *  Returns silently on any of: cues off, reduced motion, hidden tab, no context
 *  yet, no Web Audio. Never throws and never constructs a context — this runs from
 *  a toast render and an error path, where a throw would take the surface with it.
 *
 *  An explicit *voice* (a mobile push naming a per-kind sound, MOBILE-COMPANION `MC-6`)
 *  overrides the point's own/personality voice. It rides the SAME gates and the SAME
 *  single synth call — a push cue is a cue like any other, not a second sound path —
 *  and `Object.hasOwn` keeps a stale or inherited name off the synth, falling back to
 *  the point's voice.
 *
 *  🔑 THIS IS THE ONLY CALLER OF `synth`, and the four gates above it are the whole
 *  reason. `synth` is module-private and nothing else in here reaches it, so there is
 *  no way to make sound that skips the master toggle — which is what
 *  `personalityA11y.test.ts` asserts structurally rather than trusting to review. */
export function playCue(point: CuePoint, voice?: CueName): void {
  if (!soundCuesEnabled()) return
  if (prefersReducedMotion()) return
  if (typeof document !== 'undefined' && document.hidden) return
  const c = ctx
  if (!c) return
  try {
    if (c.state === 'suspended') void c.resume().catch(() => {})
    synth(c, CUES[voice && Object.hasOwn(CUES, voice) ? voice : cueVoice(point)])
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
