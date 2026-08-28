import { useEffect, useState } from 'react'
import { AlertTriangle, ExternalLink, FileText, UserRound } from 'lucide-react'
import { Button } from '../../ui/Button'
import { LoadError } from '../../ui/ListScaffold'
import { Segmented } from '../../ui/Segmented'
import { fvs } from '../../design/fontWeight'
import type { IdentityReportView } from '../../lib/api'
import { api } from '../../lib/api'

/** A settable cadence. `''` is excluded on purpose: it is the server saying "I could not read your
 *  config", which is a state to REPORT and never a value to write. */
type Cadence = Exclude<IdentityReportView['cadence'], ''>

/** The cadence strip's options. The keys are `learning.identity_report_cadence`'s enum members —
 *  `off` is a MEMBER, not a sibling switch, so there is no second control that could disagree
 *  with this one. `test_lv4_identity_report_schedule.py` asserts these three against the backend's
 *  `IDENTITY_REPORT_CADENCES`, so this copy cannot drift from the vocabulary. */
const CADENCE_OPTIONS: { key: Cadence; label: string }[] = [
  { key: 'monthly', label: 'Monthly' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'off', label: 'Off' },
]

/** The one wording both audiences get. Used verbatim as the visible label AND as the group's
 *  accessible name: a different `ariaLabel` would make the spoken name disagree with the words on
 *  screen, which is the label-in-name failure a bare `Segmented` already cost this app seven times
 *  (`ui/segmentedNamed.test.tsx`). */
const CADENCE_LABEL = 'Write one automatically'

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
  report: IdentityReportView | undefined
  error: unknown
  onRetry: () => void
  onDelivered: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [slug, setSlug] = useState('')
  // The cadence is held locally so the strip moves on click rather than after a refetch, and
  // re-synced from the server value whenever the report reloads — GuardrailsPanel's shape. A
  // strip that waited for the round trip would look broken on a slow save; one that never
  // re-synced would keep showing an optimistic value a failed PATCH never persisted.
  const [cadence, setCadence] = useState(report?.cadence ?? '')
  useEffect(() => { if (report?.cadence !== undefined) setCadence(report.cadence) }, [report?.cadence])

  // A failed fetch renders as an EMPTY STATE unless the error is read — and "nothing has been
  // learned" is the one claim this panel must never make by accident.
  if (report === undefined && error) {
    return <LoadError what="identity report" error={error} onRetry={onRetry} />
  }
  if (!report) return null

  const setCadenceTo = async (next: string) => {
    // Narrowed against the strip's OWN option list rather than cast. `Segmented` hands back a
    // bare string, and a cast would let a future typo'd option key reach the PATCH and come back
    // a 400 the user has to interpret.
    const chosen = CADENCE_OPTIONS.find((o) => o.key === next)?.key
    if (!chosen) return
    const previous = cadence
    setCadence(chosen)
    setFailure('')
    try {
      await api.patchConfig('learning.identity_report_cadence', chosen)
      // Re-read: the window the header states is DERIVED from the cadence server-side, so a
      // saved change that left "last 30 days" beside "Weekly" would be the panel disagreeing
      // with the document its own button writes.
      onRetry()
    } catch (e) {
      // Reverted, not left optimistic. A strip showing "Off" after a failed save is a settings
      // panel presenting a value it never persisted — the exact claim GuardrailsPanel's 🔴
      // comment records as the defect worth a rail.
      setCadence(previous)
      // Named with the control's OWN visible words, not with the config path: GuardrailsPanel's 🪤
      // records the defect of saying "Couldn't save budgets.max_dollars_per_day" about a control
      // the UI calls "Max dollars / day".
      setFailure(`Couldn't save “${CADENCE_LABEL}”: ${String((e as Error)?.message || e)}`)
    }
  }

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

      {/* The cadence — the ONE switch. `off` is a member of this strip, so there is no second
          control that could disagree with it, and the strip is what makes the scheduled half
          discoverable at all: before this the job existed and nothing on any surface said so. */}
      <div className="flex flex-wrap items-baseline gap-s">
        <span data-testid="cadence-label" className="text-on-surface-var text-[0.8125rem]">
          {CADENCE_LABEL}
        </span>
        {report.cadence === '' ? (
          // A settings control must not present a FABRICATED value as saved state. An unreadable
          // config gives `''`, and rendering the strip at its first option would claim "Monthly"
          // is what you saved — indistinguishable from the truth, and wrong.
          <span className="text-warn text-[0.8125rem]">
            Your settings could not be read, so this cannot be shown or changed here.
          </span>
        ) : (
          <>
            <Segmented
              size="sm"
              ariaLabel={CADENCE_LABEL}
              value={cadence}
              options={CADENCE_OPTIONS}
              onChange={setCadenceTo}
            />
            <span className="text-on-surface-low text-[0.8125rem]">
              {cadence === 'off'
                ? 'Nothing is scheduled — use “Write it up” whenever you want one.'
                : 'Saved to your inbox on this cadence, with a versioned copy you can reread.'}
            </span>
          </>
        )}
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
