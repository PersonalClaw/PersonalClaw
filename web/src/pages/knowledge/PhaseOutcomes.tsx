import { RefreshCw } from 'lucide-react'
import type { KnowledgeIngestGraph, PhaseOutcome } from '../../lib/api'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'

/** What each step status is called where a person reads it. */
export const OUTCOME_WORDS: Record<PhaseOutcome['status'], string> = {
  done: 'done',
  failed: 'failed',
  skipped: 'skipped',
  not_applicable: 'not needed',
}

/** A step as the item page names it: the label the graph read serves, else the node type spelled
 *  out (`document_read` → "Document read"). */
export function stepLabel(graph: KnowledgeIngestGraph, nodeType: string): string {
  const served = graph.nodes.find((n) => n.node_type === nodeType)?.label
  if (served) return served
  const spoken = nodeType.replace(/_/g, ' ')
  return spoken.charAt(0).toUpperCase() + spoken.slice(1)
}

/** One step's outcome in a sentence: "Vision: skipped — No image model is set up." — for a
 *  tooltip or an accessible name, where the fix cannot be a link. */
export function outcomeSentence(label: string, outcome: PhaseOutcome): string {
  const word = OUTCOME_WORDS[outcome.status] ?? outcome.status
  return `${label}: ${word}${outcome.reason ? ` — ${outcome.reason}` : ''}`
}

/** A fix is a link only when it names a route inside the app; anything else stays words, so a
 *  record can never put a link to somewhere else on the page. */
function inApp(href?: string): href is string {
  return !!href && href.startsWith('#/')
}

const lowerFirst = (s: string) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s)

/** The steps of one ingest that did not run — each skipped or failed step's own line: its name,
 *  what became of it, why, and the fix (a link to where it is done). A step that does not apply to
 *  this item gets no line; its reason is on its dot in the graph above. It offers to run the item
 *  again when what a skipped step needed is there now (`ready`), or when a step ran out of time
 *  before it finished (`retry`), which another run may give it. */
export function PhaseOutcomes({ graph, phases, onRunAgain, running }: {
  graph: KnowledgeIngestGraph
  phases: Record<string, PhaseOutcome>
  onRunAgain?: () => void
  running?: boolean
}) {
  const lines = graph.nodes
    .map((n) => ({ nodeType: n.node_type, outcome: phases[n.node_type] }))
    .filter(({ outcome }) => outcome?.status === 'skipped' || outcome?.status === 'failed')
  if (!lines.length) return null
  const ready = lines.some(({ outcome }) => outcome.status === 'skipped' && outcome.ready)
  const outOfTime = lines.some(({ outcome }) => outcome.status === 'failed' && outcome.retry)
  return (
    <div className="basis-full">
      <ul aria-label="Steps that did not run" className="flex flex-col gap-xs">
        {lines.map(({ nodeType, outcome }) => (
          <li key={nodeType} data-type="caption" className="text-on-surface-low">
            <span style={{ color: outcome.status === 'failed' ? 'var(--color-danger)' : 'var(--color-on-surface-var)' }}>
              {stepLabel(graph, nodeType)} {OUTCOME_WORDS[outcome.status]}:
            </span>
            {outcome.reason ? <>{' '}{outcome.reason}</> : null}
            {(outcome.fix ?? []).map((fix, i) => (
              <span key={`${fix.text}-${i}`}>
                {i === 0 ? ' ' : ', or '}
                {inApp(fix.href)
                  ? <TextLink href={fix.href} ink="emphasis" className="underline">{i === 0 ? fix.text : lowerFirst(fix.text)}</TextLink>
                  : (i === 0 ? fix.text : lowerFirst(fix.text))}
              </span>
            ))}
          </li>
        ))}
      </ul>
      {(ready || outOfTime) && onRunAgain && (
        <p data-type="caption" className="mt-xs flex flex-wrap items-center gap-s text-on-surface-var">
          {outOfTime && <span>A step ran out of time before it finished.</span>}
          {ready && <span>What a skipped step needed is set up now.</span>}
          <Button variant="tonal" size="xs" onClick={onRunAgain} loading={running} loadingLabel="Running again…">
            <RefreshCw size={11} aria-hidden /> Run again
          </Button>
        </p>
      )}
    </div>
  )
}
