import { analyserLevel, registerAudioSource, type AttachedSource, type AudioSourceHandle } from './audioPresence'

/** IN-APP MEDIA — PersonalClaw's own `<audio>` and `<video>` as audio-presence sources (#4776).
 *
 *  While a Session Map watches (`watchInAppMedia`, from `pages/chat/sessionMapAudio.ts`), every media
 *  element in the page that is AUDIBLY playing is a source of kind `media`: playing, not ended,
 *  not muted, above zero volume, and not a display capture (a shared screen's preview, which is
 *  muted anyway). One inside a chat turn is owned by that turn's coordinate (`data-turn-coord`).
 *
 *  Measured through the element's own `captureStream()`, so the element's sound is never rerouted:
 *  `createMediaElementSource` would take its output for good, and silence it the moment the
 *  analyser went. A cross-origin element that refuses a capture is still said to be playing, with
 *  no level. Only the level leaves, as for every source (`lib/audioPresence`).
 *
 *  An element is followed by listeners on the ELEMENT once it has played, because a media element
 *  removed from the page is paused by the browser outside the document, where a document listener
 *  would never hear it.
 */

const EVENTS = ['play', 'playing', 'pause', 'ended', 'volumechange', 'emptied', 'abort', 'error'] as const

/** A shared screen's preview, or any element playing a display capture. */
function isDisplayCapture(el: HTMLMediaElement): boolean {
  const src = el.srcObject
  if (typeof MediaStream === 'undefined' || !(src instanceof MediaStream)) return false
  return src.getVideoTracks().some((t) => 'displaySurface' in (t.getSettings?.() ?? {}))
}

/** The element is making sound right now. */
export function audiblyPlaying(el: HTMLMediaElement): boolean {
  return el.isConnected && !el.paused && !el.ended && !el.muted && el.volume > 0 && !isDisplayCapture(el)
}

/** The chat turn an element sits in, by the coordinate the Session Map's entries own. */
function ownerOf(el: HTMLMediaElement): number | null {
  const at = el.closest('[data-turn-coord]')?.getAttribute('data-turn-coord')
  const n = at == null ? NaN : Number(at)
  return Number.isInteger(n) ? n : null
}

/** Measure an element through a capture of its own output, never by rerouting it. */
function measure(el: HTMLMediaElement): AttachedSource {
  const none: AttachedSource = { read: () => null, detach: () => {} }
  const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  const capture = (el as HTMLMediaElement & { captureStream?: () => MediaStream }).captureStream
  if (!Ctor || typeof capture !== 'function') return none
  let stream: MediaStream
  try { stream = capture.call(el) } catch { return none }
  if (!stream.getAudioTracks().length) { stream.getTracks().forEach((t) => t.stop()); return none }
  const ctx = new Ctor()
  const node = ctx.createMediaStreamSource(stream)
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 512
  node.connect(analyser)
  const buf = new Float32Array(analyser.fftSize)
  return {
    read: () => analyserLevel(analyser, buf),
    detach: () => {
      try { node.disconnect() } catch { /* already gone */ }
      stream.getTracks().forEach((t) => t.stop())
      void ctx.close().catch(() => {})
    },
  }
}

interface Tracked { handle: AudioSourceHandle; follow: () => void }
const tracked = new Map<HTMLMediaElement, Tracked>()
let watchers = 0

function forget(el: HTMLMediaElement) {
  const t = tracked.get(el)
  if (!t) return
  tracked.delete(el)
  for (const ev of EVENTS) el.removeEventListener(ev, t.follow)
  t.handle.unregister()
}

/** Bring one element's source in line with whether it is audibly playing. */
function sync(el: HTMLMediaElement) {
  const audible = audiblyPlaying(el)
  let t = tracked.get(el)
  if (!t) {
    if (!audible) return
    const handle = registerAudioSource('media', () => measure(el))
    const follow = () => sync(el)
    for (const ev of EVENTS) el.addEventListener(ev, follow)
    t = { handle, follow }
    tracked.set(el, t)
  }
  t.handle.setOwner(ownerOf(el))
  t.handle.setLive(audible)
  // Gone from the page, or played to its end: nothing left to follow.
  if (!audible && (!el.isConnected || el.ended)) forget(el)
}

const onDocumentMedia = (e: Event) => {
  if (e.target instanceof HTMLMediaElement) sync(e.target)
}

/** Follow the page's media while the caller watches; the last watcher leaving forgets them all.
 *  Returns the stop. */
export function watchInAppMedia(): () => void {
  if (watchers++ === 0) {
    // Media events do not bubble: capture them on the way down.
    for (const ev of ['play', 'playing', 'volumechange'] as const) document.addEventListener(ev, onDocumentMedia, true)
    document.querySelectorAll('audio, video').forEach((el) => sync(el as HTMLMediaElement))
  }
  let stopped = false
  return () => {
    if (stopped) return
    stopped = true
    if (--watchers > 0) return
    for (const ev of ['play', 'playing', 'volumechange'] as const) document.removeEventListener(ev, onDocumentMedia, true)
    for (const el of [...tracked.keys()]) forget(el)
  }
}

/** For tests: forget every element and watcher. */
export function resetMediaPresence() {
  for (const el of [...tracked.keys()]) forget(el)
  if (watchers > 0) for (const ev of ['play', 'playing', 'volumechange'] as const) document.removeEventListener(ev, onDocumentMedia, true)
  watchers = 0
}
