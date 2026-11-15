import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Ban, Check, CheckCircle2, Play, X } from 'lucide-react'
import { api, ApiError, hasApiCode, type OrganizeProposal } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { clockTime } from '../../lib/epoch'
import type { NeedsYouRow } from '../../lib/needsYou'
import {
  waitingAnswerId, waitingQuestion, waitingReply, type CardQuestion, type ReplyTarget,
} from '../../lib/waitingAnswer'
import { approvalOf, isStale, staleAt } from '../../lib/waitingList'
import { kindOf, learningCardOf, type KindWords } from '../../lib/needsYouKinds'
import { StatusPill } from '../../ui/StatusPill'
import { rowSubject } from '../../lib/rowSubject'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'
import { Button } from '../../ui/Button'
import { InlineConfirm, WrappingAnswer } from '../../ui/InlineConfirm'
import { ProjectChip } from '../../ui/ProjectChip'
import { EDGE } from '../../ui/SearchField'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { useHeldHeight } from '../../lib/useHeldHeight'
import { TrustFolderButton } from '../triggers/HeldBackNote'
import { TextLink } from '../../ui/TextLink'
import { InlineLoadError } from '../../ui/ListScaffold'
import { TextInput } from '../../ui/forms'
import { MarkdownLine } from '../../ui/MarkdownLine'
import { previewLine } from '../../lib/previewText'
import { cutMarkdown } from '../../lib/cutMarkdown'
import { widgetlessText } from '../../ui/widget/blocks'
import { Markdown } from '../../ui/Markdown'
import { openable } from './RunningLane'

// ── Needs you's cards ────────────────────────────────────────────────────────────────────────────
//
// One card per thing waiting on you (`GET /api/waiting`, a skill proposal, an Inbox note): what
// asked, what it asks, and its answer on the card, each answer through the item's own route
// (`useNeedActions`). Home draws them, and a project's page can draw the same ones for its own
// work, so there is one copy of every answer and of every approval card. Selecting a card opens its
// detail (`NeedDetail`), where a free-text ask takes its reply.
//
// Also here: the words each answer's outcome is said in, and what the group says of a source it
// could not read (`NeedsUnreadable`), the one sentence that stands in for "nothing is waiting" while
// the list is incomplete.

/** A waiting source's kind, in words, for "Could not read …". Exported for the Projects list's
 *  note, which says the same sources in the same words. */
export const SOURCE_WORDS: Record<string, string> = {
  approval: 'approvals',
  question: 'agents’ questions',
  loop_question: 'loops’ questions',
  gate: 'workflow runs',
  park: 'triggers',
  browse_grant: 'browser grants',
  plan_review: 'plans to review',
  bridge_confirmation: 'control bridge actions',
}

// ── Per-card outcome ────────────────────────────────────────────────────────────────────────

export type Outcome =
  | { state: 'busy' }
  | { state: 'done'; text: string }
  | { state: 'ended'; text: string }
  | { state: 'failed'; text: string }

/** The gateway's own sentence, plus what the user can do about it. The server text comes first
 *  and verbatim: it is the only part that says WHICH knob to turn. */
export function failureText(verb: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  // A server sentence ends in its own full stop; this one adds the next sentence's.
  return `Could not ${verb}: ${detail.replace(/[.\s]+$/, '')}. Nothing was recorded, so it still needs you; try again.`
}

/** What Learning being off means for a learning card: nothing here can decide it. */
export const LEARNING_OFF = "Learning is off, so this can't be decided here."

/** The refusals that keep a card waiting with the server's sentence and no "try again": a taken
 *  name doesn't free itself, and a budget doesn't raise itself. */
const KEPT_WITH_ITS_SENTENCE = new Set([
  'learning_proposal_not_installed', 'learning_proposal_not_applied', 'skill_proposal_not_installed', 'at_budget',
])

/** What a refused answer means for its card, by the gateway's code (on main every 404 read as
 *  "answered or ended somewhere else" and every other refusal as "try again"):
 *  - Learning off: its sentence, and the card's verbs go;
 *  - a 404 (decided or ended somewhere else) or an answered or ended question: ended;
 *  - work that moved since it asked: ended, sent to the panel;
 *  - an install that was refused, a budget, an autonomy grant refused: still waiting, the server's
 *    sentence;
 *  - anything else: still waiting, `failureText`. */
export function outcomeFor(err: unknown, verb: string): Outcome {
  if (hasApiCode(err, 'learning_disabled')) return { state: 'ended', text: LEARNING_OFF }
  const ended = endedText(err)
  if (ended) return { state: 'ended', text: ended }
  if (hasApiCode(err, 'question_answered') || hasApiCode(err, 'question_ended')) {
    return { state: 'ended', text: (err as Error).message }
  }
  if (hasApiCode(err, 'loop_merge_moved') || hasApiCode(err, 'loop_conflict_moved')) {
    return { state: 'ended', text: 'It changed since it asked. Review it in the panel.' }
  }
  if (err instanceof ApiError && (KEPT_WITH_ITS_SENTENCE.has(err.code) || (err.status === 400 && err.code.startsWith('autonomy'))) && err.message) {
    return { state: 'failed', text: err.message }
  }
  return { state: 'failed', text: failureText(verb, err) }
}

/** When an answer is refused because there is nothing left to decide, the sentence for it, or
 *  null for a real failure: the work that asked has ended (409, the gateway names which), or it
 *  was answered or ended somewhere else (404). Retrying either would change nothing. */
export function endedText(err: unknown): string | null {
  if (hasApiCode(err, 'approval_owner_ended') && err instanceof Error) return err.message
  if (err instanceof ApiError && err.status === 404) return ENDED_ELSEWHERE
  return null
}

/** What a card says once what it asked was decided or ended somewhere else: a proposal is decided,
 *  an ask answered, a run ended. */
export const ENDED_ELSEWHERE = 'It is no longer waiting: it was decided or ended somewhere else.'

/** What a gate's or a park's answer did, in words. */
export function answeredText(q: CardQuestion, value: string | boolean): string {
  if (q.event) return 'Woken. The run is moving again.'
  if (!q.approval) return `Answered “${String(value)}”. The run is moving again.`
  if (q.triggerId) {
    return value === true ? 'Approved. It is running again.' : 'Declined. It asks again the next time it stops.'
  }
  return value === true ? 'Approved. The run is moving again.' : 'Declined. The run ends here.'
}

/** An approval's title is a tool's name or the command itself: it stays as written wherever it is
 *  drawn or named, as its card draws it. Any other ask's title is an agent's line. */
const isApproval = (row: NeedsYouRow) => row.origin === 'waiting' && row.item.kind === 'approval'
/** An ask's title as a name or a tooltip: as written for an approval, its plain line for any other. */
export const needName = (row: NeedsYouRow) => (isApproval(row) ? row.title : previewLine(row.title))
/** An ask's title as a heading: text for an approval, rendered for any other. */
export const needHeading = (row: NeedsYouRow) => (isApproval(row) ? row.title : <MarkdownLine text={row.title} />)

/** What a deadline leaves, as a card's tag row says it: "4 min left" while it is under 15 minutes
 *  away, "Answer by 11:32" beyond, nothing with none or once it has passed (the expired line says
 *  so). In caption, no colour, moving with the card's `now`. */
export function timeLeft(item: { expires_at?: string | null }, now: number): string {
  const at = staleAt(item as never)
  if (at === null || at <= now) return ''
  const ms = at - now
  return ms < 15 * 60_000 ? `${Math.max(1, Math.ceil(ms / 60_000))} min left` : `Answer by ${clockTime(item.expires_at)}`
}

/** The name of the thing a proposal is about, when its headline leads with what it does: a
 *  template's or a skill's slug, a workflow, a project (its card's `name`). `""` for an ask, whose
 *  tag row names its source instead. */
export function thingName(row: NeedsYouRow): string {
  if (row.origin === 'proposal') return row.proposal.card?.name ?? ''
  const card = learningCardOf(row)
  return card && typeof card.name === 'string' ? card.name : ''
}

/** The words a source's line says that the tag doesn't: what names specific work. "Loop · Docs"
 *  under the tag Loop names "Docs"; an agent in its chat names itself whole; a source that is only
 *  a kind ("Browser", "Skills", "Your note") names nothing the tag hasn't. */
const GENERIC_SOURCES = new Set(['Browser', 'Skills', 'Your note', 'Folder trust', 'Control bridge', 'Loop', 'Workflow run', 'Chat'])
export function sourceSlot(row: NeedsYouRow): string {
  const by = (row.raisedBy ?? '').trim()
  if (!by || GENERIC_SOURCES.has(by)) return ''
  const cut = by.indexOf(' · ')
  return cut === -1 ? by : by.slice(cut + 3).trim()
}

/** A card's name for assistive tech, with no cut beyond the server's own: "<tag> <thing>:
 *  <headline>" when the tag row names the thing itself, else "<headline>, <source>" (`rowSubject`'s
 *  `', '` joiner, which no screen reader reads aloud as a symbol). */
export function needSubject(row: NeedsYouRow): string {
  const thing = thingName(row)
  const headline = previewLine(needName(row))
  if (thing) return thing === headline ? `${kindOf(row).tag} ${thing}` : `${kindOf(row).tag} ${thing}: ${headline}`
  return rowSubject([headline, sourceSlot(row)], Infinity, { joiner: ', ' })
}

/** The key a row is selected by, in `?open=`. */
export function rowKey(row: NeedsYouRow): string {
  return row.key
}

/** What Needs you says of the sources it could not read: their names, then, when nothing else is
 *  listed, "Nothing else is waiting on you." It never says nothing waits while a source is unread.
 *  On a project's page *project* names it: "Nothing else in Garden is waiting on you." Renders
 *  nothing while every source was read. */
export function NeedsUnreadable({ unreadable, nothingElse, project }: {
  /** The kinds the waiting list could not read (`GET /api/waiting`'s `unreadable`). */
  unreadable: readonly string[]
  /** Nothing else is listed: the group's second line says so. */
  nothingElse: boolean
  /** The project's name, on its page. */
  project?: string
}) {
  if (unreadable.length === 0) return null
  return (
    <>
      <p role="status" data-type="body-s" className="text-on-surface">
        {`Could not read ${unreadable.map((k) => SOURCE_WORDS[k] ?? k).join(', ')}: what they hold may be waiting on you and is not shown here.`}
      </p>
      {nothingElse ? (
        <p data-type="body-s" className="text-on-surface-low">
          {project ? `Nothing else in ${project} is waiting on you.` : 'Nothing else is waiting on you.'}
        </p>
      ) : null}
    </>
  )
}

export interface CardActions {
  approval: (key: string, id: string, action: 'approve' | 'reject') => Promise<unknown>
  question: (key: string, q: CardQuestion, value: string | boolean) => void
  grant: (key: string, id: string, action: 'approve' | 'reject') => void
  bridge: (key: string, token: string, run: boolean) => void
  /** A skill proposal's answer; *done* is what its outcome says (its card's verb, done). */
  proposal: (key: string, id: string, action: 'accept' | 'reject', done?: string) => void
  /** A learning proposal's answer, through the learning routes; *done* as above. */
  learning?: (key: string, id: string, action: 'accept' | 'reject', done?: string) => void
  /** An app's proposal: Approve applies it (the Inbox's apply), Reject dismisses its row. */
  appProposal?: (key: string, itemId: string, action: 'approve' | 'reject') => void
  /** A chat to file: Accept or Decline, the proposal rebuilt from its row. */
  organize?: (key: string, session: string, proposal: OrganizeProposal, action: 'accept' | 'decline') => void
  /** A typed line for a free-text ask; true once it is sent, so the field empties only then. */
  reply: (key: string, target: ReplyTarget, text: string) => Promise<boolean>
  /** A folder's trust, recorded by its own button (`TrustFolderButton`): the card says so. */
  trusted: (key: string) => void
}

/** A card's kind, its first word: a neutral pill with its icon and noun from the registry
 *  (`needsYouKinds`), no tab stop, never coral. */
function KindTag({ kind }: { kind: KindWords }) {
  const Icon = kind.icon
  return (
    <StatusPill tone="neutral" groundedOn="var(--color-surface-low)" className="inline-flex shrink-0 items-center gap-1">
      <Icon size={12} aria-hidden="true" />{kind.tag}
    </StatusPill>
  )
}

/** A card's tag row: its kind, then the thing it is about or its source, the dot and the name one
 *  unit so a wrapped row never leaves the dot behind; at its end the project chip and the time left
 *  to answer. */
function NeedTagRow({ row, chip, now }: { row: NeedsYouRow; chip?: ReactNode; now: number }) {
  const slot = thingName(row) || sourceSlot(row)
  const left = row.origin === 'waiting' ? timeLeft(row.item, now) : ''
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-xs">
      <KindTag kind={kindOf(row)} />
      {slot ? (
        <span data-type="caption" data-name-slot="" className="inline-flex min-w-[50%] items-baseline gap-1 text-on-surface-low">
          <span aria-hidden="true">·</span><span className="min-w-0 truncate">{slot}</span>
        </span>
      ) : null}
      {chip || left ? (
        <span className="ml-auto flex shrink-0 items-center gap-xs">
          {chip}
          {left ? <span data-type="caption" className="text-on-surface-low">{left}</span> : null}
        </span>
      ) : null}
    </div>
  )
}

/** What a card's answer carries on every card: a 44 px box below 1024 px and a transparent edge,
 *  which forced colours draw as `ButtonBorder`, so a pair keeps its edges there. */
const ANSWER = `border border-transparent ${TOUCH_TARGET}`

/** The asks only the Inbox raised that are answered there: their way out is their Inbox row. */
const ANSWERED_IN_THE_INBOX = new Set(['held_action', 'declined_wait', 'escalated_run'])

/** One Needs you card in the group: what asked, what it asks, and its answer. The card opens its
 *  detail; its own controls keep their clicks. *project* is the project its work is in, drawn as
 *  its chip: on an approval on a line of its own above the approval card, which has no head row to
 *  hold it, and on any other card at the end of its asker's line (or on a line of its own when it
 *  has no asker). Plain text on a phone, since the card is itself a control (`ProjectChip`). */
export function NeedCard({ row, outcome, actions, now, selected, onOpen, project, off }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
  selected: boolean
  onOpen: () => void
  /** The project the item's work is in, or none. */
  project?: { id: string; name: string } | null
  /** Why no answer can be sent now ("Can't reach PersonalClaw"): each answer is off, saying it. */
  off?: string
}) {
  const open = openable(needName(row), onOpen, selected)
  const chip = project ? <ProjectChip id={project.id} name={project.name} inControl /> : null
  // An answer's control leaves with its answer: the focus stays on its card, not the page's body,
  // so the keys go on from there.
  const root = useRef<HTMLDivElement>(null)
  const hadFocus = useRef(false)
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  useEffect(() => {
    const el = root.current
    if (!settled || !el || !hadFocus.current) return
    const active = document.activeElement
    if (active && active !== document.body && active !== document.documentElement) return
    el.querySelector<HTMLElement>('[data-row-hit-target]')?.focus()
  }, [settled])
  return (
    // A card stretches to its grid row's height, its answers right under its own content and the
    // row's slack below them. Its hover is opaque `surface-container`, a ground no answer uses. A
    // focused card keeps an outline under forced colours, which drop its ring (a box-shadow).
    <div ref={root} {...open.props} data-need-card="" data-need-key={rowKey(row)}
      onFocus={() => { hadFocus.current = true }}
      onBlur={(e) => { if (e.relatedTarget instanceof Node && !e.currentTarget.contains(e.relatedTarget)) hadFocus.current = false }}
      className={`flex min-w-0 flex-1 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s hover:bg-surface-container forced-colors:has-[>button:focus-visible]:outline-2 forced-colors:has-[>button:focus-visible]:outline-[Highlight] ${open.className}`}>
      {open.target}
      <NeedBody row={row} outcome={outcome} actions={actions} now={now} chip={chip} selected={selected} off={off} />
    </div>
  )
}

/** The selected Needs you item, in full: the same card and the same answers, and where it asks. */
export function NeedDetail({ row, outcome, actions, now }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
}) {
  const own = row.origin === 'waiting' ? row.item.route : row.origin === 'proposal' ? '#/skills?mode=proposals'
    : `#/inbox?open=${encodeURIComponent(row.note.id)}`
  // An ask answered on Needs you itself (a browser grant) has nowhere else to open: no link here.
  const route = own.replace(/^#\/?/, '').split('?')[0] === 'mission-control' ? '' : own
  const asked = row.origin === 'waiting' ? clockTime(row.item.asked_at) : ''
  const item = row.origin === 'waiting' ? row.item : null
  const reply = item ? waitingReply(item) : null
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  const questions = item?.kind === 'question' && Array.isArray(item.brief.questions) ? item.brief.questions : []
  return (
    <div className="flex min-w-0 flex-col gap-m">
      <NeedBody row={row} outcome={outcome} actions={actions} now={now} />
      {questions.length > 1 ? (
        <ol className="flex min-w-0 list-decimal flex-col gap-xs pl-l">
          {questions.map((q, i) => (
            <li key={i} data-type="body-s" className="text-on-surface">
              {typeof (q as { question?: unknown }).question === 'string' ? <MarkdownLine text={(q as { question: string }).question} /> : ''}
            </li>
          ))}
        </ol>
      ) : null}
      {item?.session ? <ConversationTail session={item.session} /> : null}
      {/* A loop's line is on its card (`NeedBody`), and so in its panel: any other ask that takes words
          is answered here. */}
      {reply && reply.kind !== 'loop' && item && !settled && !isStale(item, now) ? (
        <ReplyField subject={needName(row)} busy={outcome?.state === 'busy'}
          placeholder="Reply in one line: Enter sends, Escape closes"
          onSend={(text) => actions.reply(row.key, reply, text)} />
      ) : null}
      <dl data-type="body-s" className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs text-on-surface-var">
        {row.raisedBy ? <><dt className="text-on-surface-low">Asked by</dt><dd>{row.raisedBy}</dd></> : null}
        {asked ? <><dt className="text-on-surface-low">Asked</dt><dd>{asked}</dd></> : null}
        {row.origin === 'waiting' && row.item.expires_at ? (
          <><dt className="text-on-surface-low">Answer by</dt><dd>{clockTime(row.item.expires_at)}</dd></>
        ) : null}
      </dl>
      {route ? <TextLink href={route} ink="emphasis" size="sm">Open where it asks</TextLink> : null}
    </div>
  )
}

/** A Needs you item's content and answer, shared by its card and its detail. *chip* is the card's
 *  project chip, which the detail does not draw. *selected* is the card whose panel is open: its
 *  answers leave it, so no ask is answered twice, and their space holds the line that says to
 *  answer it there.
 *
 *  Each answer sits right under the card's own content, and what it leads to opens below it: the
 *  outcome once it lands takes the answers' place at the height they took (with a second
 *  confirmation's, `useHeldHeight`), and a failure says why under them, so a press moves nothing
 *  above or beside the pointer. Every answer to what waits is `secondary`, a skill proposal's accept
 *  `tonal`: none is solid coral on a card. */
function NeedBody({ row, outcome, actions, now, chip, selected = false, off }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
  chip?: ReactNode
  selected?: boolean
  off?: string
}) {
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  const busy = outcome?.state === 'busy'
  const subject = needSubject(row)
  // A proposal's or a note's lines are clamped (the panel holds them whole); what an ask's answer
  // decides on never is, since its answer acts in one press.
  const clamped = row.origin !== 'waiting'
  const item = row.origin === 'waiting' ? row.item : null
  const expired = item ? isStale(item, now) : false
  const answersRef = useRef<HTMLDivElement>(null)
  const held = useHeldHeight(answersRef, !settled && !selected)
  const inPanel = (
    <p data-type="caption" className="text-on-surface-low">
      {row.origin === 'waiting' ? 'Answer it in the panel' : 'Decide it in the panel'}
    </p>
  )
  /** What stands where the answers were: the outcome once answered, the panel's line while open. */
  const replaced = settled ? <CardOutcome outcome={outcome} /> : selected ? inPanel : null

  if (item?.kind === 'approval') {
    const approval = approvalOf(item)
    if (approval && !expired) {
      return (
        <div className="flex min-w-0 flex-col gap-xs">
          <PendingApprovalCard approval={approval} busy={busy || !!off} form="queue"
            tagEnd={chip || timeLeft(item, now) ? <>{chip}{timeLeft(item, now) ? <span data-type="caption" className="text-on-surface-low">{timeLeft(item, now)}</span> : null}</> : undefined}
            answersReplacedBy={replaced ?? undefined}
            onDecide={(action) => actions.approval(row.key, approval.id, action)} />
          {outcome?.state === 'failed' ? <CardOutcome outcome={outcome} /> : null}
        </div>
      )
    }
  }

  const question = item ? waitingQuestion(item) : null
  const answerId = item ? waitingAnswerId(item) : ''
  const proposal = row.origin === 'proposal' ? row.proposal : null
  const answersHere = noteAnswer(row)
  const trustsFolder = item?.kind === 'folder_trust' && typeof item.brief.dir === 'string' && item.brief.dir !== ''
    ? item.brief.dir : ''
  const inTheInbox = item && ANSWERED_IN_THE_INBOX.has(item.kind) && item.route ? item.route : ''
  const opensWhereAsked = item && !question && !answerId && !trustsFolder && !inTheInbox
  const reply = item ? waitingReply(item) : null
  // A loop's question, and a stalled or blocked loop, take their one line on the card.
  const cardReply = reply?.kind === 'loop' ? reply : null
  const answerable = !!(proposal || answersHere || question || cardReply || trustsFolder
    || ((item?.kind === 'browse_grant' || item?.kind === 'bridge_confirmation') && answerId))
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <NeedTagRow row={row} chip={chip} now={now} />
      {/* What an agent, a loop or a person wrote renders as its line; an approval's title is a tool's
          name, and a browse grant's second line is ours, so both stay text. */}
      <p data-type="title-m" className={`min-w-0 break-words text-on-surface${clamped ? ' line-clamp-2' : ''}`}>{item?.kind === 'approval' ? row.title : <MarkdownLine text={row.title} tooltip={false} />}</p>
      {row.subtitle ? <p data-type="body-s" className={`min-w-0 break-words text-on-surface${clamped ? ' line-clamp-1' : ''}`}>{item?.kind === 'browse_grant' ? row.subtitle : <MarkdownLine text={row.subtitle} tooltip={false} />}</p> : null}
      {/* A gate's or a trigger's prompt is a body, with any widget block left out. */}
      {question?.prompt && question.prompt !== row.title ? (
        <div data-type="body-s" className="my-1 min-w-0 text-on-surface"><Markdown>{widgetlessText(question.prompt)}</Markdown></div>
      ) : null}
      {expired && !settled ? (
        <p role="status" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface-var">
          <Ban size={14} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
          {item?.expires_at
            ? `This stopped waiting at ${clockTime(item.expires_at)}, so it can no longer be answered here.`
            : 'This can no longer be answered here.'}
        </p>
      ) : null}
      {opensWhereAsked && item.route ? (
        <TextLink href={item.route} ink="emphasis" size="sm" aria-label={`Answer it where it asks: ${subject}`}>
          Answer it where it asks
        </TextLink>
      ) : null}
      {inTheInbox ? (
        <TextLink href={inTheInbox} ink="emphasis" size="sm" aria-label={`Open it in the Inbox: ${subject}`}>
          Open it in the Inbox
        </TextLink>
      ) : null}
      {row.origin === 'note' && !answersHere ? (
        <TextLink href={`#/inbox?open=${encodeURIComponent(row.note.id)}`} ink="emphasis" size="sm" aria-label={`Open it in the Inbox: ${subject}`}>
          Open it in the Inbox
        </TextLink>
      ) : null}

      {/* Settled or expired, the verbs are GONE, not disabled: a second answer on a settled item
          is a real action. A FAILED card keeps them: retrying is the point of being told. */}
      {replaced && (settled || answerable) ? (
        <div data-held-answers="" style={held ? { minHeight: held } : undefined}>{replaced}</div>
      ) : expired || settled || !answerable ? null : (
        <div ref={answersRef} className="flex min-w-0 flex-col gap-xs">
          {cardReply || !answerable ? null : (
            <div className="flex min-w-0 flex-wrap items-start gap-xs">
              {proposal ? (
                <SkillAnswers proposal={proposal} subject={subject} busy={busy} off={off}
                  onAnswer={(action, done) => actions.proposal(row.key, proposal.id, action, done)} />
              ) : null}
              {answersHere?.kind === 'learning' && actions.learning ? (
                <LearningAnswers pid={answersHere.pid} card={answersHere.card} subject={subject} busy={busy} off={off}
                  onAnswer={(action, done) => actions.learning?.(row.key, answersHere.pid, action, done)} />
              ) : null}
              {answersHere?.kind === 'app' && actions.appProposal ? (
                <YesNo subject={subject} busy={busy} off={off} yes="Approve" no="Reject"
                  onYes={() => actions.appProposal?.(row.key, answersHere.itemId, 'approve')}
                  onNo={() => actions.appProposal?.(row.key, answersHere.itemId, 'reject')} />
              ) : null}
              {answersHere?.kind === 'organize' && actions.organize ? (
                <YesNo subject={subject} busy={busy} off={off} yes="Accept" no="Decline"
                  onYes={() => actions.organize?.(row.key, answersHere.session, answersHere.proposal, 'accept')}
                  onNo={() => actions.organize?.(row.key, answersHere.session, answersHere.proposal, 'decline')} />
              ) : null}
              {question ? (
                <QuestionActions question={question} subject={subject} busy={busy} off={off}
                  onAnswer={(value) => actions.question(row.key, question, value)} />
              ) : null}
              {item?.kind === 'browse_grant' && answerId ? (
                <YesNo subject={subject} busy={busy} off={off} yes="Allow" no="Refuse"
                  onYes={() => actions.grant(row.key, answerId, 'approve')}
                  onNo={() => actions.grant(row.key, answerId, 'reject')} />
              ) : null}
              {item?.kind === 'bridge_confirmation' && answerId ? (
                <YesNo subject={subject} busy={busy} off={off} yes="Run it once" no="Drop it"
                  onYes={() => actions.bridge(row.key, answerId, true)}
                  onNo={() => actions.bridge(row.key, answerId, false)} />
              ) : null}
              {trustsFolder ? (
                <TrustFolderButton folder={trustsFolder} busy={busy || !!off} inline onTrusted={() => actions.trusted(row.key)} />
              ) : null}
            </div>
          )}
          {cardReply ? (
            <ReplyField subject={subject} busy={busy} off={off} onCard
              onSend={(text) => actions.reply(row.key, cardReply, text)} />
          ) : null}
        </div>
      )}
      {outcome?.state === 'failed' ? <CardOutcome outcome={outcome} /> : null}
    </div>
  )
}

/** What a note row answers on its card, by what its Inbox row carries: a learning proposal (its
 *  card, or none for a row filed before it), an app's proposal, a chat to file. `null` for a row
 *  only the Inbox answers (your note). */
type NoteAnswer =
  | { kind: 'learning'; pid: string; card: Record<string, unknown> | null }
  | { kind: 'app'; itemId: string }
  | { kind: 'organize'; session: string; proposal: OrganizeProposal }

function noteAnswer(row: NeedsYouRow): NoteAnswer | null {
  if (row.origin !== 'note' || row.note.item_kind !== 'proposal') return null
  const refs = (row.note.refs ?? {}) as Record<string, unknown>
  if (typeof refs.learning_proposal === 'string' && refs.learning_proposal) {
    return { kind: 'learning', pid: refs.learning_proposal, card: learningCardOf(row) }
  }
  if (refs.app && refs.proposal) return { kind: 'app', itemId: row.note.id }
  if (refs.session_organize && typeof refs.session === 'string') {
    // The proposal rebuilt from the row; the route checks every field again against the live
    // folders and tags at the press.
    const tags = typeof refs.tags === 'string' ? refs.tags.split(',').filter(Boolean) : []
    return {
      kind: 'organize', session: refs.session,
      proposal: { session: refs.session, folder_id: String(refs.folder_id ?? ''), folder_name: '', tags, source: '', reason: '' },
    }
  }
  return null
}

/** A proposal's verb, done, for its outcome ("Template saved."). */
const DONE: Record<string, string> = {
  'Save template': 'Template saved', 'Install skill': 'Skill installed', 'Add refinement': 'Refinement added',
  'Apply changes': 'Changes applied', 'Add instruction': 'Instruction added', 'Write file': 'File written',
  'Create skill': 'Skill created', 'Save prompt': 'Prompt saved', 'Save agent': 'Agent saved',
  'Save principle': 'Principle saved', 'Use the learned one': 'The learned one replaced yours', 'Remove them': 'Removed',
  'Keep mine': 'Kept yours', 'Keep them': 'Kept', 'Reject': 'Rejected', 'Reject them': 'Rejected',
}
const done = (verb: string) => `${DONE[verb] ?? 'Accepted'}.`

/** A learning proposal's reject, named as its panel names it. */
function rejectVerb(variant: string): string {
  if (variant === 'lesson.conflict') return 'Keep mine'
  if (variant === 'retirement.text_not_typed') return 'Keep them'
  if (variant === 'lesson.captures') return 'Reject them'
  return 'Reject'
}

/** A learning proposal's pair on its card, in its decide bar's words (its card's `accept`): an
 *  accept that installs or applies is `tonal`, one that only records is `secondary`, and one nothing
 *  here can accept is no button but its sentence, beside Reject. Every accept's press reads the
 *  proposal's review first, so a change since the list was read (a file now there) is caught: one
 *  that replaces or removes what is there asks once more, inline, in the review's words, and nothing
 *  is sent before its yes. A row filed before the card (no `refs.card`) can't tell from its kind
 *  what its Accept does, so its Accept always asks once more. */
function LearningAnswers({ pid, card, subject, busy, off, onAnswer }: {
  pid: string; card: Record<string, unknown> | null; subject: string; busy: boolean; off?: string
  onAnswer: (action: 'accept' | 'reject', done: string) => void
}) {
  const accept = (card?.accept ?? {}) as { effect?: string; verb?: string; sentence?: string }
  const variant = String(card?.variant ?? '')
  const [asking, setAsking] = useState<{ sentence: string; verb: string } | null>(null)
  const [told, setTold] = useState<Told | null>(null)
  const [reading, setReading] = useState(false)
  const verb = card ? accept.verb || 'Accept' : 'Accept'
  const no = rejectVerb(variant)
  if (asking) {
    return (
      <InlineConfirm sentence={asking.sentence} verb={asking.verb.toLowerCase()} onBack={() => setAsking(null)}
        onYes={() => { setAsking(null); onAnswer('accept', done(asking.verb)) }} />
    )
  }
  if (told?.state === 'ended') return <ReadSaid told={told} />
  const press = () => {
    setReading(true)
    setTold(null)
    void api.learningProposalReview(pid).then((review) => {
      setReading(false)
      if (review.accept.effect === 'none') { setTold({ state: 'none', text: review.accept.sentence }); return }
      if (review.accept.confirm || !card) { setAsking({ sentence: review.accept.sentence, verb: review.accept.verb || 'Accept' }); return }
      onAnswer('accept', done(review.accept.verb || verb))
    }).catch((err: unknown) => {
      setReading(false)
      setTold(toldBy(err, 'read this proposal'))
    })
  }
  const none = told?.state === 'none' || (card && accept.effect === 'none')
  return (
    <>
      {none ? (
        <p data-type="body-s" className="basis-full text-on-surface">{told?.state === 'none' ? told.text : accept.sentence}</p>
      ) : (
        <Button size="xs" variant={card && (accept.effect === 'installs' || accept.effect === 'applies') ? 'tonal' : 'secondary'}
          loading={busy || reading} disabled={!!off} disabledReason={off} ariaLabel={`${verb}: ${subject}`} onClick={press} className={ANSWER}>
          <Check size={13} aria-hidden="true" /> {verb}
        </Button>
      )}
      <Button size="xs" variant="secondary" loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`${no}: ${subject}`} onClick={() => onAnswer('reject', done(no))} className={ANSWER}>
        <X size={13} aria-hidden="true" /> {no}
      </Button>
      {told?.state === 'failed' ? <ReadSaid told={told} /> : null}
    </>
  )
}

/** What a proposal's read before its Accept said, when it sent nothing: there is nothing here to
 *  accept (`none`, beside Reject), it was decided or ended somewhere else or Learning is off
 *  (`ended`, its verbs gone), or the read failed (`failed`, its verbs kept to try again). */
type Told = { state: 'none' | 'ended' | 'failed'; text: string }

function toldBy(err: unknown, verb: string): Told {
  const outcome = outcomeFor(err, verb)
  return { state: outcome.state === 'ended' ? 'ended' : 'failed', text: 'text' in outcome ? outcome.text : '' }
}

function ReadSaid({ told }: { told: Told }) {
  return told.state === 'failed' ? (
    <p role="alert" data-type="body-s" className="flex basis-full min-w-0 items-start gap-xs text-on-surface">
      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
      {told.text}
    </p>
  ) : (
    <p role="status" data-type="body-s" className="basis-full text-on-surface-var">{told.text}</p>
  )
}

/** A skill proposal's pair on its card: its card's verb (Add refinement, Install skill), `tonal`,
 *  and Reject. The press reads the proposal again first: a skill removed since the list was read
 *  means accepting installs it as new, which it asks once more about. */
function SkillAnswers({ proposal, subject, busy, off, onAnswer }: {
  proposal: NonNullable<Extract<NeedsYouRow, { origin: 'proposal' }>['proposal']>
  subject: string; busy: boolean; off?: string
  onAnswer: (action: 'accept' | 'reject', done: string) => void
}) {
  const verb = proposal.card?.accept.verb || 'Accept'
  const [asking, setAsking] = useState<string | null>(null)
  const [told, setTold] = useState<Told | null>(null)
  const [reading, setReading] = useState(false)
  if (asking) {
    return (
      <InlineConfirm sentence={asking} verb="install it" onBack={() => setAsking(null)}
        onYes={() => { setAsking(null); onAnswer('accept', done('Install skill')) }} />
    )
  }
  if (told?.state === 'ended') return <ReadSaid told={told} />
  const press = () => {
    // A proposal read before its card says what it always said.
    if (!proposal.card) { onAnswer('accept', 'Accepted. The skill is saved.'); return }
    if (!proposal.target_resolves) { onAnswer('accept', done(verb)); return }
    setReading(true)
    setTold(null)
    void api.skillProposalDetail(proposal.id).then((now) => {
      setReading(false)
      const gone = (now as { target_resolves?: boolean }).target_resolves === false
      if (gone) setAsking(`It is no longer installed, so accepting installs auto/${proposal.slug.replace(/^auto\//, '')} as a new skill.`)
      else onAnswer('accept', done(verb))
    }).catch((err: unknown) => {
      // Not read, so not known to be there: nothing is sent blind.
      setReading(false)
      setTold(toldBy(err, 'read this proposal'))
    })
  }
  return (
    <>
      <Button size="xs" variant="tonal" loading={busy || reading} disabled={!!off} disabledReason={off}
        ariaLabel={`${verb}: ${subject}`} onClick={press} className={ANSWER}>
        <Check size={13} aria-hidden="true" /> {verb}
      </Button>
      <Button size="xs" variant="secondary" loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`Reject: ${subject}`} onClick={() => onAnswer('reject', 'Rejected.')} className={ANSWER}>
        <X size={13} aria-hidden="true" /> Reject
      </Button>
      {told ? <ReadSaid told={told} /> : null}
    </>
  )
}

/** A yes and a no, each named with what it answers. Both carry `loading` while the answer is on
 *  its way, so neither can be sent twice and assistive tech hears it is busy. */
function YesNo({ subject, busy, yes, no, onYes, onNo, yesVariant = 'secondary', off }: {
  subject: string; busy: boolean; yes: string; no: string; onYes: () => void; onNo: () => void
  /** `tonal` for an accept that installs or applies; every answer to what waits is `secondary`. */
  yesVariant?: 'secondary' | 'tonal'
  /** Why it can't be sent now: both are off, saying it. */
  off?: string
}) {
  return (
    <>
      <Button size="xs" variant={yesVariant} loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`${yes}: ${subject}`} onClick={onYes} className={ANSWER}>
        <Check size={13} aria-hidden="true" /> {yes}
      </Button>
      <Button size="xs" variant="secondary" loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`${no}: ${subject}`} onClick={onNo} className={ANSWER}>
        <X size={13} aria-hidden="true" /> {no}
      </Button>
    </>
  )
}

/** How a card's answer went, in words, in a live region: a card that only changed colour is one a
 *  screen-reader user cannot tell from a pending one. */
function CardOutcome({ outcome }: { outcome: Outcome | undefined }) {
  if (!outcome || outcome.state === 'busy') return null
  if (outcome.state === 'failed') {
    return (
      <p role="alert" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface">
        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
        {outcome.text}
      </p>
    )
  }
  return (
    <p role="status" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface-var">
      {outcome.state === 'done'
        ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
        : <Ban size={14} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />}
      {outcome.text}
    </p>
  )
}

/** A gate's or a park's answers, as buttons: one click per option. A question with no preset options
 *  says where to answer it in your own words rather than offering a text box this card could not
 *  honestly submit. A parked run decides nothing, so its one button wakes it. */
function QuestionActions({ question, subject, busy, onAnswer, off }: {
  question: CardQuestion
  subject: string
  busy: boolean
  onAnswer: (value: string | boolean) => void
  off?: string
}) {
  if (question.event) {
    return (
      <Button size="xs" variant="secondary" loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`Wake it now: ${subject}`} onClick={() => onAnswer(true)} className={ANSWER}>
        <Play size={13} aria-hidden="true" /> Wake it now
      </Button>
    )
  }
  if (question.approval) {
    return <YesNo subject={subject} busy={busy} off={off} yes="Approve" no="Deny" onYes={() => onAnswer(true)} onNo={() => onAnswer(false)} />
  }
  if (question.choices.length === 0) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        This question has no preset options —{' '}
        <TextLink href={`#/workflows/runs/${encodeURIComponent(question.runId)}`} ink="emphasis" aria-label={`Open the run: ${subject}`}>
          open the run
        </TextLink>{' '}
        to answer it in your own words.
      </p>
    )
  }
  // A choice's words are the run's own, so its pill wraps (`Button` never does): a long one never
  // runs past the card at 320 px.
  return (
    <>
      {question.choices.map((choice) => (
        <WrappingAnswer key={choice} disabled={busy || !!off} ariaBusy={busy}
          ariaLabel={`${choice}: ${subject}`} onClick={() => onAnswer(choice)}>
          {choice}
        </WrappingAnswer>
      ))}
    </>
  )
}

/** The one-line reply a free-text ask takes: Enter sends it to the ask's own route, and a send that
 *  fails keeps the line and says why under it. Nothing is sent while the owner types, and an empty
 *  line sends nothing. *onCard*: the card's own field, across its width with the chat list's field
 *  edge (`EDGE`), a 44 px box below 1024 px. */
function ReplyField({ subject, busy, onSend, onCard = false, placeholder = 'Reply in one line: Enter sends', off }: {
  subject: string
  busy: boolean
  onSend: (text: string) => Promise<boolean>
  onCard?: boolean
  placeholder?: string
  off?: string
}) {
  const [text, setText] = useState('')
  return (
    <TextInput value={text} onChange={setText} ariaLabel={`Reply to ${subject}`}
      placeholder={placeholder} disabled={!!off} disabledReason={off}
      size={onCard ? 'sm' : undefined} surface={onCard ? 'high' : undefined}
      className={onCard ? `${EDGE} max-lg:min-h-11` : undefined}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' || e.nativeEvent.isComposing || busy) return
        e.preventDefault()
        const line = text.trim()
        if (!line) return
        void onSend(line).then((sent) => { if (sent) setText('') })
      }} />
  )
}

/** The last few lines of the conversation an ask came from, read-only: what the agent said before
 *  it asked, so the owner can answer from here. */
function ConversationTail({ session }: { session: string }) {
  const { data, error, refresh } = useQuery(`chat:peek:${session}`, async () => api.chatSessionDetail(session), { persist: false })
  if (error && !data) return <InlineLoadError what="the conversation it came from" error={error} onRetry={refresh} />
  const lines = (data?.messages ?? []).filter((m) => m.role === 'user' || m.role === 'assistant').slice(-4)
  if (lines.length === 0) return null
  return (
    <section role="region" aria-label="The conversation it came from" className="flex min-w-0 flex-col gap-xs border-l-2 border-outline-variant/40 pl-m">
      {/* Each line renders as the transcript does: the agent's as its reply (any widget block left
          out), yours as text written in lines; each cut at a block boundary, never in a fence. */}
      {lines.map((m, i) => (
        <div key={i} data-type="body-s" className={`flex min-w-0 items-baseline gap-1.5 ${m.role === 'user' ? 'text-on-surface-var' : 'text-on-surface'}`}>
          <span className="shrink-0 text-on-surface-low">{m.role === 'user' ? 'You: ' : 'Agent: '}</span>
          <div className="min-w-0 flex-1">
            <Markdown breaks={m.role === 'user'}>{cutMarkdown(m.role === 'user' ? String(m.content ?? '') : widgetlessText(String(m.content ?? '')), 400)}</Markdown>
          </div>
        </div>
      ))}
    </section>
  )
}
