import { useCallback, useRef, useState } from 'react'

export type MicState = 'idle' | 'recording' | 'transcribing'

/** Microphone → MediaRecorder → webm blob → host transcribe callback. Toggling
 *  while recording stops + transcribes; the inserted text comes back from the
 *  host (which calls /api/stt/transcribe). Releases the mic track on stop. */
export function useMicRecorder(onTranscribe?: (blob: Blob) => Promise<string>, onText?: (text: string) => void, onError?: (msg: string) => void) {
  const [state, setState] = useState<MicState>('idle')
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])

  const stop = useCallback(() => {
    const rec = recRef.current
    if (rec && rec.state !== 'inactive') rec.stop()
  }, [])

  const start = useCallback(async () => {
    if (!onTranscribe) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      recRef.current = rec
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        recRef.current = null
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        if (!blob.size) { setState('idle'); return }
        setState('transcribing')
        try {
          const text = await onTranscribe(blob)
          if (text) onText?.(text)
        } catch (e) {
          // A transcribe failure (STT down / network) must not reject unhandled —
          // route it through the same error channel as a mic-permission failure so
          // every composer surfaces it, not just ones whose wrapper happens to catch.
          onError?.((e as Error)?.message
            ? `Couldn't transcribe the audio: ${(e as Error).message}`
            : 'Couldn’t transcribe the audio — try again.')
        } finally {
          setState('idle')
        }
      }
      rec.start()
      setState('recording')
    } catch (e) {
      // mic permission denied / unavailable — report it so the click isn't a
      // silent no-op, then return to idle.
      const name = (e as { name?: string })?.name || ''
      onError?.(name === 'NotAllowedError' || name === 'SecurityError'
        ? 'Microphone access was blocked — allow it in your browser to use voice input.'
        : 'No microphone available for voice input.')
      setState('idle')
    }
  }, [onTranscribe, onText, onError])

  const toggle = useCallback(() => {
    if (state === 'recording') stop()
    else if (state === 'idle') void start()
  }, [state, stop, start])

  return { state, toggle }
}
