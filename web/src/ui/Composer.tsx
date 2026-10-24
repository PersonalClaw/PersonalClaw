import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowUp, Square, CornerDownLeft, ListPlus, Sparkles, Mic, Ear, Loader2, Paperclip, Check, MonitorUp } from 'lucide-react'
import { IconButton } from './IconButton'
import { duration, spring, physics, expr, sweep } from '../design/motion'
import { AgentOrb, ORB_LOOKS } from './AgentOrb'
import { voiceOrbPhase } from './composer/voiceOrb'
import { AgentModelPill, ApprovalPill, ReplyStylePill, PlusMenu } from './composer/controls'
import { MarkdownInput, type MarkdownInputHandle } from './composer/MarkdownInput'
import { resolveSendButton } from './composer/sendButtonState'
import { StopSweep } from './composer/StopSweep'
import { DROP_GROW_PX, RISE_GROW_PX, riseScale } from './composer/rise'
import { useMicRecorder } from './composer/useMicRecorder'
import type { ComposerProps } from './composer/types'
import { useIsMobile } from '../app/useIsMobile'
import { usePersonality } from '../app/personality'
import { api } from '../lib/api'
import { cx } from './cx'
import { Button } from './Button'
import { useQuery } from '../lib/data'
import { usePushToTalk, ensureMicGrant, useCaptureReport } from '../lib/pushToTalk'
import { MicCaptureChip } from './MicCaptureChip'
import { ContextDisclosure, nearLimit, nearLimitSaid } from './composer/ContextDisclosure'

// MIN_MAX_H is the resize FLOOR (user can shrink to ~1 line); DEFAULT_REST_H is
// the resting height a fresh composer opens at — ~3-4 lines tall (17px × 1.5
// line-height ≈ 25px/line) so there's room to type before it has to grow.
const MIN_MAX_H = 48, DEFAULT_REST_H = 92, MAX_MAX_H = 480, STORE_KEY = 'composer-resth2'

const DEFAULT_CONTROLS = { agent: true, model: true, approval: true, reasoning: false, attach: true, mic: true, optimize: true }

/** A focused composer in the default look (`web/DESIGN.md` §5): the plain 2px coral ring every
 *  input gets from the global focus ring (`design/tokens.css`), drawn on the composer's sheet
 *  because its editor draws none. Reaches 4px out, well inside the page gutter it sits in. */
const FOCUS_RING = { outlineWidth: '2px', outlineStyle: 'solid', outlineColor: 'var(--color-primary)', outlineOffset: '2px' } as const

/** The one configurable composer — used by Chat and the goal composer. `controls` picks
 *  which inline pills + "+"-menu items appear; `data`/`selection` feed real
 *  agent/model/mode options. Pill cluster: [+] agent-and-model · approval ·
 *  reply style … [mic] [send]. Textarea auto-grows to a user-resizable max. */
export function Composer({
  value, onChange, onSend, streaming, phase, processing, onStop, placeholder = 'Ask anything',
  controls = DEFAULT_CONTROLS, data, selection, onSelect, onAttach, onOpenPrompts, plusMenuExtra, onFocusChange,
  mentionProject, onMentionFile, onMentionKnowledge, onLargePaste,
  onOptimize, optimizing, history, onRecall, onTranscribe, onMicError, canQueue, canSteer, contextPct, contextWindow, sessionCost, contextPin, onCompact, contextParts, contextActions, servedModel, askBeforeModelSwitch, autoChain, minChars = 1,
  openModelSignal, openAgentSignal, openReasoningSignal, handsFree, onHandsFreeSubmit, screenShare,
  replyStyle, sendHeldReason, hostPills,
}: ComposerProps) {
  const [focused, setFocused] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  // The decorative light is the identity's (`web/DESIGN.md` §7). Claw Arcade keeps it, so its
  // composer wakes on focus and on a file dragged over it: the gradient ring, the coral rim and
  // the spring rise below. Everywhere else a focused composer shows `FOCUS_RING` and stays put.
  const light = !!usePersonality().personality.behavior.decorativeLight
  const awake = light && (focused || dragOver)
  // On mobile the return key inserts a newline (send is button-only) — a phone
  // keyboard's Enter must not fire a half-typed message. Reactive to rotation/resize.
  const isMobile = useIsMobile()
  // "Send on Enter" is read HERE rather than threaded in from each page, so every composer in the
  // app answers to the setting from one shared, persisted cache key. Threading it would mean
  // repeating the prop at each ComposerStage site (chat, the launcher, the loop composer) — the
  // same per-site omission that let this setting ship persisted, written by two controls, and read
  // by nothing. An unresolved or failed read leaves `data` undefined, which MarkdownInput treats as
  // "keep sending on Enter" — matching the config default rather than silently changing the keyboard.
  const { data: sendOnEnter } = useQuery('chat:send-on-enter', () => api.dashboardConfig().then((c) => c.send_on_enter), { persist: true })
  const canSend = value.trim().length >= minChars
  const setFocus = (f: boolean) => { setFocused(f); onFocusChange?.(f) }
  // Send→check bloom (brand shape-morph): on a send from the idle button, flash a
  // success check for a beat before the turn's `streaming` takes over the button.
  // Purely cosmetic + self-clearing; the real send fires immediately.
  const [justSent, setJustSent] = useState(false)
  const sentTimer = useRef<number | undefined>(undefined)
  const fireSend = () => {
    onSend()
    // A held send is the host's to refuse (`sendHeldReason`), so nothing blooms "sent".
    if (sendHeldReason) return
    window.clearTimeout(sentTimer.current)
    setJustSent(true)
    sentTimer.current = window.setTimeout(() => setJustSent(false), 620)
  }
  useEffect(() => () => window.clearTimeout(sentTimer.current), [])
  // Drag-and-drop file attach — mirrors the "+" button and a pasted image (all call onAttach).
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

  // The composer's own width, so its awake springs grow it by a bounded number of px
  // (`composer/rise.ts`) instead of a fraction that, at `full` width, spent its gutter.
  const rootRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = rootRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Hands-free: the loop is a composer-local mode, so the
  // toggle and the mode live together. Dictation lands in the draft (the surface
  // the user is already watching) and only a confirmation phrase sends it.
  const [listening, setListening] = useState(false)
  const canHandsFree = !!handsFree && !!onHandsFreeSubmit && !!onTranscribe
  useEffect(() => { if (!canHandsFree) setListening(false) }, [canHandsFree])

  // mic → STT: insert the transcript at the caret (or append) when it returns.
  const mic = useMicRecorder(onTranscribe, (text) => {
    inputRef.current?.insertAtCaret(text)
  }, onMicError, canHandsFree ? {
    enabled: listening,
    confirmationPhrases: handsFree!.confirmationPhrases,
    exitPhrases: handsFree!.exitPhrases,
    muted: (handsFree!.muteWhileSpeaking ?? true) && !!handsFree!.speaking,
    onBuffer: (text) => onChange(text),
    onSubmit: (text) => { onChange(text); onHandsFreeSubmit!(text) },
  } : undefined)

  // Push-to-talk: the desktop shell's global chord drives the
  // SAME recorder the mic button drives — one owner of the microphone, so a chord press
  // and a button click cannot end up holding two tracks. `capturing` is read from the
  // recorder's real state, which is what the shell's menu-bar indicator is drawn from.
  // Inert in a browser tab (no bridge, no config read, no binding).
  usePushToTalk({
    capturing: mic.state === 'recording',
    enabled: !!controls.mic && !!onTranscribe && !listening,
    // The grant is checked BEFORE opening the stream so a denied microphone produces an
    // actionable sentence rather than a bare getUserMedia rejection.
    onStart: () => { void ensureMicGrant().then((err) => (err ? onMicError?.(err) : mic.toggle())) },
    onStop: mic.toggle,
  })
  // Hands-free holds the same recorder open while it listens: said as its own capture source, so
  // the tray, every window and the gateway show it as the composer's dictation shows (#3833).
  useCaptureReport(listening && mic.state === 'recording', 'hands-free')

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
  // The footer's controls in two groups: what the next message is (`+`, and who answers it), then
  // how the turn runs (the reply style, and the host's task mode and Execution Boundary). Wide, they
  // share one wrapping row beside the send cluster. On a phone (#3808: at 390 the footer wrapped a
  // row per control, `+` alone on the first, and the composer grew from 276 px to 316) the first
  // group keeps the first row beside the send cluster, the pill truncating its label rather than
  // wrapping under `+`, and the second group takes the row below, its pills capped at 5.25rem
  // (4.25rem below 360 px) so three share it with the Context control. The send cluster drops to a line of its own only when the first
  // row cannot hold it beside `+` and the shortest pill (320 px). One tree in both layouts, switched
  // by classes alone, so crossing the breakpoint keeps every control mounted and Send keeps its focus.
  const hasLead = !!controls.attach || !!controls.agent || !!controls.model
  const hasContext = contextPct !== undefined || contextWindow !== undefined || !!sessionCost
  const hasRest = !!controls.approval || !!replyStyle || !!hostPills || hasContext
  // A host with no first group (the loop composer) keeps the one wrapping row at every width.
  const twoRows = isMobile && hasLead
  const lead = (
    <div data-composer-lead className={cx('flex min-w-0 flex-nowrap items-center gap-xs', twoRows && 'flex-1 basis-[6.5rem]')}>
      {controls.attach && <PlusMenu onAttach={() => fileRef.current?.click()} onOpenPrompts={onOpenPrompts} extra={plusMenuExtra} />}
      {/* One pill for who answers: the agent, the model and, for a model that declares levels,
          the reasoning effort. `/agent`, `/model` and `/effort` each open it; `/effort` opens its
          levels too. */}
      {(controls.agent || controls.model) && (
        <AgentModelPill data={data} agent={sel?.agent ?? ''} model={sel?.model ?? ''} effort={sel?.reasoning ?? ''}
          parts={{ agent: !!controls.agent, model: !!controls.model, effort: !!controls.reasoning }}
          served={servedModel} autoChain={autoChain} askBeforeSwitch={askBeforeModelSwitch}
          openSignal={(openAgentSignal ?? 0) + (openModelSignal ?? 0) + (openReasoningSignal ?? 0)} effortSignal={openReasoningSignal}
          onSelect={(p) => onSelect?.(p)} />
      )}
    </div>
  )
  const rest = (
    <>
      {controls.approval && <ApprovalPill value={sel?.approval ?? 'normal'} onSelect={(m) => onSelect?.({ approval: m })} />}
      {/* Reply style — feature-detected on the prop, not a `controls` flag:
          it needs a conversation to scope itself to, and the goal composer has none.
          Always rendered when present, including at Default, so a change in how replies
          read is attributable to a control the owner can see. */}
      {replyStyle && <ReplyStylePill {...replyStyle} />}
      {hostPills}
      {/* Context and cost, collapsed until opened: no number shows until the owner opens this or
          pins it to the status line. */}
      {hasContext && (
        <ContextDisclosure pct={contextPct} window={contextWindow} parts={contextParts} cost={sessionCost} pin={contextPin}
          actionFor={(a) => (a === 'compact' && (streaming || processing) ? undefined : contextActions?.[a])} />
      )}
    </>
  )

  // Right-hand action cluster: optimize · voice · send/stop/queue.
  // Two names, not three: "Stop recording" is a different ACTION, so it earns its own name.
  // Transcription is the same action working — `loading` announces that as `aria-busy`, so the
  // name stays the one the user can find the control by.
  const micLabel = mic.state === 'recording' ? 'Stop recording' : 'Voice input'
  // The label states what the control does, never why it is unavailable — a reason
  // folded into the name makes the action unfindable by the name it has when it works.
  const handsFreeLabel = !listening
    ? 'Hands-free voice'
    : mic.muted
      ? 'Hands-free voice — paused while speaking'
      : `Hands-free voice — say “${handsFree?.confirmationPhrases[0] ?? 'go ahead'}” to send`
  const sendKind = resolveSendButton({ processing: !!processing, streaming: !!streaming, canSend, canQueue: !!canQueue, canSteer: !!canSteer, justSent, held: !!sendHeldReason })
  // While a turn runs the button is the Stop control: a stop square with a thin arc turning round
  // it (`docs/design/agent-orb.md`). The lap is `design/motion`'s `sweep()`, which is null under
  // reduced motion, and then no arc is drawn: a loop has no shorter version. The arc reads the
  // turn's phase as the agent orb does (`ORB_LOOKS[phase].moves`): it turns while the turn works and
  // holds still while it waits on the owner (an approval or a question), so the card that needs her
  // holds her attention. A turn the gateway has not described yet is waiting to start, which moves.
  const stopSweep = sendKind === 'stop' && sweep() !== null
  const turning = !phase || ORB_LOOKS[phase].moves
  const actions = (
    <div className="flex items-center gap-xs">
      {/* The reason rides `disabledReason`, NOT the label. Folding it into the label mutates the
          accessible NAME, so the action stops being findable by the name it has when it works. */}
      {/* 🔑 THE SAME RULE, ONE STATE FURTHER. The comment above bans folding a REASON into the
          name; this button was folding its PROGRESS in ("Optimizing…"), for the same reason the
          reason used to live there — there was nowhere else to put it. `loading` is that place:
          `aria-busy` says "working" and the primitive cross-fades the glyph to a spinner, so the
          name stays "Optimize prompt (⌘↵)" in every state and the hand-rolled
          `icon={optimizing ? Loader2 : …}` + `[&_svg]:animate-spin` swap is gone. The click guard
          goes too — `loading` refuses it through the primitive's own `off` check. */}
      {controls.optimize && onOptimize && (
        <IconButton icon={Sparkles}
          label="Optimize prompt (⌘↵)"
          disabledReason="Type something first"
          active={optimizing} size={40}
          disabled={!optimizing && !canSend}
          loading={optimizing}
          onClick={onOptimize} />
      )}
      {/* The in-app half of the capture indicator. Rendered from the recorder's LIVE
          state, so a denied microphone (no stream) shows nothing — and a capture that
          ends any way at all takes the chip with it. */}
      {mic.state === 'recording' && <MicCaptureChip onStop={mic.toggle} />}
      {/* Its neighbour's twin: transcription is in flight, so it is `loading`. "Stop recording"
          stays a real name change (the action genuinely differs); "Transcribing…" was progress
          wearing the name, and `aria-busy` carries that now. */}
      {controls.mic && onTranscribe && !listening && (
        <IconButton icon={Mic} label={micLabel}
          active={mic.state !== 'idle'} size={40}
          loading={mic.state === 'transcribing'}
          onClick={mic.toggle} />
      )}
      {/* Screen context. Rendered only when the config master
          switch is on, so an install that never opted in has no capture affordance at
          all. `active` gives it the pressed state; the browser's own picker and capture
          indicator do the consent work, and the header chip pulses alongside them for
          as long as the stream is live. When no model can read a frame the control is
          still SHOWN, disabled, carrying the server's reason — an absent control would
          leave the user guessing why sharing isn't there. */}
      {screenShare?.available && (
        <IconButton icon={MonitorUp} label={screenShare.sharing ? 'Stop sharing screen' : 'Share screen'}
          active={screenShare.sharing} size={40}
          disabled={!!screenShare.disabledReason}
          disabledReason={screenShare.disabledReason}
          onClick={screenShare.disabledReason ? undefined : screenShare.onToggle} />
      )}
      {/* Hands-free: keeps listening and only sends when a confirmation phrase lands.
          It replaces the push-to-talk button while active so there is one mic owner —
          two live capture paths would fight over the same track. */}
      {controls.mic && canHandsFree && (
        <IconButton icon={Ear} label={handsFreeLabel}
          active={listening} size={40}
          disabled={!listening && mic.state !== 'idle'}
          disabledReason="Finish the current recording first"
          onClick={() => setListening((v) => !v)} />
      )}
      {/* The send/stop/steer/sent/processing choice is a pure state machine
          (resolveSendButton) so it's unit-testable without mounting the composer. Every kind
          renders in this one box, so a change of kind updates the same button in place: a Send
          button with keyboard focus is the Stop control once the turn starts, still focused.
          `data-stop-sweep` says which branch the Stop control took: `animated` while its arc turns,
          `still` while the turn waits on the owner, `instant` under reduced motion (no arc). */}
      <span className="relative inline-flex" data-stop-sweep={sendKind === 'stop' ? (!stopSweep ? 'instant' : turning ? 'animated' : 'still') : undefined}>
        {(() => {
          switch (sendKind) {
            // one-shot pre-send processing (e.g. the goal analyze pass) → a spinning
            // send button, no stop/queue affordance.
            case 'processing':
              return <IconButton icon={Loader2} label="Processing…" filled size={40} className="[&_svg]:animate-spin" onClick={undefined} />
            // mid-stream with a typed draft → STEER it into the running turn
            // (injected at the next model boundary), not queued to run after.
            case 'steer':
              return <IconButton icon={CornerDownLeft} label="Steer — send into the running turn" filled size={40} onClick={() => onSend()} />
            // mid-stream with a typed draft, on a turn that cannot take it in → it runs next.
            case 'queue':
              return <IconButton icon={ListPlus} label="Queue — sent when this turn ends" filled size={40} onClick={() => onSend()} />
            case 'stop':
              return <IconButton icon={Square} label="Stop" filled size={40} onClick={onStop} />
            // Idle send. On send it briefly morphs arrow→check with a bounce bloom
            // (the streaming spinner then takes over for the turn).
            case 'sent':
              return <IconButton icon={Check} label="Sent" filled size={40} onClick={undefined} iconKey="sent" bloom />
            // Disabled until the draft meets minChars. The name stays "Send message" in every
            // state — the reason rides `disabledReason` (the accessible description), so the
            // button is still findable by its own name while it is unavailable.
            case 'send-disabled':
              return <IconButton icon={ArrowUp} label="Send message" disabledReason="Type a bit more first" filled={false} disabled size={40} onClick={undefined} iconKey="send" />
            // Held while a file the message carries uploads; the reason names the file.
            case 'send-held':
              return <IconButton icon={ArrowUp} label="Send message" disabledReason={sendHeldReason} filled={false} disabled size={40} onClick={undefined} iconKey="send" />
            case 'send':
              return <IconButton icon={ArrowUp} label="Send message" filled disabled={false} size={40} onClick={fireSend} iconKey="send" />
          }
        })()}
        {stopSweep && <StopSweep turning={turning} />}
      </span>
    </div>
  )

  return (
    <div ref={rootRef} className="relative w-full" style={{ maxWidth: 'var(--content-width)' }}>
      {/* Under the decorative light, two "awake" states drive the composer's motion,
          both scaled through expr() (bold rises tall, refined barely): FOCUS = "rises
          to meet you"; DRAG-OVER = "opens its arms to receive" — a bigger lift + fuller
          ring than focus, since a file is about to land. Springs on a bounce tier so it
          settles with life. The scale's GROWTH is capped in px (`riseScale`) so a wide
          composer rises without crossing the page gutter it sits in. Without the light
          the composer never moves on focus. */}
      <motion.div
        layout
        animate={{
          y: !awake ? 0 : dragOver ? -expr(11, 0.4) : -expr(6, 0.4),
          scale: !awake ? 1 : dragOver ? riseScale(expr(0.028, 0.4), DROP_GROW_PX, width) : riseScale(expr(0.016, 0.4), RISE_GROW_PX, width),
        }}
        transition={physics.playful}
        className="relative"
      >
        {/* The gradient ring, the decorative light's alone. Pill↔pill-rounder shape
            morph: on mobile the resting composer rounds up toward a soft capsule; on
            focus (or on desktop) it settles to the standard sheet radius. Ring + surface
            morph in lockstep. The ring's glow intensity scales with expressiveness and
            surges to full on drag-over, where it also breathes (a slow opacity pulse) so
            the halo reads as a live, liquid surface eager to receive the drop rather than
            a static outline. */}
        {light && (
          <motion.div aria-hidden className="absolute -inset-px"
            initial={false}
            animate={{
              opacity: dragOver ? [0.7 + expr(0.3, 0.4), 0.85 + expr(0.15, 0.4), 0.7 + expr(0.3, 0.4)] : focused ? 0.55 + expr(0.4, 0.4) : 0,
              borderRadius: (isMobile && !focused && !dragOver) ? 'var(--radius-2xl)' : 'calc(var(--radius-xli) + 1px)',
            }}
            transition={dragOver ? { opacity: { duration: duration.beat, ease: 'easeInOut', repeat: Infinity }, borderRadius: spring.spatialDefault } : spring.spatialDefault}
            style={{ background: 'conic-gradient(from 0deg, var(--ring-stop-1), var(--ring-stop-2), var(--ring-stop-3), var(--ring-stop-4), var(--ring-stop-1))' }} />
        )}
        {/* Awake shadow is the composer's OWN token, not the cards' `--shadow-lift`: that
            one reaches ~60px and was cut into a hard line by the page gutter the composer
            sits in. The wide glow is the halo's job (DotGlow), which fades before edges.
            Without the light a focused composer keeps its Rest shadow and wears `FOCUS_RING`. */}
        <motion.div
          // `initial={false}`: the card mounts at rest. With no `initial`, motion animated it from the
          // element's own first style (no radius, no shadow) to its resting shape on every mount: a
          // spring of about half a second that no one asked for, and on a slow machine its tail was
          // still asking for frames when an idle Chat was measured (#3905, R6). Focus and drag-over
          // still spring, from the resting values.
          initial={false}
          animate={{ boxShadow: awake ? 'var(--shadow-composer-focus)' : 'var(--shadow-rest)', borderRadius: (isMobile && !focused && !dragOver) ? 'var(--radius-2xl)' : 'var(--radius-xli)' }}
          transition={spring.spatialDefault}
          style={!light && focused ? FOCUS_RING : undefined}
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
                transition={physics.playful}
                className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center gap-s rounded-[var(--radius-xli)] border-2 border-dashed border-primary/70 bg-surface-container/85 text-primary"
                data-type="label-m"
              >
                <motion.span
                  animate={{ y: [0, -expr(4, 0.3), 0] }}
                  transition={{ duration: duration.beat, ease: 'easeInOut', repeat: Infinity }}
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
              transition={physics.snappy}
            />
          </div>

          {/* Near the limit the composer says so, pinned or not: the one context reading that is
              never collapsed, because it is the one that needs a decision. */}
          {nearLimit(contextPct) && (
            <div className="flex items-center gap-s px-xs">
              <p role="status" data-type="caption" className="text-warn">{nearLimitSaid(contextPct!)}</p>
              {/* The fix beside the reading: the conversation compacted, as `/compact` does. Only
                  while no turn runs, when the gateway can take it. */}
              {onCompact && !streaming && !processing && (
                <Button variant="ghost-accent" size="xs" onClick={onCompact}>Compact now</Button>
              )}
            </div>
          )}

          {/* Voice mode (#4963): the 64 px orb, above the draft its dictation fills. `listening`
              while it hears the owner, the turn's own phase the rest of the time; under reduced
              motion the orb is one still frame. Its name is the phase's words. */}
          {listening && (
            <div data-voice-orb="" className="flex justify-center pt-s">
              <AgentOrb phase={voiceOrbPhase({ heard: mic.state === 'recording' && !mic.muted, phase })} size={64} />
            </div>
          )}

          {/* live-markdown editor (CodeMirror 6): the caret line shows raw
              markdown, other lines render. Replaces the old <textarea>. */}
          <MarkdownInput
            ref={inputRef}
            value={value} onChange={onChange} onSend={fireSend} canSend={canSend}
            placeholder={placeholder ?? 'Ask anything'}
            minHeight={restH} maxHeight={MAX_MAX_H}
            onFocusChange={setFocus}
            onOptimize={onOptimize} history={history} onRecall={onRecall}
            onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} mentionProject={mentionProject}
            slashCommands={!!controls.slash}
            onLargePaste={onLargePaste}
            onPasteFiles={canAttach ? onAttach : undefined}
            mobile={isMobile}
            sendOnEnter={sendOnEnter}
          />

          <div data-composer-footer={twoRows ? 'two-rows' : 'one-row'}
            className={cx('flex items-end justify-between', twoRows ? 'flex-wrap gap-x-s gap-y-xs' : 'gap-s')}>
            <div data-composer-row={twoRows ? undefined : 'lead'} className={twoRows ? 'contents' : 'flex min-w-0 flex-wrap items-center gap-xs'}>
              <div data-composer-row={twoRows ? 'lead' : undefined} className="contents">{lead}</div>
              {hasRest && (
                <div data-composer-row={twoRows ? 'rest' : undefined}
                  className={twoRows ? cx('order-last flex min-w-0 basis-full flex-wrap items-center gap-xs',
                    '[--composer-pill-max:5.25rem] [--composer-pill-max-wide:5.25rem]',
                    'max-[359px]:[--composer-pill-max:4.25rem] max-[359px]:[--composer-pill-max-wide:4.25rem]') : 'contents'}>
                  {rest}
                </div>
              )}
            </div>
            <div data-composer-actions className="ml-auto">{actions}</div>
          </div>

          <input ref={fileRef} type="file" multiple hidden onChange={(e) => { const f = e.target.files; if (f && onAttach) onAttach(Array.from(f)); e.target.value = '' }} />
        </motion.div>
      </motion.div>
    </div>
  )
}
