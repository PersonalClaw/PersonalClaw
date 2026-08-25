import { useState } from 'react'
import { AlertTriangle, ExternalLink, FileText, UserRound } from 'lucide-react'
import { Button } from '../../ui/Button'
import { LoadError } from '../../ui/ListScaffold'
import { fvs } from '../../design/fontWeight'
import type { IdentityReport } from '../../lib/api'
import { api } from '../../lib/api'

/** The periodic identity report — "how I've adapted to you" (LV-4).
 *
 *  **Never a modal.** The plan's amendment is explicit about that, so this is a panel on a page
 *  the user chose to open. Nothing here interrupts.
 *
 *  Two states, one surface. On mount the panel renders the DETERMINISTIC report (a GET that
 *  spends no model call), so the numbers are there before anyone clicks. "Write it up" is the
 *  POST: it composes the narrative, persists a versioned artifact and raises one inbox item —
 *  the same backend function a scheduled job calls, so the hand-run and the monthly run cannot
 *  diverge.
 *
 *  **Counts come from the server's `count`, never from `items.length`.** Every section ships an
 *  exact count plus a capped sample, and rendering the sample's length would under-report the
 *  moment a home got busy. Where the sample is short, the panel says so.
 *
 *  **A degraded narrative is stated, not hidden.** `narrative_status === 'unavailable'` means a
 *  narrative was attempted and no model answered; the figures are unaffected and the panel says
 *  exactly that, because a blank space reads as "nothing to say". */
export function IdentityReportPanel({ report, error, onRetry, onDelivered }: {
  report: IdentityReport | undefined
  error: unknown
  onRetry: () => void
  onDelivered: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [slug, setSlug] = useState('')

  // A failed fetch renders as an EMPTY STATE unless the error is read — and "nothing has been
  // learned" is the one claim this panel must never make by accident.
  if (report === undefined && error) {
    return <LoadError what="identity report" error={error} onRetry={onRetry} />
  }
  if (!report) return null

  const write = async () => {
    setBusy(true)
    setFailure('')
    try {
      const delivery = await api.deliverIdentityReport(report.window_days)
      setSlug(delivery.artifact_slug)
      onDelivered()
    } catch (e) {
      setFailure(e instanceof Error ? e.message : 'could not write the report')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-m" aria-labelledby="identity-report-heading">
      <div className="flex flex-wrap items-center gap-s">
        <UserRound size={16} className="text-on-surface-var" />
        <span id="identity-report-heading" data-type="title-m" className="text-on-surface">
          How I've adapted to you
        </span>
        <span className="text-on-surface-low text-[0.75rem]">
          last {report.window_days} days
        </span>
        <span className="flex-1" />
        {/* `disabledReason` for the EMPTY-record gate, native `disabled` for the in-flight one.
            The distinction is the `disabledReasonTriage` ratchet's whole subject: "nothing has
            been learned yet" is a state the user can act on, so the button must stay reachable
            and say so (aria-disabled, focusable), while a busy gate must stay native or an
            in-flight action becomes re-clickable. Passing a reason for `busy` would do exactly
            that. Measured: the first draft put both on `title` and reds the ratchet. */}
        <Button
          onClick={write}
          disabled={busy || report.total === 0}
          title="Compose the narrative, save it as a versioned artifact and send it to your inbox."
          disabledReason={report.total === 0
            ? 'Nothing has been learned yet, so there is nothing to write up.'
            : undefined}
        >
          <FileText size={14} /> {busy ? 'Writing…' : 'Write it up'}
        </Button>
      </div>

      {failure && (
        <p className="text-danger text-[0.8125rem]" role="alert">{failure}</p>
      )}
      {slug && (
        <p className="text-on-surface-low text-[0.8125rem]">
          Saved and sent to your inbox.{' '}
          <a className="text-primary underline" href={`#/artifacts/${encodeURIComponent(slug)}`}>
            Open the report <ExternalLink size={12} className="inline align-baseline" />
          </a>
        </p>
      )}

      {report.narrative && (
        <p className="rounded-lg bg-surface-container px-l py-l text-on-surface text-[0.9375rem]">
          {report.narrative}
        </p>
      )}
      {report.narrative_status === 'unavailable' && (
        <p
          className="inline-flex items-center gap-1.5 text-warn text-[0.8125rem]"
          title="The figures are gathered without a model. Only the prose needs one."
        >
          <AlertTriangle size={12} />
          No model was available to summarise this period — the figures below are unaffected.
        </p>
      )}

      <div className="grid gap-m" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(14rem, 1fr))' }}>
        <Group
          title="Preferences I hold"
          count={report.facets.count}
          shown={report.facets.items.length}
          lines={report.facets.items.map((f) => `${f.text} — ${f.cls}, ${f.state}`)}
        />
        <Group
          title="Lessons I follow"
          count={report.lessons.count}
          shown={report.lessons.items.length}
          lines={report.lessons.items.map((l) => l.text)}
        />
        <Group
          title="Skills I built"
          count={report.skills.count}
          shown={report.skills.items.length}
          lines={report.skills.items.map((s) => `${s.name} — ${s.uses} use${s.uses === 1 ? '' : 's'}, ${s.aging_state}`)}
        />
        <Group
          title="Waiting on you"
          count={report.proposals.count}
          shown={report.proposals.items.length}
          lines={report.proposals.items.map((p) => p.label)}
        />
      </div>
    </section>
  )
}

/** One section. `count` is the server's exact figure; `shown` is how much of it is listed.
 *
 *  The two are separate props rather than `lines.length` twice, so a capped sample cannot make
 *  the heading lie — and the "of N" note appears only when something really was dropped. */
function Group({ title, count, shown, lines }: {
  title: string
  count: number
  shown: number
  lines: string[]
}) {
  return (
    <div className="flex min-w-0 flex-col gap-s rounded-lg bg-surface-container px-l py-l">
      <div className="flex items-baseline gap-s">
        <span data-type="title-s" className="text-on-surface" style={fvs(600)}>{count}</span>
        <span className="text-on-surface-var text-[0.8125rem]">{title}</span>
      </div>
      {lines.length === 0
        ? <p className="text-on-surface-low text-[0.75rem]">Nothing recorded yet.</p>
        : (
          <ul className="flex flex-col gap-1">
            {lines.map((line) => (
              <li key={line} className="truncate text-on-surface-low text-[0.75rem]" title={line}>{line}</li>
            ))}
          </ul>
        )}
      {shown < count && (
        <p className="text-on-surface-low text-[0.6875rem]">Showing {shown} of {count}.</p>
      )}
    </div>
  )
}
