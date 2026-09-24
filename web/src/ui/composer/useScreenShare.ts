import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import {
  SHARE_MAX_EDGE,
  acquireDisplayStream,
  drawFrame,
  frameSource,
  stopStream,
} from './displayCapture'

const JPEG_QUALITY = 0.72

type Delivery = 'native' | 'described' | 'none'

export interface ScreenShareState {
  /** The config master switch (`dashboard.screen_share_enabled`) is on. */
  available: boolean
  /** A display stream is live right now. */
  sharing: boolean
  /** Server-composed reason the control is unavailable (empty when it is usable).
   *  The underlying delivery mode ('native' pixels vs a fenced description) is
   *  deliberately NOT exported: it is a server-side routing decision, and a second
   *  copy of it here would be a value the UI could render out of step with the
   *  decision actually taken on the turn. */
  disabledReason: string
  /** Start or stop sharing. */
  toggle: () => void
  /** Capture ONE frame and stage it for the next turn. No-op when not sharing. */
  captureAndStage: (session: string) => Promise<void>
  /** The last frame captured, as a data URL — what "Pin frame" pins. */
  lastFrame: () => string
}

/**
 * `getDisplayMedia`-backed screen sharing for one chat session (MULTIMODAL-IO §5.2).
 *
 * **Indicator honesty is the whole design.** Consent and visibility are the
 * BROWSER's job: its own picker chooses what is shared, and its own capture
 * indicator (tab badge / OS overlay / "Stop sharing" bar) says so for as long as the
 * track is live. This hook never tries to keep a stream alive past that indicator —
 * it listens for the track's `ended` event, so pressing the browser's own stop
 * button tears our state down too. The in-app pulsing chip is an ADDITION to that
 * indicator, never a replacement: a capture surface whose only signal is one the app
 * controls is exactly the pattern a user cannot audit.
 *
 * **Frame-on-send, not a stream.** Nothing is sent to the backend while the user
 * types. `captureAndStage` grabs one frame at the moment a turn is sent. Personal
 * scale means the model sees the screen when it is addressed, not at 30fps.
 */
export function useScreenShare(session: string, onError?: (msg: string) => void): ScreenShareState {
  const [available, setAvailable] = useState(false)
  const [delivery, setDelivery] = useState<Delivery>('none')
  const [disabledReason, setDisabledReason] = useState('')
  const [sharing, setSharing] = useState(false)
  const streamRef = useRef<MediaStream | null>(null)
  // The acquisition, the offscreen <video> frame source and the track teardown are
  // shared with the screen SNIP (`displayCapture.ts`) — one getDisplayMedia call site
  // in the app, so there is one place a teardown could be got wrong.
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const lastFrameRef = useRef('')
  const sessionRef = useRef(session)
  sessionRef.current = session

  // Ask the server whether the control should be offered, and why not. The reason is
  // composed server-side (it owns the model-binding decision) so the UI can't drift
  // into its own explanation of a call it doesn't make.
  const refresh = useCallback(() => {
    api.screenShareState(sessionRef.current)
      .then((s) => { setAvailable(!!s.enabled); setDelivery(s.delivery); setDisabledReason(s.reason || '') })
      .catch(() => { setAvailable(false); setDelivery('none'); setDisabledReason('') })
  }, [])
  useEffect(() => { refresh() }, [refresh, session])

  const teardown = useCallback((notifyServer: boolean) => {
    const stream = streamRef.current
    streamRef.current = null
    lastFrameRef.current = ''
    if (videoRef.current) { videoRef.current.srcObject = null; videoRef.current = null }
    stopStream(stream)
    setSharing(false)
    if (notifyServer && sessionRef.current) {
      // Drops the server-side slot immediately rather than leaving it for the next
      // drain: stopping the share must un-stage the frame it already captured.
      api.screenShareSignal(sessionRef.current, 'stop').catch(() => {})
    }
  }, [])

  // Stop on unmount — leaving a capture running after the surface is gone would keep
  // the browser indicator lit with nothing in the app explaining it.
  useEffect(() => () => teardown(true), [teardown])

  const start = useCallback(async () => {
    const acquired = await acquireDisplayStream()
    if (typeof acquired === 'string') {
      // A cancelled picker is not an error — the user changed their mind.
      if (acquired === 'unsupported') onError?.('This browser cannot share a screen.')
      else if (acquired === 'failed') onError?.('Screen sharing could not start.')
      return
    }
    const stream = acquired
    try {
      await api.screenShareSignal(sessionRef.current, 'start')
    } catch {
      // The server refused (the switch went off under us). Do not keep a capture
      // running that the backend will not accept a frame from.
      stopStream(stream)
      onError?.('Screen sharing is off. Turn it on in Settings → Chat.')
      refresh()
      return
    }
    streamRef.current = stream
    const video = await frameSource(stream)
    videoRef.current = video
    setSharing(true)
    // The browser's own "Stop sharing" button ends the track without telling us any
    // other way. Without this the chip would keep pulsing over a dead stream — the
    // app claiming to see a screen it cannot.
    stream.getVideoTracks().forEach((t) => t.addEventListener('ended', () => teardown(true)))
  }, [onError, refresh, teardown])

  const toggle = useCallback(() => {
    if (streamRef.current) teardown(true)
    else void start()
  }, [start, teardown])

  const captureAndStage = useCallback(async (target: string) => {
    const stream = streamRef.current
    const video = videoRef.current
    if (!stream || !video || !target) return
    const track = stream.getVideoTracks()[0]
    if (!track || track.readyState !== 'live') return
    let dataUrl = ''
    try {
      const canvas = drawFrame(video, SHARE_MAX_EDGE)
      if (!canvas) return
      dataUrl = canvas.toDataURL('image/jpeg', JPEG_QUALITY)
    } catch {
      // A frame we could not grab is simply not sent. Failing the turn over it would
      // punish the user for a capture glitch.
      return
    }
    if (!dataUrl) return
    lastFrameRef.current = dataUrl
    try {
      await api.stageScreenFrame(target, dataUrl)
    } catch {
      // Refused (the switch went off mid-session): stop sharing rather than keep
      // capturing frames the server will not take.
      teardown(false)
      refresh()
      onError?.('Screen sharing is off. Turn it on in Settings → Chat.')
    }
  }, [onError, refresh, teardown])

  return {
    available,
    sharing,
    disabledReason: available && delivery === 'none' ? disabledReason : '',
    toggle,
    captureAndStage,
    lastFrame: () => lastFrameRef.current,
  }
}
