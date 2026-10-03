import { Globe, Rss, FolderOpen, Puzzle, type LucideIcon } from 'lucide-react'
import type { SourceKind, WatchedSource } from '../../lib/api'

// ── Watched-source display vocabulary ───────────────────────────────────────────────
//
// Everything that is a PROMISE about backend state — the health statuses, the raw/no-AI
// enrichment, the two remediation kinds, the provider guidance strings — is defined by the
// backend and shipped in the list response. What lives here is only presentation: a label,
// a tone, an icon. `tests/test_knowledge_sources_api.py` holds `HEALTH_META` and
// `RAW_ENRICHMENT` to the Python vocabularies, so a fifth status added in
// `knowledge_providers/base.py` reds CI here instead of falling silently through a default
// branch — which would happen to the one status that most needed its own message.

/** The enrichment value §6.3 makes a structural promise about: an item from a `raw` source
 *  runs through a graph whose LLM nodes are ABSENT, not skipped-by-flag. The 'no AI' chip is
 *  a readout of this field on the source row and nothing else — a chip driven by a UI guess
 *  would be decoration over a guarantee. */
export const RAW_ENRICHMENT = 'raw'

/** The one status whose remediation is a single knob, so the create flow and the list both
 *  branch on it by name rather than on a literal spelled out at each site. Held to
 *  `base.HEALTH_NEEDS_RENDER` by the Python parity test, and to `HEALTH_META` by
 *  `sourceHealth.test.ts` — the constant and the map cannot drift apart. */
export const HEALTH_NEEDS_RENDER = 'needs render tier'

export interface HealthMeta {
  label: string
  /** Which token family carries the status. `ok` is a settled state, `warn` is actionable,
   *  `danger` is "nothing is being collected". */
  tone: 'ok' | 'warn' | 'danger'
  /** What the status MEANS, for the row's tooltip — a one-word status is not an explanation. */
  hint: string
}

/** The five statuses `record_poll` can persist, keyed by the exact stored string.
 *  `needs render tier` is separate from `degraded` on purpose: a timeout and a JS shell
 *  produce completely different remediations, and flattening them hides the one knob that
 *  fixes the second. */
export const HEALTH_META: Record<string, HealthMeta> = {
  'ok': { label: 'Healthy', tone: 'ok', hint: 'The last poll ran and this source is up to date.' },
  'degraded': { label: 'Degraded', tone: 'warn', hint: 'The last poll failed in a way the next one may recover from. The cursor was kept.' },
  'error': { label: 'Error', tone: 'danger', hint: 'The poll could not run at all — usually no provider is enrolled for this kind.' },
  'needs render tier': { label: 'Needs render tier', tone: 'warn', hint: 'This page builds its content with JavaScript, so a plain fetch sees an empty shell.' },
  'needs browse tier': { label: 'Needs browse tier', tone: 'warn', hint: 'Even a rendered fetch saw an empty shell; this page needs the full gateway browse tier, which is turned off by default.' },
}

/** Presentation for a status the backend has and this map does not — reached only if the
 *  parity test above was deleted. Labelled honestly rather than silently rendered as OK. */
const UNKNOWN_HEALTH: HealthMeta = {
  label: 'Unknown',
  tone: 'warn',
  hint: 'This dashboard does not recognise the status the backend reported.',
}

export function healthMeta(status: string | undefined): HealthMeta {
  return HEALTH_META[status ?? ''] ?? UNKNOWN_HEALTH
}

/** Tailwind classes per tone, so a chip and its dot cannot drift apart. */
export const TONE_CLASS: Record<HealthMeta['tone'], string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
}

/** An icon per create FORM (the backend's `form` discriminator), not per provider name:
 *  a connector-pack provider that arrives later gets the generic one instead of no icon. */
const FORM_ICON: Record<string, LucideIcon> = {
  web_page: Globe,
  feed: Rss,
  dir: FolderOpen,
}

export function formIcon(form: string): LucideIcon {
  return FORM_ICON[form] ?? Puzzle
}

/** A poll cadence as something a human reads. Sources poll on the order of minutes to
 *  hours, so minutes/hours is the whole useful range. */
export function fmtInterval(secs: number): string {
  if (!secs || secs < 60) return `${Math.max(0, Math.round(secs))}s`
  if (secs < 3600) return `${Math.round(secs / 60)} min`
  const hours = secs / 3600
  return `${hours % 1 === 0 ? hours : hours.toFixed(1)} hr`
}

/** Why a row's cadence is not the one chosen for it, for its title; '' when it is. The row
 *  states `poll_every_secs`, how often the engine really checks it: a source that fetches
 *  over the network is held to the floor in settings whatever it asked for, and a row that
 *  said "every 5 min" while the engine checked every 15 was stating the request. */
export function cadenceNote(source: Pick<WatchedSource, 'poll_interval_secs' | 'poll_every_secs'>): string {
  const chosen = source.poll_interval_secs
  if (!chosen || chosen >= source.poll_every_secs) return ''
  return `Set to every ${fmtInterval(chosen)}, but a source that fetches over the network is checked `
    + `at most every ${fmtInterval(source.poll_every_secs)} (Settings → Watched sources).`
}

/** A watched folder's first scan while it still has something to say: files still waiting to
 *  be read in (a check reads a capped number), or the files the scan's bound left for later.
 *  '' when it has neither, and for every source that is not a folder. */
export function firstScanLine(scan: WatchedSource['first_scan']): string {
  if (!scan) return ''
  const parts: string[] = []
  if (scan.waiting > 0) {
    parts.push(`${scan.waiting} more ${scan.waiting === 1 ? 'file is' : 'files are'} waiting to be read in.`)
  }
  if (scan.left_out > 0) {
    // Not "the older ones": a file too large for what was left of the scan's bytes is left out
    // too, wherever it falls in the order.
    const taken = scan.found - scan.left_out
    parts.push(`The first scan took ${taken} of ${scan.found} files, the newest first; `
      + (scan.left_out === 1 ? 'the other one comes in when it changes.' : `the other ${scan.left_out} come in when they change.`))
  }
  return parts.join(' ')
}

/** The links a watched folder's scan left out because they lead outside it: a folder source
 *  takes in only what is inside the folder. '' when there are none, and for every source that is
 *  not a folder. */
export function linksOutsideLine(count: WatchedSource['links_outside']): string {
  if (!count) return ''
  return `${count} ${count === 1 ? 'link here leads' : 'links here lead'} outside this folder, `
    + `so what ${count === 1 ? 'it points' : 'they point'} to is not read in: a folder source reads only what is inside it.`
}

/** What a folder's first check does, stated from the provider's own bound (the kind catalog
 *  ships it), so the create form cannot promise a number the scan does not apply. */
export function firstScanPromise(kind: Pick<SourceKind, 'first_scan_max_files' | 'first_scan_max_bytes'>): string {
  const files = kind.first_scan_max_files
  const bytes = kind.first_scan_max_bytes
  if (!files || !bytes) return ''
  return `Its first check reads in what is already there, newest first — up to ${files.toLocaleString('en-US')} files `
    + `or ${Math.round(bytes / (1024 * 1024))} MB. The rest come in when they change.`
}

/** The poll cadences the create form offers. Deliberately coarse: the engine clamps
 *  anything below its own network floor anyway, and a free-text seconds field invites a
 *  value that is abusive to someone else's server. */
export const INTERVAL_CHOICES = [900, 3600, 21600, 86400]

/** The facts under an EVENT-DRIVEN source's name (the artifact mirror).
 *
 *  Its own line rather than a branch inside the poller's, because the two shapes diverge on
 *  every fact: a poller states its cadence, its last run, what it collected and when it runs
 *  next, and none of those exist here. Interleaving them behind a boolean is how a ` · ` ends
 *  up separating nothing. It names WHERE the one switch is, because this row deliberately has
 *  no pause control — the mirror is governed by `knowledge.auto_ingest_artifacts` — and a
 *  state with no next step sends a user hunting for one.
 *
 *  No leading kind name, unlike the poller line. `kinds` is built from POLL-CAPABLE providers
 *  only, so this row has no entry there and would fall back to the raw provider string:
 *  measured on the running page as a lowercase "artifacts ·" directly under the row's own
 *  "Artifacts" heading — the same word twice, in two casings, separating nothing.
 */
export function eventDrivenMetaLine(): string {
  return 'Indexed as artifacts change · turn off in Settings → Sources'
}
