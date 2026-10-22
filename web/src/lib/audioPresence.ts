/** AUDIO PRESENCE — how loud PersonalClaw's own audio is, for the Session Map to show (#4776).
 *
 *  Three kinds of source report here: a spoken reply PersonalClaw is playing (`playback`, owned by
 *  the reply's turn), PersonalClaw's own media elements while they audibly play (`media`, owned by
 *  the chat turn they sit in, `lib/mediaPresence`), and a microphone stream the owner ALREADY opened
 *  for dictation or hands-free voice (`microphone`). The Session Map's rail subscribes while it is visible and wants the
 *  presence, and is told one normalized level per source, at most once a frame.
 *
 *  ── WHAT LEAVES THE CAPTURE PATH ────────────────────────────────────────────────────────────
 *
 *  A level: one number from 0 to 1, in sixteenths. Never samples, never a spectrum, never a
 *  history. Nothing here is kept past the frame that read it, logged, sent or stored.
 *
 *  ── WHEN ANYTHING RUNS ──────────────────────────────────────────────────────────────────────
 *
 *  · A source REGISTERS with an `attach` it has not called. Nothing is measured, and no analyser
 *    exists, until a subscriber arrives: `attach` runs on the first subscribe (or on registering,
 *    while one is subscribed), and the handle it returns is detached when the last subscriber
 *    leaves or the source unregisters. A microphone is never opened here: the source wraps the
 *    stream its owner already holds.
 *  · A subscriber that wants no levels (the rail under reduced motion, which shows a still
 *    "Playing" or "Listening" instead) is told only which kinds are live: it attaches nothing and
 *    runs no frame.
 *  · ONE frame loop, and only while a subscriber wants levels AND a source says it is live. A source
 *    that goes quiet (a reply ended, the microphone muted for it) says so, its level is cleared at
 *    once, and with no live source the loop schedules nothing.
 *  · A level not refreshed for `STALE_MS` is cleared, so a source that stops reporting without
 *    saying so (a stalled analyser, a context the browser suspended) cannot leave the rail lit.
 */

export type AudioPresenceKind = 'playback' | 'media' | 'microphone'

/** What a source returns from `attach`: how to read its level now, and how to stop. */
export interface AttachedSource {
  /** The level right now, 0..1 (any value; it is clamped and quantized here), or `null` when the
   *  source has nothing to say this frame. */
  read: () => number | null
  /** Disconnect whatever `attach` connected. Called once. */
  detach: () => void
}

export interface AudioSourceHandle {
  /** The source is audibly live (`true`) or not (`false`). Not live clears its level at once. */
  setLive: (live: boolean) => void
  /** Who the audio belongs to: a playing reply's turn coordinate. */
  setOwner: (owner: number | null) => void
  /** Leave for good: detach and forget. */
  unregister: () => void
}

/** What a subscriber is told: the level of each kind that is live, and whose a playback or a
 *  media element is. */
export interface AudioPresence {
  playback: { level: number; owner: number | null } | null
  media: { level: number; owner: number | null } | null
  microphone: { level: number } | null
}

export type AudioPresenceSink = (presence: AudioPresence) => void

/** A level not refreshed for this long is cleared. */
export const STALE_MS = 250

/** Levels are reported in this many steps between 0 and 1. */
export const LEVEL_STEPS = 16

interface Source {
  kind: AudioPresenceKind
  attach: () => AttachedSource
  attached: AttachedSource | null
  live: boolean
  owner: number | null
  level: number
  /** When the level last moved off zero, by the frame clock. */
  heardAt: number
}

const sources = new Set<Source>()
/** Every subscriber, and whether it wants levels (`true`) or only which kinds are live. */
const sinks = new Map<AudioPresenceSink, boolean>()

/** Some subscriber wants levels: the sources are attached and the frame loop may run. */
function measuring(): boolean {
  for (const levels of sinks.values()) if (levels) return true
  return false
}
let frame: number | null = null
/** The last presence told, so an unchanged frame tells nobody. */
let told = ''

/** Clamp and quantize a raw level to `LEVEL_STEPS`. */
export function quantize(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 0
  return Math.round(Math.min(1, raw) * LEVEL_STEPS) / LEVEL_STEPS
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now()
}

function presence(): AudioPresence {
  let playback: AudioPresence['playback'] = null
  let media: AudioPresence['media'] = null
  let microphone: AudioPresence['microphone'] = null
  for (const s of sources) {
    // Live is what is said; a level is said only while the source is measured.
    if (!s.live) continue
    if (s.kind === 'playback') {
      if (!playback || s.level > playback.level) playback = { level: s.level, owner: s.owner }
    } else if (s.kind === 'media') {
      // An owned element outranks an unowned one, since it has a marker to show on; between two
      // alike, the louder.
      const owned = s.owner !== null
      const ownedNow = media !== null && media.owner !== null
      if (!media || (owned && !ownedNow) || (owned === ownedNow && s.level > media.level)) {
        media = { level: s.level, owner: s.owner }
      }
    } else if (!microphone || s.level > microphone.level) microphone = { level: s.level }
  }
  return { playback, media, microphone }
}

function tell() {
  const p = presence()
  const key = JSON.stringify(p)
  if (key === told) return
  told = key
  for (const sink of [...sinks.keys()]) sink(p)
}

function anyLive(): boolean {
  if (!measuring()) return false
  for (const s of sources) if (s.live && s.attached) return true
  return false
}

function tick() {
  frame = null
  const t = now()
  for (const s of sources) {
    if (!s.live || !s.attached) continue
    let raw: number | null = null
    try { raw = s.attached.read() } catch { raw = null }
    const level = raw === null ? 0 : quantize(raw)
    if (level > 0) { s.level = level; s.heardAt = t }
    else if (t - s.heardAt >= STALE_MS) s.level = 0
  }
  tell()
  schedule()
}

function schedule() {
  if (frame !== null || !anyLive() || typeof requestAnimationFrame === 'undefined') return
  frame = requestAnimationFrame(tick)
}

function stopIfIdle() {
  if (frame !== null && !anyLive()) {
    cancelAnimationFrame(frame)
    frame = null
  }
}

function attach(s: Source) {
  if (s.attached || !measuring()) return
  try { s.attached = s.attach() } catch { s.attached = null }
  s.heardAt = now()
}

function detach(s: Source) {
  const a = s.attached
  s.attached = null
  s.level = 0
  if (a) { try { a.detach() } catch { /* already gone */ } }
}

/** Register a source. `attach` is called only while someone subscribes, and its handle is
 *  detached when no one does; the source starts not live. */
export function registerAudioSource(kind: AudioPresenceKind, attachSource: () => AttachedSource): AudioSourceHandle {
  const s: Source = { kind, attach: attachSource, attached: null, live: false, owner: null, level: 0, heardAt: 0 }
  sources.add(s)
  attach(s)
  return {
    setLive(live) {
      if (!sources.has(s) || s.live === live) return
      s.live = live
      if (!live) s.level = 0
      else s.heardAt = now()
      tell()
      if (live) schedule()
      else stopIfIdle()
    },
    setOwner(owner) {
      if (!sources.has(s) || s.owner === owner) return
      s.owner = owner
      tell()
    },
    unregister() {
      if (!sources.delete(s)) return
      detach(s)
      tell()
      stopIfIdle()
    },
  }
}

/** Subscribe to the presence. With `levels` (the default) the first such subscriber attaches every
 *  source and the last one to leave detaches them all; without, the subscriber is told only which
 *  kinds are live and whose a playback is, at level 0. Returns the unsubscribe. */
export function subscribeAudioPresence(sink: AudioPresenceSink, { levels = true }: { levels?: boolean } = {}): () => void {
  sinks.set(sink, levels)
  for (const s of sources) attach(s)
  sink(presence())
  schedule()
  return () => {
    if (!sinks.delete(sink)) return
    if (!measuring()) for (const s of sources) detach(s)
    if (!sinks.size) told = ''
    stopIfIdle()
  }
}

/** The RMS level of an analyser's current window, scaled so speech at a normal level reads near
 *  the top. The one reading every source shares: `AnalyserNode.getFloatTimeDomainData`. */
export function analyserLevel(analyser: AnalyserNode, buf: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(buf)
  let sum = 0
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
  return Math.sqrt(sum / buf.length) * 4
}

/** For tests: forget every source and subscriber. */
export function resetAudioPresence() {
  for (const s of sources) detach(s)
  sources.clear()
  sinks.clear()
  if (frame !== null && typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(frame)
  frame = null
  told = ''
}
