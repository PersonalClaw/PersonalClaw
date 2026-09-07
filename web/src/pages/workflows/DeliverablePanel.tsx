import { useCallback, useEffect, useState } from 'react'
import { FileText, NotebookPen } from 'lucide-react'
import { Segmented } from '../../ui/Segmented'
import { FormSkeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { Markdown } from '../../ui/Markdown'
import { api, type WorkflowDeliverableAbsence, type WorkflowDeliverableDoc, type WorkflowRunDeliverable } from '../../lib/api'

/** The run's DOCUMENT deliverable and working log (PP-16 unit 1).
 *
 *  The loop cockpit has had this since loops shipped — `OutputsPanel`'s Deliverable tab over
 *  `GET /api/loops/{id}/report` — and a run detail had nothing: of the 26 run routes, none served a
 *  document. The closest, Artifacts, answers a different question (what a run PUBLISHED; this is the
 *  document its worker maintains in place). So this is the run-side half, in the run BODY rather than
 *  a drawer, matching where the loop puts it and making the common case — no document yet — visible
 *  without a click.
 *
 *  **The filename is not decided here.** The backend derives it by asking each loop kind's own
 *  `deliverable_name`, so a goal run says `REPORT.md`, a monitor run `MONITOR_LOG.md` and a design
 *  run `DESIGN.md` because those kinds say so. This component RENDERS the name and its provenance;
 *  it does not know the mapping, which is the point — a kind that renames its document renames it
 *  here with no edit.
 *
 *  **ABSENT IS NOT AN EMPTY DOCUMENT, and absent gets NAMED.** Five different facts arrive as "no
 *  content", and rendering one blank panel for all five is the bug this file exists to prevent: a
 *  verifiable goal produces a passing check and is FINISHED with no document, while a young open-ended
 *  goal simply has not written yet. `ABSENT_COPY` is exported so the test asserts the sentence
 *  rendered rather than trusting that it was.
 *
 *  **`instructed: false` is the honest twist, and it is measured.** No bundled template names its
 *  kind's document anywhere in its spec today — the loop side's BRIEF names it (goal.build_brief
 *  writes it into the DoD and the cycle nudge), the template side's prompts do not. So an absent
 *  REPORT.md on the run side is usually a template gap, not a slow worker, and telling a user "not
 *  written yet" would send them to wait for something that is never coming.
 *
 *  **No cost figure, deliberately.** Issue #2566: a loop's ledger carries no money keys, so the
 *  shared totals read `$0.00` for a loop-backed run — and PP-16 is what sends loop-backed runs through
 *  this surface. The backend serves no money field here rather than one that would be a lie. */

/** One sentence per named absence. Keyed by the wire vocabulary so a new backend reason surfaces as a
 *  missing key at type-check time rather than as a blank panel at runtime. */
export const ABSENT_COPY: Record<WorkflowDeliverableAbsence, string> = {
  template_unknown:
    'This template is not one of the five loop kinds, so nothing declares a document name for it. Unknown, not absent — a bespoke template may well produce a document under a name only it knows.',
  kind_has_no_document:
    'This kind produces no document — the passing check or the diff IS the output. Nothing is missing here.',
  not_written: 'The worker has not written it yet.',
  no_root: 'This run has no directory to read from — it was never launched, or retention has swept it.',
  unreadable: 'The file is there and could not be read. That is a permissions or disk problem, not a slow worker.',
}

/** What a document with no content shows in place of a body. Exported so the absent case is asserted
 *  on the rendered text and not inferred from the absence of a document. */
export function absentCopy(doc: WorkflowDeliverableDoc): string {
  return doc.absent_reason ? ABSENT_COPY[doc.absent_reason] : 'Nothing to show.'
}

/** A document reading surface — an elevated page so long-form output reads as a finished document
 *  rather than raw markdown flush against the container. The same treatment the loop cockpit's
 *  `DocSurface` gives its Deliverable tab, so the two sides of PP-16 read alike. */
function DocSurface({ children }: { children: string }) {
  return (
    <div className="rounded-xl bg-surface px-2xl py-xl ring-1 ring-outline-variant/30">
      <Markdown>{children}</Markdown>
    </div>
  )
}

function Absent({ doc, instructed }: { doc: WorkflowDeliverableDoc; instructed: boolean | null }) {
  return (
    <div className="flex flex-col gap-xs rounded-xl bg-surface-high p-l">
      <p data-type="body-s" className="text-on-surface-low">{absentCopy(doc)}</p>
      {/* Only where it CHANGES the reading: a document the template never asks for will not arrive by
          waiting, and that is a different instruction to the user than "check back later". */}
      {doc.absent_reason === 'not_written' && instructed === false && (
        <p data-type="caption" className="text-on-surface-low">
          This run’s template never names <span className="font-mono">{doc.name}</span>, so nothing has
          asked the worker to write one. Waiting will not produce it.
        </p>
      )}
    </div>
  )
}

/** The name a slot was looked for under, or an explicit dash. Never blank: a missing label reads as a
 *  rendering bug, where “—” reads as the answer it is. */
function slotLabel(prefix: string, doc: WorkflowDeliverableDoc): string {
  return `${prefix} · ${doc.name ?? '—'}`
}

export function DeliverablePanel({ runId }: { runId: string }) {
  const [data, setData] = useState<WorkflowRunDeliverable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [slot, setSlot] = useState<'report' | 'log'>('report')

  const load = useCallback(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunDeliverable(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run’s document') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  useEffect(() => load(), [load])

  // The shaped primitive rather than a bare `<Skeleton />`: that atom with no className is a 0px
  // invisible div, which renders as broken rather than as loading, and it is `aria-hidden` by design
  // so it cannot carry the `role="status"` announcement either. No `what` noun — `loadingNounPairing`
  // only accepts one sourced from a declaration it can verify, and this panel reports failure through
  // `InlineError`, so there is no declared noun to borrow.
  if (loading) return <FormSkeleton sections={1} rows={3} title={false} />
  if (error) return <InlineError onRetry={load}>{error}</InlineError>
  if (!data) return null

  const doc = slot === 'report' ? data.report : data.log
  const declared = data.derivation.declared_by

  return (
    <section className="flex flex-col gap-s" aria-labelledby="run-deliverable-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-s">
        {/* h2, one rung under the run's PageTitle h1. The size comes from `data-type`, never the tag,
            so the rung is an outline decision and not a visual one. */}
        <h2 id="run-deliverable-heading" data-type="label-l" className="flex items-center gap-xs text-on-surface-var">
          <FileText size={14} aria-hidden /> Document
        </h2>
        {/* WHERE the filename came from. A name with no provenance is a claim; naming the kind and
            variant that declared it makes it a reading of the alias table. */}
        {declared && (
          <p data-type="caption" className="text-on-surface-low">
            <span className="font-mono">{declared.name || '—'}</span>
            {' — declared by the '}
            <span className="font-mono">{declared.kind}</span>
            {declared.variant ? <> kind’s <span className="font-mono">{declared.variant}</span> variant</> : ' kind'}
          </p>
        )}
      </div>

      <Segmented
        ariaLabel="Document"
        value={slot}
        onChange={(v) => setSlot(v as 'report' | 'log')}
        options={[
          { key: 'report', label: slotLabel('Deliverable', data.report) },
          { key: 'log', label: slotLabel('Log', data.log) },
        ]}
      />

      {doc.present && doc.content !== null ? (
        <>
          <DocSurface>{doc.content}</DocSurface>
          <p data-type="caption" className="text-on-surface-low">
            {doc.bytes?.toLocaleString()} bytes, read from this run’s{' '}
            {doc.found_in === 'workspace' ? 'workspace' : 'run directory'}
            {doc.truncated && ' · truncated for display — open the file for the rest'}
            {/* A clipped blob is REPORTED for the same reason truncation is: a document that
                silently loses a chunk is indistinguishable from one that never had it. */}
            {doc.clipped_blobs > 0 &&
              ` · ${doc.clipped_blobs} blob${doc.clipped_blobs === 1 ? '' : 's'} clipped (a run of 512+ characters with no whitespace is not prose)`}
          </p>
        </>
      ) : (
        <Absent doc={doc} instructed={data.instructed} />
      )}

      {/* Where the backend looked, so "not written" is checkable rather than a claim. Only when
          something is missing — on a document that rendered, the path is noise. */}
      {!doc.present && data.roots.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          Looked in: {data.roots.map((r) => `${r.path}${r.exists ? '' : ' (missing)'}`).join(', ')}
        </p>
      )}

      {/* The other slot's headline, so a user who lands on an absent deliverable learns the log exists
          without clicking to find out. */}
      {!doc.present && slot === 'report' && data.log.present && (
        <p data-type="caption" className="flex items-center gap-xs text-on-surface-low">
          <NotebookPen size={12} aria-hidden /> The working log has content — switch to Log.
        </p>
      )}
    </section>
  )
}
