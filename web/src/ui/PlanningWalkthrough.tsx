import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Loader2, Wrench, FileSearch, Check, MessageSquarePlus, CircleDot, Pencil, X, RefreshCw, AlertTriangle } from 'lucide-react'
import { TopBar } from './TopBar'
import { listItemEnter, stagger, spring, expr } from '../design/motion'
import { confirm } from './dialog'
import { useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { cleanSay, toolDetail } from '../lib/agentFeed'
import { planningTarget, type CommentTarget } from './content/commentTarget'
import type { PlanSession, PlanStep } from '../lib/api'

/** The shared live-breakdown + stepwise gated planning walkthrough — used by BOTH
 *  the Code feature and Goal Loop (the vision's "factored once, serves both").
 *
 *  Split view: LEFT streams the planner agent's loop events from the hidden planner
 *  session over the shared WS; RIGHT is the ordered step rail the planner designed,
 *  with the CURRENT step's artifact + an Approve / Comment gate. Approve advances;
 *  a comment sends the step back for a re-draft. When every step is approved the
 *  plan finalizes (status → review) and the host hands off to Plan Review / launch.
 *
 *  Everything feature-specific is injected via `cfg`: the planner WS key, the four
 *  plan API calls, the copy, and the per-kind artifact renderer. */
export interface WalkthroughConfig {
  /** Hidden planner session key, e.g. `code-plan-<id>` / `goal-plan-<id>`. */
  planSessionKey: (id: string) => string
  api: {
    getSession: (id: string) => Promise<PlanSession | null>
    start: (id: string) => Promise<unknown>
    approve: (id: string, stepId: string) => Promise<unknown>
    comment: (id: string, stepId: string, text: string) => Promise<unknown>
    /** Direct in-place edit of an artifact's markdown body (no planner round-trip). */
    edit: (id: string, stepId: string, markdown: string) => Promise<{ session: PlanSession }>
    /** True once the host's entity has flipped to `review` (planning complete). */
    isReady: (id: string) => Promise<boolean>
    /** Explicit retry of a FAILED design pass — clears the failure marker, then
     *  re-runs design. Distinct from `start` (which must NOT clear, so a passive
     *  auto-start/remount can't re-spawn a stuck planner). Optional: hosts without a
     *  retry endpoint fall back to `start`. */
    retry?: (id: string) => Promise<unknown>
  }
  copy: {
    subtitle: string          // TopBar subtitle
    activityLabel: string     // left-panel header
    activityEmpty: string     // left-panel empty state
    cancel: string            // bottom cancel link
  }
  /** Per-kind artifact renderer (Code vs Goal Loop differ here). Receives a
   *  `commentTarget` that routes a text-highlight comment to THIS step's planning
   *  agent (the same path as the prose comment box) — so selecting a passage in a
   *  planning artifact and commenting sends it to the planner. */
  renderArtifact: (kind: string, artifact: Record<string, unknown>, commentTarget?: CommentTarget) => React.ReactNode
}

type Line = { kind: 'tool'; label: string; detail?: string }

/** The latest sentence of a streaming agent message — the one-liner the ticker
 *  shows. We keep the raw accumulation to re-split as more arrives, then surface
 *  only the trailing (current) sentence so the rail reads as a live progress
 *  ticker rather than a growing wall of thinking. */
function lastSentence(raw: string): string {
  const clean = cleanSay(raw).replace(/\s+/g, ' ').trim()
  if (!clean) return ''
  // Split on sentence terminators; the last non-empty segment is what's "current".
  const parts = clean.split(/(?<=[.!?])\s+/)
  return (parts[parts.length - 1] || '').trim()
}

export function PlanningWalkthrough({ id, cfg, onReady, onBack }: {
  id: string
  cfg: WalkthroughConfig
  onReady: () => void
  onBack: () => void
}) {
  // The Investigation rail shows the concrete TOOL CALLS (what the planner is
  // actually doing) as a list…
  const [lines, setLines] = useState<Line[]>([])
  // …plus a single one-liner TICKER of the agent's current sentence, which
  // updates in place as the message streams — instead of dumping the whole
  // accumulating "thinking" prose into the rail.
  const [ticker, setTicker] = useState('')
  // Bumped on EVERY WS frame for this plan session (incl. unrendered chat_status), so
  // the stall-retry timer treats a silent-but-alive planner as progress (see below).
  const [heartbeat, setHeartbeat] = useState(0)
  // Raw streamed prose since the last tool call, re-split each chunk to surface
  // only the trailing sentence. A tool call resets it (a new thought begins).
  const sayBuf = useRef('')
  const [session, setSession] = useState<PlanSession | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  // In-flight guard: the action buttons use disabled={busy}, but setBusy is async —
  // two rapid clicks (e.g. double-click Retry, which RE-SPAWNS a planner pass) both
  // pass before the disabled prop re-renders → a double approve/comment/retry/edit.
  // A synchronous ref short-circuits the 2nd call immediately (the cockpit pattern).
  const inFlight = useRef(false)
  // Surfaces a failed approve/comment/edit/retry instead of silently swallowing it
  // (the action handlers had a finally-only try, so a 409/5xx/network error left the
  // user clicking with no feedback). Cleared when a new action starts.
  const [err, setErr] = useState<string | null>(null)
  // In-place edit of the current artifact's markdown body (null = not editing).
  const [editText, setEditText] = useState<string | null>(null)
  const planKey = cfg.planSessionKey(id)
  const feedRef = useRef<HTMLDivElement>(null)
  const started = useRef(false)

  // live planner activity over the shared WS (same shape the worker emits)
  useChatSocket((m: WsMessage) => {
    if (String(m.data?.session ?? '') !== planKey) return
    // ANY frame for this plan session proves the planner is alive — bump a heartbeat
    // so the stall-retry timer resets even during a long SILENT reasoning phase that
    // emits no tool_call / chat_chunk (just chat_status "Thinking…"). Without this, a
    // legitimate >3-min silent think falsely surfaced "Retry planning", and clicking it
    // KILLS the healthy planner. chat_status stays unrendered (feed noise) but counts.
    setHeartbeat((h) => h + 1)
    if (m.type === 'tool_call') {
      // Prefer the concrete input (the file read, the search query, the command) —
      // that's what makes the investigation legible — falling back to the purpose.
      const detail = toolDetail(String(m.data.input_preview ?? ''), String(m.data.purpose ?? ''))
      sayBuf.current = ''  // a new action → the prior thought is done; reset the ticker buffer
      setTicker('')
      setLines((l) => [...l, { kind: 'tool' as const, label: String(m.data.tool ?? 'tool'), detail }].slice(-200))
    } else if (m.type === 'chat_chunk') {
      const piece = String(m.data.content ?? '')
      if (piece) {
        // Accumulate raw (un-sanitized) so a tag split across chunks re-sanitizes
        // once whole; show only the trailing sentence as the live ticker.
        sayBuf.current = (sayBuf.current + piece).slice(-4000)
        setTicker(lastSentence(sayBuf.current))
      }
    }
    // chat_status (coarse "Thinking…" lines) is intentionally NOT surfaced — the
    // tool list + sentence ticker convey progress without the noise.
  })

  // poll the plan session; auto-start it the first time if none exists yet.
  // onReady is held in a ref (not a dep): the parent passes it as a fresh arrow each
  // render, so depending on it tore down + recreated the 3s interval on every parent
  // re-render — and tick() runs on each recreate, firing extra getSession calls. Keying
  // the effect on `id` alone keeps one stable interval; the ref calls the latest onReady.
  const onReadyRef = useRef(onReady); onReadyRef.current = onReady
  useEffect(() => {
    let alive = true
    let gone = false
    const tick = async () => {
      let s: PlanSession | null = null
      try {
        s = await cfg.api.getSession(id)
      } catch (e) {
        // A 404 means the loop was deleted (or stopped+removed) out from under the
        // walkthrough — exit to the list instead of polling a dead loop forever. A
        // transient error (5xx/network) is ignored; the next tick recovers.
        if ((e as { status?: number })?.status === 404) { gone = true; if (alive) onBack() }
        return
      }
      if (!alive || gone) return
      if (s) setSession(s)
      if (!s && !started.current) {
        started.current = true
        await cfg.api.start(id).catch(() => {})
        return
      }
      if (await cfg.api.isReady(id).catch(() => false)) { if (alive) onReadyRef.current() }
    }
    tick()
    const iv = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(iv) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }) }, [lines])

  // Reset per-step input when the walkthrough moves to a different step, so a
  // typed-but-unsent comment (or an open in-place edit) from the prior step can't
  // bleed onto the next one — e.g. approving step N with text still in the box
  // would otherwise show that text on step N+1's gate (and a stray "redraft" would
  // send N's feedback to N+1). Keyed on the current step id; clears on advance.
  const currentId = session?.steps?.find((s) => s.status !== 'approved')?.id ?? null
  useEffect(() => { setComment(''); setEditText(null) }, [currentId])

  // Stall detection: a planner pass that errors/times out leaves the step running
  // (or no session) with no new activity — track quiet time + offer Retry after a
  // threshold so it's never a forever-spinner. Any session change or new WS line
  // counts as progress and resets the clock.
  const [quietMs, setQuietMs] = useState(0)
  const lastProgress = useRef(Date.now())
  const sessionSig = JSON.stringify(session?.steps?.map((s) => [s.id, s.status, !!s.artifact?.markdown]) ?? [])
  useEffect(() => { lastProgress.current = Date.now(); setQuietMs(0) }, [sessionSig, lines.length, ticker, heartbeat])
  useEffect(() => {
    const iv = setInterval(() => setQuietMs(Date.now() - lastProgress.current), 5000)
    return () => clearInterval(iv)
  }, [])
  // Offer Retry only after a LONG quiet stretch. A planner legitimately investigating
  // a brownfield repo can be quiet for a while — especially a native agent that may
  // not stream tool events, or one deep in a single long operation — so a tight
  // threshold (was 90s) false-fired "Retry" mid-work, and clicking it kills a healthy
  // in-flight pass. 3min tolerates real investigation while still rescuing a dead pass
  // well before the planner's own 600s server-side timeout.
  const STALL_MS = 180_000
  const stalled = quietMs > STALL_MS

  const steps = session?.steps ?? []
  const current = steps.find((s) => s.status !== 'approved') ?? null

  // A typed-but-unsent comment is refinement the user wrote for THIS step. Approve
  // ignores it (only "Send comment & redraft" uses it), so the prominent primary
  // Approve would silently discard it → confirm first (themed dialog, not native
  // confirm()), so the feedback can't vanish unnoticed.
  async function requestApprove(step: PlanStep) {
    if (comment.trim()) {
      if (!(await confirm({
        title: 'Approve this step?',
        body: 'You have an unsent comment for this step. Approving as-is discards it and moves on. Send the comment instead if you want it folded into a redraft.',
        confirmLabel: 'Approve as-is',
      }))) return
    }
    void doApprove(step)
  }
  async function doApprove(step: PlanStep) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await cfg.api.approve(id, step.id); setComment('') }
    catch (e) { setErr(`Couldn't approve this step: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  async function sendComment(step: PlanStep) {
    const text = comment.trim(); if (!text) return
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await cfg.api.comment(id, step.id, text); setComment('') }
    catch (e) { setErr(`Couldn't send that comment: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  // Escape hatch when a pass stalls/fails (planner errored / model unavailable /
  // timed out). Uses the dedicated `retry` hook when available — it CLEARS a recorded
  // design failure so design re-runs; `start` deliberately doesn't (so a passive
  // remount can't re-spawn). Falls back to `start` for hosts without retry.
  async function retry() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await (cfg.api.retry ?? cfg.api.start)(id) }
    catch (e) { setErr(`Couldn't restart planning: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  async function saveEdit(step: PlanStep) {
    if (editText === null) return
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try {
      const r = await cfg.api.edit(id, step.id, editText)
      if (r?.session) setSession(r.session)
      setEditText(null)
    }
    catch (e) { setErr(`Couldn't save your edit: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
  }

  const approvedCount = steps.filter((s) => s.status === 'approved').length

  // The header should tell the truth about who's working: the walkthrough is GATED most
  // of the time, waiting on the user to approve/comment each step — during which the
  // planner is idle. A perpetual "Planning…" + spinner implied active background work
  // and nudged the user to wait instead of act. Spin only while the planner is actually
  // drafting (current step pending/running); when a step awaits review, say so plainly.
  const awaitingReview = current?.status === 'awaiting_review'
  const headerLabel = awaitingReview ? 'Awaiting your review' : stalled ? 'Planning paused' : 'Planning…'

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2">
          {awaitingReview
            ? <CircleDot size={16} className="text-primary" />
            : <Loader2 size={16} className={stalled ? 'text-on-surface-low' : 'animate-spin text-primary'} />}
          <span data-type="title-l" className="text-on-surface">{headerLabel}</span>
          <span className="text-on-surface-low text-[0.8125rem]">{cfg.copy.subtitle}</span>
        </div>}
        right={<button type="button" onClick={onBack}
          className="rounded-pill px-3 h-9 text-on-surface-low text-[0.8125rem] transition-colors hover:bg-surface-high hover:text-on-surface">{cfg.copy.cancel}</button>}
      />
      {/* Fixed shell: the row fills the remaining height and NEITHER the page nor
          the grid scrolls — only each pane scrolls internally. The PLAN is the
          hero (wide main column); the planner's live activity is a secondary side
          rail (narrow, full-height) since the events matter less than the plan. */}
      <div className="min-h-0 flex-1 px-l py-l">
        <div className="mx-auto flex h-full min-h-0 w-full flex-col gap-4 lg:flex-row" style={{ maxWidth: 'var(--content-width)' }}>
          {/* MAIN: the plan — compact steps rail + the current step's artifact gate */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
            <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
              <div className="mb-2 text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>
                Planning steps {steps.length ? `(${approvedCount}/${steps.length} approved)` : ''}
              </div>
              {steps.length === 0 ? (
                // A persisted design failure is a DEFINITIVE failed state (the planner
                // ran + produced nothing) — surface it immediately with the reason +
                // an explicit Retry, instead of the indefinite "preparing…" spinner
                // that used to mask a stuck planner silently re-spawning.
                session?.design_error ? (
                  <div className="flex flex-col items-start gap-1.5 text-[0.8125rem]">
                    <p className="inline-flex items-center gap-1.5" style={{ color: 'var(--color-warn)', fontVariationSettings: '"wght" 550' }}>
                      <AlertTriangle size={14} /> Planning didn't produce a plan
                    </p>
                    <p className="text-on-surface-low">{session.design_error}</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry planning
                    </button>
                  </div>
                ) : stalled ? (
                  <div className="flex flex-col items-start gap-1.5 text-[0.8125rem]">
                    <p className="text-on-surface-low">The planner has been quiet for a while — it may still be investigating, or it may have hit an error or an unavailable model. Retry only if it seems stuck.</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry planning
                    </button>
                  </div>
                ) : (
                  <p className="text-on-surface-low text-[0.8125rem]">The planner is preparing the steps…</p>
                )
              ) : (
                <motion.ol className="flex flex-col gap-1"
                  variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
                  {steps.map((s, i) => (
                    <motion.li key={s.id} variants={listItemEnter} className="flex items-center gap-2 text-[0.8125rem]">
                      {s.status === 'approved' ? <Check size={14} className="shrink-0 text-ok" />
                        : s.status === 'awaiting_review' ? <CircleDot size={14} className="shrink-0 text-primary" />
                        : s.status === 'running' ? <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
                        : <span className="ml-0.5 mr-0.5 h-2.5 w-2.5 shrink-0 rounded-full border border-outline-variant" />}
                      <span className={s.status === 'approved' ? 'text-on-surface-low line-through' : 'text-on-surface'}>{i + 1}. {s.title}</span>
                      <span className="text-on-surface-low text-[0.7rem]">{s.kind.replace(/_/g, ' ')}</span>
                    </motion.li>
                  ))}
                </motion.ol>
              )}
            </div>

            {/* Step-advance choreography: as each step is approved and the next
                becomes current, the artifact gate slides out left / the next slides
                in from the right — the SAME directional step-swap idiom the
                downstream Plan Review uses, so moving through the plan reads
                consistently. Keyed on the current step id; mode="wait" so the
                outgoing step finishes before the next enters (one clear signal). */}
            <AnimatePresence mode="wait" initial={false}>
              {current && (
              // step-advance slide distance scales with expressiveness — bold slides
              // the artifact gate further (a clearer "next step" gesture), refined
              // barely. Reduced-motion neutralizes the transform via root MotionConfig.
              <motion.div key={current.id}
                initial={{ opacity: 0, x: expr(16, 0.4) }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -expr(16, 0.4) }}
                transition={spring.spatialFast}
                className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
                <div className="mb-1.5 flex items-center gap-2">
                  <span data-type="title-s" className="text-on-surface">{current.title}</span>
                  <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var text-[0.7rem]">{current.kind.replace(/_/g, ' ')}</span>
                </div>
                {current.objective && <p className="mb-2 text-on-surface-low text-[0.8125rem]">{current.objective}</p>}
                {current.status === 'awaiting_review' ? (
                  <>
                    {editText !== null ? (
                      // Direct edit of the artifact's markdown body — the user
                      // finalizes it themselves (no planner round-trip). The backend
                      // edits ONLY the markdown body; structured fields (stories,
                      // decisions, entities — the executable projection source) are
                      // preserved. When the artifact has no markdown body (its content
                      // lives entirely in those structured fields), the box opens empty
                      // — say so, so a blank box doesn't read as "the content is gone".
                      <div className="flex flex-col gap-2">
                        {!(typeof current.artifact?.markdown === 'string' && current.artifact.markdown.trim()) && (
                          <p className="text-on-surface-low text-[0.72rem]">
                            This step's structured detail is preserved — this box only adds/edits a prose summary alongside it.
                          </p>
                        )}
                        <textarea autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} rows={14}
                          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!busy) void saveEdit(current) } }}
                          placeholder="Write the step's prose body in markdown…"
                          className="w-full resize-y rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 font-mono text-on-surface text-[0.78rem] outline-none focus:border-primary placeholder:text-on-surface-low" />
                        <div className="flex items-center gap-2">
                          <button type="button" disabled={busy} onClick={() => saveEdit(current)}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-on-primary text-[0.8125rem] disabled:opacity-50">
                            <Check size={14} /> Save edits
                          </button>
                          <button type="button" disabled={busy} onClick={() => setEditText(null)}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var text-[0.8125rem]">
                            <X size={14} /> Cancel
                          </button>
                        </div>
                        {err && (
                          <p role="alert" className="text-[0.8125rem]" style={{ color: 'var(--color-danger)' }}>{err}</p>
                        )}
                      </div>
                    ) : (
                      <div className="group/art relative">
                        {/* Edit the artifact body in place (finalize it yourself). Faintly
                            visible by default (opacity-60), full on hover/focus — a pure
                            opacity-0 hid this action from touch users entirely (no hover,
                            no easy focus), so editing a step's artifact was reachable only
                            with a mouse (the WorkspacePicker "Use" fix, same class). */}
                        <button type="button" title="Edit this artifact"
                          onClick={() => setEditText(typeof current.artifact?.markdown === 'string' ? current.artifact.markdown as string : '')}
                          className="absolute right-0 top-0 z-10 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[0.7rem] text-on-surface-low opacity-60 transition-opacity hover:bg-surface-high hover:text-on-surface hover:opacity-100 focus-visible:opacity-100 group-hover/art:opacity-100">
                          <Pencil size={12} /> Edit
                        </button>
                        {cfg.renderArtifact(current.kind, current.artifact ?? {}, planningTarget((message) => { void cfg.api.comment(id, current.id, message) }))}
                      </div>
                    )}
                    {/* The feedback you've already given on this step — shown so a
                        re-drafted artifact can be checked against what you asked for,
                        instead of your comment vanishing the moment it's sent. */}
                    {editText === null && !!current.comments?.length && (
                      <motion.div className="mt-3 flex flex-col gap-1.5"
                        variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
                        {current.comments.map((c, i) => (
                          <motion.div key={i} variants={listItemEnter} className="flex items-start gap-1.5 rounded-lg border border-outline-variant/40 bg-surface-container/40 px-2.5 py-1.5 text-[0.78rem]">
                            <MessageSquarePlus size={12} className="mt-0.5 shrink-0 text-on-surface-low" />
                            <span className="min-w-0 whitespace-pre-wrap text-on-surface-var">{c.text}</span>
                          </motion.div>
                        ))}
                      </motion.div>
                    )}
                    {editText === null && <div className="mt-3 flex flex-col gap-2">
                      <textarea value={comment} onChange={(e) => setComment(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!busy && comment.trim()) void sendComment(current) } }}
                        placeholder="Comment to refine this step (⌘↵ to send), or approve as-is…" rows={2}
                        className="w-full resize-none rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 text-on-surface text-[0.8125rem] outline-none focus:border-primary" />
                      <div className="flex items-center gap-2">
                        <button type="button" disabled={busy} onClick={() => requestApprove(current)}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-on-primary text-[0.8125rem] disabled:opacity-50">
                          <Check size={14} /> Approve & continue
                        </button>
                        <button type="button" disabled={busy || !comment.trim()} onClick={() => sendComment(current)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var text-[0.8125rem] disabled:opacity-40">
                          <MessageSquarePlus size={14} /> Send comment & redraft
                        </button>
                      </div>
                      {err && (
                        <p role="alert" className="text-[0.8125rem]" style={{ color: 'var(--color-danger)' }}>{err}</p>
                      )}
                    </div>}
                  </>
                ) : stalled ? (
                  <div className="flex flex-col items-start gap-1.5 text-[0.8125rem]">
                    <p className="text-on-surface-low">This step has been quiet for a while — the planner may still be working, or it may have errored / the model is unavailable. Retry only if it seems stuck.</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry this step
                    </button>
                    {err && (
                      <p role="alert" className="text-[0.8125rem]" style={{ color: 'var(--color-danger)' }}>{err}</p>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]">
                    <Loader2 size={14} className="animate-spin text-primary" />
                    {current.comments?.length ? 'Re-drafting with your feedback…' : 'Drafting this step…'}
                  </div>
                )}
              </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* SIDE RAIL: the planner's live activity — secondary, full-height, scrolls
              on its own. On narrow screens it drops below the plan, height-bounded. */}
          <aside className="flex min-h-0 shrink-0 flex-col rounded-xl border border-outline-variant/50 bg-surface-container/60 lg:w-[340px] max-lg:max-h-[34vh]">
            <div className="flex items-center gap-1.5 border-b border-outline-variant/40 px-3.5 py-2.5 text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>
              <FileSearch size={14} className="text-primary" /> {cfg.copy.activityLabel}
            </div>
            {/* Tool calls — the concrete steps — followed INLINE by the live message
                ticker (the agent's current sentence) where regular prose used to
                appear: a single shimmering line that swaps its text as the message
                streams. `overflow-x-hidden` + min-w-0/truncation keep the rail from
                ever scrolling sideways on a long tool detail or sentence. */}
            <div ref={feedRef} className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden px-3.5 py-3 text-[0.8125rem]">
              {lines.length === 0 && !ticker ? (
                <p className="text-on-surface-low">{cfg.copy.activityEmpty}</p>
              ) : (
                <>
                  {lines.map((l, i) => (
                    // Each concrete tool call rises+fades in as it lands — one signal
                    // per appended row (the streaming sentence ticker below is
                    // deliberately NOT re-mounted; it shimmers in place).
                    <motion.div key={i} variants={listItemEnter} initial="initial" animate="animate"
                      className="flex min-w-0 items-start gap-1.5">
                      <Wrench size={12} className="mt-0.5 shrink-0 text-on-surface-low" />
                      <span className="min-w-0 flex-1 break-words text-on-surface-low">
                        <b className="text-on-surface-var">{l.label}</b>{l.detail ? <> · <code className="break-all text-on-surface-low/90 text-[0.78rem]">{l.detail}</code></> : ''}
                      </span>
                    </motion.div>
                  ))}
                  {ticker && (
                    <div className="flex min-w-0 items-start gap-1.5">
                      <Loader2 size={12} className="mt-0.5 shrink-0 animate-spin text-primary" />
                      <span key={ticker} className="text-shimmer min-w-0 flex-1 truncate text-[0.8125rem]" title={ticker}>{ticker}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}

/** A labelled structured section within an artifact — shared by both features'
 *  artifact renderers. */
export function ArtifactSection({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 text-on-surface-var text-[0.75rem]" style={{ fontVariationSettings: '"wght" 600' }}>{icon} {label}</div>
      {children}
    </div>
  )
}

/** Coercion helpers shared by the per-kind artifact renderers. */
export const artifactList = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x) => x && typeof x === 'object') as Record<string, unknown>[] : []
export const artifactStrings = (v: unknown): string[] =>
  Array.isArray(v) ? (v as unknown[]).map((x) => typeof x === 'string' ? x : String((x as Record<string, unknown>)?.title ?? (x as Record<string, unknown>)?.text ?? '')).filter(Boolean) : []
