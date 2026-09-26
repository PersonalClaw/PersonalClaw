import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Check, ChevronDown, ChevronUp, Loader2, ShieldCheck } from 'lucide-react'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { TextLink } from '../../ui/TextLink'
import { Checkbox } from '../../ui/forms'
import { SearchField } from '../../ui/SearchField'
import { MoreRow } from '../../ui/MoreRow'
import { ResultAnnouncement } from '../../ui/ListControls'
import { LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { listItemEnter, stagger } from '../../design/motion'
import {
  api,
  type OnboardingImportItem,
  type OnboardingImportItemState,
  type OnboardingImportReport,
  type OnboardingImportScan,
  type OnboardingImportSource,
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
/** Where each category lands here — the destination in plain words, so ticking a box
 *  is an informed choice rather than a guess at a noun. */
const CATEGORY_BLURB: Record<string, string> = {
  instructions: 'Your CLAUDE.md / AGENTS.md conventions, saved as memories.',
  memories: 'Notes the other tool was already remembering for you.',
  mcp_servers: 'MCP server definitions, added to your MCP config.',
  skills: 'Skills, copied in and re-scanned like a Store install.',
  settings: 'Staged for you to review — never merged into live config.',
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

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** What was left out of ONE item, value-free. The skipped count covers dropped secret
 *  keys and credential files inside a skill; a redaction is a credential-shaped string
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
  if (n('existing')) parts.push(`${n('existing')} already here`)
  if (n('conflict')) parts.push(plural(n('conflict'), 'conflict', 'conflicts'))
  if (n('rejected')) parts.push(`${n('rejected')} can't be imported`)
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
  if (outcome('imported')) parts.push(`${outcome('imported')} imported`)
  if (left('new')) parts.push(`${left('new')} left out`)
  if (outcome('existing') + left('existing')) parts.push(`${outcome('existing') + left('existing')} already here`)
  if (outcome('conflict') + left('conflict')) parts.push(`${outcome('conflict') + left('conflict')} to review`)
  if (outcome('rejected') + left('rejected')) parts.push(`${outcome('rejected') + left('rejected')} refused`)
  if (report.missing.length) parts.push(`${report.missing.length} no longer found`)
  return parts.length ? parts.join(' · ') : 'Nothing to import'
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
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<OnboardingImportReport | null>(null)
  const [failure, setFailure] = useState('')

  const load = useCallback(() => {
    setScan(null)
    setScanError(null)
    api.onboardingImportScan().then((s) => {
      setScan(s)
      // Everything that CAN come over starts ticked: the user came here to bring their
      // setup over, and un-ticking is a smaller act than hunting for what to tick.
      setPicked(new Set(s.sources.filter((x) => x.detected)
        .flatMap((x) => x.items.filter((i) => i.state === 'new').map((i) => i.fingerprint))))
      setOpen(new Set())
    }).catch(setScanError)
  }, [])
  useEffect(load, [load])

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
    setBusy(true)
    setFailure('')
    try {
      setReport(await api.runOnboardingImport({ fingerprints: chosen.map((i) => i.fingerprint) }))
    } catch (e) {
      // The gateway's own sentence, verbatim. `errText` already decided what a user
      // should read; paraphrasing it here would hide which write failed.
      setFailure((e as Error)?.message || 'The import could not be completed.')
    } finally {
      setBusy(false)
    }
  }, [chosen])

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
        <TextLink onClick={onSkip}>Skip this</TextLink>
      </div>
    )
  }
  if (scan === null) {
    return (
      <div role="status" aria-busy="true" className="flex items-center py-s">
        <LoadingStatus what="detected tools" />
        <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
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
                  : `We found ${found} on this machine. Bring ${one ? 'its' : 'their'} setup over — ${one ? 'it is' : 'they are'} only read, nothing in ${one ? 'it' : 'them'} is changed, and credentials are never imported. Everything is ticked; choose item by item inside any group.`}
              </p>

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

              <div className="flex items-center gap-m">
                {nothingNew
                  ? (
                    <Button variant="primary" size="md" onClick={() => onDone(settledSummary)}>
                      Continue <ArrowRight size={16} aria-hidden="true" />
                    </Button>
                  )
                  : (
                    <Button variant="primary" size="md" loading={busy}
                      disabled={chosen.length === 0}
                      disabledReason="Pick at least one thing to bring over"
                      onClick={run}>
                      {failure
                        ? 'Try again'
                        : chosen.length ? `Import ${plural(chosen.length, 'item', 'items')}` : 'Import selected'}
                      <ArrowRight size={16} aria-hidden="true" />
                    </Button>
                  )}
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
      <p data-type="body-s" className="text-on-surface-var">
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
            {source.secrets_skipped} credential value{source.secrets_skipped === 1 ? '' : 's'} or file{source.secrets_skipped === 1 ? '' : 's'} will not be imported.
          </p>
        )}
      </div>
      <div className="flex flex-col gap-m">
        {groups.map((group) => (
          <GroupRow key={group.key} group={group} picked={picked} onPick={onPick}
            open={open.has(group.key)} onToggle={() => onToggle(group.key)} />
        ))}
      </div>
    </motion.section>
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
  const choosable = group.items.filter((i) => i.state === 'new')
  const chosen = choosable.filter((i) => picked.has(i.fingerprint)).length
  const all = choosable.length > 0 && chosen === choosable.length
  const count = all ? `${choosable.length}` : `${chosen} of ${choosable.length}`
  const settled = settledOf(group.items)
  const verb = choosable.length > 0 ? 'Choose' : 'Show'
  return (
    <div className="flex items-start gap-s">
      {choosable.length > 0
        ? <Checkbox checked={all} indeterminate={chosen > 0 && !all} className="mt-0.5"
            onChange={(on) => onPick(choosable.map((i) => i.fingerprint), on)}
            ariaLabel={`Bring over ${label} from ${tool}, ${count}`} />
        : <Check size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-s">
          <span data-type="body-s" className="text-on-surface">{label}</span>
          {choosable.length > 0 && (
            <span data-type="caption" className="text-on-surface-low tabular-nums">
              <span aria-hidden="true">· </span>{count}
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
              {matching.length} of {group.items.length} match
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
            Show {Math.min(PAGE, remaining)} more ({remaining} not shown)
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
  return (
    <li tabIndex={choosable ? undefined : -1} className="flex items-start gap-s py-xs">
      {choosable
        ? <Checkbox checked={picked} onChange={(on) => onPick([item.fingerprint], on)} className="mt-0.5"
            ariaLabel={withheld ? `${item.title}, ${withheld}` : item.title} />
        : <span aria-hidden="true" className="size-4 shrink-0" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-s">
          <span data-type="body-s" className="min-w-0 flex-1 break-words text-on-surface">{item.title}</span>
          <span data-type="caption" className={`shrink-0 ${STATE_TONE[item.state]}`}>{STATE_LABEL[item.state]}</span>
        </div>
        {!choosable && item.detail && (
          <p data-type="caption" className="text-on-surface-low">{asSentence(item.detail)}</p>
        )}
        {withheld && (
          <p data-type="caption" className="flex items-start gap-xs text-on-surface-var">
            <ShieldCheck size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
            {withheld}
          </p>
        )}
      </div>
    </li>
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
  return (
    <div className="flex flex-col gap-l">
      <p data-type="body-m" className="flex items-center gap-xs" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {summaryOfReport(report)}
      </p>

      {conflicts.length > 0 && (
        <OutcomeList title="Kept what you already had" tone="text-warn" rows={conflicts} />
      )}
      {rejected.length > 0 && (
        <OutcomeList title="Refused for safety" tone="text-danger" rows={rejected} />
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
      {leftOut.length > 0 && (
        <section role="group" aria-label="Left out, as you chose" className="flex flex-col gap-s">
          <span data-type="label-s" className="text-on-surface">Left out, as you chose</span>
          <ul className="flex flex-col gap-xs">
            {leftOut.slice(0, 8).map((u) => (
              <li key={u.fingerprint} data-type="caption" className="text-on-surface-var">
                {labelOfCategory(u.category)} · {u.title}
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
                {labelOfCategory(category)} · {n}
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
                    {r.key}{r.destination ? ` → ${r.destination}` : ''}
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
  rows: ReportRow[]
}) {
  return (
    <section role="group" className="flex flex-col gap-s" aria-label={title}>
      <span data-type="label-s" className={`flex items-center gap-xs ${tone}`}>
        <AlertTriangle size={13} aria-hidden="true" /> {title}
      </span>
      <ul className="flex flex-col gap-xs">
        {rows.map((r) => (
          <li key={r.fingerprint} data-type="caption">
            <span className="text-on-surface">{labelOfCategory(r.category)} · {r.key}</span>
            {r.detail && <span className="text-on-surface-low"> — {r.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
