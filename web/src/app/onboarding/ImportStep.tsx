import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, Check, ChevronDown, ChevronUp, Loader2, ShieldAlert, ShieldCheck, ShieldX } from 'lucide-react'
import { InlineError } from '../../ui/InlineError'
import { TextLink } from '../../ui/TextLink'
import { Checkbox } from '../../ui/forms'
import { SearchField } from '../../ui/SearchField'
import { MoreRow } from '../../ui/MoreRow'
import { Meter } from '../../ui/Meter'
import { ResultAnnouncement } from '../../ui/ListControls'
import { LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { listItemEnter, stagger } from '../../design/motion'
import { StepActions } from './StepActions'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote, ruleGloss } from '../../lib/scanFindings'
import {
  api,
  hasApiCode,
  type OnboardingImportItem,
  type OnboardingImportItemState,
  type OnboardingImportJob,
  type OnboardingImportReading,
  type OnboardingImportReport,
  type OnboardingImportScan,
  type OnboardingImportSource,
  type OnboardingImportStatus,
} from '../../lib/api'

/** PEP-5 — the onboarding step that brings another local agent tool's setup over.
 *
 *  **The switching cost is the work you already did.** A user arriving from Claude
 *  Code or Codex has written the instructions, the MCP servers and the skills they
 *  care about. This step reads those roots (never writes to them), lists what it
 *  found item by item, and imports exactly what is ticked.
 *
 *  **Collapsed by default, chosen item by item.** Each tool's findings are grouped by
 *  category and a group is ONE row — a checkbox, the category, where it lands and a
 *  count ("Skills · 12") — ticked, because the user came here to bring their setup
 *  over. The row's "Choose" disclosure opens the list: every item, its state, and its
 *  own box. The group's box is DERIVED from its items rather than stored beside them,
 *  so it is tri-state ("9 of 12", mixed) and cannot disagree with the list it sums up.
 *
 *  **Fingerprints are the wire.** The scan names every item by a fingerprint the
 *  server mints from source + category + key; the pick sends fingerprints back and
 *  nothing else. The server re-scans and imports only fingerprints ITS scan found —
 *  an item carries a filesystem path, and a client-supplied one would be a way to have
 *  any directory copied into the home. A pick that was gone by then is reported.
 *
 *  **Only a `new` item is a choice.** An item already here, or one that differs from
 *  what you have, is written by nothing whatever is ticked — the server's plan says so
 *  before the import, from the same check the import uses. So it is listed with its
 *  state and the reason, and gets no checkbox that would pretend otherwise.
 *
 *  **Nothing is swallowed.** The report is rendered in full: what landed, what you
 *  left out, what was already ours, every `conflict` with the reason the existing thing
 *  was KEPT, every `rejected` with the floor that refused it, every pick that no longer
 *  existed, and the counts of credentials withheld. A POST that fails outright shows
 *  the gateway's own sentence with a retry — an import that quietly reports 4 successes
 *  while a fifth write raised is the defect this shape exists to make impossible.
 *
 *  **Re-entry is free.** Item identity is a fingerprint of source+category+key and the
 *  importer keeps a ledger of what it wrote, so a second visit shows those items as
 *  already here and offers nothing twice. The step needs no resume point of its own.
 *
 *  **Skipping is free too.** Nothing here is required to continue, and a machine with
 *  no other agent tool gets one honest line instead of a dead step.
 *
 *  **A months-long history is the normal case, not an edge.** Thousands of transcripts —
 *  12,005 files and 5.3 GB in the history this was measured on — took 41 s to read before the
 *  step could show anything, and the import ran as one request with no progress. So the scan
 *  lists each conversation from the start of its file and says, in one line, that it is still
 *  reading the rest (`ReadingLine`); when the gateway's reading pass finishes, the step fetches
 *  the listing again, and every count is final. The import is a JOB: it runs in the gateway,
 *  this step watches it over the import stream (`ImportProgress`), a stop takes effect between
 *  two items, and a gateway restart part-way is said as such — nothing it wrote is half
 *  written, so scanning again shows what is already here. */

/** The closed category vocabulary, in human words. The server sends the raw values
 *  (it owns the enum), so an unmapped one renders as its own name rather than
 *  disappearing — a category the writers gain later is visible the day it ships. */
const CATEGORY_LABEL: Record<string, string> = {
  instructions: 'Instructions',
  memories: 'Memories',
  mcp_servers: 'MCP servers',
  skills: 'Skills',
  agents: 'Agents',
  prompts: 'Prompts',
  conversations: 'Conversations',
  denied_commands: 'Denied commands',
}
/** Where each category lands here — the destination in plain words, so ticking a box
 *  is an informed choice rather than a guess at a noun. */
const CATEGORY_BLURB: Record<string, string> = {
  instructions: 'Your CLAUDE.md / AGENTS.md, rules and project instructions, saved as memories.',
  memories: 'Notes the other tool was already remembering for you.',
  mcp_servers: 'MCP server definitions, added to your MCP config. The values they set go to your credential store.',
  skills: 'Skills, copied in and re-scanned like a Store install.',
  agents: 'Subagents, added to your Agents page.',
  prompts: 'Slash commands and saved prompts, run in chat as @name.',
  conversations: 'Past conversations, listed in Chat under the dates they happened.',
  denied_commands: 'Commands the other tool refused to run, added to your shell denylist in Settings › Security.',
}

/** Each state's word beside an item. `new` is the only one with a checkbox. */
const STATE_LABEL: Record<OnboardingImportItemState, string> = {
  new: 'New',
  existing: 'Already here',
  conflict: 'Conflict',
  rejected: "Can't import",
}
const STATE_TONE: Record<OnboardingImportItemState, string> = {
  new: 'text-on-surface-low',
  existing: 'text-ok',
  conflict: 'text-warn',
  rejected: 'text-danger',
}

/** A group this long gets a filter box: past a screenful, finding one item by eye stops
 *  being faster than typing part of its name. */
const FILTER_AT = 8
/** Rows an opened group renders before "Show more". A tool can hold hundreds of memories
 *  or skills; rendering them all at once would make one tick cost a long commit. */
const PAGE = 50

export function labelOfCategory(category: string): string {
  return CATEGORY_LABEL[category] ?? category.replace(/_/g, ' ')
}

/** A count as a person reads it: a months-long history is five-digit counts, and "12161" is
 *  harder to read than "12,161". */
const num = (n: number) => n.toLocaleString()

const plural = (n: number, one: string, many: string) => `${num(n)} ${n === 1 ? one : many}`

/** Whether an item begins ticked: every `new` one, bar what the other tool does not use and a
 *  skill whose security scan has warnings. */
const startsTicked = (i: OnboardingImportItem) => i.state === 'new' && i.preselected !== false

/** A skill whose scan has warnings: it comes over only when the user accepts them, on its own
 *  row. A group's box never ticks it, because a shortcut over many items is not a decision about
 *  this one. */
const needsAcceptance = (i: OnboardingImportItem) => i.state === 'new' && i.scan?.verdict === 'warning'

/** What was left out of ONE item, value-free. The skipped count is the credential files
 *  inside a skill, which are never installed; a redaction is a credential-shaped string
 *  replaced in text that still comes over. */
function withheldOf(item: OnboardingImportItem): string {
  const parts: string[] = []
  if (item.secrets_skipped) parts.push(`${plural(item.secrets_skipped, 'credential', 'credentials')} left out`)
  if (item.redactions) {
    parts.push(`${plural(item.redactions, 'credential-like string', 'credential-like strings')} redacted`)
  }
  return parts.join(' · ')
}

/** A server sentence starts lower-case so it can follow a name ("weather — an MCP server
 *  of this name…"); standing alone under an item it reads as its own sentence. */
function asSentence(detail: string): string {
  return detail ? `${detail.charAt(0).toUpperCase()}${detail.slice(1)}.` : ''
}

/** The non-choosable states of a set of items, as the quiet tail of a row: "3 already
 *  here · 1 conflict". Empty when every item is new. */
function settledOf(items: OnboardingImportItem[]): string {
  const n = (state: OnboardingImportItemState) => items.filter((i) => i.state === state).length
  const parts: string[] = []
  if (n('existing')) parts.push(`${num(n('existing'))} already here`)
  if (n('conflict')) parts.push(plural(n('conflict'), 'conflict', 'conflicts'))
  if (n('rejected')) parts.push(`${num(n('rejected'))} can't be imported`)
  return parts.join(' · ')
}

/** The one-line summary the collapsed step row shows. Every non-zero outcome appears,
 *  the choice included: an item the user left out, or one that was gone when the import
 *  ran, is as much a fact about this import as one that landed — and a conflict that only
 *  lived inside the expanded body would vanish the moment the user moved on. */
export function summaryOfReport(report: OnboardingImportReport): string {
  const outcome = (o: string) => report.results.filter((r) => r.outcome === o).length
  const left = (s: OnboardingImportItemState) => report.unselected.filter((u) => u.state === s).length
  const parts: string[] = []
  if (outcome('imported')) parts.push(`${num(outcome('imported'))} imported`)
  if (left('new')) parts.push(`${num(left('new'))} left out`)
  if (outcome('existing') + left('existing')) parts.push(`${num(outcome('existing') + left('existing'))} already here`)
  if (outcome('conflict') + left('conflict')) parts.push(`${num(outcome('conflict') + left('conflict'))} to review`)
  if (outcome('rejected') + left('rejected')) parts.push(`${num(outcome('rejected') + left('rejected'))} refused`)
  if (report.missing.length) parts.push(`${num(report.missing.length)} no longer found`)
  if (report.not_reached?.length) parts.push(`${num(report.not_reached.length)} left when you stopped`)
  return parts.length ? parts.join(' · ') : 'Nothing to import'
}

/** Every fingerprint the scan lists for a detected tool. */
const fingerprintsOf = (scan: OnboardingImportScan | null) =>
  new Set((scan?.sources ?? []).filter((s) => s.detected).flatMap((s) => s.items.map((i) => i.fingerprint)))

/** The picks after the listing is fetched again: what the user chose stays chosen, and an item
 *  the last listing did not have — a conversation the reading pass found — starts the way every
 *  item starts. Picks of items that are gone fall away with them. */
function repick(picked: ReadonlySet<string>, before: ReadonlySet<string>, scan: OnboardingImportScan): Set<string> {
  const next = new Set<string>()
  for (const source of scan.sources.filter((s) => s.detected)) {
    for (const item of source.items) {
      if (picked.has(item.fingerprint) || (!before.has(item.fingerprint) && startsTicked(item))) next.add(item.fingerprint)
    }
  }
  return next
}

/** One tool's items of one category — the unit a row stands for. */
interface Group {
  key: string
  source: OnboardingImportSource
  category: string
  items: OnboardingImportItem[]
}

/** Every non-empty (tool, category) group, tools in scan order and categories in the
 *  server's declaration order — then any category the vocabulary does not know yet. */
function groupsOf(scan: OnboardingImportScan): Group[] {
  const groups: Group[] = []
  for (const source of scan.sources.filter((s) => s.detected)) {
    const categories = [...new Set([...scan.categories, ...source.items.map((i) => i.category)])]
    for (const category of categories) {
      const items = source.items.filter((i) => i.category === category)
      if (items.length) groups.push({ key: `${source.source}:${category}`, source, category, items })
    }
  }
  return groups
}

export function ImportStep({ onDone, onSkip }: {
  /** Move on, with the line the collapsed row will carry. */
  onDone: (summary: string) => void
  /** Move on having imported nothing — always available. */
  onSkip: () => void
}) {
  const [scan, setScan] = useState<OnboardingImportScan | null>(null)
  const [scanError, setScanError] = useState<unknown>(null)
  const [picked, setPicked] = useState<ReadonlySet<string>>(() => new Set())
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  /** The POST that starts an import, in flight. */
  const [starting, setStarting] = useState(false)
  /** The running import this step is watching, as the stream last reported it. */
  const [job, setJob] = useState<OnboardingImportJob | null>(null)
  /** The import the step was watching is gone from the gateway: it restarted part-way. */
  const [interrupted, setInterrupted] = useState(false)
  const [report, setReport] = useState<OnboardingImportReport | null>(null)
  const [failure, setFailure] = useState('')
  /** The reading pass as the stream reports it — fresher than the listing's own `reading`. */
  const [reading, setReading] = useState<OnboardingImportReading | null>(null)
  const scanRef = useRef<OnboardingImportScan | null>(null)
  scanRef.current = scan

  const load = useCallback(() => {
    setScan(null)
    setScanError(null)
    setInterrupted(false)
    api.onboardingImportScan().then((s) => {
      setScan(s)
      setReading(s.reading ?? null)
      // Everything that CAN come over starts ticked: the user came here to bring their
      // setup over, and un-ticking is a smaller act than hunting for what to tick. The one
      // exception is an item the other tool itself does not use (`preselected: false` — a
      // project's MCP server nobody approved there, a file an override replaces): bringing that
      // over is the user's call, and the item's note says why.
      setPicked(new Set(s.sources.filter((x) => x.detected)
        .flatMap((x) => x.items.filter(startsTicked).map((i) => i.fingerprint))))
      setOpen(new Set())
    }).catch(setScanError)
    // A reload while an import runs comes back to it, not to a listing that ignores it.
    api.onboardingImportJob().then((j) => { if (j?.status === 'running') setJob(j) }).catch(() => { /* the listing still stands */ })
  }, [])
  useEffect(load, [load])

  /** The listing fetched again once the reading pass has finished: the same screen, with every
   *  count final. What the user chose and opened stays as it was. */
  const refresh = useCallback(() => {
    api.onboardingImportScan().then((s) => {
      const before = fingerprintsOf(scanRef.current)
      setPicked((prev) => repick(prev, before, s))
      setScan(s)
      setReading(s.reading ?? null)
    }).catch(() => { /* keep the listing on screen; it says it is still reading */ })
  }, [])

  /** A job has ended: its report, or its failure, replaces the progress. */
  const settle = useCallback((ended: OnboardingImportJob) => {
    setJob(null)
    if (ended.status === 'failed') {
      setFailure(ended.error || 'The import could not be completed.')
      return
    }
    if (ended.report) {
      setReport(ended.report)
      return
    }
    api.onboardingImportJob()
      .then((j) => (j?.report ? setReport(j.report) : setFailure(j?.error || 'The import could not be completed.')))
      .catch((e) => setFailure((e as Error)?.message || 'The import finished, but its report could not be read.'))
  }, [])

  // One stream for everything this step waits on in the gateway: the reading pass and the
  // import. Open while either runs; a frame naming no job, or another one, while this step
  // watches one means the gateway restarted under it.
  const readingRuns = Boolean(reading?.running)
  const watching = job !== null && job.status === 'running'
  const watchedId = job?.id ?? ''
  useEffect(() => {
    if (!readingRuns && !watching) return
    let es: EventSource
    try { es = new EventSource(api.onboardingImportStreamUrl()) } catch { return }
    let readingWas = readingRuns
    es.addEventListener('status', (e) => {
      let frame: OnboardingImportStatus
      try { frame = JSON.parse((e as MessageEvent).data) as OnboardingImportStatus } catch { return }
      setReading(frame.reading)
      if (readingWas && !frame.reading.running) refresh()
      readingWas = frame.reading.running
      if (!watchedId) return
      if (!frame.job || frame.job.id !== watchedId) {
        setJob(null)
        setInterrupted(true)
        return
      }
      if (frame.job.status === 'running') setJob(frame.job)
      else settle(frame.job)
    })
    // A dropped stream is not a failed import: EventSource reconnects on its own, and the
    // first frame after a gateway restart says the job is gone.
    es.onerror = () => { /* transient — EventSource retries */ }
    return () => es.close()
  }, [readingRuns, watching, watchedId, refresh, settle])

  const detected = useMemo(() => (scan?.sources ?? []).filter((s) => s.detected), [scan])
  const groups = useMemo(() => (scan ? groupsOf(scan) : []), [scan])
  /** Every choosable item, in scan order — the order the pick is sent in. */
  const choosable = useMemo(
    () => detected.flatMap((s) => s.items.filter((i) => i.state === 'new')),
    [detected],
  )
  const chosen = choosable.filter((i) => picked.has(i.fingerprint))

  const pick = useCallback((fingerprints: string[], on: boolean) => {
    setPicked((prev) => {
      const next = new Set(prev)
      for (const fp of fingerprints) {
        if (on) next.add(fp)
        else next.delete(fp)
      }
      return next
    })
  }, [])
  const toggleOpen = useCallback((key: string) => {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const run = useCallback(async () => {
    setStarting(true)
    setFailure('')
    try {
      // Each picked skill with warnings carries the consent its scan showed: what the user
      // accepted, which the install checks against the bytes it installs.
      const accepted = Object.fromEntries(
        chosen.filter(needsAcceptance).map((i) => [i.fingerprint, i.scan?.consent ?? '']),
      )
      const started = await api.runOnboardingImport({
        fingerprints: chosen.map((i) => i.fingerprint),
        ...(Object.keys(accepted).length ? { accepted } : {}),
      })
      if (started.status === 'running') setJob(started)
      else settle(started)
    } catch (e) {
      if (hasApiCode(e, 'import_running')) {
        // Another tab started one: watch that one rather than start a second.
        api.onboardingImportJob().then((j) => { if (j) (j.status === 'running' ? setJob(j) : settle(j)) }).catch(() => {})
        return
      }
      // The gateway's own sentence, verbatim. `errText` already decided what a user
      // should read; paraphrasing it here would hide which write failed.
      setFailure((e as Error)?.message || 'The import could not be completed.')
    } finally {
      setStarting(false)
    }
  }, [chosen, settle])

  /** Asked to stop: the stream shows `stopping` until the item it is on has landed, then the
   *  report. A stop that fails leaves the import running, and the progress still says so. */
  const [stopFailure, setStopFailure] = useState('')
  const stop = useCallback(() => {
    setStopFailure('')
    api.stopOnboardingImport()
      .then(() => setJob((j) => (j ? { ...j, stopping: true } : j)))
      .catch((e) => setStopFailure((e as Error)?.message || 'The import could not be stopped.'))
  }, [])

  /** What assistive tech is told, out of a polite live region. Every phase this step
   *  passes through is silent otherwise: the scan resolves, the import finishes and
   *  the counts appear with no focus move and no visible text a screen reader would
   *  reach on its own. The import's own progress is announced by its bar, not here —
   *  a live region re-read twice a second would drown out everything else. */
  const announcement = report
    ? `Import finished: ${summaryOfReport(report)}.`
    : starting || job
      ? 'Importing your setup…'
      : scan === null
        ? ''  // the loading region below carries this phase
        : detected.length === 0
          ? 'No other agent tools were found on this machine.'
          : `Found ${detected.map((s) => s.display_name).join(' and ')}.`

  if (job) {
    return (
      <div className="flex flex-col gap-l">
        <p role="status" aria-live="polite" className="sr-only">{announcement}</p>
        <ImportProgress job={job} />
        {stopFailure && <InlineError icon multiline>{stopFailure}</InlineError>}
        {/* The one action an import has is to stop it: a stand-in primary rendered as a blank
            spinner, which is what the drive showed, and it promised nothing. */}
        <StepActions
          secondary={{
            label: job.stopping ? 'Stopping…' : 'Stop importing', onClick: stop,
            disabled: job.stopping, disabledReason: 'Stopping after the item it is on',
          }} />
      </div>
    )
  }
  if (interrupted) {
    // No live-region line here: the error below is an alert, and it says this already.
    return (
      <div className="flex flex-col gap-l">
        <div className="flex flex-col gap-s">
          <InlineError icon multiline>The import stopped when PersonalClaw restarted.</InlineError>
          <p data-type="caption" className="text-on-surface-var">
            Everything that landed before it stopped is kept, and nothing is half written.
            Scan again to see what is already here and bring over the rest.
          </p>
        </div>
        <StepActions primary={{ label: 'Scan again', onClick: load }} secondary={{ label: 'Skip this', onClick: onSkip }} />
      </div>
    )
  }

  // 🔴 A FAILED SCAN MUST NOT REMOVE THE WAY PAST AN OPTIONAL STEP. This early return replaced the
  // whole step body, including the `onSkip` link that lives in the normal return below — so with the
  // scan route failing, the only buttons left on the screen were "Go back to step 1", "Retry" and
  // "Skip setup" (measured). On a step whose own heading asks "Already use another local agent
  // tool?", a transient fetch failure made the answer "no" unreachable and turned the flow's third
  // step into a wall: backwards, retry a server that is down, or abandon setup entirely.
  //
  // `EssentialsStep`'s catalog-error branch always did this correctly and is the reference — the
  // defect was inconsistency between two sibling steps, not a missing idea.
  if (scan === null && scanError) {
    return (
      <div className="flex flex-col gap-m">
        <LoadError what="detected tools" error={scanError} onRetry={load} />
        <StepActions secondary={{ label: 'Skip this', onClick: onSkip }} />
      </div>
    )
  }
  if (scan === null) {
    // Said, not just spun: on a months-long history this takes a few seconds, and a bare
    // spinner reads as a step that is stuck.
    return (
      <div role="status" aria-busy="true" className="flex items-center gap-s py-s">
        <LoadingStatus what="detected tools" />
        <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
        <span data-type="body-s" className="text-on-surface-var" aria-hidden="true">
          Looking for other agent tools on this machine…
        </span>
      </div>
    )
  }

  /** Nothing is new anywhere — a re-entered first run, or a tool whose every item is
   *  already here or differs from yours. The list still shows what is where; the step's
   *  action is to move on, not to import nothing. */
  const nothingNew = choosable.length === 0
  const settledSummary = settledOf(detected.flatMap((s) => s.items)) || 'Nothing to import'
  /** The tools by name, in one sentence that agrees with how many there are — "another agent
   *  tool" read as one tool on a machine that has two, and "its setup" could only mean one. */
  const found = detected.map((s) => s.display_name).join(' and ')
  const one = detected.length === 1
  /** New items the step leaves unticked, so "everything is ticked" stays true. */
  const unticked = choosable.filter((i) => !startsTicked(i)).length

  return (
    <div className="flex flex-col gap-l">
      <p role="status" aria-live="polite" className="sr-only">{announcement}</p>

      {report
        ? <Report report={report} scan={scan} onContinue={() => onDone(summaryOfReport(report))} />
        : detected.length === 0
          ? <Nothing looked={scan.sources} onContinue={() => onDone('Nothing to import')} />
          : (
            <>
              <p data-type="body-s" className="text-on-surface-var">
                {nothingNew
                  ? `Everything we found in ${found} is already here, or differs from what you have — nothing new to bring over.`
                  : `We found ${found} on this machine. Bring ${one ? 'its' : 'their'} setup over — ${one ? 'it is' : 'they are'} only read, and nothing in ${one ? 'it' : 'them'} is changed. Everything is ticked${unticked ? `, except ${plural(unticked, 'item', 'items')} the other tool does not use` : ''}; choose item by item inside any group.`}
              </p>

              <ReadingLine reading={reading ?? scan.reading ?? null} />


              <motion.div className="flex flex-col gap-s" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.05) } }}>
                {detected.map((source) => (
                  <SourceCard key={source.source} source={source}
                    groups={groups.filter((g) => g.source.source === source.source)}
                    picked={picked} onPick={pick} open={open} onToggle={toggleOpen} />
                ))}
              </motion.div>

              {failure && (
                <div className="flex flex-col gap-s">
                  <InlineError icon multiline>{failure}</InlineError>
                  <p data-type="caption" className="text-on-surface-low">
                    Whatever already landed was recorded, so importing again brings over only
                    what is still missing.
                  </p>
                </div>
              )}

              {/* In the flow's navigation bar with every step's actions (`StepActions`). */}
              <StepActions
                primary={nothingNew
                  ? { label: 'Continue', onClick: () => onDone(settledSummary) }
                  : {
                    label: failure
                      ? 'Try again'
                      : chosen.length ? `Import ${plural(chosen.length, 'item', 'items')}` : 'Import selected',
                    onClick: run, loading: starting,
                    disabled: chosen.length === 0, disabledReason: 'Pick at least one thing to bring over',
                  }}
                secondary={{ label: 'Skip this', onClick: onSkip }} />

            </>
          )}
    </div>
  )
}

/** While conversations are still being read in full: how far that has got, and what it means for
 *  the listing. Nothing waits for it — an import reads each conversation as it brings it over —
 *  but until it finishes a conversation's message count is not known and a count can still move,
 *  so the listing says that rather than letting a provisional number pass as a final one.
 *  `data-import-scan="reading"` marks it for the scale drive, which times the listing's arrival
 *  at final. Gone once every file is read. */
function ReadingLine({ reading }: { reading: OnboardingImportReading | null }) {
  if (!reading || reading.of === 0 || reading.read >= reading.of) return null
  const figure = `${num(reading.read)} of ${num(reading.of)}`
  return (
    <div data-import-scan="reading" className="flex flex-col gap-xs rounded-md bg-surface-container px-m py-s">
      <p data-type="body-s" className="text-on-surface">
        {reading.running
          ? `Still reading your conversations in full: ${figure}.`
          : `Read ${figure} of your conversations in full.`}
      </p>
      <Meter label="Conversations read in full" pct={(reading.read / reading.of) * 100} size="thin" />
      <p data-type="caption" className="text-on-surface-low">
        You can import now: each conversation is read as it comes over. Until this finishes, a
        conversation shows no message count, and the counts here can still change.
      </p>
    </div>
  )
}

/** The import, as it runs: how many of the picks have landed or been reported, what each came
 *  to so far, and the one it is on. It runs in the gateway, one item at a time, so a stop takes
 *  effect after the item it is on, and whatever landed stays. `data-import-progress` marks it for
 *  the scale drive, which samples it. */
function ImportProgress({ job }: { job: OnboardingImportJob }) {
  const n = (outcome: string) => job.counts[outcome] ?? 0
  const tally = [
    n('imported') && `${num(n('imported'))} imported`,
    n('existing') && `${num(n('existing'))} already here`,
    n('conflict') && `${num(n('conflict'))} to review`,
    n('rejected') && `${num(n('rejected'))} refused`,
  ].filter(Boolean).join(' · ')
  const preparing = job.phase === 'scanning'
  return (
    <div data-import-progress className="flex flex-col gap-s rounded-lg bg-surface-high p-m">
      <p data-type="title-s" className="text-on-surface tabular-nums">
        {preparing
          ? 'Getting ready to import…'
          : `Importing ${num(job.done)} of ${num(job.total)}`}
      </p>
      <Meter label="Items imported" pct={job.total ? (job.done / job.total) * 100 : 0}
        detail={tally || undefined} />
      {job.current && !job.stopping && (
        <p data-type="caption" className="break-words text-on-surface-var">Now: {job.current}</p>
      )}
      <p data-type="caption" className="text-on-surface-low">
        {job.stopping
          ? 'Stopping after the item it is on. Everything that has landed is kept.'
          : 'Your setup comes over first, then your conversations. Stop at any point: everything that has landed is kept, and importing again brings over the rest.'}
      </p>
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
      <p data-type="body-s" className="text-on-surface-var">
        No other agent tools found on this machine. We looked for{' '}
        {looked.map((s) => s.display_name).join(' and ')} — if you install one later, you can
        import from it any time.
      </p>
      <StepActions primary={{ label: 'Continue', onClick: onContinue }} />
    </div>
  )
}

/** One detected tool: what it is, where it lives, what it holds — and its groups. The
 *  tool is the frame the groups sit in rather than a checkbox of its own: every choice is
 *  an item, a group's box is a shortcut over its items, and a third box over the groups
 *  would be one more derived control saying the same thing. */
function SourceCard({ source, groups, picked, onPick, open, onToggle }: {
  source: OnboardingImportSource
  groups: Group[]
  picked: ReadonlySet<string>
  onPick: (fingerprints: string[], on: boolean) => void
  open: ReadonlySet<string>
  onToggle: (key: string) => void
}) {
  const total = source.items.length
  return (
    <motion.section variants={listItemEnter} role="group" aria-label={source.display_name}
      className="flex flex-col gap-m rounded-lg bg-surface-high p-m">
      <div className="min-w-0">
        <div className="flex items-baseline gap-s">
          <span data-type="title-m" className="text-on-surface">{source.display_name}</span>
          <span data-type="caption" className="text-on-surface-low">{plural(total, 'thing', 'things')} found</span>
        </div>
        <p data-type="caption" className="mt-0.5 break-all font-mono text-on-surface-low">{source.root}</p>
        {source.secrets_skipped > 0 && (
          <p data-type="caption" className="mt-xs flex items-start gap-xs text-on-surface-var">
            <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
            {/* A reassurance on the FIRST screen a new user sees, about credentials being left
                behind. Both nouns take the same count — the sentence is one disjunction over one
                number — so "1 credential value or file" and "3 credential values or files". */}
            {num(source.secrets_skipped)} credential value{source.secrets_skipped === 1 ? '' : 's'} or file{source.secrets_skipped === 1 ? '' : 's'} will not be imported.
          </p>
        )}
      </div>
      <div className="flex flex-col gap-m">
        {groups.map((group) => (
          <GroupRow key={group.key} group={group} picked={picked} onPick={onPick}
            open={open.has(group.key)} onToggle={() => onToggle(group.key)} />
        ))}
      </div>
      <NotImportedList source={source} />
    </motion.section>
  )
}

/** What the tool holds that does not come over, with how many and why. Listed on the card, beside
 *  what does, because a kind left out without a word reads as a kind the tool never had. */
function NotImportedList({ source }: { source: OnboardingImportSource }) {
  const entries = source.not_imported ?? []
  if (entries.length === 0) return null
  return (
    <section role="group" aria-label={`Not brought over from ${source.display_name}`}
      className="flex flex-col gap-xs">
      <span data-type="label-s" className="text-on-surface">Not brought over</span>
      <ul className="flex flex-col gap-xs">
        {entries.map((entry) => (
          <li key={entry.what} data-type="caption" className="text-on-surface-var">
            <span className="text-on-surface">{entry.what}</span>
            <span className="text-on-surface-low tabular-nums"> · {num(entry.count)}</span>
            <span className="text-on-surface-low"> — {entry.why}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** One (tool, category) group, collapsed to a row: its box, its name, its count, where it
 *  lands — and the "Choose" disclosure that opens its items.
 *
 *  The box is tri-state and derived: ticked when every choosable item is, MIXED when some
 *  are, clear when none are. A click on a mixed box chooses the whole group. The count
 *  follows the same items, so "9 of 12" and the list it opens can never disagree. A group
 *  with nothing choosable has no box at all — its items are already here or differ from
 *  yours, and a checkbox there would promise a write that no choice can cause. Its
 *  disclosure then says "Show", because there is nothing in it to choose. */
function GroupRow({ group, picked, onPick, open, onToggle }: {
  group: Group
  picked: ReadonlySet<string>
  onPick: (fingerprints: string[], on: boolean) => void
  open: boolean
  onToggle: () => void
}) {
  const listId = useId()
  const label = labelOfCategory(group.category)
  const tool = group.source.display_name
  // The box, and the count beside it, stand for the items it can tick. A skill whose scan has
  // warnings is chosen on its own row, so it is counted apart: "1 needs your OK".
  const choosable = group.items.filter((i) => i.state === 'new' && !needsAcceptance(i))
  const pending = group.items.filter(needsAcceptance)
  const chosen = choosable.filter((i) => picked.has(i.fingerprint)).length
  const all = choosable.length > 0 && chosen === choosable.length
  const count = all ? num(choosable.length) : `${num(chosen)} of ${num(choosable.length)}`
  const settled = settledOf(group.items)
  const verb = choosable.length + pending.length > 0 ? 'Choose' : 'Show'
  return (
    <div className="flex items-start gap-s">
      {choosable.length > 0
        ? <Checkbox checked={all} indeterminate={chosen > 0 && !all} className="mt-0.5"
            onChange={(on) => onPick(choosable.map((i) => i.fingerprint), on)}
            ariaLabel={`Bring over ${label} from ${tool}, ${count}`} />
        : pending.length > 0
          ? <ShieldAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-warning" />
          : <Check size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-s">
          <span data-type="body-s" className="text-on-surface">{label}</span>
          {choosable.length > 0 && (
            <span data-type="caption" className="text-on-surface-low tabular-nums">
              <span aria-hidden="true">· </span>{count}
            </span>
          )}
          {pending.length > 0 && (
            <span data-type="caption" className="text-warning">
              <span aria-hidden="true">· </span>{pending.length} {pending.length === 1 ? 'needs' : 'need'} your OK
            </span>
          )}
          {settled && (
            <span data-type="caption" className="text-on-surface-low">
              <span aria-hidden="true">· </span>{settled}
            </span>
          )}
          {/* 🔑 A named disclosure wired to the list it opens. `aria-expanded` alone says "this
              expands" without saying WHAT; `aria-controls` names the list — the pairing
              `DisclosureCard` carries. It is a separate control from the box on purpose: looking
              inside a group must not change what the group brings over. The visible word leads
              the name, and the name adds WHICH group, because five rows of "Choose" are
              otherwise one repeated word to a screen reader. */}
          <TextLink size="xs" ink="emphasis" className="ml-auto shrink-0" onClick={onToggle}
            icon={open ? ChevronUp : ChevronDown} iconPosition="trailing"
            aria-expanded={open} aria-controls={listId} aria-label={`${verb} ${label} from ${tool}`}>
            {verb}
          </TextLink>
        </div>
        <p data-type="caption" className="text-on-surface-low">
          {CATEGORY_BLURB[group.category] ?? `Imported as ${label.toLowerCase()}.`}
        </p>
        {open && <ItemList id={listId} group={group} label={label} picked={picked} onPick={onPick} />}
      </div>
    </div>
  )
}

/** An opened group: every item, filterable past a screenful and paged past `PAGE`.
 *
 *  "Show more" moves focus to the first row it revealed. Without that, revealing the LAST
 *  page unmounts the very control that was focused, and focus falls to the document — a
 *  keyboard user is sent back to the top of the page for asking to see more. */
function ItemList({ id, group, label, picked, onPick }: {
  id: string
  group: Group
  label: string
  picked: ReadonlySet<string>
  onPick: (fingerprints: string[], on: boolean) => void
}) {
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const listRef = useRef<HTMLUListElement>(null)
  const focusRow = useRef<number | null>(null)
  const tool = group.source.display_name
  const needle = query.trim().toLowerCase()
  const matching = needle
    ? group.items.filter((i) => `${i.title} ${i.key}`.toLowerCase().includes(needle))
    : group.items
  const visible = matching.slice(0, limit)
  const remaining = matching.length - visible.length

  useEffect(() => {
    const row = focusRow.current
    if (row === null) return
    focusRow.current = null
    const li = listRef.current?.children[row] as HTMLElement | undefined
    ;(li?.querySelector<HTMLElement>('input') ?? li)?.focus()
  }, [limit])

  return (
    <div className="mt-s flex flex-col gap-s">
      {group.items.length > FILTER_AT && (
        <>
          <SearchField size="sm" surface="container" value={query}
            onChange={(v) => { setQuery(v); setLimit(PAGE) }}
            placeholder={`Filter ${label.toLowerCase()}`}
            ariaLabel={`Filter ${label} from ${tool}`} />
          <ResultAnnouncement count={matching.length} noun="items" active={needle !== ''} />
          {needle && (
            <p data-type="caption" className="text-on-surface-low">
              {num(matching.length)} of {num(group.items.length)} match
            </p>
          )}
        </>
      )}
      <ul ref={listRef} id={id} aria-label={`${label} from ${tool}`}
        className="flex max-h-[18rem] flex-col overflow-y-auto rounded-md bg-surface-container px-s py-xs">
        {visible.map((item) => (
          <ItemRow key={item.fingerprint} item={item} picked={picked.has(item.fingerprint)} onPick={onPick} />
        ))}
        {visible.length === 0 && (
          <li data-type="caption" className="py-xs text-on-surface-low">Nothing matches “{query.trim()}”.</li>
        )}
      </ul>
      {remaining > 0 && (
        <div>
          <TextLink size="sm" ink="emphasis"
            onClick={() => { focusRow.current = visible.length; setLimit((n) => n + PAGE) }}>
            Show {Math.min(PAGE, remaining)} more ({num(remaining)} not shown)
          </TextLink>
        </div>
      )}
    </div>
  )
}

/** One item: its name, its state, what was left out of it, and — only when importing it
 *  would do something — its own checkbox. A row without a box is still programmatically
 *  focusable, so "Show more" can land on it. */
function ItemRow({ item, picked, onPick }: {
  item: OnboardingImportItem
  picked: boolean
  onPick: (fingerprints: string[], on: boolean) => void
}) {
  const withheld = withheldOf(item)
  const choosable = item.state === 'new'
  const accepting = needsAcceptance(item)
  const warnings = (item.scan?.findings ?? []).filter((f) => f.severity === 'warning').length
  const acceptance = accepting
    ? `import anyway, accepting ${plural(warnings, 'warning', 'warnings')}`
    : ''
  return (
    <li tabIndex={choosable ? undefined : -1} className="flex items-start gap-s py-xs">
      {choosable
        ? <Checkbox checked={picked} onChange={(on) => onPick([item.fingerprint], on)} className="mt-0.5"
            ariaLabel={[item.title, item.origin, withheld, acceptance].filter(Boolean).join(', ')} />
        : <span aria-hidden="true" className="size-4 shrink-0" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-s">
          <span data-type="body-s" className="min-w-0 flex-1 break-words text-on-surface">{item.title}</span>
          <span data-type="caption" className={`shrink-0 ${STATE_TONE[item.state]}`}>{STATE_LABEL[item.state]}</span>
        </div>
        {/* Where in the tool it was found — two scopes can hold a server of one name. */}
        {item.origin && (
          <p data-type="caption" className="break-words text-on-surface-low">{item.origin}</p>
        )}
        {!choosable && item.detail && (
          <p data-type="caption" className="text-on-surface-low">{asSentence(item.detail)}</p>
        )}
        {item.note && (
          <p data-type="caption" className="text-on-surface-var">{item.note}</p>
        )}
        {withheld && (
          <p data-type="caption" className="flex items-start gap-xs text-on-surface-var">
            <ShieldCheck size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
            {withheld}
          </p>
        )}
        {item.scan && item.scan.findings.length > 0 && <ScanFindings item={item} accepting={accepting} />}
      </div>
    </li>
  )
}

/** What a skill's security scan found, on its own row before anything is imported: each finding's
 *  rule, file, what it means and the line that tripped it, the way the Store's install card lists
 *  them. A warning skill says what ticking it does, because the tick IS the acceptance: the
 *  import records it in the audit log. A dangerous one says nothing overrides it. */
function ScanFindings({ item, accepting }: { item: OnboardingImportItem; accepting: boolean }) {
  const findings = item.scan?.findings ?? []
  const dangerous = item.scan?.verdict === 'dangerous'
  const tone = dangerous ? 'danger' : 'warning'
  const hidden = hiddenFindingsNote(findings.length)
  return (
    <div className="mt-xs flex flex-col gap-xs rounded-md px-s py-xs"
      style={{ background: `color-mix(in srgb, var(--color-${tone}) 10%, transparent)` }}>
      <p data-type="caption" className="flex items-center gap-xs" style={{ color: `var(--color-${tone})` }}>
        {dangerous ? <ShieldX size={12} aria-hidden="true" /> : <ShieldAlert size={12} aria-hidden="true" />}
        {dangerous
          ? 'The security scan found dangerous content. Nothing overrides that.'
          : `The security scan flagged ${plural(findings.length, 'warning', 'warnings')}.`}
      </p>
      <ul className="flex flex-col gap-xs">
        {findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => (
          <li key={`${f.rule}:${f.path}:${i}`} data-type="caption" className="text-on-surface-var">
            <span className="font-mono">
              <span className="uppercase" style={{ color: `var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})` }}>{f.severity}</span>
              {' '}{f.rule}{f.path && <span className="text-on-surface-low"> in {f.path}</span>}
            </span>
            {ruleGloss(f.rule) && <span className="block">{ruleGloss(f.rule)}</span>}
            {f.evidence && <span className="block break-all font-mono text-on-surface-low">{f.evidence}</span>}
          </li>
        ))}
      </ul>
      {hidden && <p data-type="caption" className="italic text-on-surface-low">{hidden}</p>}
      {accepting && (
        <p data-type="caption" className="text-on-surface-var">
          Ticking it imports it anyway: you accept these warnings, and the audit log records that you did.
        </p>
      )}
    </div>
  )
}

/** A row of the report that names an item and why — a write outcome or a pre-write state,
 *  which carry the same four fields for exactly this purpose. */
type ReportRow = Pick<OnboardingImportItem, 'fingerprint' | 'category' | 'key' | 'detail'>

/** Every row of the report, in the order it needs attention. `conflict` and `rejected` are
 *  the reason this is a list and not a count: a conflict means something different was
 *  already at the destination and was KEPT, and a rejection means a security floor refused
 *  the item. Either one hidden behind "4 imported" is a write the user believes happened —
 *  so they come FIRST, with every pick that was gone by the time the import ran (by name,
 *  from the scan the user picked it in) and what the user LEFT OUT, since the choice is part
 *  of the answer.
 *
 *  🔑 WHAT LANDED IS A COUNT PER GROUP UNTIL ASKED FOR. Listed in full it buried the rest:
 *  driven with a 76-item import, the two conflicts and the Continue button sat below 76 rows
 *  of success. The same shape as the picker — collapsed to a number, open on request — and
 *  nothing is withheld, because every row is one disclosure away. */
function Report({ report, scan, onContinue }: {
  report: OnboardingImportReport
  scan: OnboardingImportScan
  onContinue: () => void
}) {
  const [showLanded, setShowLanded] = useState(false)
  const landedId = useId()
  const imported = report.results.filter((r) => r.outcome === 'imported')
  const landed = [...imported.reduce((m, r) => m.set(r.category, (m.get(r.category) ?? 0) + 1), new Map<string, number>())]
  const conflicts: ReportRow[] = [
    ...report.results.filter((r) => r.outcome === 'conflict'),
    ...report.unselected.filter((u) => u.state === 'conflict'),
  ]
  const rejected: ReportRow[] = [
    ...report.results.filter((r) => r.outcome === 'rejected'),
    ...report.unselected.filter((u) => u.state === 'rejected'),
  ]
  const leftOut = report.unselected.filter((u) => u.state === 'new')
  const scanned = new Map(scan.sources.flatMap((s) => s.items.map((i) => [i.fingerprint, i] as const)))
  const gone = report.missing.map((fp) => scanned.get(fp)?.title ?? fp)
  const unreached = (report.not_reached ?? []).map((fp) => scanned.get(fp))
  /** A row by the name the list showed it under — its title and where it was found — rather than
   *  its key, which for a scoped server or a project file is the importer's own bookkeeping. */
  const named = (r: ReportRow) => {
    const item = scanned.get(r.fingerprint)
    return item ? [item.title, item.origin].filter(Boolean).join(' · ') : r.key
  }
  return (
    <div className="flex flex-col gap-l">
      <p data-type="body-m" className="flex items-center gap-xs" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {summaryOfReport(report)}
      </p>

      {conflicts.length > 0 && (
        <OutcomeList title="Kept what you already had" tone="text-warn" rows={conflicts} name={named} />
      )}
      {rejected.length > 0 && (
        <OutcomeList title="Refused for safety" tone="text-danger" rows={rejected} name={named} />
      )}
      {gone.length > 0 && (
        <section role="group" aria-label="No longer there" className="flex flex-col gap-s">
          <span data-type="label-s" className="flex items-center gap-xs text-warn">
            <AlertTriangle size={13} aria-hidden="true" /> No longer there
          </span>
          <p data-type="caption" className="text-on-surface-var">
            {gone.length === 1 ? 'This was' : 'These were'} gone from the other tool by the time the
            import ran, so nothing was brought over for {gone.length === 1 ? 'it' : 'them'}: {gone.join(', ')}.
          </p>
        </section>
      )}
      {unreached.length > 0 && (
        <section role="group" aria-label="Not reached, because you stopped" className="flex flex-col gap-s">
          <span data-type="label-s" className="text-on-surface">Not reached, because you stopped</span>
          <p data-type="caption" className="text-on-surface-var">
            The import stopped before {plural(unreached.length, 'item', 'items')} you picked, so
            nothing was written for {unreached.length === 1 ? 'it' : 'them'}. Importing again
            brings {unreached.length === 1 ? 'it' : 'them'} over.
          </p>
          <ul className="flex flex-col gap-xs">
            {unreached.slice(0, 8).map((item, index) => (
              <li key={item?.fingerprint ?? index} data-type="caption" className="text-on-surface-var">
                {item ? `${labelOfCategory(item.category)} · ${[item.title, item.origin].filter(Boolean).join(' · ')}` : 'An item the scan no longer lists'}
              </li>
            ))}
          </ul>
          <MoreRow total={unreached.length} shown={8} />
        </section>
      )}
      {leftOut.length > 0 && (
        <section role="group" aria-label="Left out, as you chose" className="flex flex-col gap-s">
          <span data-type="label-s" className="text-on-surface">Left out, as you chose</span>
          <ul className="flex flex-col gap-xs">
            {leftOut.slice(0, 8).map((u) => (
              <li key={u.fingerprint} data-type="caption" className="text-on-surface-var">
                {labelOfCategory(u.category)} · {named(u)}
              </li>
            ))}
          </ul>
          <MoreRow total={leftOut.length} shown={8} />
        </section>
      )}
      {imported.length > 0 && (
        <section role="group" aria-label="Brought over" className="flex flex-col gap-s">
          <span data-type="label-s" className="text-on-surface">Brought over</span>
          <ul className="flex flex-wrap gap-x-m gap-y-xs">
            {landed.map(([category, n]) => (
              <li key={category} data-type="caption" className="text-on-surface-var">
                {labelOfCategory(category)} · {num(n)}
              </li>
            ))}
          </ul>
          <div>
            <TextLink size="sm" onClick={() => setShowLanded((v) => !v)}
              icon={showLanded ? ChevronUp : ChevronDown} iconPosition="trailing"
              aria-expanded={showLanded} aria-controls={landedId}>
              {showLanded ? 'Hide each item' : 'Show each item'}
            </TextLink>
          </div>
          {showLanded && (
            <dl id={landedId} className="flex flex-col gap-xs">
              {imported.map((r) => (
                <div key={r.fingerprint} data-type="caption" className="flex gap-s">
                  <dt className="w-[7rem] shrink-0 text-on-surface-low">{labelOfCategory(r.category)}</dt>
                  <dd className="min-w-0 flex-1 break-words text-on-surface-var">
                    {named(r)}{r.destination ? ` → ${r.destination}` : ''}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      )}

      {report.notes.length > 0 && (
        <ul className="flex flex-col gap-xs">
          {report.notes.map((note) => (
            <li key={note} data-type="caption" className="flex items-start gap-xs text-on-surface-var">
              <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
              {note}
            </li>
          ))}
        </ul>
      )}

      <StepActions primary={{ label: 'Continue', onClick: onContinue }} />
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
function OutcomeList({ title, tone, rows, name }: {
  title: string
  tone: string
  rows: ReportRow[]
  /** How a row is named: the title (and where it was found) the list showed it under. */
  name: (row: ReportRow) => string
}) {
  return (
    <section role="group" className="flex flex-col gap-s" aria-label={title}>
      <span data-type="label-s" className={`flex items-center gap-xs ${tone}`}>
        <AlertTriangle size={13} aria-hidden="true" /> {title}
      </span>
      <ul className="flex flex-col gap-xs">
        {rows.map((r) => (
          <li key={r.fingerprint} data-type="caption">
            <span className="text-on-surface">{labelOfCategory(r.category)} · {name(r)}</span>
            {r.detail && <span className="text-on-surface-low"> — {r.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
