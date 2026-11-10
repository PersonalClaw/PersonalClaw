import { useCallback, useEffect, useRef, useState } from 'react'
import { accumulateTranscript } from './duplex'
import { analyserLevel, registerAudioSource, type AudioSourceHandle } from '../../lib/audioPresence'

export type MicState = 'idle' | 'recording' | 'transcribing'

/** How long one hands-free piece records before it is transcribed. A webm MediaRecorder recording
 *  is only decodable from its own header, so hands-free records a chain of short recordings on one
 *  open microphone rather than slicing one recording. */
export const HANDS_FREE_SEGMENT_MS = 4000

/** Thrown by a host's transcribe callback when speech-to-text refused or failed the audio and the
 *  host has already said why: the recorder adds no message of its own, and hands-free turns off. */
export class TranscriptionRefused extends Error {
  constructor(message?: string) {
    super(message)
    this.name = 'TranscriptionRefused'
  }
}

export interface HandsFreeOptions {
  /** Continuous listening: accumulate transcripts, send on a confirmation phrase. */
  enabled: boolean
  confirmationPhrases: readonly string[]
  exitPhrases: readonly string[]
  /** Fired with the accumulated text once a confirmation phrase lands. The text is empty when the
   *  phrase was all that was said: the host then sends what it holds, or nothing. */
  onSubmit: (text: string) => void
  /** True while a spoken reply is playing — the mic mutes and its buffered audio
   *  is discarded rather than transcribed. */
  muted?: boolean
  /** Buffer changes, so the host can show what has accumulated so far. */
  onBuffer?: (text: string) => void
  /** The loop ended on its own: the microphone could not be opened or stopped, or a piece could
   *  not be transcribed. The reason has been reported (through `onError`, or by the host itself);
   *  the host turns the mode off. */
  onEnded?: () => void
}

/** Said when the microphone stops while hands-free listens. */
const MIC_STOPPED = 'The microphone stopped — check that it is connected, then turn hands-free voice on again.'

/** One recording: its own chunks, and whether it is being thrown away. */
interface Piece {
  rec: MediaRecorder
  chunks: BlobPart[]
  discard: boolean
}

/** Microphone → MediaRecorder → webm blob → host transcribe callback. Toggling
 *  while recording stops + transcribes; the inserted text comes back from the
 *  host (which calls /api/stt/transcribe). Releases the mic track on stop.
 *
 *  In hands-free mode the microphone stays open for the whole mode. It records short pieces, one
 *  after the other with no gap, transcribes each in the background, folds the transcripts into an
 *  accumulation buffer in the order they were recorded, and only calls `onSubmit` when a
 *  confirmation phrase lands — a half-finished thought never becomes an executed instruction.
 *  While `handsFree.muted` is true the mic is released and any captured audio is dropped on the
 *  floor, so the assistant's own voice cannot re-enter as input. A blocked or stopped microphone, or
 *  a piece that cannot be transcribed, ends the loop (`onEnded`). */
export function useMicRecorder(
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>,
  onText?: (text: string) => void,
  onError?: (msg: string) => void,
  handsFree?: HandsFreeOptions,
) {
  const [state, setState] = useState<MicState>('idle')
  const pieceRef = useRef<Piece | null>(null)
  // The stream this recorder opened, as an audio-presence source for the Session Map: the one
  // place the map hears the microphone, and only while the owner already holds it open.
  const presenceRef = useRef<AudioSourceHandle | null>(null)
  const segmentTimerRef = useRef<number | null>(null)
  // Hands-free is a loop, so the callbacks must be read at fire time, not
  // captured — a stale closure would gate against last render's phrase list.
  const hfRef = useRef<HandsFreeOptions | undefined>(handsFree)
  hfRef.current = handsFree
  const transcribeRef = useRef(onTranscribe)
  transcribeRef.current = onTranscribe
  const errorRef = useRef(onError)
  errorRef.current = onError
  const bufferRef = useRef('')
  const loopingRef = useRef(false)
  // Hands-free's one microphone stream, open from the mode's first piece until the mode ends or a
  // spoken reply mutes it.
  const streamRef = useRef<MediaStream | null>(null)
  // One count per hands-free session: a transcript that comes back after its session ended (the
  // mode was turned off, or the loop failed) belongs to no buffer and is dropped.
  const sessionRef = useRef(0)
  // Each piece's transcript is folded after the one recorded before it, whatever order the
  // transcriptions come back in.
  const foldRef = useRef<Promise<void>>(Promise.resolve())
  // A dictation press is opening the microphone; and whether the hook is still mounted.
  const startingRef = useRef(false)
  const mountedRef = useRef(true)

  const clearSegmentTimer = () => {
    if (segmentTimerRef.current !== null) {
      window.clearTimeout(segmentTimerRef.current)
      segmentTimerRef.current = null
    }
  }

  const stop = useCallback(() => {
    clearSegmentTimer()
    const rec = pieceRef.current?.rec
    if (rec && rec.state !== 'inactive') rec.stop()
  }, [])

  /** Stop recording AND throw away whatever was captured. */
  const drain = useCallback(() => {
    const piece = pieceRef.current
    if (piece) { piece.discard = true; piece.chunks = [] }
    stop()
  }, [stop])

  function releaseStream() {
    const stream = streamRef.current
    streamRef.current = null
    presenceRef.current?.unregister()
    presenceRef.current = null
    stream?.getTracks().forEach((t) => t.stop())
  }

  /** End the hands-free loop: drop the piece recording now and anything still being transcribed,
   *  forget the half-dictated buffer, and release the microphone. */
  function endLoop() {
    loopingRef.current = false
    sessionRef.current += 1
    bufferRef.current = ''
    foldRef.current = Promise.resolve()
    drain()
    releaseStream()
    setState('idle')
  }

  /** The loop could not go on: say why (unless the host already did), and hand the mode back. */
  function fail(message: string | null) {
    if (message) errorRef.current?.(message)
    endLoop()
    hfRef.current?.onEnded?.()
  }

  function micBlocked(e: unknown): string {
    const name = (e as { name?: string })?.name || ''
    return name === 'NotAllowedError' || name === 'SecurityError'
      ? 'Microphone access was blocked — allow it in your browser to use voice input.'
      : 'No microphone available for voice input.'
  }

  function transcribeFailed(e: unknown): string | null {
    if (e instanceof TranscriptionRefused) return null
    return (e as Error)?.message
      ? `Couldn't transcribe the audio: ${(e as Error).message}`
      : 'Couldn’t transcribe the audio — try again.'
  }

  /** Fold one hands-free transcript into the buffer. A silent piece changes nothing. */
  function fold(text: string) {
    const hf = hfRef.current
    if (!hf) return
    const step = accumulateTranscript(bufferRef.current, text, {
      confirmation: hf.confirmationPhrases,
      exit: hf.exitPhrases,
    })
    if (step.action === 'ignore') return
    bufferRef.current = step.action === 'submit' ? '' : step.buffer
    hf.onBuffer?.(bufferRef.current)
    if (step.action === 'submit') hf.onSubmit(step.buffer)
  }

  /** Record the next hands-free piece on the mode's open stream. */
  function startPiece() {
    const stream = streamRef.current
    const hf = hfRef.current
    if (!stream || !loopingRef.current || !hf?.enabled || hf.muted) return
    const session = sessionRef.current
    let rec: MediaRecorder
    try {
      // A stream whose microphone went away (a headset unplugged, the grant revoked) records nothing.
      const tracks = stream.getAudioTracks?.() ?? []
      if (tracks.length && tracks.every((t) => t.readyState === 'ended')) throw new Error('the microphone track ended')
      rec = new MediaRecorder(stream)
      rec.start()
    } catch {
      // The pieces already recorded still fold, then the mode hands itself back with why.
      setState('idle')
      foldRef.current = foldRef.current.then(() => {
        if (session === sessionRef.current) fail(MIC_STOPPED)
      })
      return
    }
    const piece: Piece = { rec, chunks: [], discard: false }
    pieceRef.current = piece
    rec.ondataavailable = (e) => { if (e.data.size) piece.chunks.push(e.data) }
    rec.onstop = () => {
      if (pieceRef.current === piece) { clearSegmentTimer(); pieceRef.current = null }
      const blob = new Blob(piece.chunks, { type: 'audio/webm' })
      piece.chunks = []
      if (session !== sessionRef.current) return
      // This piece is queued for transcription first, so it folds even if the next one cannot start.
      const transcribe = transcribeRef.current
      if (!piece.discard && blob.size && transcribe) {
        // Started now, folded in turn; settled at once so a refusal is never an unhandled rejection.
        const answer = transcribe(blob, { duplex: true }).then(
          (text) => ({ text }),
          (error: unknown) => ({ error }),
        )
        foldRef.current = foldRef.current.then(async () => {
          const got = await answer
          if (session !== sessionRef.current) return
          // A fold that throws must not stall every piece after it.
          try {
            if ('error' in got) fail(transcribeFailed(got.error))
            else fold(got.text)
          } catch (e) {
            console.error('hands-free could not fold a transcript', e)
          }
        })
      }
      // The next piece starts the moment this one ends, so nothing said in between is lost.
      if (!piece.discard) startPiece()
      if (!pieceRef.current) setState('idle')
    }
    setState('recording')
    segmentTimerRef.current = window.setTimeout(() => { if (pieceRef.current === piece) stop() }, HANDS_FREE_SEGMENT_MS)
  }

  /** Open the mode's microphone, then record its first piece. */
  async function openLoop() {
    const session = sessionRef.current
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e) {
      if (session === sessionRef.current) fail(micBlocked(e))
      return
    }
    const hf = hfRef.current
    // The mode ended, or a reply began, while the browser was opening the microphone.
    if (session !== sessionRef.current || !loopingRef.current || streamRef.current || !hf?.enabled || hf.muted) {
      stream.getTracks().forEach((t) => t.stop())
      return
    }
    streamRef.current = stream
    presenceRef.current?.unregister()
    presenceRef.current = micPresence(stream)
    // When the microphone goes away mid-mode, the piece recording now ends (and is still heard), and
    // the next one cannot start, which ends the mode with its notice.
    stream.getAudioTracks?.()[0]?.addEventListener?.('ended', () => { if (session === sessionRef.current) stop() })
    startPiece()
  }

  // Dictation (the mic button and the desktop chord): one recording per press, transcribed when
  // it stops and handed to `onText`.
  const start = useCallback(async () => {
    if (!onTranscribe) return
    // One recording at a time: a press while the microphone is still opening, while one records,
    // or while hands-free listens opens nothing more, so no recorder is left holding the microphone.
    if (startingRef.current || pieceRef.current || loopingRef.current) return
    startingRef.current = true
    // The stream this press opened, released if its recorder cannot start.
    let opened: MediaStream | null = null
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      opened = stream
      // The page went away, or hands-free took the microphone, while it was opening.
      if (!mountedRef.current || loopingRef.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      const rec = new MediaRecorder(stream)
      const piece: Piece = { rec, chunks: [], discard: false }
      rec.ondataavailable = (e) => { if (e.data.size) piece.chunks.push(e.data) }
      rec.onstop = async () => {
        clearSegmentTimer()
        presenceRef.current?.unregister()
        presenceRef.current = null
        stream.getTracks().forEach((t) => t.stop())
        if (pieceRef.current === piece) pieceRef.current = null
        const blob = new Blob(piece.chunks, { type: 'audio/webm' })
        piece.chunks = []
        if (piece.discard || !blob.size) {
          setState('idle')
          return
        }
        setState('transcribing')
        try {
          const text = await onTranscribe(blob, { duplex: false })
          if (text) onText?.(text)
        } catch (e) {
          // A transcribe failure (STT down / network) must not reject unhandled —
          // route it through the same error channel as a mic-permission failure so
          // every composer surfaces it, not just ones whose wrapper happens to catch.
          const msg = transcribeFailed(e)
          if (msg) onError?.(msg)
        } finally {
          setState('idle')
        }
      }
      rec.start()
      // Only a recording that started holds the microphone: a recorder that could not start leaves
      // the next press free to try again.
      presenceRef.current?.unregister()
      presenceRef.current = micPresence(stream)
      pieceRef.current = piece
      setState('recording')
    } catch (e) {
      // mic permission denied / unavailable, or a recorder that would not start — release what was
      // opened and report it so the click isn't a silent no-op, then return to idle.
      opened?.getTracks().forEach((t) => t.stop())
      onError?.(micBlocked(e))
      setState('idle')
    } finally {
      startingRef.current = false
    }
  }, [onTranscribe, onText, onError])

  const toggle = useCallback(() => {
    if (state === 'recording') {
      if (!loopingRef.current) stop()
      else { endLoop(); hfRef.current?.onEnded?.() }
    }
    else if (state === 'idle' && !loopingRef.current) void start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, stop, start])

  // Mute-during-playback: while a spoken reply plays, release the mic and drop
  // whatever it captured; resume the loop when playback ends. Only the hands-free
  // loop is muted — push-to-talk is the user holding the mic deliberately. A piece already being
  // transcribed was said before the reply began, so it is still folded in.
  const muted = !!handsFree?.enabled && !!handsFree?.muted
  useEffect(() => {
    if (!handsFree?.enabled || !loopingRef.current) return
    if (muted) {
      drain()
      releaseStream()
      setState('idle')
      return
    }
    if (!streamRef.current) void openLoop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [muted, handsFree?.enabled])

  // Entering hands-free mode starts the loop; leaving it ends the loop, drains the
  // in-flight piece and forgets the half-dictated buffer (it would otherwise be
  // prepended to whatever is said next). The mode is the host's state, so the loop
  // follows the flag rather than making the caller sequence a start.
  const handsFreeOn = !!handsFree?.enabled
  useEffect(() => {
    if (handsFreeOn) {
      if (loopingRef.current) return
      loopingRef.current = true
      sessionRef.current += 1
      bufferRef.current = ''
      foldRef.current = Promise.resolve()
      if (!muted) void openLoop()
      return
    }
    if (loopingRef.current) endLoop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFreeOn])

  // Leaving the page ends whatever is recording and releases the microphone.
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  useEffect(() => () => {
    loopingRef.current = false
    sessionRef.current += 1
    const piece = pieceRef.current
    if (piece) { piece.discard = true; piece.chunks = [] }
    clearSegmentTimer()
    if (piece && piece.rec.state !== 'inactive') piece.rec.stop()
    releaseStream()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { state, toggle, drain, listening: loopingRef.current, muted }
}

/** The microphone stream a recording ALREADY holds, as an audio-presence source. Nothing is
 *  opened here: measuring wraps this stream in an analyser only while a Session Map subscribes
 *  (`lib/audioPresence`), and only its level leaves. Live while its track is live, enabled and not
 *  muted by the system; an ended or muted track is quiet at once. */
function micPresence(stream: MediaStream): AudioSourceHandle {
  const handle = registerAudioSource('microphone', () => {
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return { read: () => null, detach: () => {} }
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
        void ctx.close().catch(() => {})
      },
    }
  })
  const track = stream.getAudioTracks?.()[0]
  const live = () => !!track && track.readyState === 'live' && track.enabled && !track.muted
  handle.setLive(live())
  track?.addEventListener?.('ended', () => handle.setLive(false))
  track?.addEventListener?.('mute', () => handle.setLive(false))
  track?.addEventListener?.('unmute', () => handle.setLive(live()))
  return handle
}
