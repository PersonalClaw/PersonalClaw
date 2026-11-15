import { useState, type ReactNode } from 'react'
import { api, hasApiCode, type LearningProposalReview, type SkillProposalDetail } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { clockTime } from '../../../lib/epoch'
import type { NeedsYouRow } from '../../../lib/needsYou'
import { learningCardOf } from '../../../lib/needsYouKinds'
import { Eyebrow } from '../../../ui/Eyebrow'
import { InlineLoadError, ListSkeleton } from '../../../ui/ListScaffold'
import { Markdown } from '../../../ui/Markdown'
import { MarkdownLine } from '../../../ui/MarkdownLine'
import { QuietButton } from '../../../ui/QuietButton'
import { TextLink } from '../../../ui/TextLink'
import { UnifiedDiff } from '../../../ui/UnifiedDiff'
import {
  CardOutcome, ConversationTail, DECIDE_BAR, ENDED_ELSEWHERE, LEARNING_OFF, LearningAnswers, NeedDetail, SkillAnswers,
  needSubject, type CardActions, type Outcome,
} from '../NeedCards'

// ── Needs you's panel: the thing itself ─────────────────────────────────────────────────────────
//
// The panel beside a card is what the card is about, drawn only from what its producer wrote: the
// thing itself (a template's steps, a run's failure, a skill's change), why it was proposed, where
// it came from, its facts, and what Accept does in a decide bar at its foot. It never draws the card
// again: the kind and the thing's name are the pane's header (`NeedTagRow`), the headline its `h2`.
//
// Each part is a plain `<section>` under an `Eyebrow as="h3"`, drawn only with content; a block of
// content sits one surface step down and folds in place past 40 lines, so nothing scrolls inside
// the pane. The decide bar sticks to the pane's foot while the pane is at least 32rem tall (a size
// query on the scroller, so 200% text turns it off) and is the body's last child otherwise.

/** Lines a block shows before it folds. */
export const FOLD_LINES = 40

/** Whether the row's panel is its own body (a proposal's read), whose header is its tag row; what
 *  waits keeps its detail (`NeedDetail`), which draws the card's parts, until its body is here. */
export function panelOwnsBody(_row: NeedsYouRow): boolean {
  return true
}

/** The selected row's panel body: a proposal's own read, or, for what waits, the ask's detail. */
export function NeedPanel({ row, outcome, actions, now }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
}) {
  const card = learningCardOf(row)
  if (row.origin === 'note' && typeof row.note.refs?.learning_proposal === 'string' && actions.learning) {
    return <LearningPanel row={row} pid={String(row.note.refs.learning_proposal)} card={card} outcome={outcome} actions={actions} />
  }
  if (row.origin === 'proposal') return <SkillPanel row={row} outcome={outcome} actions={actions} />
  return <NeedDetail row={row} outcome={outcome} actions={actions} now={now} />
}

/** One part of the panel, under its own `h3`, drawn only with content. */
function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-s">
      <Eyebrow as="h3">{title}</Eyebrow>
      {children}
    </section>
  )
}

/** A block of content one surface step down, folding in place past `FOLD_LINES`: its whole text
 *  stays in the DOM once shown, never behind a hover. */
export function Folded({ text, mono = false, label }: { text: string; mono?: boolean; label: string }) {
  const lines = text.split('\n')
  const [all, setAll] = useState(false)
  const cut = lines.length > FOLD_LINES && !all
  const shown = cut ? lines.slice(0, FOLD_LINES).join('\n') : text
  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
      {mono ? (
        <pre aria-label={label} data-type="body-s" className="min-w-0 whitespace-pre-wrap font-mono text-on-surface [overflow-wrap:anywhere]">{shown}</pre>
      ) : (
        <div data-type="body-m" className="min-w-0 max-w-[70ch] text-on-surface"><Markdown>{shown}</Markdown></div>
      )}
      {lines.length > FOLD_LINES ? (
        <span className="self-start">
          <QuietButton ariaExpanded={all} onClick={() => setAll((v) => !v)}>
            {all ? 'Show less' : `Show all ${lines.length} lines`}
          </QuietButton>
        </span>
      ) : null}
    </div>
  )
}

/** The panel's facts: terms in `on-surface-low`, values in `on-surface`, so a label ranks below its
 *  value in both modes. */
function Facts({ items }: { items: [string, string][] }) {
  const shown = items.filter(([, v]) => v)
  if (shown.length === 0) return null
  return (
    <dl data-type="body-s" className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs">
      {shown.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-on-surface-low">{term}</dt>
          <dd className="text-on-surface">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The decide bar: what Accept does, and the verbs. Sticky at the pane's foot while the pane is
 *  32rem tall or more; `ScrollFade` lays its bottom fade on the bar's top edge while it is stuck. */
export function DecideBar({ sentence, children }: { sentence?: string; children: ReactNode }) {
  return (
    <div data-decide-bar="" className={`${DECIDE_BAR} gap-s`}>
      {sentence ? <p data-type="body-s" className="max-w-[70ch] text-on-surface">{sentence}</p> : null}
      <div className="flex min-w-0 flex-wrap items-start gap-s [&_button]:whitespace-normal">{children}</div>
    </div>
  )
}

/** When a time is, on the reader's clock. */
const when = (iso: string | undefined | null) => (iso ? clockTime(iso) : '')

/** A chat key's address in the app, when it names a chat. */
function chatRoute(sessionKey: string): string {
  const key = sessionKey.replace(/^dashboard:/, '')
  return key && !key.includes(':') ? `#/chat/${encodeURIComponent(key)}` : ''
}

// ── A learning proposal ───────────────────────────────────────────────────────────────────────

function LearningPanel({ row, pid, card, outcome, actions }: {
  row: Extract<NeedsYouRow, { origin: 'note' }>
  pid: string
  card: Record<string, unknown> | null
  outcome: Outcome | undefined
  actions: CardActions
}) {
  const { data: review, error, loading, refresh } = useQuery(`learning:review:${pid}`, async () => api.learningProposalReview(pid), { persist: false })
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  const subject = needSubject(row)
  if (hasApiCode(error, 'learning_disabled')) {
    return (
      <p data-type="body-m" className="text-on-surface">
        {LEARNING_OFF} <TextLink href="#/learning" ink="emphasis">Turn learning on in Learning</TextLink>
      </p>
    )
  }
  if (hasApiCode(error, 'learning_proposal_gone')) return <p data-type="body-m" className="text-on-surface">{ENDED_ELSEWHERE}</p>
  const bar = settled ? <CardOutcome outcome={outcome} /> : (
    <DecideBar sentence={review?.accept.sentence}>
      <LearningAnswers pid={pid} card={card} subject={subject} busy={outcome?.state === 'busy'} inBar
        onAnswer={(action, done) => actions.learning?.(row.key, pid, action, done)} />
    </DecideBar>
  )
  if (loading && !review) return <><ListSkeleton rows={3} what="the proposal" />{bar}</>
  if (error && !review) return <><InlineLoadError what="the proposal" error={error} onRetry={refresh} />{bar}</>
  if (!review) return bar
  const chat = chatRoute(review.session_key)
  const empty = isEmpty(review.content)
  return (
    <div className="@container flex min-w-0 flex-col gap-l">
      {empty ? (
        <p data-type="body-m" className="text-on-surface">The proposer left this empty; reject it.</p>
      ) : (
        <Part title={THING[review.content.type] ?? 'What it proposes'}><ProposalContent pid={pid} content={review.content} /></Part>
      )}
      {review.why ? (
        <Part title="Why it was proposed">
          <p data-type="body-m" className="max-w-[70ch] text-on-surface">The agent says: {review.why}</p>
        </Part>
      ) : null}
      {chat || review.runs.length || review.source_gone ? (
        <Part title="Where it came from">
          {review.source_gone ? <p data-type="body-s" className="text-on-surface-var">The chat it came from was deleted.</p> : null}
          {chat && !review.source_gone ? (
            <>
              <TextLink href={chat} ink="emphasis" size="sm">Open the chat</TextLink>
              <Eyebrow as="h4">Up to when it was proposed</Eyebrow>
              <ConversationTail session={review.session_key.replace(/^dashboard:/, '')} before={review.created_at} hosted />
            </>
          ) : null}
          {review.runs.length ? (
            <ul className="flex min-w-0 flex-wrap gap-x-m gap-y-xs">
              {review.runs.map((rid) => (
                <li key={rid}><TextLink href={`#/workflows/runs/${encodeURIComponent(rid)}`} ink="emphasis" size="sm">Run {rid.slice(0, 8)}</TextLink></li>
              ))}
            </ul>
          ) : null}
        </Part>
      ) : null}
      <Facts items={[['Proposed', when(review.created_at)], ['Last seen', review.updated_at !== review.created_at ? when(review.updated_at) : '']]} />
      <TextLink href={`#/learning?open=${encodeURIComponent(pid)}`} ink="emphasis" size="sm">Open in Learning</TextLink>
      {bar}
    </div>
  )
}

/** Each content type's part name. */
const THING: Record<string, string> = {
  steps: 'The steps', plan_shape: 'The pattern', spec: 'The steps', prompt: 'The prompt', agent: 'The agent',
  lesson: 'What went wrong', choice: 'Yours and the learned one', lessons: 'The captures', procedure: 'The procedure',
  ops: 'The changes', items: 'What it would remove', markdown: 'What it proposes',
}

function isEmpty(content: LearningProposalReview['content']): boolean {
  const c = content as Record<string, unknown>
  if (c.type === 'markdown' || c.type === 'procedure') return !String(c.text ?? '').trim()
  if (c.type === 'steps') return !Array.isArray(c.steps) || c.steps.length === 0
  return false
}

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => String(x ?? '')) : [])
const recs = (v: unknown): Record<string, unknown>[] =>
  (Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object') : [])

/** The thing a learning proposal proposes, by its content's type (`learning.review._content`). */
function ProposalContent({ pid, content }: { pid: string; content: LearningProposalReview['content'] }) {
  const c = content as Record<string, unknown>
  switch (c.type) {
    case 'steps':
      return (
        <ol data-type="body-m" className="flex min-w-0 max-w-[70ch] list-decimal flex-col gap-xs pl-l text-on-surface">
          {strs(c.steps).map((s, i) => <li key={i}><MarkdownLine text={s} tooltip={false} /></li>)}
        </ol>
      )
    case 'plan_shape':
      return (
        <>
          <p data-type="body-m" className="max-w-[70ch] text-on-surface">{String(c.sentence ?? '')}</p>
          <p data-type="body-s" className="text-on-surface-var">
            This names a repeated pattern. It has no steps to save, so it can't be accepted. Open the latest run to make a template from it, or reject this.
          </p>
          <ul className="flex min-w-0 flex-wrap gap-x-m gap-y-xs">
            {strs(c.runs).map((rid) => (
              <li key={rid}><TextLink href={`#/workflows/runs/${encodeURIComponent(rid)}`} ink="emphasis" size="sm">Run {rid.slice(0, 8)}</TextLink></li>
            ))}
          </ul>
        </>
      )
    case 'spec':
      return (
        <>
          <ol className="flex min-w-0 flex-col gap-s">
            {recs(c.steps).map((s, i) => (
              <li key={i} className="flex min-w-0 flex-col gap-xs">
                <span data-type="label-m" className="text-on-surface">{String(s.id ?? '')}</span>
                <Folded text={String(s.prompt ?? '')} mono label={`Step ${String(s.id ?? i + 1)}'s prompt`} />
              </li>
            ))}
          </ol>
          <Pasted content={c} />
        </>
      )
    case 'prompt':
      return (
        <>
          <Folded text={String(c.text ?? '')} mono label="The prompt" />
          {strs(c.variables).length ? (
            <p data-type="body-s" className="text-on-surface-var">Uses {strs(c.variables).map((v) => `{{${v}}}`).join(', ')}</p>
          ) : null}
          <Pasted content={c} />
        </>
      )
    case 'agent':
      return (
        <>
          <Folded text={String(c.text ?? '')} mono label="Its system prompt" />
          {c.voice ? <p data-type="body-s" className="text-on-surface-var">Its voice: {String(c.voice)}</p> : null}
          <Pasted content={c} />
        </>
      )
    case 'lesson':
      return (
        <>
          <p data-type="body-m" className="max-w-[70ch] text-on-surface">{String(c.lesson ?? '')}</p>
          {c.capsule ? <Folded text={String(c.capsule)} mono label="How to run it again" /> : null}
          {c.error ? (
            <>
              <Eyebrow as="h4">The error, as the run recorded it</Eyebrow>
              <Folded text={String(c.error)} mono label="The error" />
            </>
          ) : null}
        </>
      )
    case 'choice':
      return (
        <div className="grid min-w-0 gap-m @[600px]:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
            <Eyebrow as="h4">Yours{c.memory ? `, in ${String(c.memory)}` : ''}</Eyebrow>
            {strs(c.yours).map((y, i) => <p key={i} data-type="body-m" className="text-on-surface">{y}</p>)}
          </div>
          <div className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
            <Eyebrow as="h4">Learned{c.who ? `, ${String(c.who)}` : ''}</Eyebrow>
            <p data-type="body-m" className="text-on-surface">{String(c.learned ?? '')}</p>
            {c.negative ? <p data-type="body-s" className="text-on-surface-var">Not: {String(c.negative)}</p> : null}
          </div>
        </div>
      )
    case 'lessons':
      return <Captures pid={pid} captures={recs(c.captures)} total={Number(c.total ?? 0)} />
    case 'procedure':
      return <Folded text={String(c.text ?? '')} label="The procedure" />
    case 'ops':
      return (
        <>
          <ul className="flex min-w-0 flex-col gap-s">
            {recs(c.ops).map((op, i) => (
              <li key={i} className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
                <span data-type="label-m" className="text-on-surface">{opWords(op)}</span>
                {op.fields && typeof op.fields === 'object' ? (
                  <pre data-type="body-s" className="whitespace-pre-wrap font-mono text-on-surface [overflow-wrap:anywhere]">{JSON.stringify(op.fields, null, 2)}</pre>
                ) : null}
              </li>
            ))}
          </ul>
          {strs(c.predicted_fixes).length ? (
            <>
              <Eyebrow as="h4">What it should fix</Eyebrow>
              <ul data-type="body-s" className="list-disc pl-l text-on-surface">{strs(c.predicted_fixes).map((f, i) => <li key={i}>{f}</li>)}</ul>
            </>
          ) : null}
        </>
      )
    case 'items':
      return (
        <ul className="flex min-w-0 flex-col gap-s">
          {recs(c.items).map((it, i) => (
            <li key={i} className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
              <span data-type="label-s" className="text-on-surface-low">{String(it.kind ?? '')}</span>
              <p data-type="body-m" className="text-on-surface">{String(it.text ?? '')}</p>
              {it.source ? <p data-type="body-s" className="text-on-surface-var">The same as in {String(it.source)}</p> : null}
            </li>
          ))}
        </ul>
      )
    default:
      return <Folded text={String(c.text ?? '')} label="What it proposes" />
  }
}

/** A template change's op, in words. */
function opWords(op: Record<string, unknown>): string {
  const kind = String(op.op ?? '')
  const node = op.node_id ? ` ${String(op.node_id)}` : ''
  const verb = kind === 'update_node' ? 'Change step' : kind === 'add_node' ? 'Add step' : kind === 'remove_node' ? 'Remove step' : kind || 'Change'
  return `${verb}${node}`
}

/** What a card pasted as a template, a prompt or an agent was, its fence gone. */
function Pasted({ content }: { content: Record<string, unknown> }) {
  if (!content.pasted) return null
  return (
    <>
      <Eyebrow as="h4">What you pasted</Eyebrow>
      <Folded text={String(content.pasted)} mono label="What you pasted" />
    </>
  )
}

/** A captures batch's captures, each cut at 600 characters with "Show all of it", which reads
 *  that capture whole. A capture no longer kept says so. */
function Captures({ pid, captures, total }: { pid: string; captures: Record<string, unknown>[]; total: number }) {
  const [whole, setWhole] = useState<Record<string, string>>({})
  const show = (uid: string) => {
    void api.learningProposalReview(pid, uid).then((r) => {
      const found = recs((r.content as Record<string, unknown>).captures).find((x) => x.uid === uid)
      if (found) setWhole((w) => ({ ...w, [uid]: String(found.text ?? '') }))
    }).catch(() => {})
  }
  return (
    <>
      <p data-type="body-s" className="text-on-surface-var">These are decided together: a single capture can't be accepted or rejected alone.</p>
      <ul className="flex min-w-0 flex-col gap-s">
        {captures.map((cap) => {
          const uid = String(cap.uid ?? '')
          const source = [cap.day, cap.cadence, cap.kind].filter(Boolean).map(String).join(' · ')
          return (
            <li key={uid} className="flex min-w-0 flex-col gap-xs rounded-md bg-surface-low p-m">
              <p data-type="body-m" className="whitespace-pre-wrap text-on-surface [overflow-wrap:anywhere]">{whole[uid] ?? String(cap.text ?? '')}</p>
              {cap.cut && !whole[uid] ? (
                <span className="self-start"><QuietButton onClick={() => show(uid)}>Show all of it</QuietButton></span>
              ) : null}
              <p data-type="caption" className="text-on-surface-low">{cap.kept ? source : 'This capture is no longer kept'}</p>
            </li>
          )
        })}
      </ul>
      {total > captures.length ? <p data-type="body-s" className="text-on-surface-var">And {total - captures.length} more, in Learning.</p> : null}
    </>
  )
}

// ── A skill proposal ──────────────────────────────────────────────────────────────────────────

function SkillPanel({ row, outcome, actions }: {
  row: Extract<NeedsYouRow, { origin: 'proposal' }>
  outcome: Outcome | undefined
  actions: CardActions
}) {
  const proposal = row.proposal
  const { data: detail, error, loading, refresh } = useQuery<SkillProposalDetail>(
    `skill-proposals:detail:${proposal.id}`, async () => api.skillProposalDetail(proposal.id), { persist: false })
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  const subject = needSubject(row)
  if (hasApiCode(error, 'skill_proposal_gone')) return <p data-type="body-m" className="text-on-surface">{ENDED_ELSEWHERE}</p>
  const lands = proposal.card?.accept.sentence ?? ''
  const bar = settled ? <CardOutcome outcome={outcome} /> : (
    <DecideBar sentence={lands}>
      <SkillAnswers proposal={proposal} subject={subject} busy={outcome?.state === 'busy'} inBar
        onAnswer={(action, done) => actions.proposal(row.key, proposal.id, action, done)} />
    </DecideBar>
  )
  if (loading && !detail) return <><ListSkeleton rows={3} what="the proposal" />{bar}</>
  if (error && !detail) return <><InlineLoadError what="the proposal" error={error} onRetry={refresh} />{bar}</>
  if (!detail) return bar
  const target = proposal.accept_target || detail.refine_target || ''
  const refine = (proposal.kind ?? detail.kind) === 'refine'
  const triggers = (detail.triggers || '').split(',').map((s) => s.trim()).filter(Boolean)
  const chat = chatRoute(detail.session_key)
  return (
    <div className="@container flex min-w-0 flex-col gap-l">
      {refine && detail.diff ? (
        <Part title={`Change to ${target}${detail.version ? `, refinement v${detail.version}` : ''}`}>
          <div className="rounded-md bg-surface-low p-m">
            <UnifiedDiff patch={detail.diff} wrap label={`The change to ${target}`} />
          </div>
        </Part>
      ) : (
        <Part title="The procedure"><Folded text={detail.procedure_md || ''} label="The procedure" /></Part>
      )}
      {triggers.length ? (
        <Part title="It fires on">
          <ul className="flex min-w-0 flex-wrap gap-xs">
            {triggers.map((t) => <li key={t} data-type="label-s" className="rounded-full bg-surface-high px-s py-px text-on-surface">{t}</li>)}
          </ul>
        </Part>
      ) : null}
      {chat || detail.source_excerpt ? (
        <Part title="Where it came from">
          {detail.source_excerpt ? (
            <blockquote data-type="body-m" className="max-w-[70ch] rounded-md bg-surface-low p-m text-on-surface">{detail.source_excerpt}</blockquote>
          ) : null}
          {chat ? (
            <>
              <TextLink href={chat} ink="emphasis" size="sm">Open the chat</TextLink>
              <Eyebrow as="h4">Up to when it was proposed</Eyebrow>
              <ConversationTail session={detail.session_key.replace(/^dashboard:/, '')} before={detail.created_at} hosted />
            </>
          ) : null}
        </Part>
      ) : null}
      <Facts items={[['Proposed', when(detail.created_at)]]} />
      <TextLink href="#/skills?mode=proposals" ink="emphasis" size="sm">Open in Skills</TextLink>
      {bar}
    </div>
  )
}
