import { useEffect, useRef, useState } from 'react'
import { api, hasApiCode } from '../../lib/api'
import type { useComposerNotice } from '../../ui/composer/ComposerNotice'
import { useVoiceConfig } from './voiceConfig'
import { type ChatTurn, markCoordOf, turnText } from './chatTypes'
import { analyserLevel, registerAudioSource, type AudioSourceHandle } from '../../lib/audioPresence'

// ── The chat's read-aloud ─────────────────────────────────────────────────────────────────────
//
// Speak on a reply, "Speak replies aloud" for a reply to a message this tab sent, and the audio a
// reading streams back over the socket (`voice_chunk`, handed here by the chat's stream). Used by
// the transcript (its Speak buttons), the composer (hands-free waits while a reply is spoken, and a
// send owes its reply a reading) and the stream's sinks.

/** How many of each chat's replies answer a message THIS tab sent. Settings → Speech &
 *  Transcription → "Speak replies aloud" reads such a reply out here, if it is on when the reply
 *  finishes, and in no other tab, so a chat open in two tabs is read out once. Module scope, not a
 *  ref: a new chat's first send remounts the session view under the created id, and its reply
 *  finishes there. */
export const repliesToSpeak = new Map<string, number>()

export function useChatVoice({ sessionRef, turns, notice }: {
  sessionRef: { current: string | null }
  turns: ChatTurn[]
  /** The composer's notice line, where a reading that could not play says why. */
  notice: ReturnType<typeof useComposerNotice>
}) {
  // Voice playback uses the Web Audio API, not an <audio> element: Chrome
  // rejects piper's WAV stream via HTMLAudioElement.play() with NotSupportedError
  // even though the bytes are valid (decodeAudioData decodes them fine). The
  // AudioContext is created + resumed inside the Speak click gesture so the
  // chunks — which arrive 1-2s later over WS, outside the activation window —
  // still play (a context resumed during a gesture stays running).
  const audioCtxRef = useRef<AudioContext | null>(null)
  const audioPlayHeadRef = useRef(0)  // ctx-time cursor so chunks queue gaplessly
  const audioSourcesRef = useRef<AudioBufferSourceNode[]>([])  // live sources, for Stop
  // Which assistant turn is currently being spoken (drives the play/stop button).
  // Set when Speak is clicked, cleared when the last scheduled chunk finishes or
  // the user stops. Generation counter ignores stale chunks after a stop/restart.
  // The voice settings follow Settings while this chat is open (`chat/voiceConfig`). Read through
  // a ref where a socket frame decides: the frame handler is created once.
  const { voiceCfg, speakReplies, mapAudio } = useVoiceConfig()
  const speakRepliesRef = useRef(speakReplies)
  speakRepliesRef.current = speakReplies
  const [speakingTurn, setSpeakingTurn] = useState<number | null>(null)
  const speakGenRef = useRef(0)
  // The reading this tab asked for, by the name it gave it, and whether "Speak replies aloud"
  // started it. The audio streams to every tab with this chat open; only the one that asked plays
  // it, so a chat open in two tabs (or on a second device) is read out once.
  const readingRef = useRef<{ request: string; auto: boolean } | null>(null)

  // Every chunk plays through ONE gain node on its way out, so the Session Map's audio presence
  // has one place to listen while a reading plays (`lib/audioPresence`).
  const audioOutRef = useRef<GainNode | null>(null)
  function getAudioCtx(): AudioContext | null {
    if (!audioCtxRef.current) {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) return null
      audioCtxRef.current = new Ctor()
      try {
        audioOutRef.current = audioCtxRef.current.createGain()
        audioOutRef.current.connect(audioCtxRef.current.destination)
      } catch { audioOutRef.current = null }
    }
    return audioCtxRef.current
  }

  // The reading as an audio-presence source: measured only while a Session Map subscribes, live
  // only from the moment its audio really starts playing to the moment the last of it ends or is
  // stopped, and owned by the reply being read. Only a level leaves (`analyserLevel`).
  const presenceRef = useRef<AudioSourceHandle | null>(null)
  const liveTimerRef = useRef<number | null>(null)
  useEffect(() => {
    const handle = registerAudioSource('playback', () => {
      let analyser: AnalyserNode | null = null
      let buf: Float32Array<ArrayBuffer> | null = null
      return {
        read: () => {
          const ctx = audioCtxRef.current
          const out = audioOutRef.current
          if (!analyser && ctx && out) {
            analyser = ctx.createAnalyser()
            analyser.fftSize = 512
            out.connect(analyser)
            buf = new Float32Array(analyser.fftSize)
          }
          return analyser && buf ? analyserLevel(analyser, buf) : null
        },
        detach: () => {
          if (analyser) { try { audioOutRef.current?.disconnect(analyser) } catch { /* already gone */ } }
          analyser = null
          buf = null
        },
      }
    })
    presenceRef.current = handle
    return () => {
      if (liveTimerRef.current !== null) window.clearTimeout(liveTimerRef.current)
      handle.unregister()
      presenceRef.current = null
    }
  }, [])
  /** The reading is no longer audible: stopped, failed or ended. */
  function quiet() {
    if (liveTimerRef.current !== null) { window.clearTimeout(liveTimerRef.current); liveTimerRef.current = null }
    presenceRef.current?.setLive(false)
  }

  function stopSpeak() {
    speakGenRef.current++  // invalidate in-flight chunks from the stopped run
    readingRef.current = null  // and the frames still on their way
    for (const src of audioSourcesRef.current) { try { src.stop() } catch { /* already ended */ } }
    audioSourcesRef.current = []
    audioPlayHeadRef.current = 0
    setSpeakingTurn(null)
    quiet()
  }

  function speak(text: string, turnIndex: number, auto = false) {
    // Toggle: clicking Speak on the turn that's already playing stops it.
    if (speakingTurn === turnIndex) { stopSpeak(); return }
    stopSpeak()  // stop any other turn first — only one plays at a time
    const s = sessionRef.current
    // Prime audio within the click's user-activation so later chunks can play.
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
    speakGenRef.current++
    const request = `read-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    readingRef.current = { request, auto }
    setSpeakingTurn(turnIndex)
    // Whose audio this is, by the coordinate the Session Map's entries own — said now, but nothing
    // is live until the audio actually starts.
    const turn = turns[turnIndex]
    presenceRef.current?.setOwner(turn ? markCoordOf(turn, turnIndex) : null)
    // Surface TTS failures instead of silently doing nothing after the Speak button's brief
    // spinner. The two refusals are the server's own sentences, and each names its fix: no
    // text-to-speech model set up (`tts_unbound`), or text-to-speech switched off (`tts_disabled`).
    // A Speak that works takes such a line down: once the reply is playing, "switched off" is false.
    return api.voiceSynthesize(text, s ?? '', request).then(() => notice.clear('speak'), (e: Error) => {
      if (readingRef.current?.request === request) readingRef.current = null
      setSpeakingTurn((cur) => (cur === turnIndex ? null : cur))
      quiet()
      const refused = hasApiCode(e, 'tts_unbound') || hasApiCode(e, 'tts_disabled')
      notice.showError(refused ? e.message : `Couldn’t play audio: ${e.message}`, 'speak')
    })
  }

  // "Speak replies aloud": a reply to a message this tab sent is spoken once it has rendered,
  // through the same path as its Speak button, if the setting is on when the reply finishes.
  // `replyFinished` asks for it from a socket handler, where the finished text has not rendered
  // yet; the effect runs after it has.
  const [spokenReplyDue, setSpokenReplyDue] = useState(0)
  const lastSpokenReply = useRef<number | null>(null)
  function replyFinished(sid: string | null, { last }: { last: boolean }) {
    if (!sid) return
    const owed = repliesToSpeak.get(sid) ?? 0
    if (last) repliesToSpeak.delete(sid)
    else if (owed > 1) repliesToSpeak.set(sid, owed - 1)
    else repliesToSpeak.delete(sid)
    if (owed > 0 && speakRepliesRef.current) setSpokenReplyDue((n) => n + 1)
  }
  useEffect(() => {
    if (!spokenReplyDue) return
    const i = turns.map((t) => t.role).lastIndexOf('assistant')
    const text = i >= 0 ? turnText(turns[i]) : ''
    if (!text || lastSpokenReply.current === i) return
    lastSpokenReply.current = i
    void speak(text, i, true)
    // Keyed on the request alone: `turns` changing afterwards must not read a reply twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spokenReplyDue])
  // Counted whatever the setting says now, which may change before the reply finishes. Priming
  // inside the send gesture lets the reply's audio start when it arrives, seconds later.
  function oweSpokenReply(sid: string) {
    repliesToSpeak.set(sid, (repliesToSpeak.get(sid) ?? 0) + 1)
    if (!speakReplies) return
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
  }
  // Switched off while a reply is being read out on its own: it stops at once. A Speak you
  // pressed is yours, and keeps playing.
  useEffect(() => {
    if (!speakReplies && readingRef.current?.auto) stopSpeak()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speakReplies])
  // voice_chunk WAV stream → decode + schedule on the AudioContext timeline so
  // sentences play back-to-back without gaps or overlap. decodeAudioData is
  // async, so guard ordering with a per-chunk schedule against a running cursor.
  async function enqueueAudio(b64: string) {
    const ctx = getAudioCtx()
    if (!ctx) return
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {})
    const gen = speakGenRef.current
    let bytes: Uint8Array
    try {
      const bin = atob(b64)
      bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    } catch { return }
    let buf: AudioBuffer
    try { buf = await ctx.decodeAudioData(bytes.buffer.slice(0) as ArrayBuffer) }
    catch { return }
    if (gen !== speakGenRef.current) return  // a stop/restart happened mid-decode
    const now = ctx.currentTime
    const startAt = Math.max(now, audioPlayHeadRef.current)
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(audioOutRef.current ?? ctx.destination)
    src.start(startAt)
    audioSourcesRef.current.push(src)
    audioPlayHeadRef.current = startAt + buf.duration
    // Audible from its start time, not from the Speak press: the first chunk arrives seconds later.
    if (liveTimerRef.current === null && audioSourcesRef.current.length === 1) {
      liveTimerRef.current = window.setTimeout(() => {
        liveTimerRef.current = null
        if (gen === speakGenRef.current && audioSourcesRef.current.length) presenceRef.current?.setLive(true)
      }, Math.max(0, (startAt - ctx.currentTime) * 1000))
    }
    // When a source ends, drop it; if it was the last one, playback is done.
    src.onended = () => {
      audioSourcesRef.current = audioSourcesRef.current.filter((x) => x !== src)
      if (gen === speakGenRef.current && audioSourcesRef.current.length === 0) { setSpeakingTurn(null); quiet() }
    }
  }

  return { voiceCfg, mapAudio, speakingTurn, speak, replyFinished, oweSpokenReply, enqueueAudio, readingRef }
}
