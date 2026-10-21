import { Fragment, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Activity, AlertTriangle, ArrowRight, Bot, ChevronRight, ShieldAlert, Shuffle, Sparkles, Scissors, Wrench } from 'lucide-react'
import { fvs } from '../../../design/fontWeight'
import { spring } from '../../../design/motion'
import { api } from '../../../lib/api'
import { reportActionFailure } from '../../../app/reportingWrite'
import { Button } from '../../../ui/Button'
import { InlineError } from '../../../ui/InlineError'
import { Markdown } from '../../../ui/Markdown'
import { TextLink } from '../../../ui/TextLink'
import { NoModelSetupState, isNoModelSetupError } from '../NoModelSetupState'
import { ToolCard } from '../ToolCard'
import { SdlcProgressCard, sdlcRefFromTool } from '../SdlcProgressCard'
import { WorkflowProgressCard, workflowRefFromTool } from '../WorkflowProgressCard'
import { ManualAutomationCard, manualAutomationFromTool } from '../ManualAutomationCard'
import { ApprovalCard } from '../ApprovalCard'
import { QuestionCard, type QuestionReply } from '../QuestionCard'
import { ThinkingBlock } from '../ThinkingBlock'
import { ContextLedger } from '../ContextLedger'
import { parseOptions, parseSwitchToAgent } from '../parseAssistant'
import { workedFor } from '../turnRuns'
import { waitsPastItsTurn } from '../approvalSegment'
import { type Segment, type ToolSegment, type ApprovalSegment, type QuestionSegment, type ActivitySegment, type ThinkingSegment, type ErrorSegment, type MemoryCitation, type SkillUsed, type ReplyCut, type ServedModel, failedStepCount, unaskedStepCount, LEDGER_ACTIVITY_KINDS, skillsUsedLabel, skillsUsedTitle } from '../chatTypes'

// ── An assistant turn, as the transcript draws it ────────────────────────────────────────────────
//
// The turn's work folded under its answer (`AgentWork`), its live cards kept in sight, the notes
// on how it ended, and the chips and the ledger under it. Part of the chat's transcript region
// (`transcript/Transcript.tsx`).

// The approval-card scope picker's one vocabulary (resolved with the user): a per-
// approval SCOPE choice, not a mode toggle. `approved` = allow once; `trust` = allow
// all tools this session (sets session trust); `trust_agent` = always allow all tools
// for this agent (persists AgentProfile.approval_mode="auto") + this session; `rejected`
// = deny. (`trust_reads`/`yolo` remain valid backend actions the Permission axis uses,
// but the card no longer offers them — the card speaks only scope.)
export type ApproveAction = 'approved' | 'rejected' | 'trust' | 'trust_agent' | 'trust_reads' | 'yolo'

/** Answer an ask raised by work the chat started (`ApprovalSegment.queued`) where it is held, the
 *  approvals queue, by its registry id. Its card offers Allow and Deny alone; anything but an
 *  Allow is a Deny. */
export function answerQueued(id: string, action: ApproveAction) {
  // `true` once it landed: the card then sends nothing more for it (`ApprovalChoice.onClick`).
  return api.resolveApproval(id, action === 'approved' ? 'approve' : 'reject')
    .then(() => true)
    .catch(reportActionFailure('record your decision'))
}

/** Render an assistant turn's ordered segments. Legacy `[OPTIONS: …]` markers in
 *  historical messages get stripped from the prose (they are never rendered as
 *  buttons — follow-up chips are the single suggestion surface) and referenced
 *  file paths surface as clickable chips below the prose. */
export function AssistantSegments({ segments, liveCards, isLast, messageTs, streaming, onApprove, onAnswerQuestion, onSwitchToAgent, onOpenFile, onSetupModel, onRetry, chatSessionKey, citations, skillsUsed, cutOff, modelSubstitution, servedModel, workedMs }: {
  segments: Segment[]; isLast: boolean
  /** The tool results of the whole chat that show a run's live card, one per run. */
  liveCards: Set<Segment>
  messageTs?: string
  streaming?: boolean
  onApprove: (id: string, action: ApproveAction) => void | Promise<unknown>
  onAnswerQuestion: (id: string, reply: QuestionReply) => Promise<unknown>
  onSwitchToAgent: (continuation: string) => void
  onOpenFile: (path: string) => void
  /** The no-model empty-state's CTA — routes to Settings → Models through the hash router. */
  onSetupModel: () => void
  /** Send the turn's message again, for a turn that ended on its error without an answer — a
   *  failure, or a restart that cut it off. Given for the last settled turn only. */
  onRetry?: () => void
  chatSessionKey?: string
  citations?: MemoryCitation[]
  skillsUsed?: SkillUsed[]
  /** How the reply did not finish (`meta.finish_reason`): its model's output cap, or an answer
   *  cut off before the model finished, whose kept part is marked where it stops. */
  cutOff?: ReplyCut
  /** "Ran on X instead of Y: …" — another model answered than the one chosen for this chat. */
  modelSubstitution?: string
  /** The model that answered this turn and how it was chosen, which the turn's chip names. */
  servedModel?: ServedModel
  /** How long the turn worked, in milliseconds (`turnRuns.workedMs`), which its folded work says;
   *  absent when nothing timed it. */
  workedMs?: number
}) {
  const fullText = segments.filter((s) => s.kind === 'text').map((s) => (s as { text: string }).text).join('\n')
  // A restricted-mode turn may OFFER a one-click escalation to Agent.
  const { switchTo } = parseSwitchToAgent(fullText)

  // Transparency signals (what FED the turn / what was LEARNED / telemetry) are
  // pulled OUT of the inline flow and consolidated into one collapsible ledger at
  // the turn footer — holistic, non-intrusive, on demand (not three scattered lines).
  const ledger: { fed?: string; fedNoMemory?: boolean; learned?: string; learnedOrigin?: string; learnedRef?: string; stats?: string } = {}
  for (const s of segments) {
    if (s.kind !== 'activity') continue
    const ak = (s as ActivitySegment).activityKind
    if (ak === 'context' || ak === 'context_without_memory') {
      ledger.fed = (s as ActivitySegment).text
      ledger.fedNoMemory = ak === 'context_without_memory'
    }
    // The learned row carries its emitter's `origin` (and a preference's key) too — the
    // ledger is where the chip lives, so the discriminator has to travel with the text or the
    // tap has nothing to route on. Read off the SAME segment, so the two can never describe
    // different events.
    else if (ak === 'learned') {
      ledger.learned = (s as ActivitySegment).text
      ledger.learnedOrigin = (s as ActivitySegment).origin
      ledger.learnedRef = (s as ActivitySegment).ref
    }
    else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text
  }
  const hasLedger = Boolean(ledger.fed || ledger.learned || ledger.stats || servedModel)
  // The last segment the turn shows in its body: the ledger's rows are pulled out into its footer.
  const inLedger = (s: Segment) => s.kind === 'activity' && LEDGER_ACTIVITY_KINDS.includes((s as ActivitySegment).activityKind || '')
  const lastShown = [...segments].reverse().find((s) => !inLedger(s))
  // An answer cut off before the model finished is marked where its kept text stops, above the
  // error the turn ends on: that text is the turn's last.
  const cutHere = cutOff === 'incomplete' && !streaming ? [...segments].reverse().find((s) => s.kind === 'text') : undefined

  // Render one segment as its own card/line. Tool/approval/error cards carry their
  // OWN leading icon + status glyph, so there is no separate timeline dot+rail (the
  // old dot duplicated each card's ✓ and never centered on the connector). An SDLC
  // tool result becomes a live progress widget; text renders as prose.
  // `answer`: the segment is part of the final answer. Only the answer's blocks open beside the
  // reading: the work folds away once the turn is done, which unmounts its blocks, so a block opened
  // from there would be gone from its pane on a fold and missing from a reload.
  const renderItem = (seg: Segment, i: number, answer = false): React.ReactNode => {
    if (seg.kind === 'tool') {
      const t = seg as ToolSegment
      // A Code project / Goal Loop created or started from chat renders as a live
      // progress widget (status + stages/sub-goals + activity + cockpit link)
      // instead of a bare tool log line — once its result has landed with an id.
      const sdlc = t.done ? sdlcRefFromTool(t.tool, t.output) : null
      if (sdlc) return <SdlcProgressCard key={seg.id || i} refObj={sdlc} />
      // A workflow run started or inspected from chat renders as a live progress card for
      // the same reason: a run is a living thing, not the frozen JSON the tool returned. Once,
      // where the chat first names it: every status read the agent made after is its tool card.
      const wf = liveCards.has(seg) ? workflowRefFromTool(t.tool, t.output) : null
      if (wf) return <WorkflowProgressCard key={seg.id || i} refObj={wf} />
      // An automation made to run when she runs it ("a button that runs …") is shown with its
      // Run now, the button she asked for.
      const manual = t.done && t.ok !== false ? manualAutomationFromTool(t.tool, t.output) : null
      if (manual) return <ManualAutomationCard key={seg.id || i} refObj={manual} />
      return <ToolCard key={seg.id || i} seg={t} />
    }
    if (seg.kind === 'activity') return <ActivityLine key={i} seg={seg as ActivitySegment} />
    if (seg.kind === 'thinking') return <ThinkingBlock key={i} text={(seg as ThinkingSegment).text} defaultOpen={streaming} />
    if (seg.kind === 'error') {
      const { text, settings } = seg as ErrorSegment
      // A fresh instance with no model resolves the turn to a WHAT/WHY/FIX
      // envelope that reads as a stack dump. Reframe THAT case as a calm setup
      // nudge; every other turn error keeps the plain danger strip.
      // The notice a turn ENDED on — nothing after it but the footer ledger — is where she reads
      // that her question went unanswered, so the way to send it again is right there, not only
      // in the hover row below.
      const endsTheTurn = seg === lastShown
      return isNoModelSetupError(text)
        ? <NoModelSetupState key={i} detail={text} onSetup={onSetupModel} />
        : (
          <InlineError key={i} icon multiline className="my-1" onRetry={endsTheTurn ? onRetry : undefined}>
            {text}
            {settings && <>{' '}<TextLink href={`#/settings/${settings}`} icon={ArrowRight} iconPosition="trailing" ink="emphasis">Open Settings</TextLink></>}
          </InlineError>
        )
    }
    if (seg.kind === 'question') {
      const q = seg as QuestionSegment
      return <QuestionCard key={q.id} seg={q} onAnswer={onAnswerQuestion} />
    }
    if (seg.kind === 'approval') {
      const ap = seg as ApprovalSegment
      return ap.queued
        ? <ApprovalCard key={ap.id || i} seg={ap} answers="once" onAct={answerQueued} />
        : <ApprovalCard key={ap.id || i} seg={ap} onAct={onApprove} />
    }
    if (seg.kind === 'text') {
      // hide the raw [OPTIONS: …] and [SWITCH_TO_AGENT: …] markers from the prose
      const body = parseSwitchToAgent(parseOptions(seg.text).body).body
      if (!body) return null
      // `beside`: the answer's code, diffs and tables open beside the reading, where the chat gives
      // them a place.
      const prose = <Markdown key={i} widgets beside={answer} onFileClick={onOpenFile} chatSessionKey={chatSessionKey} messageTs={messageTs} streaming={streaming} citations={citations}>{body}</Markdown>
      return seg === cutHere ? <Fragment key={i}>{prose}<ReplyCutNote cut="incomplete" /></Fragment> : prose
    }
    return null
  }
  const isProcess = (s: Segment) =>
    s.kind === 'tool' || s.kind === 'error' || s.kind === 'approval' || s.kind === 'question' ||
    (s.kind === 'activity' && !inLedger(s))

  // Split the turn into the agent's WORK (tool calls, narration, approvals — up to
  // and including the last process step) and its FINAL ANSWER (trailing text after
  // the last step). Once the turn is complete the work folds into a compact
  // "Worked for 12s · N steps" disclosure so the user reads the answer first and
  // opens the intermediate steps only on demand. While streaming, work stays open.
  const processIdxs = segments.flatMap((s, i) => (isProcess(s) ? [i] : []))
  const lastProcessIdx = processIdxs.length ? processIdxs[processIdxs.length - 1] : -1
  const stepCount = processIdxs.length
  const workSegs = lastProcessIdx >= 0 ? segments.slice(0, lastProcessIdx + 1) : []
  const failedCount = failedStepCount(workSegs)
  const unaskedCount = unaskedStepCount(workSegs)
  const finalSegs = (lastProcessIdx >= 0 ? segments.slice(lastProcessIdx + 1) : segments).filter((s) => s.kind === 'text')

  // An SDLC create/start/status segment becomes a LIVE progress card that must stay
  // visible — never buried inside the collapsed "Worked for 12s · N steps" disclosure
  // (the card is a living, auto-refreshing widget, not a log line). Pull those out of
  // the work fold + render them at the top level between the work + the final answer.
  const isSdlc = (s: Segment) => s.kind === 'tool'
    && !!(s as ToolSegment).done && !!sdlcRefFromTool((s as ToolSegment).tool, (s as ToolSegment).output)
  // A workflow card is live too, so it gets the same exemption: burying an auto-refreshing
  // widget inside a collapsed disclosure hides the one thing the user came back to check. So is
  // an ask still waiting for work the chat started (`waitsPastItsTurn`).
  const isWorkflow = (s: Segment) => liveCards.has(s)
  // A question still waiting on her is never folded away either: the agent is halted on it.
  const waitsForHer = (s: Segment) => s.kind === 'question' && (s as QuestionSegment).answerable && !(s as QuestionSegment).outcome
  const isLiveCard = (s: Segment) => isSdlc(s) || isWorkflow(s) || waitsPastItsTurn(s) || waitsForHer(s)
  const sdlcNodes = segments.filter(isLiveCard).map((s, i) => renderItem(s, i)).filter(Boolean)
  // The ledger's rows are said in the footer only. What fed the turn arrives as the turn starts,
  // inside the work's span, and was drawn there too, live: a line a reload never showed.
  const workNodes = workSegs.filter((s) => !isLiveCard(s) && !inLedger(s)).map((s, i) => renderItem(s, i)).filter(Boolean)
  const finalNodes = finalSegs.map((s, i) => renderItem(s, i, true)).filter(Boolean)
  const hasFinal = finalNodes.length > 0
  // Collapse the work only when the turn is done AND produced a final answer to
  // focus on; otherwise show it inline (still streaming, or it ended on a step).
  const collapseWork = !streaming && hasFinal && stepCount > 0

  return (
    <>
      {workNodes.length > 0 && (
        collapseWork
          ? <AgentWork stepCount={stepCount} took={workedFor(workedMs)} failedCount={failedCount} unaskedCount={unaskedCount}>{workNodes}</AgentWork>
          : <div className="flex flex-col gap-1">{workNodes}</div>
      )}
      {/* Live SDLC progress cards stay at the top level, always visible — never
          folded into the collapsed work disclosure. */}
      {sdlcNodes.length > 0 && <div className="flex flex-col gap-1">{sdlcNodes}</div>}
      {finalNodes}
      {cutOff === 'length' && !streaming && <ReplyCutNote cut="length" />}
      {modelSubstitution && !streaming && <ModelSubstitutionNote text={modelSubstitution} />}

      {/* What CAPABILITY fed the turn — a peer of the ledger's "what context fed it",
          kept as its own always-visible chip rather than a collapsed ledger row: the count is
          the whole signal, and burying it behind a disclosure would make "which skills am I
          actually paying for" a thing you have to go looking for. */}
      {skillsUsed && skillsUsed.length > 0 && <SkillsUsedChip skills={skillsUsed} />}

      {hasLedger && <ContextLedger fed={ledger.fed} fedNoMemory={ledger.fedNoMemory} learned={ledger.learned} learnedOrigin={ledger.learnedOrigin} learnedRef={ledger.learnedRef} stats={ledger.stats} served={servedModel} />}

      {/* Agent-driven one-click escalation: the model proposed a switch out
          of a restricted mode; the user approves with a single click, which flips
          the session to Agent AND runs the continuation. Shown on the last turn
          once it's done (the consent gate that keeps Ask/Plan from self-escalating). */}
      {isLast && !streaming && switchTo !== null && (
        <div className="mt-3">
          <Button variant="primary" size="sm" onClick={() => onSwitchToAgent(switchTo)} className="gap-1.5">
            <Bot size={15} strokeWidth={2.2} />
            Switch to Agent &amp; run it
          </Button>
        </div>
      )}

    </>
  )
}

/** The agent's intermediate work for a completed turn, folded into one compact
 *  disclosure so the FINAL ANSWER leads and the steps that produced it open only
 *  on demand. Summarizes as "Worked for 12s · N steps" + how many of them failed, and names no
 *  tool: the cards inside say what each step was. The time is how long the turn worked
 *  (`turnRuns`); a turn nothing timed says "Worked through N steps", with no time made up.
 *  Collapsed by default; expanding reveals the original tool/approval/activity cards unchanged.
 *  (Replaces the old dot+rail timeline, whose connector added little once every step was followed
 *  by prose and whose dot ✓ duplicated each card's own status glyph.)
 *
 *  The failure count is on the line and never truncates: the reply below can say the work
 *  succeeded when a step did not, and the fold is where that shows. The count of steps the agent
 *  CLI ran without asking her sits beside it for the same reason: folded, the cards that say so
 *  are out of sight. */
function AgentWork({ stepCount, took, failedCount, unaskedCount, children }: { stepCount: number; took: string; failedCount: number; unaskedCount: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const steps = `${stepCount} ${stepCount === 1 ? 'step' : 'steps'}`
  return (
    <div className="mb-1.5">
      {/* Raw: a full-row disclosure, which no primitive draws. */}
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="group/work flex w-full items-center gap-1.5 rounded-md py-1 text-left text-on-surface-low/85 text-[0.75rem] transition-colors hover:text-on-surface-low">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={12} />
        </motion.span>
        <Wrench size={12} className="shrink-0 opacity-70" />
        <span className="shrink-0" style={fvs(500)}>
          {open ? 'Hide work' : took ? `Worked for ${took} · ${steps}` : `Worked through ${steps}`}
        </span>
        {!open && failedCount > 0 && (
          <span className="inline-flex shrink-0 items-center gap-xs text-danger" style={fvs(550)}>
            · <AlertTriangle size={12} aria-hidden /> {failedCount} failed
          </span>
        )}
        {!open && unaskedCount > 0 && (
          <span className="inline-flex shrink-0 items-center gap-xs text-warning" style={fvs(550)}>
            · <ShieldAlert size={12} aria-hidden /> {unaskedCount} ran without asking you
          </span>
        )}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div className="mt-1 ml-1.5 flex flex-col gap-1 border-l border-outline-variant/40 pl-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** A quiet inline activity line for LIVE progress (status / session / tool steps).
 *  Context-provenance, learning, and telemetry are NOT rendered here — they're
 *  consolidated into the per-turn {@link ContextLedger} footer instead. */
function ActivityLine({ seg }: { seg: ActivitySegment }) {
  return (
    <div className="my-1 flex items-center gap-1.5 text-on-surface-low text-[0.75rem]">
      <Activity size={12} className="shrink-0 opacity-70" /><span>{seg.text}</span>
    </div>
  )
}

/** A reply that did not finish, said at the point it stops: one that reached the model's OUTPUT
 *  cap, or the part of an answer that arrived before it was cut off (the turn's error under it
 *  then says why, with Retry).
 *
 *  Measured on the bundled model: a 320-token cap ended replies mid-sentence and nothing said
 *  so, which reads as the model trailing off — or as the product being broken — rather than as
 *  a limit. The line sits directly under the reply, where the unfinished sentence is. */
function ReplyCutNote({ cut }: { cut: ReplyCut }) {
  return (
    <div className="mt-1.5 mb-1 flex items-center gap-1.5 text-on-surface-low/80 text-[0.75rem]">
      <Scissors size={11} className="shrink-0 opacity-70" aria-hidden />
      <span>{cut === 'length' ? "Cut off: this reply reached the model's maximum length." : "Partial: the model's answer was cut off here, before it finished."}</span>
    </div>
  )
}

/** The reply came from another model than the one chosen for it, said under the reply.
 *
 *  An agent's pinned model — or the chat's own pick — could not run, and the chat model answered.
 *  The live line said so before the reply streamed; this is the half that survives a reload, so
 *  an old reply never reads as the chosen model's. The sentence is the server's, word for word. */
function ModelSubstitutionNote({ text }: { text: string }) {
  return (
    <div data-testid="model-substitution-note" className="mt-1.5 mb-1 flex items-start gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-warning)' }}>
      <Shuffle size={11} className="mt-0.5 shrink-0 opacity-80" aria-hidden />
      <span>{text}</span>
    </div>
  )
}

/** "used skill trip-research" — the per-turn skill-allocation chip, naming what joined the turn.
 *
 *  Hover carries the skills' full keys in the allocator's own order via `title` — the same
 *  affordance the {@link ContextLedger} trigger beside it uses, so the two footer chips
 *  behave alike instead of introducing a second hover mechanism for one line of text.
 *  `title` on a non-interactive element is a HOVER affordance only, not an accessible
 *  name, so the names are also written into the visible label below (`skillsUsedLabel`)
 *  rather than living solely in the tooltip.
 *
 *  A `reduced` skill is called out two ways rather than one, because a name alone would
 *  present a summary-only load as a full one: the chip appends "· M summarized" so the
 *  distinction survives without hovering, and the hover list marks each such skill by name.
 *  Rendered as a non-interactive element on purpose — there is no per-skill surface to land
 *  on, and a chip that looked like a button but did nothing would be the worse lie. */
function SkillsUsedChip({ skills }: { skills: SkillUsed[] }) {
  const reduced = skills.filter((s) => s.state === 'reduced').length
  return (
    <div className="mt-2 mb-1 flex items-center gap-1.5 text-on-surface-low/80 text-[0.75rem]"
      title={skillsUsedTitle(skills)}>
      <Sparkles size={11} className="shrink-0 opacity-70" />
      <span>
        {skillsUsedLabel(skills)}
        {reduced > 0 && <span className="opacity-80"> · {reduced} summarized</span>}
      </span>
    </div>
  )
}
