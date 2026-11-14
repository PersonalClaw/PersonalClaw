import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Ban, Check, CheckCircle2, Play, X } from 'lucide-react'
import { api, ApiError, hasApiCode } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { clockTime } from '../../lib/epoch'
import type { NeedsYouRow } from '../../lib/needsYou'
import {
  waitingAnswerId, waitingQuestion, waitingReply, type CardQuestion, type ReplyTarget,
} from '../../lib/waitingAnswer'
import { approvalOf, isStale } from '../../lib/waitingList'
import { rowSubject } from '../../lib/rowSubject'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'
import { Button } from '../../ui/Button'
import { WrappingAnswer } from '../../ui/InlineConfirm'
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
  return `Could not ${verb}: ${detail.replace(/[.\s]+$/, '')}. Nothing was recorded — it still needs you, so try again.`
}

/** When an answer is refused because there is nothing left to decide, the sentence for it, or
 *  null for a real failure: the work that asked has ended (409, the gateway names which), or it
 *  was answered or ended somewhere else (404). Retrying either would change nothing. */
export function endedText(err: unknown): string | null {
  if (hasApiCode(err, 'approval_owner_ended') && err instanceof Error) return err.message
  if (err instanceof ApiError && err.status === 404) {
    return 'It is no longer waiting: it was answered or ended somewhere else.'
  }
  return null
}

/** What a gate's or a park's answer did, in words. */
export function answeredText(q: CardQuestion, value: string | boolean): string {
  if (q.event) return 'Woken — the run is moving again.'
  if (!q.approval) return `Answered “${String(value)}” — the run is moving again.`
  if (q.triggerId) {
    return value === true ? 'Approved — it is running again.' : 'Declined — it asks again the next time it stops.'
  }
  return value === true ? 'Approved — the run is moving again.' : 'Declined — the run ends here.'
}

/** An approval's title is a tool's name or the command itself: it stays as written wherever it is
 *  drawn or named, as its card draws it. Any other ask's title is an agent's line. */
const isApproval = (row: NeedsYouRow) => row.origin === 'waiting' && row.item.kind === 'approval'
/** An ask's title as a name or a tooltip: as written for an approval, its plain line for any other. */
export const needName = (row: NeedsYouRow) => (isApproval(row) ? row.title : previewLine(row.title))
/** An ask's title as a heading: text for an approval, rendered for any other. */
export const needHeading = (row: NeedsYouRow) => (isApproval(row) ? row.title : <MarkdownLine text={row.title} />)

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
  proposal: (key: string, id: string, action: 'accept' | 'reject') => void
  /** A typed line for a free-text ask; true once it is sent, so the field empties only then. */
  reply: (key: string, target: ReplyTarget, text: string) => Promise<boolean>
  /** A folder's trust, recorded by its own button (`TrustFolderButton`): the card says so. */
  trusted: (key: string) => void
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
  const subject = rowSubject([needName(row), row.raisedBy, previewLine(row.subtitle)])
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
          <PendingApprovalCard approval={approval} busy={busy || !!off} form="queue" tagEnd={chip}
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
  const trustsFolder = item?.kind === 'folder_trust' && typeof item.brief.dir === 'string' && item.brief.dir !== ''
    ? item.brief.dir : ''
  const inTheInbox = item && ANSWERED_IN_THE_INBOX.has(item.kind) && item.route ? item.route : ''
  const opensWhereAsked = item && !question && !answerId && !trustsFolder && !inTheInbox
  const reply = item ? waitingReply(item) : null
  // A loop's question, and a stalled or blocked loop, take their one line on the card.
  const cardReply = reply?.kind === 'loop' ? reply : null
  const answerable = !!(proposal || question || cardReply || trustsFolder
    || ((item?.kind === 'browse_grant' || item?.kind === 'bridge_confirmation') && answerId))
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      {row.raisedBy && chip ? (
        <div className="flex min-w-0 items-center gap-xs">
          <p data-type="label-s" className="min-w-[50%] flex-1 truncate text-on-surface-low">{row.raisedBy}</p>
          {chip}
        </div>
      ) : row.raisedBy ? <p data-type="label-s" className="min-w-0 truncate text-on-surface-low">{row.raisedBy}</p>
        : chip ? <div className="flex min-w-0 justify-end">{chip}</div> : null}
      {/* What an agent, a loop or a person wrote renders as its line; an approval's title is a tool's
          name, and a browse grant's second line is ours, so both stay text. */}
      <p data-type="label-m" className="min-w-0 text-on-surface-var">{item?.kind === 'approval' ? row.title : <MarkdownLine text={row.title} />}</p>
      {row.subtitle ? <p data-type="body-s" className="min-w-0 text-on-surface-low">{item?.kind === 'browse_grant' ? row.subtitle : <MarkdownLine text={row.subtitle} />}</p> : null}
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
      {proposal ? (
        <TextLink href="#/skills?mode=proposals" ink="emphasis" size="sm" aria-label={`Review it in Skills: ${subject}`}>
          Review it in Skills
        </TextLink>
      ) : null}
      {row.origin === 'note' ? (
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
                // A skill proposal's accept installs it: the one tinted answer a card draws.
                <YesNo subject={subject} busy={busy} off={off} yes="Accept" no="Reject" yesVariant="tonal"
                  onYes={() => actions.proposal(row.key, proposal.id, 'accept')}
                  onNo={() => actions.proposal(row.key, proposal.id, 'reject')} />
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
        ariaLabel={`${yes} ${subject}`} onClick={onYes} className={ANSWER}>
        <Check size={13} aria-hidden="true" /> {yes}
      </Button>
      <Button size="xs" variant="secondary" loading={busy} disabled={!!off} disabledReason={off}
        ariaLabel={`${no} ${subject}`} onClick={onNo} className={ANSWER}>
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
        ariaLabel={`Wake ${subject} now`} onClick={() => onAnswer(true)} className={ANSWER}>
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
        <WrappingAnswer key={choice} disabled={busy || !!off} aria-busy={busy || undefined}
          aria-label={`Answer ${subject} — ${choice}`} onClick={() => onAnswer(choice)}>
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
