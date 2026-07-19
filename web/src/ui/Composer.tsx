import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowUp, Square, CornerDownLeft, Sparkles, Mic, Loader2, Paperclip, Check } from 'lucide-react'
import { IconButton } from './IconButton'
import { spring, bounce, expr } from '../design/motion'
import { AgentPill, ModelPill, ApprovalPill, ReasoningPill, effortsForAgent, PlusMenu } from './composer/controls'
import { MarkdownInput, type MarkdownInputHandle } from './composer/MarkdownInput'
import { resolveSendButton } from './composer/sendButtonState'
import { useMicRecorder } from './composer/useMicRecorder'
import type { ComposerProps } from './composer/types'
import { useIsMobile } from '../app/useIsMobile'

// MIN_MAX_H is the resize FLOOR (user can shrink to ~1 line); DEFAULT_REST_H is
// the resting height a fresh composer opens at — ~3-4 lines tall (17px × 1.5
// line-height ≈ 25px/line) so there's room to type before it has to grow.
const MIN_MAX_H = 48, DEFAULT_REST_H = 92, MAX_MAX_H = 480, STORE_KEY = 'composer-resth2'

const DEFAULT_CONTROLS = { agent: true, model: true, approval: true, reasoning: false, attach: true, mic: true, optimize: true }

/** The one configurable composer — used by Chat and the goal composer. `controls` picks
 *  which inline pills + "+"-menu items appear; `data`/`selection` feed real
 *  agent/model/mode options. Pill cluster: [+] agent · model · approval ·
 *  reasoning … [mic] [send]. Textarea auto-grows to a user-resizable max. */
export function Composer({
  value, onChange, onSend, streaming, processing, onStop, placeholder = 'Ask anything',
  controls = DEFAULT_CONTROLS, data, selection, onSelect, onAttach, onOpenPrompts, plusMenuExtra, onFocusChange,
  mentionProject, onMentionFile, onMentionKnowledge, onLargePaste,
  onOptimize, optimizing, history, onTranscribe, onMicError, canQueue, contextPct, minChars = 1,
  openModelSignal, openAgentSignal, openReasoningSignal,
}: ComposerProps) {
  const [focused, setFocused] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  // On mobile the return key inserts a newline (send is button-only) — a phone
  // keyboard's Enter must not fire a half-typed message. Reactive to rotation/resize.
  const isMobile = useIsMobile()
  const canSend = value.trim().length >= minChars
  const setFocus = (f: boolean) => { setFocused(f); onFocusChange?.(f) }
  // Send→check bloom (brand shape-morph): on a send from the idle button, flash a
  // success check for a beat before the turn's `streaming` takes over the button.
  // Purely cosmetic + self-clearing; the real send fires immediately.
  const [justSent, setJustSent] = useState(false)
  const sentTimer = useRef<number | undefined>(undefined)
  const fireSend = () => {
    onSend()
    window.clearTimeout(sentTimer.current)
    setJustSent(true)
    sentTimer.current = window.setTimeout(() => setJustSent(false), 620)
  }
  useEffect(() => () => window.clearTimeout(sentTimer.current), [])
  // Drag-and-drop file attach — mirrors the "+" button (both call onAttach).
  // depth counter so child dragenter/leave events don't flicker the overlay.
  const dragDepth = useRef(0)
  const canAttach = controls.attach && !!onAttach
  const onDragEnter = (e: React.DragEvent) => {
    if (!canAttach || !Array.from(e.dataTransfer?.types ?? []).includes('Files')) return
    e.preventDefault(); dragDepth.current += 1; setDragOver(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    if (!canAttach) return
    e.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragOver(false)
  }
  const onDrop = (e: React.DragEvent) => {
    if (!canAttach) return
    e.preventDefault(); dragDepth.current = 0; setDragOver(false)
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length) onAttach!(files)
  }

  const inputRef = useRef<MarkdownInputHandle>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // mic → STT: insert the transcript at the caret (or append) when it returns.
  const mic = useMicRecorder(onTranscribe, (text) => {
    inputRef.current?.insertAtCaret(text)
  }, onMicError)

  // `restH` is the user-set resting height — the editor is at least this tall
  // even when empty (so the handle resizes it at rest), and auto-grows with
  // content up to MAX_MAX_H. Dragging the handle sets restH.
  const [restH, setRestH] = useState<number>(() => {
    const v = Number(localStorage.getItem(STORE_KEY))
    return v >= MIN_MAX_H && v <= MAX_MAX_H ? v : DEFAULT_REST_H
  })
  useEffect(() => { localStorage.setItem(STORE_KEY, String(restH)) }, [restH])

  function onHandleDown(e: React.PointerEvent) {
    e.preventDefault()
    const startY = e.clientY
    const startH = restH
    const move = (ev: PointerEvent) => setRestH(Math.max(MIN_MAX_H, Math.min(MAX_MAX_H, startH + (startY - ev.clientY))))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }

  const sel = selection
  const pills = (
    <div className="flex items-center gap-1 min-w-0 flex-wrap">
      {controls.attach && <PlusMenu onAttach={() => fileRef.current?.click()} onOpenPrompts={onOpenPrompts} extra={plusMenuExtra} />}
      {controls.agent && <AgentPill data={data} value={sel?.agent ?? ''} openSignal={openAgentSignal} onSelect={(a) => onSelect?.({ agent: a })} />}
      {controls.model && <ModelPill data={data} agent={sel?.agent ?? ''} value={sel?.model ?? ''} contextPct={contextPct} openSignal={openModelSignal} onSelect={(m) => onSelect?.({ model: m })} />}
      {controls.approval && <ApprovalPill value={sel?.approval ?? 'normal'} onSelect={(m) => onSelect?.({ approval: m })} />}
      {controls.reasoning && <ReasoningPill value={sel?.reasoning ?? ''} efforts={effortsForAgent(data, sel?.agent ?? '')} openSignal={openReasoningSignal} onSelect={(e) => onSelect?.({ reasoning: e })} />}
    </div>
  )

  // Right-hand action cluster: optimize · voice · send/stop/queue.
  const micLabel = mic.state === 'recording' ? 'Stop recording' : mic.state === 'transcribing' ? 'Transcribing…' : 'Voice input'
  const actions = (
    <div className="flex items-center gap-1">
      {controls.optimize && onOptimize && (
        <IconButton icon={optimizing ? Loader2 : Sparkles}
          label={optimizing ? 'Optimizing…' : !canSend ? 'Optimize prompt — type something first' : 'Optimize prompt (⌘↵)'}
          active={optimizing} size={40}
          disabled={!optimizing && !canSend}
          className={optimizing ? '[&_svg]:animate-spin' : undefined}
          onClick={optimizing || !canSend ? undefined : onOptimize} />
      )}
      {controls.mic && onTranscribe && (
        <IconButton icon={mic.state === 'transcribing' ? Loader2 : Mic} label={micLabel}
          active={mic.state !== 'idle'} size={40}
          className={mic.state === 'transcribing' ? '[&_svg]:animate-spin' : undefined}
          onClick={mic.state === 'transcribing' ? undefined : mic.toggle} />
      )}
      {/* The send/stop/steer/sent/processing choice is a pure state machine
          (resolveSendButton) so it's unit-testable without mounting the composer. */}
      {(() => {
        switch (resolveSendButton({ processing: !!processing, streaming: !!streaming, canSend, canQueue: !!canQueue, justSent })) {
          // one-shot pre-send processing (e.g. the goal analyze pass) → a spinning
          // send button, no stop/queue affordance.
          case 'processing':
            return <IconButton icon={Loader2} label="Processing…" filled size={40} className="[&_svg]:animate-spin" onClick={undefined} />
          // mid-stream with a typed draft → STEER it into the running turn
          // (injected at the next model boundary), not queued to run after.
          case 'steer':
            return <IconButton icon={CornerDownLeft} label="Steer — send into the running turn" filled size={40} onClick={() => onSend()} />
          case 'stop':
            return <IconButton icon={Square} label="Stop" filled size={40} onClick={onStop} />
          // Idle send. On send it briefly morphs arrow→check with a bounce bloom
          // (the streaming spinner then takes over for the turn).
          case 'sent':
            return <IconButton icon={Check} label="Sent" filled size={40} onClick={undefined} iconKey="sent" bloom />
          // Disabled until the draft meets minChars (so AT + the disabled
          // affordance read correctly).
          case 'send-disabled':
            return <IconButton icon={ArrowUp} label="Send message — type a bit more first" filled={false} disabled size={40} onClick={undefined} iconKey="send" />
          case 'send':
            return <IconButton icon={ArrowUp} label="Send message" filled disabled={false} size={40} onClick={fireSend} iconKey="send" />
        }
      })()}
    </div>
  )

  return (
    <div className="relative w-full" style={{ maxWidth: 'var(--content-width)' }}>
      {/* Two "awake" states drive the composer's motion, both scaled through expr()
          (bold rises tall, refined barely): FOCUS = "rises to meet you"; DRAG-OVER =
          "opens its arms to receive" — a bigger lift + fuller ring than focus, since
          a file is about to land. Springs on a bounce tier so it settles with life. */}
      <motion.div
        layout
        animate={{
          y: dragOver ? -expr(11, 0.4) : focused ? -expr(6, 0.4) : 0,
          scale: dragOver ? 1 + expr(0.028, 0.4) : focused ? 1 + expr(0.016, 0.4) : 1,
        }}
        transition={bounce.lift}
        className="relative"
      >
        {/* Pill↔pill-rounder shape morph: on mobile the resting composer rounds up
            toward a soft capsule; on focus (or on desktop) it settles to the
            standard sheet radius. Ring + surface morph in lockstep. The ring's glow
            intensity scales with expressiveness and surges to full on drag-over,
            where it also breathes (a slow opacity pulse) so the halo reads as a live,
            liquid surface eager to receive the drop rather than a static outline. */}
        <motion.div aria-hidden className="absolute -inset-px"
          animate={{
            opacity: dragOver ? [0.7 + expr(0.3, 0.4), 0.85 + expr(0.15, 0.4), 0.7 + expr(0.3, 0.4)] : focused ? 0.55 + expr(0.4, 0.4) : 0,
            borderRadius: (isMobile && !focused && !dragOver) ? 'var(--radius-2xl)' : 'calc(var(--radius-xli) + 1px)',
          }}
          transition={dragOver ? { opacity: { duration: 1.4, ease: 'easeInOut', repeat: Infinity }, borderRadius: spring.spatialDefault } : spring.spatialDefault}
          style={{ background: 'conic-gradient(from 0deg, var(--ring-stop-1), var(--ring-stop-2), var(--ring-stop-3), var(--ring-stop-4), var(--ring-stop-1))' }} />
        <motion.div
          animate={{ boxShadow: (focused || dragOver) ? 'var(--shadow-lift)' : 'var(--shadow-rest)', borderRadius: (isMobile && !focused && !dragOver) ? 'var(--radius-2xl)' : 'var(--radius-xli)' }}
          transition={spring.spatialDefault}
          className="relative flex flex-col gap-s bg-surface-container px-m py-s"
          onDragEnter={onDragEnter} onDragOver={canAttach ? (e) => e.preventDefault() : undefined}
          onDragLeave={onDragLeave} onDrop={onDrop}
        >
          {/* drag-and-drop file attach overlay — shown while dragging files over
              the composer; dropping calls onAttach (same path as the + button). It
              blooms in on a bounce spring and the clip gently floats (scaled by
              expr) so "drop here" feels like an invitation, not a static scrim. */}
          <AnimatePresence>
            {dragOver && (
              <motion.div
                initial={{ opacity: 0, scale: 0.94 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.96 }}
                transition={bounce.playful}
                className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center gap-2 rounded-[var(--radius-xli)] border-2 border-dashed border-primary/70 bg-surface-container/85 text-primary"
                data-type="label-m"
              >
                <motion.span
                  animate={{ y: [0, -expr(4, 0.3), 0] }}
                  transition={{ duration: 1.6, ease: 'easeInOut', repeat: Infinity }}
                >
                  <Paperclip size={16} />
                </motion.span>
                Drop files to attach
              </motion.div>
            )}
          </AnimatePresence>
          {/* resize handle — always available so the composer can be made taller
              even at rest (empty), not only once it has wrapped to multiple lines.
              The grip springs wider + brighter on hover/drag (scaled by expr) so it
              telegraphs "grab me" with a little life instead of a bare color swap. */}
          <div onPointerDown={onHandleDown} title="Drag to resize"
            className="absolute left-1/2 -translate-x-1/2 -top-1.5 z-10 h-4 w-12 flex items-center justify-center cursor-ns-resize group">
            <motion.span
              className="h-1.5 rounded-pill bg-on-surface-low/30 group-hover:bg-on-surface-low/70 transition-colors"
              initial={false}
              animate={{ width: 40 }}
              whileHover={{ width: 40 + expr(14, 0.3) }}
              transition={bounce.subtle}
            />
          </div>

          {/* live-markdown editor (CodeMirror 6): the caret line shows raw
              markdown, other lines render. Replaces the old <textarea>. */}
          <MarkdownInput
            ref={inputRef}
            value={value} onChange={onChange} onSend={fireSend} canSend={canSend}
            placeholder={placeholder ?? 'Ask anything'}
            minHeight={restH} maxHeight={MAX_MAX_H}
            onFocusChange={setFocus}
            onOptimize={onOptimize} history={history}
            onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} mentionProject={mentionProject}
            slashCommands={!!controls.slash}
            onLargePaste={onLargePaste}
            mobile={isMobile}
          />

          <div className="flex items-end justify-between gap-s">
            {pills}
            {actions}
          </div>

          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { const f = e.target.files; if (f && onAttach) onAttach(Array.from(f)); e.target.value = '' }} />
        </motion.div>
      </motion.div>
    </div>
  )
}
