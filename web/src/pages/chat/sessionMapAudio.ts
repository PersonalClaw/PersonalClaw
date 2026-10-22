import { useEffect, useRef, useState } from 'react'
import { subscribeAudioPresence } from '../../lib/audioPresence'
import { watchInAppMedia } from '../../lib/mediaPresence'
import { prefersReducedMotion } from '../../design/motion'

/** THE SESSION MAP'S AUDIO PRESENCE — what the rail shows of PersonalClaw's own audio (#4776).
 *
 *  While the owner has turned it on (Settings › Speech › "Session Map reacts to audio", off by
 *  default), the rail subscribes to `lib/audioPresence` — and only while it is mounted and the
 *  document is visible. Two things come back:
 *
 *   · WHO: the reply being read aloud (its turn coordinate) and whether the microphone the owner
 *     opened is listening. These change a few times a conversation, so they are React state: the
 *     marker's name says "playing" or "listening", and its line is drawn thicker.
 *   · HOW LOUD: one level per kind, many times a second. That never touches React: it is written
 *     to two CSS variables on the rail (`--map-play-level`, `--map-mic-level`), which the playing or
 *     listening marker's energy line scales by on a short CSS transition.
 *
 *  Under reduced motion the rail subscribes for WHO alone: nothing is measured, no frame runs, and
 *  the thicker, named marker is the still indicator. The text and the system's own capture
 *  indicators stay what says the microphone is on; this is decoration beside them.
 */

export interface RailAudio {
  /** The coordinate of the reply being read aloud, or `null`. */
  playing: number | null
  /** The microphone the owner opened is listening. */
  listening: boolean
  /** Levels are shown (motion allowed); `false` means the still indicator only. */
  moving: boolean
}

const QUIET: RailAudio = { playing: null, listening: false, moving: false }

function documentVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}

/** Subscribe the rail while `enabled` and the document is visible; write levels onto `target`.
 *  With `still` (the touch drawer, which draws no level line) it is told who alone, as under
 *  reduced motion. */
export function useRailAudio(enabled: boolean, target: { current: HTMLElement | null }, { still = false }: { still?: boolean } = {}): RailAudio {
  const [visible, setVisible] = useState(documentVisible)
  const [audio, setAudio] = useState<RailAudio>(QUIET)
  const shown = useRef<RailAudio>(QUIET)

  useEffect(() => {
    if (!enabled) return
    const onVisibility = () => setVisible(documentVisible())
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [enabled])

  useEffect(() => {
    if (!enabled || !visible) return
    const moving = !still && !prefersReducedMotion()
    // The page's own media elements report only while a map watches them.
    const unwatch = watchInAppMedia()
    const stop = subscribeAudioPresence((p) => {
      // A reply being read aloud is what plays; else an owned media element in a chat turn. Media
      // with no turn has no marker to show on.
      const play = p.playback ?? (p.media?.owner != null ? p.media : null)
      const next: RailAudio = { playing: play?.owner ?? null, listening: !!p.microphone, moving }
      const el = target.current
      if (el && moving) {
        el.style.setProperty('--map-play-level', String(play?.level ?? 0))
        el.style.setProperty('--map-mic-level', String(p.microphone?.level ?? 0))
      }
      const was = shown.current
      if (was.playing !== next.playing || was.listening !== next.listening || was.moving !== next.moving) {
        shown.current = next
        setAudio(next)
      }
    }, { levels: moving })
    return () => {
      stop()
      unwatch()
      shown.current = QUIET
      setAudio(QUIET)
      target.current?.style.removeProperty('--map-play-level')
      target.current?.style.removeProperty('--map-mic-level')
    }
  }, [enabled, visible, target, still])

  return audio
}
