import { useCallback, useEffect, useRef, useState } from 'react'
import { accumulateTranscript } from './duplex'

export type MicState = 'idle' | 'recording' | 'transcribing'

/** How long one hands-free segment records before it is transcribed. A webm
 *  MediaRecorder stream is only decodable from its header, so continuous listening
 *  is a chain of complete short recordings rather than one sliced stream. */
export const HANDS_FREE_SEGMENT_MS = 4000

export interface HandsFreeOptions {
  /** Continuous listening: accumulate transcripts, send on a confirmation phrase. */
  enabled: boolean
  confirmationPhrases: readonly string[]
  exitPhrases: readonly string[]
  /** Fired with the accumulated text once a confirmation phrase lands. */
  onSubmit: (text: string) => void
  /** True while a spoken reply is playing — the mic mutes and its buffered audio
   *  is discarded rather than transcribed (MULTIMODAL-IO §4.2). */
  muted?: boolean
  /** Buffer changes, so the host can show what has accumulated so far. */
  onBuffer?: (text: string) => void
}

/** Microphone → MediaRecorder → webm blob → host transcribe callback. Toggling
 *  while recording stops + transcribes; the inserted text comes back from the
 *  host (which calls /api/stt/transcribe). Releases the mic track on stop.
 *
 *  In hands-free mode (MULTIMODAL-IO §4.1) the hook keeps re-recording short
 *  segments, folds each transcript into an accumulation buffer, and only calls
 *  `onSubmit` when a confirmation phrase lands — a half-finished thought never
 *  becomes an executed instruction. While `handsFree.muted` is true the mic is
 *  released and any captured audio is dropped on the floor, so the assistant's
 *  own voice cannot re-enter as input. */
export function useMicRecorder(
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>,
  onText?: (text: string) => void,
  onError?: (msg: string) => void,
  handsFree?: HandsFreeOptions,
) {
  const [state, setState] = useState<MicState>('idle')
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const segmentTimerRef = useRef<number | null>(null)
  // Hands-free is a loop, so the callbacks must be read at fire time, not
  // captured — a stale closure would gate against last render's phrase list.
  const hfRef = useRef<HandsFreeOptions | undefined>(handsFree)
  hfRef.current = handsFree
  const bufferRef = useRef('')
  // Set while a segment is being abandoned (mute / hands-free off): its audio is
  // discarded instead of transcribed. This is the "drain the mic buffer" half of
  // mute-during-playback — without it the queued speech is merely delayed, and the
  // assistant's own words arrive as input a moment later.
  const discardRef = useRef(false)
  const loopingRef = useRef(false)

  const clearSegmentTimer = () => {
    if (segmentTimerRef.current !== null) {
      window.clearTimeout(segmentTimerRef.current)
      segmentTimerRef.current = null
    }
  }

  const stop = useCallback(() => {
    clearSegmentTimer()
    const rec = recRef.current
    if (rec && rec.state !== 'inactive') rec.stop()
  }, [])

  /** Stop recording AND throw away whatever was captured. */
  const drain = useCallback(() => {
    discardRef.current = true
    chunksRef.current = []
    stop()
  }, [stop])

  const start = useCallback(async () => {
    if (!onTranscribe) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      recRef.current = rec
      chunksRef.current = []
      discardRef.current = false
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      rec.onstop = async () => {
        clearSegmentTimer()
        stream.getTracks().forEach((t) => t.stop())
        recRef.current = null
        const discarded = discardRef.current
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        chunksRef.current = []
        discardRef.current = false
        if (discarded || !blob.size) {
          setState('idle')
          if (!discarded) maybeContinue()
          return
        }
        setState('transcribing')
        try {
          const hf = hfRef.current
          const text = await onTranscribe(blob, { duplex: !!hf?.enabled })
          if (hf?.enabled) {
            // Hands-free: the transcript feeds the buffer, not the composer.
            const step = accumulateTranscript(bufferRef.current, text, {
              confirmation: hf.confirmationPhrases,
              exit: hf.exitPhrases,
            })
            bufferRef.current = step.action === 'submit' ? '' : step.buffer
            hf.onBuffer?.(bufferRef.current)
            if (step.action === 'submit') hf.onSubmit(step.buffer)
          } else if (text) {
            onText?.(text)
          }
        } catch (e) {
          // A transcribe failure (STT down / network) must not reject unhandled —
          // route it through the same error channel as a mic-permission failure so
          // every composer surfaces it, not just ones whose wrapper happens to catch.
          onError?.((e as Error)?.message
            ? `Couldn't transcribe the audio: ${(e as Error).message}`
            : 'Couldn’t transcribe the audio — try again.')
        } finally {
          setState('idle')
          maybeContinue()
        }
      }
      rec.start()
      setState('recording')
      // Hands-free listens in bounded segments; push-to-talk records until the
      // user toggles off.
      if (hfRef.current?.enabled) {
        segmentTimerRef.current = window.setTimeout(() => stop(), HANDS_FREE_SEGMENT_MS)
      }
    } catch (e) {
      // mic permission denied / unavailable — report it so the click isn't a
      // silent no-op, then return to idle.
      loopingRef.current = false
      const name = (e as { name?: string })?.name || ''
      onError?.(name === 'NotAllowedError' || name === 'SecurityError'
        ? 'Microphone access was blocked — allow it in your browser to use voice input.'
        : 'No microphone available for voice input.')
      setState('idle')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onTranscribe, onText, onError, stop])

  /** Start the next hands-free segment, unless the loop stopped or is muted. */
  function maybeContinue() {
    const hf = hfRef.current
    if (!loopingRef.current || !hf?.enabled || hf.muted) return
    void start()
  }

  const toggle = useCallback(() => {
    if (state === 'recording') { loopingRef.current = false; stop() }
    else if (state === 'idle') void start()
  }, [state, stop, start])

  // Mute-during-playback: while a spoken reply plays, release the mic and drop
  // whatever it captured; resume the loop when playback ends. Only the hands-free
  // loop is muted — push-to-talk is the user holding the mic deliberately.
  const muted = !!handsFree?.enabled && !!handsFree?.muted
  useEffect(() => {
    if (!handsFree?.enabled) return
    if (muted) {
      if (recRef.current) drain()
      return
    }
    if (loopingRef.current && !recRef.current && state === 'idle') void start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [muted, handsFree?.enabled])

  // Entering hands-free mode starts the loop; leaving it ends the loop, drains the
  // in-flight segment and forgets the half-dictated buffer (it would otherwise be
  // prepended to whatever is said next). The mode is the host's state, so the loop
  // follows the flag rather than making the caller sequence a start.
  const handsFreeOn = !!handsFree?.enabled
  useEffect(() => {
    if (handsFreeOn) {
      if (loopingRef.current) return
      loopingRef.current = true
      if (!recRef.current && !muted) void start()
      return
    }
    loopingRef.current = false
    bufferRef.current = ''
    if (recRef.current) drain()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFreeOn])

  return { state, toggle, drain, listening: loopingRef.current, muted }
}
