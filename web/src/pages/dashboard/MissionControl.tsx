import { useCallback, useMemo, useState } from 'react'
import { AlertTriangle, Check, CheckCircle2, X } from 'lucide-react'
import { api, type ChatSessionSummary, type InboxItem, type PendingApproval } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { rowSubject } from '../../lib/rowSubject'
import { Button } from '../../ui/Button'
import { LANES, toLanes, type Lane } from '../../lib/attentionLanes'

// ── Mission Control — the locked four-lane attention view (AMBIENT-SURFACES AS-8) ──────────
//
// The whole point of this surface is that it is a CONTROL surface, not a list: the two verbs
// that resolve an item are ON the card, so "what needs me" and "deal with it" are one glance
// and one click instead of a glance plus a navigation. Two consequences follow, and they are
// the reason most of the code below exists:
//
//  1. A resolved card must LOOK resolved the moment the POST returns. A button that fires and
//     leaves the card looking pending trains the user to click again — and a second approve on
//     an already-resolved id is a real action with a real effect, not a no-op.
//  2. A FAILED action must say so, in the gateway's own words, on the card. Rendering nothing
//     on failure is indistinguishable from success here, because the only other feedback a
//     user has is the card disappearing on the next read — which is exactly what does NOT
//     happen when the call failed.
//
// Lane derivation is NOT done here. `lib/attentionLanes` owns `laneFor`/`toLanes` as pure
// functions over the unified attention store, so the classification is testable without a DOM
// and one rule cannot drift between this view and any other consumer of the same lanes.

/** ── THE ONE RECONCILIATION POINT with `dashboard/views_store.py` ──────────────────────────
 *
 *  The locked preset registers four `core:` tile refs, one per lane. This map is the only place
 *  those strings appear on the frontend, so reconciling the view registry with this surface is a
 *  single-line edit rather than a hunt.
 *
 *  Keyed by `Lane` and asserted (in `missionControl.test.tsx`) to cover `LANES` exactly, in
 *  order — a ref map that silently omits a lane would register a view with a hole in it.
 *
 *  These strings are the SERVER's, copied from `_MISSION_CONTROL_CORE_REFS`. They were
 *  `core:attention-*` here while the registry said `core:lane-*` — two halves built on separate
 *  branches against a contract written in prose, and nothing failed: a tile ref is a string on
 *  both sides, so neither lint, nor mypy, nor either suite could see the mismatch. Hence the
 *  cross-language rail in `tests/test_dashboard_mission_control_preset.py`, which reads this map out of
 *  this file and asserts set equality with the registry — the only check that can fail here. */
export const LANE_REFS: Record<Lane, string> = {
  'needs-approval': 'core:lane-needs-approval',
  'your-turn': 'core:lane-your-turn',
  working: 'core:lane-working',
  idle: 'core:lane-idle',
}

/** The preset's own id, for the same reason: one string, one place. */
export const MISSION_CONTROL_VIEW_ID = 'mission-control'

/** Which lane a preset tile ref belongs to, or null for a ref this view does not own. The
 *  inverse of `LANE_REFS`, for a host that mounts the preset tile-by-tile. */
export function laneForRef(ref: string): Lane | null {
  return LANES.find((l) => LANE_REFS[l] === ref) ?? null
}

// The lane headings and — just as load-bearing — the sentence an EMPTY lane says. A lane that
// vanishes when it empties reads as "nothing needs me", which is a different and much worse
// claim than "nothing needs approval": the user cannot tell an empty queue from a queue that
// failed to render. So all four lanes always paint, and an empty one is explicit about it.
const LANE_LABEL: Record<Lane, string> = {
  'needs-approval': 'Needs approval',
  'your-turn': 'Your turn',
  working: 'Working',
  idle: 'Idle',
}
const LANE_EMPTY: Record<Lane, string> = {
  'needs-approval': 'Nothing is waiting on your approval.',
  'your-turn': 'Nothing is waiting on an answer from you.',
  working: 'Nothing is running right now.',
  idle: 'Nothing is idle.',
}

// One `dashboard:`-namespaced key, one fetch, all THREE inputs. `toLanes` takes them together, so
// reading them under separate keys would let the view paint a lane split computed from three
// different instants.
const ATTENTION_KEY = 'dashboard:mission-control'

interface Attention {
  items: InboxItem[]
  approvals: PendingApproval[]
  activity: SessionActivity[]
}

/** ── WHY A THIRD SOURCE ───────────────────────────────────────────────────────────────────────
 *
 *  "Working" is NOT derivable from the attention store. `InboxItemStatus`' lifecycle is
 *  pending → seen → handled | dismissed: two open states, two closed, and no in-flight one. The
 *  single candidate, `sent`, is an enum member nobody writes. Keying the lane off it would have
 *  shipped a permanently empty lane wearing a confident label — which is strictly worse than no
 *  lane, because an empty "Working" asserts that nothing is running.
 *
 *  So running work is observed where it actually exists: the live chat sessions.
 *  `laneFor` never returns `'working'`, so this argument is the ONLY path to that lane.
 *
 *  🔴 MEASURED WIRE-vs-TYPE GAP. `GET /api/chat/sessions` serializes each live session through
 *  `ChatSession.to_dict()` (`dashboard/state.py:711`), which DOES carry `stopping` — but the
 *  frontend's `ChatSessionSummary` declares neither `stopping` nor `pending_approval`, and types
 *  `running` as optional. Two shapes of one entity disagreeing about three fields, the same defect
 *  `ChatSession.last_ts` carries a warning about. Normalizing here rather than widening the shared
 *  type keeps this inside one file; absent ⇒ `false` is the right default either way, because the
 *  fields are missing exactly for the disk-only sessions, which are by definition not running. */
export interface SessionActivity {
  key: string
  title: string
  running: boolean
  stopping: boolean
  pending_approval: boolean
}

function activityOf(s: ChatSessionSummary): SessionActivity {
  const wire = s as ChatSessionSummary & { stopping?: boolean; pending_approval?: boolean }
  return {
    key: s.key,
    title: s.title,
    running: Boolean(s.running),
    stopping: Boolean(wire.stopping),
    pending_approval: Boolean(wire.pending_approval),
  }
}

/** Deliberately NOT `.catch(() => [])` on any leg. A swallowed read failure paints four empty
 *  lanes, and four empty lanes is this surface's "all clear" — the single most misleading thing it
 *  could show. The error reaches `useQuery` and the view says it could not load.
 *
 *  The three lists are handed to `toLanes` UNMERGED, and that is a correctness requirement rather
 *  than a stylistic one: `chat_runner._mirror_approval_to_inbox()` raises an `agent_request` inbox
 *  row for any approval that outlives its prompt, carrying `refs.approval = <request_id>` — and
 *  `PendingApproval.id` IS that id. So a pending approval is already on the wire TWICE.
 *  De-duplicating it is `toLanes`' job (it drops the mirror when the approval is in the same
 *  snapshot and KEEPS it when it is not, because losing a row is worse than a stale one).
 *  Concatenating the lists here would double-count every mirrored approval and make each lane's
 *  count a lie. */
async function readAttention(): Promise<Attention> {
  const [items, approvals, sessions] = await Promise.all([
    api.inboxPending(),
    api.approvals(),
    api.chatSessions(),
  ])
  return { items, approvals, activity: sessions.map(activityOf) }
}

/** What answering a parked run needs, read off the inbox row's free-form `refs`.
 *
 *  🔑 THE WIRE DOES CARRY THE OPTIONS. `workflows/needs_input.card_refs()` puts the whole
 *  `NeedsInputItem` under `refs.needs_input`, including `choices[]` and `resume_token`, so a
 *  question's options are real data and not something this view has to invent. `InboxItem.refs`
 *  is typed `Record<string, any>` on purpose (the inbox is a general attention store shared with
 *  channel messages), which is why the narrowing happens here rather than in `lib/api`.
 *
 *  `choices` MAY be empty for a genuine question — a freeform gate. That is reported as what it
 *  is (see `QuestionActions`) rather than papered over with a text box this surface cannot
 *  honestly submit. */
export interface CardQuestion {
  runId: string
  nodeId: string
  resumeToken: string
  prompt: string
  choices: string[]
}

export function questionOf(item: InboxItem | null | undefined): CardQuestion | null {
  const refs = item?.refs
  if (!refs || typeof refs !== 'object') return null
  const payload = refs.needs_input
  if (!payload || typeof payload !== 'object') return null
  const runId = String(payload.run_id ?? refs.workflow ?? '')
  if (!runId) return null // no run to resume ⇒ nothing this card could unblock
  const choices = (Array.isArray(payload.choices) ? payload.choices : [])
    .filter((c: unknown): c is string => typeof c === 'string' && c.length > 0)
  return {
    runId,
    nodeId: String(payload.node_id ?? refs.workflow_node ?? ''),
    resumeToken: String(payload.resume_token ?? refs.resume_token ?? ''),
    prompt: String(payload.blocker ?? item?.message ?? ''),
    choices,
  }
}

// ── Per-card outcome ────────────────────────────────────────────────────────────────────────
// Card state is keyed by card id and lives HERE rather than inside the card, because the card
// is remounted by every revalidation: local state in the leaf would forget that the last
// approve failed the moment the poll came back with the item still pending.
type Outcome =
  | { state: 'busy' }
  | { state: 'done'; text: string }
  | { state: 'failed'; text: string }

/** The gateway's own sentence, plus what the user can do about it. The server text comes first
 *  and verbatim: it is the only part that says WHICH knob to turn, and a paraphrase ("Something
 *  went wrong") is why a user re-clicks a destructive action. */
function failureText(verb: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  return `Could not ${verb}: ${detail}. Nothing was recorded — it still needs you, so try again.`
}

export function MissionControl() {
  const { data, error, loading, refresh } = useQuery<Attention>(ATTENTION_KEY, readAttention)
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})

  const items = data?.items ?? []
  const approvals = data?.approvals ?? []
  const activity = data?.activity ?? []
  // The sibling owns the split. This view never classifies an item itself — see the header note.
  const lanes = useMemo(() => toLanes(items, approvals, activity), [items, approvals, activity])

  const mark = useCallback((id: string, o: Outcome) => {
    setOutcomes((prev) => ({ ...prev, [id]: o }))
  }, [])

  const resolve = useCallback(
    (cardId: string, approvalId: string, action: 'approve' | 'reject') => {
      mark(cardId, { state: 'busy' })
      api
        .resolveApproval(approvalId, action)
        .then(() => {
          mark(cardId, { state: 'done', text: action === 'approve' ? 'Approved.' : 'Rejected.' })
          refresh()
        })
        .catch((err) => mark(cardId, { state: 'failed', text: failureText(`${action} this`, err) }))
    },
    [mark, refresh],
  )

  const answer = useCallback(
    (cardId: string, q: CardQuestion, choice: string) => {
      mark(cardId, { state: 'busy' })
      api
        .resumeWorkflowRun(q.runId, { answer: choice, resume_token: q.resumeToken || undefined })
        .then(() => {
          mark(cardId, { state: 'done', text: `Answered “${choice}” — the run is moving again.` })
          refresh()
        })
        .catch((err) => mark(cardId, { state: 'failed', text: failureText('send that answer', err) }))
    },
    [mark, refresh],
  )

  return (
    <section aria-labelledby="mission-control-title" className="flex min-w-0 flex-col gap-l">
      <div className="flex min-w-0 flex-col gap-xs">
        <h2 id="mission-control-title" data-type="title-m" className="text-on-surface">
          Mission Control
        </h2>
        <p data-type="body-s" className="text-on-surface-low">
          Everything wanting your attention, in the order it wants it. Approve, reject, and answer
          from here — you do not have to open the run.
        </p>
      </div>

      {/* A read failure is stated, never rendered as four empty lanes. `role="alert"` because it
          arrives after first paint and a user who has already looked away must be told. */}
      {error ? (
        <div
          role="alert"
          className="flex min-w-0 items-start gap-s rounded-lg border border-error/40 bg-error/10 p-s text-on-surface"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
          <div className="flex min-w-0 flex-col gap-xs">
            <p data-type="body-s">
              {failureText('load what needs your attention', error)}
            </p>
            <Button size="xs" variant="ghost-accent" onClick={refresh}>
              Try again
            </Button>
          </div>
        </div>
      ) : null}

      {LANES.map((lane) => (
        <AttentionLaneSection
          key={lane}
          lane={lane}
          cards={lanes[lane] ?? []}
          loading={loading}
          outcomes={outcomes}
          onResolve={resolve}
          onAnswer={answer}
        />
      ))}
    </section>
  )
}

// The `LaneCard` fields this view reads. Kept as a structural parameter rather than an import of
// the sibling's type so the two files share exactly one contract (`toLanes`) and this one names
// what it consumes: an id, something to call the card, and the origin object each verb needs.
type ConsumedCard = {
  id: string
  title?: string
  detail?: string
  approval?: PendingApproval | null
  item?: InboxItem | null
}

function AttentionLaneSection({
  lane,
  cards,
  loading,
  outcomes,
  onResolve,
  onAnswer,
}: {
  lane: Lane
  cards: ConsumedCard[]
  loading: boolean
  outcomes: Record<string, Outcome>
  onResolve: (cardId: string, approvalId: string, action: 'approve' | 'reject') => void
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  const headingId = `mission-control-lane-${lane}`
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        {/* h2, matching this directory's section idiom (`PinnedTiles`' "Pinned" and DashboardPage's
            `Section`), which makes the live tree `H1 › H2 › H2 …` — flat but skip-free, so
            heading-order holds. h3 is the tag strict nesting under the view title would call for,
            and `pages/discover/discoverHeadingLevel.test.ts` holds a CLOSED inventory of the files
            allowed to use one (all panel-level); joining it requires re-doing the classification
            comment there, which is outside this change. Promote both rungs together or neither. */}
        <h2 id={headingId} data-type="label-l" className="text-on-surface-var">
          {LANE_LABEL[lane]}
        </h2>
        {/* The count is in the heading row's TEXT, not a coloured pip: "how many" is the first
            thing a user wants from a lane and the last thing a colour can say. */}
        <span data-type="label-s" className="text-on-surface-low">
          {cards.length}
        </span>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {cards.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          {loading ? 'Loading…' : LANE_EMPTY[lane]}
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-s">
          {cards.map((c) => (
            <li key={c.id} className="min-w-0">
              <AttentionCard
                card={c}
                outcome={outcomes[c.id]}
                onResolve={onResolve}
                onAnswer={onAnswer}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function AttentionCard({
  card,
  outcome,
  onResolve,
  onAnswer,
}: {
  card: ConsumedCard
  outcome: Outcome | undefined
  onResolve: (cardId: string, approvalId: string, action: 'approve' | 'reject') => void
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  const approval = card.approval ?? null
  const question = questionOf(card.item)
  // ONE subject string feeds every control's accessible name on this card, capped by the shared
  // `rowSubject` rule. "Approve" alone is ambiguous the moment two cards are on screen — and this
  // view guarantees four lanes of them — so each name carries what it acts on.
  const subject = rowSubject([
    card.title,
    approval?.tool,
    approval?.session,
    card.item?.channel_name,
    card.detail,
  ])
  const resolved = outcome?.state === 'done'
  const busy = outcome?.state === 'busy'

  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s">
      <p data-type="label-m" className="min-w-0 truncate text-on-surface-var">
        {card.title || subject || card.id}
      </p>
      {card.detail ? (
        <p data-type="body-s" className="min-w-0 text-on-surface-low">
          {card.detail}
        </p>
      ) : null}
      {question?.prompt ? (
        <p data-type="body-s" className="min-w-0 text-on-surface">
          {question.prompt}
        </p>
      ) : null}

      {/* The outcome, in words, in a live region. A resolved card that only changed colour is a
          card a screen-reader user cannot tell from a pending one — and the colour is also the
          only thing stopping a sighted user from clicking approve twice. */}
      {outcome?.state === 'done' ? (
        <p
          role="status"
          data-type="body-s"
          className="flex min-w-0 items-center gap-xs text-on-surface-var"
        >
          <CheckCircle2 size={14} className="shrink-0 text-success" aria-hidden="true" />
          {outcome.text}
        </p>
      ) : null}
      {outcome?.state === 'failed' ? (
        <p role="alert" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
          {outcome.text}
        </p>
      ) : null}

      {/* Resolved ⇒ the verbs are GONE, not disabled. A disabled approve on a resolved card is
          still an invitation to try, and the second attempt on an already-resolved id is a real
          action. A FAILED card keeps its verbs: retrying is the whole point of being told. */}
      {resolved ? null : (
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          {approval ? (
            <>
              <Button
                size="xs"
                variant="primary"
                loading={busy}
                disabled={busy}
                ariaLabel={`Approve ${subject}`}
                onClick={() => onResolve(card.id, approval.id, 'approve')}
              >
                <Check size={13} aria-hidden="true" /> Approve
              </Button>
              <Button
                size="xs"
                variant="secondary"
                disabled={busy}
                ariaLabel={`Reject ${subject}`}
                onClick={() => onResolve(card.id, approval.id, 'reject')}
              >
                <X size={13} aria-hidden="true" /> Reject
              </Button>
            </>
          ) : null}

          {question ? (
            <QuestionActions
              card={card}
              question={question}
              subject={subject}
              busy={busy}
              onAnswer={onAnswer}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}

/** A parked run's options, as buttons — one click per option, which is the whole reason this
 *  card exists instead of a link to the run.
 *
 *  A question with NO choices is stated plainly. The wire carries `choices[]` and often fills it,
 *  but a freeform gate legitimately has none, and a text box here would be a control this surface
 *  cannot honestly submit: `resumeWorkflowRun`'s `answer` would carry prose the run's gate never
 *  offered. So the card says where to answer it instead of pretending it can. */
function QuestionActions({
  card,
  question,
  subject,
  busy,
  onAnswer,
}: {
  card: ConsumedCard
  question: CardQuestion
  subject: string
  busy: boolean
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  if (question.choices.length === 0) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        This question has no preset options — open the run to answer it in your own words.
      </p>
    )
  }
  return (
    <>
      {question.choices.map((choice) => (
        <Button
          key={choice}
          size="xs"
          variant="tonal"
          disabled={busy}
          ariaLabel={`Answer ${subject} — ${choice}`}
          onClick={() => onAnswer(card.id, question, choice)}
        >
          {choice}
        </Button>
      ))}
    </>
  )
}
