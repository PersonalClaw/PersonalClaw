// ── Push cue playback ────────────────────────────────────────────────────────
//
// A service worker cannot play audio, so `sw.ts` shows the push notification
// silent + vibrate and posts the per-kind VOICE here (`PUSH_CUE_MESSAGE`); this
// open client is the only thing that can voice it. The play still goes through
// `playCue`, so every suppressor (master toggle, reduced motion, hidden tab) and
// the single-AudioContext discipline apply unchanged — a push cue is a cue like
// any other, not a second, ungated sound path.

import { CUES, CUE_POINTS, playCue, type CueName, type CuePoint } from '../design/soundCues'
import { api } from '../lib/api'
import { PUSH_CUE_MESSAGE, notificationFor, soundMapFromRules } from './pushPolicy'

/** Start playing push-delivered cues. Returns a disposer that removes the listener; a no-op
 *  (with a no-op disposer) where service workers are unavailable, so callers need not guard. */
export function installPushCuePlayback(): () => void {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return () => {}
  const onMessage = (event: MessageEvent) => {
    const data = event.data as { type?: unknown; cue?: unknown; kind?: unknown } | null
    if (!data || data.type !== PUSH_CUE_MESSAGE) return
    // The worker hands over the push's kind, and this page resolves its voice through its own
    // request layer, which carries the sign-in's pair. Only a page the owner can see plays it.
    if (typeof data.kind === 'string') {
      if (document.visibilityState !== 'visible') return
      void voiceFor(data.kind).then((voice) => { if (voice) play(voice) })
      return
    }
    // {cue}: a worker from the release before resolved the voice itself. Kept for one release.
    play(data.cue)
  }
  navigator.serviceWorker.addEventListener('message', onMessage)
  return () => navigator.serviceWorker.removeEventListener('message', onMessage)
}

/** The voice the owner's notification rules give *kind*, or `undefined` for none. A rules read
 *  that fails gives none: the push is still shown, only silent. */
async function voiceFor(kind: string): Promise<string | undefined> {
  try {
    const sounds = soundMapFromRules(await api.notificationRules())
    return notificationFor({ kind, item_id: '' }, sounds).sound
  } catch {
    return undefined
  }
}

function play(cue: unknown): void {
  // Validate against the registered voices before playing — the message crosses a
  // postMessage boundary, and an unknown voice must fall to SILENCE, never to a fallback
  // tone the user did not choose (which is what playCue does for an unknown voice).
  if (typeof cue !== 'string' || !Object.hasOwn(CUES, cue)) return
  // The point is only the personality-fallback voice, which the explicit push voice always
  // overrides; when the voice IS a cue point, use it as the anchor, else a neutral one.
  const point: CuePoint = (CUE_POINTS as readonly string[]).includes(cue)
    ? (cue as CuePoint)
    : 'turn_complete'
  playCue(point, cue as CueName)
}
