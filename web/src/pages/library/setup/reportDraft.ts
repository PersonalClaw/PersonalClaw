import type { ResearchReport, ResearchReportInput } from '../../../lib/api'
import { relFuture } from '../../schedule/scheduleMeta'

// ── A report's draft ──────────────────────────────────────────────────────────────────────────────
//
// The blank and editable forms of a scheduled report, what an edit changed, and its next run in words.

/** A blank definition. `cite-source-only` is the default because the narrower policy is the
 *  one a reader can trust without knowing the report's configuration: every marker points at
 *  something the report was actually asked to monitor. */
export function blank(): ResearchReportInput {
  return {
    name: '',
    prompt: '',
    schedule: { kind: 'cron', cron_expr: '0 8 * * *' },
    tz: '',
    source: { tags: [], window_secs: 0 },
    context: null,
    citation_policy: 'cite-source-only',
    iteration_cap: 3,
    enabled: true,
  }
}

/** The editable part of a report, for its edit form. */
export function editable(r: ResearchReport): ResearchReportInput {
  return {
    name: r.name,
    prompt: r.prompt,
    schedule: r.schedule,
    tz: r.tz,
    source: r.source,
    context: r.context,
    citation_policy: r.citation_policy,
    iteration_cap: r.iteration_cap,
    enabled: r.enabled,
  }
}

/** A next run as a date and time in the zone the report runs in ("Mon, Oct 5, 8:00 AM EDT"),
 *  and how far off it is. A zone this browser does not know reads in the browser's own. */
export function nextRun(iso: string, timezone: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const style: Intl.DateTimeFormatOptions = {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  }
  let said: string
  try { said = at.toLocaleString(undefined, { ...style, timeZone: timezone || undefined }) }
  catch { said = at.toLocaleString(undefined, style) }
  const off = relFuture(iso)
  return off ? `${said}, ${off}` : said
}

/** What an edit changed: only the fields that differ from the report as it was opened, so a save
 *  that touched the prompt sends no schedule, and a schedule the form cannot show (an interval or
 *  a one-off set elsewhere) is never rewritten by a save that left it alone. */
export function changed(draft: ResearchReportInput, initial: ResearchReportInput): Partial<ResearchReportInput> {
  const out: Partial<ResearchReportInput> = {}
  for (const key of Object.keys(draft) as (keyof ResearchReportInput)[]) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(initial[key])) Object.assign(out, { [key]: draft[key] })
  }
  return out
}
