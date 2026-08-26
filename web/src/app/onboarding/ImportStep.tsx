import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Check, Loader2, ShieldCheck } from 'lucide-react'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { TextLink } from '../../ui/TextLink'
import { Checkbox } from '../../ui/forms'
import { LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { listItemEnter, spring, stagger } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import {
  api,
  type OnboardingImportReport,
  type OnboardingImportScan,
  type OnboardingImportSource,
} from '../../lib/api'

/** PEP-5 — the onboarding step that brings another local agent tool's setup over.
 *
 *  **The switching cost is the work you already did.** A user arriving from Claude
 *  Code or Codex has written the instructions, the MCP servers and the skills they
 *  care about. This step reads those roots (never writes to them), shows what it
 *  found per category, and imports exactly what is ticked.
 *
 *  **The two selection axes are the wire.** `select_items` in the engine is a cross
 *  product of sources × categories, so the UI is one too: a checkbox per detected
 *  tool and a checkbox per category. Items never travel — the server re-scans and
 *  reads the foreign root itself, because a client-supplied item carries a
 *  filesystem path and would be a way to have any directory copied into the home.
 *
 *  **Nothing is swallowed.** The report is rendered in full: what landed, what was
 *  already ours, every `conflict` with the reason the existing thing was KEPT, every
 *  `rejected` with the floor that refused it, and the counts of credentials withheld.
 *  A POST that fails outright shows the gateway's own sentence in a `role="alert"`
 *  band with a retry — an import that quietly reports 4 successes while a fifth write
 *  raised is the defect this shape exists to make impossible.
 *
 *  **Re-entry is free.** Item identity is a fingerprint of source+category+key and
 *  the importer keeps a ledger of what it wrote, so a second visit marks those items
 *  `already imported` and a re-import answers `existing` rather than duplicating. The
 *  step therefore needs no resume point of its own: redoing it costs nothing.
 *
 *  **Skipping is free too.** Nothing here is required to continue, and a machine with
 *  no other agent tool gets one honest line instead of a dead step. */

/** The closed category vocabulary, in human words. The server sends the raw values
 *  (it owns the enum), so an unmapped one renders as its own name rather than
 *  disappearing — a category the writers gain later is visible the day it ships. */
const CATEGORY_LABEL: Record<string, string> = {
  instructions: 'Instructions',
  memories: 'Memories',
  mcp_servers: 'MCP servers',
  skills: 'Skills',
  settings: 'Settings',
}
/** What each category will actually become here — the destination in plain words, so
 *  ticking a box is an informed choice rather than a guess at a noun. */
const CATEGORY_BLURB: Record<string, string> = {
  instructions: 'Your CLAUDE.md / AGENTS.md conventions, saved as memories.',
  memories: 'Notes the other tool was already remembering for you.',
  mcp_servers: 'MCP server definitions, added to your MCP config.',
  skills: 'Skills, copied in and re-scanned like a Store install.',
  settings: 'Staged for you to review — never merged into live config.',
}

export function labelOfCategory(category: string): string {
  return CATEGORY_LABEL[category] ?? category.replace(/_/g, ' ')
}

/** The one-line summary the collapsed step row shows, built from the report's own
 *  counts. Every non-zero outcome appears: a conflict that only lived inside the
 *  expanded body would vanish the moment the user moved on. */
export function summaryOfReport(report: OnboardingImportReport): string {
  const c = report.counts
  const parts: string[] = []
  if (c.imported) parts.push(`${c.imported} imported`)
  if (c.existing) parts.push(`${c.existing} already there`)
  if (c.conflict) parts.push(`${c.conflict} to review`)
  if (c.rejected) parts.push(`${c.rejected} refused`)
  return parts.length ? parts.join(' · ') : 'Nothing to import'
}

export function ImportStep({ onDone, onSkip }: {
  /** Move on, with the line the collapsed row will carry. */
  onDone: (summary: string) => void
  /** Move on having imported nothing — always available. */
  onSkip: () => void
}) {
  const [scan, setScan] = useState<OnboardingImportScan | null>(null)
  const [scanError, setScanError] = useState<unknown>(null)
  const [pickedSources, setPickedSources] = useState<Record<string, boolean>>({})
  const [pickedCategories, setPickedCategories] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<OnboardingImportReport | null>(null)
  const [failure, setFailure] = useState('')

  const load = useCallback(() => {
    setScan(null)
    setScanError(null)
    api.onboardingImportScan().then((s) => {
      setScan(s)
      // Everything detected starts ticked: the user came here to bring their setup
      // over, and un-ticking is a smaller act than hunting for what to tick.
      setPickedSources(
        Object.fromEntries(s.sources.filter((x) => x.detected).map((x) => [x.source, true])),
      )
      setPickedCategories(Object.fromEntries(s.categories.map((c) => [c, true])))
    }).catch(setScanError)
  }, [])
  useEffect(load, [load])

  const detected = useMemo(
    () => (scan?.sources ?? []).filter((s) => s.detected),
    [scan],
  )
  const chosenSources = detected.filter((s) => pickedSources[s.source])

  /** Per-category totals over the CHOSEN sources — the counts beside each checkbox.
   *  Derived from the items themselves rather than kept alongside them, so the number
   *  and the list it describes cannot disagree. */
  const tally = (category: string) => {
    let total = 0
    let existing = 0
    for (const source of chosenSources) {
      for (const item of source.items) {
        if (item.category !== category) continue
        total += 1
        if (item.existing) existing += 1
      }
    }
    return { total, existing }
  }
  const offered = (scan?.categories ?? []).filter((c) => tally(c).total > 0)
  const chosenCategories = offered.filter((c) => pickedCategories[c])
  const nothingPicked = chosenSources.length === 0 || chosenCategories.length === 0

  const run = useCallback(async () => {
    setBusy(true)
    setFailure('')
    try {
      const result = await api.runOnboardingImport({
        sources: chosenSources.map((s) => s.source),
        categories: chosenCategories,
      })
      setReport(result)
    } catch (e) {
      // The gateway's own sentence, verbatim. `errText` already decided what a user
      // should read; paraphrasing it here would hide which write failed.
      setFailure((e as Error)?.message || 'The import could not be completed.')
    } finally {
      setBusy(false)
    }
  }, [chosenCategories, chosenSources])

  /** What assistive tech is told, out of a polite live region. Every phase this step
   *  passes through is silent otherwise: the scan resolves, the import finishes and
   *  the counts appear with no focus move and no visible text a screen reader would
   *  reach on its own. */
  const announcement = report
    ? `Import finished: ${summaryOfReport(report)}.`
    : busy
      ? 'Importing your setup…'
      : scan === null
        ? ''  // the loading region below carries this phase
        : detected.length === 0
          ? 'No other agent tools were found on this machine.'
          : `Found ${detected.map((s) => s.display_name).join(' and ')}.`

  if (scan === null && scanError) {
    return <LoadError what="detected tools" error={scanError} onRetry={load} />
  }
  if (scan === null) {
    return (
      <div role="status" aria-busy="true" className="flex items-center py-2">
        <LoadingStatus what="detected tools" />
        <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <p role="status" aria-live="polite" className="sr-only">{announcement}</p>

      {report
        ? <Report report={report} onContinue={() => onDone(summaryOfReport(report))} />
        : detected.length === 0
          ? <Nothing looked={scan.sources} onContinue={() => onDone('Nothing to import')} />
          : (
            <>
              <p className="text-on-surface-var text-[0.8125rem]">
                We found another agent tool on this machine. Bring its setup over — it is
                read only over there, nothing is changed in the other tool, and credentials
                are never imported.
              </p>

              <motion.div className="flex flex-col gap-s" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.05) } }}>
                {detected.map((source) => (
                  <SourceCard key={source.source} source={source}
                    picked={!!pickedSources[source.source]}
                    onPick={(v) => setPickedSources((m) => ({ ...m, [source.source]: v }))} />
                ))}
              </motion.div>

              <div className="flex flex-col gap-2">
                <span className="text-on-surface text-[0.8125rem]" style={fvs(550)}>
                  What to bring over
                </span>
                {offered.length === 0
                  ? <p className="text-on-surface-low text-[0.8125rem]">
                      Nothing to bring over from the tools you picked.
                    </p>
                  : offered.map((category) => {
                    const { total, existing } = tally(category)
                    return (
                      <CategoryRow key={category} category={category} total={total}
                        existing={existing} picked={!!pickedCategories[category]}
                        onPick={(v) => setPickedCategories((m) => ({ ...m, [category]: v }))} />
                    )
                  })}
              </div>

              {failure && (
                <div className="flex flex-col gap-2">
                  <InlineError icon multiline>{failure}</InlineError>
                  <p className="text-on-surface-low text-[0.75rem]">
                    Whatever already landed was recorded, so importing again brings over only
                    what is still missing.
                  </p>
                </div>
              )}

              <div className="flex items-center gap-m">
                <Button variant="primary" size="md" loading={busy}
                  disabled={nothingPicked}
                  disabledReason="Pick a tool and at least one thing to bring over"
                  onClick={run}>
                  {failure ? 'Try again' : 'Import selected'}
                  <ArrowRight size={16} aria-hidden="true" />
                </Button>
                <TextLink onClick={onSkip}>Skip this</TextLink>
              </div>
            </>
          )}
    </div>
  )
}

/** A machine with nothing to adopt. It still says what it looked for: "we found
 *  nothing" is only trustworthy if you know where it looked. */
function Nothing({ looked, onContinue }: {
  looked: OnboardingImportSource[]
  onContinue: () => void
}) {
  return (
    <div className="flex flex-col gap-l">
      <p className="text-on-surface-var text-[0.8125rem]">
        No other agent tools found on this machine. We looked for{' '}
        {looked.map((s) => s.display_name).join(' and ')} — if you install one later, you can
        import from it any time.
      </p>
      <div>
        <Button variant="primary" size="md" onClick={onContinue}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

/** One detected tool: what it is, where it lives, and what it is holding. */
function SourceCard({ source, picked, onPick }: {
  source: OnboardingImportSource
  picked: boolean
  onPick: (v: boolean) => void
}) {
  const total = source.items.length
  return (
    <motion.div variants={listItemEnter} layout transition={spring.spatialFast}
      className="flex items-start gap-2 rounded-lg bg-surface-high p-3">
      <Checkbox checked={picked} onChange={onPick} className="mt-0.5"
        ariaLabel={`Import from ${source.display_name}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-on-surface text-[0.875rem]" style={fvs(550)}>{source.display_name}</span>
          <span className="text-on-surface-low text-[0.75rem]">
            {total} {total === 1 ? 'thing' : 'things'} found
          </span>
        </div>
        <p className="mt-0.5 break-all font-mono text-on-surface-low text-[0.75rem]">{source.root}</p>
        {source.secrets_skipped > 0 && (
          <p className="mt-1 flex items-start gap-1.5 text-on-surface-var text-[0.75rem]">
            <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
            {source.secrets_skipped} credential value(s) or file(s) will not be imported.
          </p>
        )}
      </div>
    </motion.div>
  )
}

/** One category checkbox: what it is, where it lands, and how much of it is already
 *  ours. The already-ours count is what makes a second visit legible instead of
 *  looking like a duplicate import waiting to happen. */
function CategoryRow({ category, total, existing, picked, onPick }: {
  category: string
  total: number
  existing: number
  picked: boolean
  onPick: (v: boolean) => void
}) {
  const label = labelOfCategory(category)
  return (
    <div className="flex items-start gap-2">
      <Checkbox checked={picked} onChange={onPick} className="mt-0.5"
        ariaLabel={`Bring over ${label} (${total})`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-on-surface text-[0.8125rem]">{label}</span>
          <span className="text-on-surface-low text-[0.75rem]">{total}</span>
          {existing > 0 && (
            <span className="text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
              {existing} already imported
            </span>
          )}
        </div>
        <p className="text-on-surface-low text-[0.75rem]">
          {CATEGORY_BLURB[category] ?? `Imported as ${label.toLowerCase()}.`}
        </p>
      </div>
    </div>
  )
}

/** Every row of the report, in full. `conflict` and `rejected` are the reason this
 *  is a list and not a count: a conflict means something different was already at the
 *  destination and was KEPT, and a rejection means a security floor refused the item.
 *  Either one hidden behind "4 imported" is a write the user believes happened. */
function Report({ report, onContinue }: {
  report: OnboardingImportReport
  onContinue: () => void
}) {
  const conflicts = report.results.filter((r) => r.outcome === 'conflict')
  const rejected = report.results.filter((r) => r.outcome === 'rejected')
  const imported = report.results.filter((r) => r.outcome === 'imported')
  return (
    <div className="flex flex-col gap-l">
      <p className="flex items-center gap-1.5 text-[0.875rem]" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {summaryOfReport(report)}
      </p>

      {imported.length > 0 && (
        <dl className="flex flex-col gap-1">
          {imported.map((r) => (
            <div key={r.fingerprint} className="flex gap-2 text-[0.75rem]">
              <dt className="w-[7rem] shrink-0 text-on-surface-low">{labelOfCategory(r.category)}</dt>
              <dd className="min-w-0 flex-1 break-words text-on-surface-var">
                {r.key}{r.destination ? ` → ${r.destination}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {conflicts.length > 0 && (
        <OutcomeList title="Kept what you already had" tone="text-warn" rows={conflicts} />
      )}
      {rejected.length > 0 && (
        <OutcomeList title="Refused for safety" tone="text-danger" rows={rejected} />
      )}

      {report.notes.length > 0 && (
        <ul className="flex flex-col gap-1">
          {report.notes.map((note) => (
            <li key={note} className="flex items-start gap-1.5 text-on-surface-var text-[0.75rem]">
              <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
              {note}
            </li>
          ))}
        </ul>
      )}

      <div>
        <Button variant="primary" size="md" onClick={onContinue}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

/** The conflict / rejection review. Each row names the item and the writer's own
 *  value-free reason, so "needs review" is actionable instead of a number.
 *
 *  `role="group"` is explicit: a bare named `<section>` is what `ariaProhibitedAttr`
 *  catches, and `group` — not the `region` landmark — is the right role for a labelled
 *  set of related rows inside a step body (the shape the essentials step's lanes use).
 *  Without the role the label is discarded and "which list am I in" is unanswerable. */
function OutcomeList({ title, tone, rows }: {
  title: string
  tone: string
  rows: OnboardingImportReport['results']
}) {
  return (
    <section role="group" className="flex flex-col gap-1.5" aria-label={title}>
      <span className={`flex items-center gap-1.5 text-[0.8125rem] ${tone}`} style={fvs(550)}>
        <AlertTriangle size={13} aria-hidden="true" /> {title}
      </span>
      <ul className="flex flex-col gap-1">
        {rows.map((r) => (
          <li key={r.fingerprint} className="text-[0.75rem]">
            <span className="text-on-surface">{labelOfCategory(r.category)} · {r.key}</span>
            {r.detail && <span className="text-on-surface-low"> — {r.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
