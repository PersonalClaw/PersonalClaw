import { useEffect, useRef, useState } from 'react'
import { unavailableWhen } from '../../../ui/unavailable'
import { motion } from 'framer-motion'
import { Loader2, ArrowRight, ArrowUp } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { ListSkeleton } from '../../../ui/ListScaffold'
import { Markdown } from '../../../ui/Markdown'
import { parseOptions, parseSwitchToAgent } from '../parseAssistant'
import { TurnHead, type TurnEnding } from '../transcript/TurnHead'
import { notify } from '../../../app/appSdk'
import { spring } from '../../../design/motion'
import { api, type ChatHistoryMsg, type RunState } from '../../../lib/api'
import { livePhaseOf, workPhaseOf, type WorkPhase } from '../../../lib/workPhase'
import { useChatSocket } from '../../../lib/useChatSocket'

/** Body of the history page's session PEEK panel: a LIVE mini-chat — the latest
 *  turns plus a compact composer for quick replies without opening the full chat
 *  UI. Streams over the shared WS; "Continue" (full page) is a small control in
 *  the composer's action row. While the chat's turn runs, wherever it was sent from, the head of
 *  its reply is the chat's own (`transcript/TurnHead`): the agent orb and the gateway's line for
 *  the turn's phase, from the chat's run state and its `work_phase` frames. */
export function SessionPeekBody({ sessionKey, onOpen }: { sessionKey: string; onOpen: () => void }) {
  const [detail, setDetail] = useState<{ title: string; messages: ChatHistoryMsg[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // The in-flight streamed reply (grows chunk by chunk; folds into `detail` on done).
  const [streamText, setStreamText] = useState<string | null>(null)
  // What the chat's turn is doing while it runs, and how it ended once its last frame says so.
  const [live, setLive] = useState<{ phase: WorkPhase; line: string } | null>(null)
  const [ending, setEnding] = useState<TurnEnding | null>(null)
  const runSeqRef = useRef(-1)
  const endRef = useRef<HTMLDivElement | null>(null)

  /** Take a run state: a snapshot's always (a restarted gateway stamps from the start again), a
   *  `work_phase` frame's only when it is newer than the one held. A snapshot's idle run state
   *  says nothing of a turn this panel watched, so only a frame sets the ending. */
  const takeRunState = (rs: RunState | null | undefined, from: 'frame' | 'snapshot') => {
    if (!rs || typeof rs.seq !== 'number' || (from === 'frame' && rs.seq <= runSeqRef.current)) return
    runSeqRef.current = rs.seq
    setLive(livePhaseOf(rs))
    if (from === 'frame') {
      const ended = rs.turn ? null : workPhaseOf(rs.phase)
      setEnding(ended === 'done' || ended === 'error' ? { phase: ended, line: String(rs.line ?? '') } : null)
    }
  }

  useEffect(() => {
    let alive = true
    setDetail(null); setFailed(false); setStreamText(null); setBusy(false)
    setLive(null); setEnding(null); runSeqRef.current = -1
    api.chatSessionDetail(sessionKey)
      .then((d) => {
        if (!alive) return
        setDetail({ title: d.title, messages: d.messages ?? [] })
        takeRunState(d.run_state, 'snapshot')
      })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [sessionKey]) // eslint-disable-line react-hooks/exhaustive-deps -- takeRunState writes only state and a ref

  // The socket came back: a reply that ended while it was down sent its chunks and its end to
  // nobody, and a restart ends every reply. The saved chat says how it ended, so it is read again
  // in place of the partial reply, and the composer is free unless a reply is still running.
  const resync = () => {
    api.chatSessionDetail(sessionKey)
      .then((d) => {
        setDetail({ title: d.title, messages: d.messages ?? [] })
        setStreamText(null)
        setBusy(!!d.run_state.turn)
        takeRunState(d.run_state, 'snapshot')
      })
      .catch(() => { /* the last read stays: a failed re-read is no news about the chat */ })
  }
  // Live stream: append chunks to the pending reply; land it on chat_done. The turn's phase
  // frames drive the head of the reply.
  useChatSocket((m) => {
    const d = m.data || {}
    if (d.session !== sessionKey) return
    if (m.type === 'work_phase') {
      takeRunState(d as unknown as RunState, 'frame')
    } else if (m.type === 'chat_chunk') {
      setStreamText((t) => (t ?? '') + String(d.content ?? ''))
    } else if (m.type === 'chat_done') {
      setBusy(false)
      setStreamText((t) => {
        if (t) setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'assistant', content: t } as ChatHistoryMsg] })
        return null
      })
    }
  }, resync)
  // Keep the tail in view as messages stream in.
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [detail?.messages.length, streamText])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput(''); setBusy(true); setEnding(null)
    setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'user', content: text } as ChatHistoryMsg] })
    try { await api.sendChat(text, sessionKey) }
    catch { setBusy(false); notify('Could not send the message', 'error') }
  }

  if (failed) return <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Couldn't load this chat.</p>
  if (!detail) return <ListSkeleton rows={5} />

  // Latest turns matter most in a peek; show the TAIL of the transcript, capped
  // so the panel stays snappy on long chats. A turn that ended without its reply says why in an
  // `error` row (a restart cut it off, the model failed), which is part of the turn: without it
  // the peek showed her question unanswered, with nothing saying so.
  const shown = detail.messages.filter((m) => m.role === 'user' || m.role === 'assistant' || m.role === 'error').slice(-12)
  return (
    <div className="flex h-full min-h-0 flex-col gap-m">
      <div className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto">
        {shown.length === 0 && !streamText ? (
          <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No messages yet — say hi below.</p>
        ) : shown.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="ml-6 self-end rounded-lg bg-surface-high px-m py-s">
              <p className="whitespace-pre-wrap break-words text-on-surface text-[0.8125rem] leading-relaxed">{String(m.content || '').slice(0, 800)}</p>
            </div>
          ) : m.role === 'error' ? (
            <p key={i} data-type="body-s" className="mr-2 whitespace-pre-wrap break-words text-error">{String(m.content || '').slice(0, 800)}</p>
          ) : (
            <div key={i} className="mr-2 min-w-0 text-[0.8125rem]">
              <Markdown widgets className="[&_p]:text-[0.8125rem]">{parseSwitchToAgent(parseOptions(String(m.content || '').slice(0, 2000)).body).body}</Markdown>
            </div>
          )
        ))}
        {/* The head of the running turn's reply, one element for the whole turn: above the
            streaming reply, so the reply appearing under it restarts neither its timing nor its
            drawing. A message sent from here is waiting to start until the gateway says more. */}
        <TurnHead running={busy || live !== null} phase={live?.phase ?? null} line={live?.line ?? ''} ending={ending}
          onShown={() => endRef.current?.scrollIntoView({ block: 'end' })} className="mr-2" />
        {/* the streaming reply, growing live */}
        {streamText && (
          <div className="mr-2 min-w-0 text-[0.8125rem]">
            <Markdown widgets streaming className="[&_p]:text-[0.8125rem]">{streamText}</Markdown>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {/* Mini composer — quick replies in-session; "Continue" opens the full UI.
          radius-lg tier: the 2xl token is a 48px sheet-scale round — outsized on a
          compact panel composer; lg stays proportionate AND tracks the user's
          global roundness slider like every token radius. */}
      <div className="shrink-0 rounded-lg bg-surface-container p-s shadow-[var(--shadow-composer)]">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Quick reply…"
          rows={2}
          aria-label="Quick reply"
          // 🔑 `outline-none` WITH NO REPLACEMENT ON EITHER SIDE. It lives in `@layer utilities`,
          // which beats the global `:focus-visible` rule in `@layer base` — so Tab into the session
          // peek panel and the caret landed here with NO visible indicator at all.
          // 🪤 THE RING GOES ON THE CONTROL, NOT THE CONTAINER, even though this is a transparent
          // input inside a box-drawing container — normally `focusRingPerElement`'s `focus-within`
          // case. The container also holds the button row directly below, so a container ring would
          // paint the whole composer whenever Open or Send takes focus: the exact ambiguity that rail
          // records for `CodePlanReview`'s shared box.
          // The pre-existing raw 0.8125rem size on this control is deliberately left alone —
          // converting it to a data-type role is a type-scale change, and moving that ratchet inside
          // a focus fix would muddle both. (Written without spelling the utility: the type-scale
          // scanner counts the pattern wherever it appears, comments included, so naming it here
          // would have raised the ceiling by one. It did, on the first draft of this comment.)
          className="w-full resize-none bg-transparent px-s py-xs text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary placeholder:text-on-surface-low"
        />
        <div className="flex items-center gap-s">
          <Button variant="ghost" size="xs" onClick={onOpen} title="Open the full chat UI"
            className="gap-1 text-on-surface-low hover:text-on-surface">
            Continue in full chat <ArrowRight size={11} className="shrink-0" />
          </Button>
          <motion.button type="button" onClick={send}
            {...unavailableWhen(!input.trim(), 'Type a message first', { busy })}
            whileTap={{ scale: 0.92 }} transition={spring.spatialFast}
            aria-label="Send"
            className="ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-pill bg-primary text-on-primary transition-colors hover:bg-primary-emphasis disabled:opacity-40 disabled:pointer-events-none aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={14} />}
          </motion.button>
        </div>
      </div>
    </div>
  )
}
