import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'

/** Longest edge a staged frame is downscaled to before encoding. Matches the
 *  vision-model input budget the platform assumes elsewhere; a 4K screenshot sent
 *  at full size is mostly wasted tokens. */
const MAX_EDGE = 1568
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
  // An offscreen <video> is the portable way to read a frame out of a display
  // stream: `ImageCapture.grabFrame` is neither in the TS DOM lib nor implemented in
  // Safari, so it would have made screen sharing a Chrome-only feature.
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
    if (stream) stream.getTracks().forEach((t) => t.stop())
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
    const md = navigator.mediaDevices as MediaDevices & {
      getDisplayMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>
    }
    if (typeof md?.getDisplayMedia !== 'function') {
      onError?.('This browser cannot share a screen.')
      return
    }
    let stream: MediaStream
    try {
      stream = await md.getDisplayMedia({ video: true, audio: false })
    } catch (e) {
      const name = (e as DOMException)?.name
      // A cancelled picker is not an error — the user changed their mind.
      if (name !== 'AbortError' && name !== 'NotAllowedError') {
        onError?.('Screen sharing could not start.')
      }
      return
    }
    try {
      await api.screenShareSignal(sessionRef.current, 'start')
    } catch {
      // The server refused (the switch went off under us). Do not keep a capture
      // running that the backend will not accept a frame from.
      stream.getTracks().forEach((t) => t.stop())
      onError?.('Screen sharing is off. Turn it on in Settings → Chat.')
      refresh()
      return
    }
    streamRef.current = stream
    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true
    video.srcObject = stream
    try { await video.play() } catch { /* a paused element still yields frames once metadata lands */ }
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
      const w = video.videoWidth
      const h = video.videoHeight
      if (!w || !h) return
      const scale = Math.min(1, MAX_EDGE / Math.max(w, h))
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(w * scale))
      canvas.height = Math.max(1, Math.round(h * scale))
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
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
