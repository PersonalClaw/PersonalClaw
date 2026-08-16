import { Globe, Rss, FolderOpen, Puzzle, type LucideIcon } from 'lucide-react'

// ── Watched-source display vocabulary (WATCHED-SOURCES §2.4/§6.3/§12) ───────────────
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

/** The four statuses `record_poll` can persist, keyed by the exact stored string.
 *  `needs render tier` is separate from `degraded` on purpose: a timeout and a JS shell
 *  produce completely different remediations, and flattening them hides the one knob that
 *  fixes the second. */
export const HEALTH_META: Record<string, HealthMeta> = {
  'ok': { label: 'Healthy', tone: 'ok', hint: 'The last poll ran and this source is up to date.' },
  'degraded': { label: 'Degraded', tone: 'warn', hint: 'The last poll failed in a way the next one may recover from. The cursor was kept.' },
  'error': { label: 'Error', tone: 'danger', hint: 'The poll could not run at all — usually no provider is enrolled for this kind.' },
  'needs render tier': { label: 'Needs render tier', tone: 'warn', hint: 'This page builds its content with JavaScript, so a plain fetch sees an empty shell.' },
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

/** The poll cadences the create form offers. Deliberately coarse: the engine clamps
 *  anything below its own network floor anyway, and a free-text seconds field invites a
 *  value that is abusive to someone else's server. */
export const INTERVAL_CHOICES = [900, 3600, 21600, 86400]
