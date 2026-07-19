import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, Square, Pause, Play, Trash2 } from 'lucide-react'

type RecState = 'idle' | 'recording' | 'paused' | 'done'

function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** Pick a widely-supported recording MIME. opus/webm is best (small, good quality);
 *  Safari falls back to mp4/aac. */
function pickMime(): string {
  const C = typeof MediaRecorder !== 'undefined' ? MediaRecorder : undefined
  if (C?.isTypeSupported('audio/webm;codecs=opus')) return 'audio/webm;codecs=opus'
  if (C?.isTypeSupported('audio/webm')) return 'audio/webm'
  if (C?.isTypeSupported('audio/mp4')) return 'audio/mp4'
  return ''
}

/** In-browser microphone recorder (ported from OpenForge's audio create modal):
 *  record / pause / resume / stop, a live duration + level meter, and a playback
 *  preview with discard. On stop it hands the parent a File ready to upload. */
export function AudioRecorder({ onRecorded, onClear }: { onRecorded: (file: File) => void; onClear: () => void }) {
  const [state, setState] = useState<RecState>('idle')
  const [duration, setDuration] = useState(0)
  const [level, setLevel] = useState(0)  // 0..1 live input level for the meter
  const [url, setUrl] = useState<string | null>(null)
  const [err, setErr] = useState('')

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startRef = useRef(0)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)

  const clearTimer = () => { if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null } }
  const stopMeter = () => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
    audioCtxRef.current?.close().catch(() => {}); audioCtxRef.current = null
    setLevel(0)
  }
  const releaseMic = () => { streamRef.current?.getTracks().forEach((t) => t.stop()); streamRef.current = null }

  // Tear down everything on unmount.
  useEffect(() => () => {
    clearTimer(); stopMeter(); releaseMic()
    if (recorderRef.current && recorderRef.current.state !== 'inactive') recorderRef.current.stop()
    if (url) URL.revokeObjectURL(url)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const startMeter = (stream: MediaStream) => {
    try {
      const ctx = new AudioContext()
      audioCtxRef.current = ctx
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser(); analyser.fftSize = 256
      src.connect(analyser)
      const buf = new Uint8Array(analyser.frequencyBinCount)
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let peak = 0
        for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128))
        setLevel(Math.min(1, peak / 128))
        rafRef.current = requestAnimationFrame(tick)
      }
      tick()
    } catch { /* meter is best-effort */ }
  }

  const start = useCallback(async () => {
    setErr('')
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setErr('Recording is not supported in this browser.'); return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []
      const mime = pickMime()
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
      recorderRef.current = rec
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      rec.onstop = () => {
        const type = (mime || 'audio/webm').split(';')[0]
        const blob = new Blob(chunksRef.current, { type })
        if (url) URL.revokeObjectURL(url)
        setUrl(URL.createObjectURL(blob))
        setState('done')
        clearTimer(); stopMeter(); releaseMic()
        const ext = type === 'audio/mp4' ? 'm4a' : type === 'audio/ogg' ? 'ogg' : 'webm'
        const ts = new Date().toISOString().slice(0, 16).replace('T', '_').replace(/:/g, '-')
        onRecorded(new File([blob], `recording-${ts}.${ext}`, { type }))
      }
      rec.start(250)
      setState('recording'); setDuration(0); startRef.current = Date.now()
      timerRef.current = setInterval(() => setDuration((Date.now() - startRef.current) / 1000), 200)
      startMeter(stream)
    } catch (e: unknown) {
      const name = (e as { name?: string })?.name
      setErr(name === 'NotAllowedError'
        ? 'Microphone access denied — allow it in your browser and try again.'
        : 'Could not access the microphone. Check your device settings.')
    }
  }, [url, onRecorded])

  const pause = () => { if (recorderRef.current?.state === 'recording') { recorderRef.current.pause(); setState('paused'); clearTimer(); stopMeter() } }
  const resume = () => {
    if (recorderRef.current?.state === 'paused') {
      recorderRef.current.resume(); setState('recording')
      startRef.current = Date.now() - duration * 1000
      timerRef.current = setInterval(() => setDuration((Date.now() - startRef.current) / 1000), 200)
      if (streamRef.current) startMeter(streamRef.current)
    }
  }
  const stop = () => { if (recorderRef.current && recorderRef.current.state !== 'inactive') recorderRef.current.stop() }
  const discard = () => {
    if (url) URL.revokeObjectURL(url)
    setUrl(null); setState('idle'); setDuration(0); onClear()
  }

  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border border-outline-variant/40 bg-surface-container py-2xl px-l">
      {err && <p className="text-danger text-[0.8125rem] text-center">{err}</p>}

      {state === 'done' && url ? (
        <>
          <audio src={url} controls className="w-full max-w-md" />
          <div className="flex items-center gap-3 text-on-surface-low text-[0.8125rem]">
            <span>Recorded · {fmtDuration(duration)}</span>
            <button type="button" onClick={discard} className="inline-flex items-center gap-1.5 rounded-pill px-3 h-8 text-danger hover:bg-danger/10 transition-colors text-[0.8125rem]"><Trash2 size={14} /> Discard & re-record</button>
          </div>
        </>
      ) : (
        <>
          {/* Mic + live level ring */}
          <div className="relative grid size-20 place-items-center">
            {state === 'recording' && (
              <span className="absolute inset-0 rounded-full" style={{ background: 'color-mix(in srgb, var(--color-danger) 25%, transparent)', transform: `scale(${1 + level * 0.6})`, transition: 'transform 120ms ease-out' }} />
            )}
            <span className="relative grid size-16 place-items-center rounded-full"
              style={{ background: state === 'recording' ? 'var(--color-danger)' : 'var(--color-surface-high)', color: state === 'recording' ? 'var(--color-on-danger)' : 'var(--color-on-surface)' }}>
              <Mic size={26} />
            </span>
          </div>

          <div className="text-on-surface text-[1.25rem] tabular-nums" style={{ fontVariationSettings: '"wght" 500' }}>{fmtDuration(duration)}</div>

          <div className="flex items-center gap-2">
            {state === 'idle' && (
              <button type="button" onClick={start} className="inline-flex items-center gap-2 rounded-pill bg-primary text-on-primary px-5 h-10 text-[0.875rem] hover:bg-primary-emphasis transition-colors"><Mic size={16} /> Start recording</button>
            )}
            {state === 'recording' && (
              <>
                <button type="button" onClick={pause} aria-label="Pause" className="inline-flex items-center gap-1.5 rounded-pill bg-surface-high px-4 h-10 text-on-surface text-[0.875rem] hover:bg-surface-highest transition-colors"><Pause size={16} /> Pause</button>
                <button type="button" onClick={stop} aria-label="Stop" className="inline-flex items-center gap-1.5 rounded-pill bg-primary text-on-primary px-4 h-10 text-[0.875rem] hover:bg-primary-emphasis transition-colors"><Square size={15} /> Stop</button>
              </>
            )}
            {state === 'paused' && (
              <>
                <button type="button" onClick={resume} aria-label="Resume" className="inline-flex items-center gap-1.5 rounded-pill bg-surface-high px-4 h-10 text-on-surface text-[0.875rem] hover:bg-surface-highest transition-colors"><Play size={16} /> Resume</button>
                <button type="button" onClick={stop} aria-label="Stop" className="inline-flex items-center gap-1.5 rounded-pill bg-primary text-on-primary px-4 h-10 text-[0.875rem] hover:bg-primary-emphasis transition-colors"><Square size={15} /> Stop</button>
              </>
            )}
          </div>
          {state === 'idle' && <p className="text-on-surface-low text-[0.75rem]">Records in your browser · transcribed on ingest if a model is configured.</p>}
        </>
      )}
    </div>
  )
}
