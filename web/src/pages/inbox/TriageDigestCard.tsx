import { useState } from 'react'
import { AlertTriangle, CheckCheck, Clock, ExternalLink, Power, RotateCcw, ScrollText, Sparkles } from 'lucide-react'
import { api, type TriageDigestView, type TriagePending } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Surface } from '../../ui/Surface'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { TextInput } from '../../ui/forms'
import { notify } from '../../app/appSdk'
import { fvs } from '../../design/fontWeight'

/** The triage digest card (PROACTIVE-ASSISTANT §5.1) + the Morning-triage pack card (§5.4).
 *
 *  🪤 THE DEFECT THIS CARD IS BUILT TO REFUSE. A digest surface has five different reasons to
 *  show no items, and four of them are not "you're all caught up":
 *
 *    the read failed          → say so, with the error, and offer a retry
 *    never installed          → offer the install; there is no schedule
 *    installed but switched off → say dormant-but-kept; the rules are still there
 *    installed, not yet run   → say when it will run
 *    ran, nothing to report   → *this* is the only one that is good news
 *
 *  So `view.state` is a five-way switch and not a `length === 0` check. The same rule applies one
 *  level down: `auto_stage_ran === false` prints "auto-execution is off", never "0 actions
 *  taken", and `ledger_complete === false` prints "some rationales were not recorded", never a
 *  clean empty list. An unmeasured value is not a zero.
 *
 *  Acting is explicit and never happens on view: the read is a GET and every button is a POST the
 *  user pressed. The reply buttons emit the SAME grammar a channel reply uses ('3 yes', 'always
 *  no 3'), so there is one parser and the taps cannot drift from the typed form. */
export function TriageDigestCard() {
  // No `.catch(() => null)`. A swallowed rejection would hand this component `undefined` and the
  // card would render its "not installed yet" arm — offering an install for something that may
  // already be running, because the request failed.
  const { data: view, error, refresh } = useQuery<TriageDigestView>('proactive:digest', () => api.proactiveDigest(), { persist: false })
  const [busy, setBusy] = useState('')
  const [cron, setCron] = useState('')
  const [help, setHelp] = useState('')

  if (error && view === undefined) {
    return (
      <Surface tone="container" radius="xl" className="mb-l p-l">
        <Header title="Morning triage" />
        <InlineError icon onRetry={refresh}>
          Couldn't read your digest: {String((error as Error)?.message || error)}
        </InlineError>
      </Surface>
    )
  }
  if (!view) return null

  // `state: 'error'` is the server's own verdict — a store read raised and it said so rather than
  // returning empty sections. Rendered with the same band as a failed fetch, because to the user
  // they are the same fact: the digest could not be read.
  if (view.state === 'error') {
    return (
      <Surface tone="container" radius="xl" className="mb-l p-l">
        <Header title="Morning triage" />
        <InlineError icon onRetry={refresh}>Couldn't read your digest: {view.error}</InlineError>
      </Surface>
    )
  }

  const install = (withCron?: string) => {
    setBusy('install')
    api.proactiveInstall(withCron)
      .then((r) => { notify(r.created ? 'Morning triage installed.' : 'Morning triage schedule updated.', 'success'); refresh() })
      .catch((e) => notify(`Couldn't install Morning triage: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  // §5.4's PACK CARD. Not an empty state — there is nothing to be empty. The cron field is the
  // "editable trigger" the criterion asks for: it is editable BEFORE the install, so the user
  // never has to install a schedule at a time they did not choose and then go fix it.
  if (view.state === 'uninstalled') {
    return (
      <Surface tone="container" radius="xl" className="mb-l p-l">
        <div className="flex items-start gap-m">
          <Sparkles size={18} className="mt-0.5 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="text-on-surface text-[0.9375rem]" style={fvs(600)}>Morning triage</h2>
            <p className="mt-1 text-on-surface-low text-[0.8125rem]">
              One scheduled digest: collect what accumulated across your inbox, channels and background runs,
              filter it through your own rules, and propose what to do. It proposes — it executes nothing
              unless you switch auto-execution on.
            </p>
            <div className="mt-m flex flex-wrap items-end gap-m">
              <label className="flex flex-col gap-1">
                <span className="text-on-surface-low text-[0.75rem]">When (cron)</span>
                <div className="w-40">
                  <TextInput value={cron} onChange={setCron} placeholder="0 8 * * *" size="sm" mono ariaLabel="Digest schedule (cron)" />
                </div>
              </label>
              <Button size="sm" variant="primary" loading={busy === 'install'} onClick={() => install(cron.trim() || undefined)}>
                Install
              </Button>
            </div>
            <p className="mt-s text-on-surface-low text-[0.75rem]">
              Installing adds a schedule you can edit or pause any time. Turn the digest itself on under{' '}
              {/* `underline` at REST, not `hover:underline`. This link sits mid-sentence, so axe's
                  `link-in-text-block` (serious) fires when it is distinguishable from the surrounding
                  prose by colour alone — measured on #/inbox in CI. The app's standalone links keep
                  `hover:underline` because that rule only applies to a link inside a text block. */}
              <a href="#/settings/inbox" className="text-primary-emphasis underline">Settings → Inbox</a>.
            </p>
          </div>
        </div>
      </Surface>
    )
  }

  // Dormant but KEPT (criterion 10). The schedule and every taught rule survive; re-enabling is
  // lossless. Saying "no digest yet" here would be a different, wrong sentence.
  if (view.state === 'off') {
    return (
      <Surface tone="container" radius="xl" className="mb-l p-l">
        <div className="flex items-start gap-m">
          <Power size={18} className="mt-0.5 shrink-0 text-on-surface-low" />
          <div className="min-w-0 flex-1">
            <Header title="Morning triage" trailing={<Badge tone="muted">Off</Badge>} />
            <p className="mt-1 text-on-surface-low text-[0.8125rem]">
              The digest is switched off, so nothing is collected and nothing is spent. Your schedule
              {view.schedule?.cron ? <> (<code className="font-mono">{view.schedule.cron}</code>)</> : null} and your
              triage rules are kept — turning it back on picks up exactly where you left off.
            </p>
            <p className="mt-s text-[0.8125rem]">
              <a href="#/settings/inbox" className="text-primary-emphasis hover:underline">Turn triage on in Settings → Inbox</a>
            </p>
          </div>
        </div>
      </Surface>
    )
  }

  if (view.state === 'never_run') {
    return (
      <Surface tone="container" radius="xl" className="mb-l p-l">
        <div className="flex items-start gap-m">
          <Clock size={18} className="mt-0.5 shrink-0 text-on-surface-low" />
          <div className="min-w-0 flex-1">
            <Header title="Morning triage" trailing={<Badge tone="muted">Scheduled</Badge>} />
            <p className="mt-1 text-on-surface-low text-[0.8125rem]">
              Installed and on. No digest has run yet
              {view.schedule?.cron ? <> — the schedule is <code className="font-mono">{view.schedule.cron}</code></> : null}.
              This is not an empty digest: there hasn't been one.
            </p>
            {view.schedule_drift && (
              <p className="mt-s text-warn text-[0.75rem]">
                The schedule's own switch disagrees with your triage setting.{' '}
                <Button size="xs" variant="ghost-accent" onClick={() => install()}>Reconcile it</Button>
              </p>
            )}
          </div>
        </div>
      </Surface>
    )
  }

  const runId = view.run_id || ''
  const reply = (text: string) => {
    setBusy(text)
    setHelp('')
    api.proactiveReply(runId, text)
      .then((r) => {
        if (r.outcome === 'help') { setHelp(r.help || r.help_reason || ''); return }
        const first = (r.results || [])[0]
        if (first?.outcome === 'already') notify(`Already answered — nothing ran again.`, 'info')
        else if (first?.rule_error) notify(`Answered, but the rule wasn't saved: ${first.rule_error}`, 'error')
        else if (first?.recorded === false) notify(`Answered, but it wasn't recorded — the next tap would act again.`, 'error')
        else notify(first?.executed ? 'Done.' : 'Noted.', 'success')
        refresh()
      })
      // 🔴 A STALE DIGEST ARRIVES HERE, NOT IN `.then`. The refusal is a 409, and `api.ts`'s `post`
      // THROWS on any non-2xx — so the first version's `r.outcome === 'expired'` branch inside
      // `.then` could never run, and the test that covered it resolved a shape the api layer cannot
      // produce. `ApiError` carries `.status`, which is what makes the two failures separable: an
      // expired digest is a re-read, a network failure is a retry.
      .catch((e) => {
        const status = (e as { status?: number })?.status
        const message = String((e as Error)?.message || e)
        if (status === 409) { notify(message || 'That digest expired.', 'error'); refresh(); return }
        notify(`Couldn't answer: ${message}`, 'error')
      })
      .finally(() => setBusy(''))
  }

  const undo = (reversal: string) => {
    setBusy(reversal)
    api.autonomyUndo(reversal)
      .then((r) => { notify(r.ok ? 'Undone.' : (r.detail || "That couldn't be undone."), r.ok ? 'success' : 'error'); refresh() })
      .catch((e) => notify(`Undo failed: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  const autoDone = view.auto_done || []
  const pending = view.pending || []
  const ledger = view.machine_did || []

  return (
    <Surface tone="container" radius="xl" className="mb-l p-l">
      <Header
        title={view.title || 'Morning triage'}
        trailing={
          <div className="flex items-center gap-s">
            {view.degraded && <Badge tone="warn">Degraded</Badge>}
            {view.permalink && (
              <a href={view.permalink} className="flex items-center gap-1 text-primary-emphasis text-[0.75rem] hover:underline">
                Run journal <ExternalLink size={12} />
              </a>
            )}
          </div>
        }
      />
      <p className="mt-1 text-on-surface-low text-[0.75rem]">
        {view.collected ?? 0} item{(view.collected ?? 0) === 1 ? '' : 's'} in this window
        {view.window_start ? <> since {view.window_start.slice(0, 16).replace('T', ' ')}</> : null}
        {view.dropped ? <> · {view.dropped} filtered by your rules</> : null}
      </p>

      {/* Why there may be no notification for a digest that plainly exists. Rendered from the
          WINDOW, not from a delivery flag: the run cannot know whether the gate held it back (see
          `handed_to_notify`), so the honest sentence names the setting and not an outcome. */}
      {view.quiet_hours?.known === false ? (
        <p className="mt-s text-warn text-[0.75rem]">
          Your notification settings could not be read, so whether this digest reached your notifications is unknown.
        </p>
      ) : view.quiet_hours?.mute_all ? (
        <p className="mt-s text-on-surface-low text-[0.75rem]">
          All notifications are muted, so this digest is here and in the run journal but was not announced.
        </p>
      ) : view.quiet_hours?.enabled ? (
        <p className="mt-s text-on-surface-low text-[0.75rem]">
          Quiet hours {view.quiet_hours.start}–{view.quiet_hours.end}: a digest that lands inside that window is
          held back from your notifications. It is still here, and in the run journal.
        </p>
      ) : null}

      {view.budget_breached && (
        <div className="mt-m">
          <InlineError icon>
            The daily budget ran out mid-digest, so the rest stayed pending: {view.budget_reason || 'no reason recorded'}
          </InlineError>
        </div>
      )}

      {/* ── What your machine did ── */}
      <SectionHead icon={CheckCheck} title="What your machine did" />
      {!view.auto_stage_ran ? (
        // NOT "0 actions". The stage never ran, which is a different fact and the default one.
        <p className="text-on-surface-low text-[0.8125rem]">
          Auto-execution is off — nothing ran without you. Everything below is a proposal.
        </p>
      ) : autoDone.length === 0 ? (
        <p className="text-on-surface-low text-[0.8125rem]">
          Auto-execution ran and found nothing it was allowed to do on its own.
        </p>
      ) : (
        <ul aria-label="What your machine did" className="flex flex-col gap-s">
          {autoDone.map((row) => (
            <li key={`${row.ordinal}-${row.action_type}`} className="flex items-start gap-m rounded-lg bg-surface-high px-m py-s">
              <div className="min-w-0 flex-1">
                <p className="truncate text-on-surface text-[0.8125rem]">
                  <span style={fvs(600)}>{verbFor(row.action_type)}</span>{' '}
                  {row.title || `item ${row.ordinal}`}
                </p>
                <p className="mt-0.5 text-on-surface-low text-[0.75rem]">
                  {row.ok ? 'because of' : 'failed —'} <code className="font-mono">{row.rule}</code>
                  {row.error ? <> · {row.error}</> : null}
                </p>
              </div>
              {row.undoable ? (
                <Button size="xs" variant="secondary" loading={busy === row.reversal} onClick={() => undo(row.reversal)}>
                  <RotateCcw size={12} /> Undo
                </Button>
              ) : (
                // Why there is no button, rather than a button that would fail.
                <span className="shrink-0 text-on-surface-low text-[0.75rem]">no undo recorded</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* ── Needs you ── */}
      <SectionHead icon={AlertTriangle} title="Needs you" count={pending.length} />
      {pending.length === 0 ? (
        <p className="text-on-surface-low text-[0.8125rem]">Nothing is waiting on you in this digest.</p>
      ) : (
        <ul aria-label="Proposals that need you" className="flex flex-col gap-s">
          {pending.map((row) => (
            <PendingRow key={row.ordinal} row={row} busy={busy} onReply={reply} />
          ))}
        </ul>
      )}
      {help && <p className="mt-s text-warn text-[0.75rem]" role="status">{help}</p>}

      {/* ── The ledger ── */}
      <SectionHead icon={ScrollText} title="In the run journal" count={ledger.length} />
      {!view.ledger_complete ? (
        // The provider reported rows it could NOT stamp with a run key. Reporting "none" here
        // would present a recording gap as a result.
        <p className="text-warn text-[0.8125rem]">
          Some of this run's rationales were not recorded, so this list is incomplete.
        </p>
      ) : ledger.length === 0 ? (
        <p className="text-on-surface-low text-[0.8125rem]">This run wrote no ledger rows.</p>
      ) : (
        <ul aria-label="This run's ledger rows" className="flex flex-col gap-1">
          {ledger.map((row) => (
            <li key={`${row.kind}-${row.seq}`} className="flex items-baseline gap-s text-[0.75rem]">
              <code className="shrink-0 font-mono text-on-surface-low">{row.kind}</code>
              <span className="min-w-0 flex-1 truncate text-on-surface-low">
                {row.ordinal ? `#${row.ordinal} ` : ''}{row.action_type ? `${row.action_type} — ` : ''}
                {row.reason || row.outcome || row.verb || row.detail || '—'}
                {row.rule ? <> · <code className="font-mono">{row.rule}</code></> : null}
              </span>
              {row.permalink && (
                <a href={row.permalink} className="shrink-0 text-primary-emphasis hover:underline" aria-label={`Open the run journal for ${row.kind}`}>
                  open
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </Surface>
  )
}

function PendingRow({ row, busy, onReply }: { row: TriagePending; busy: string; onReply: (text: string) => void }) {
  const n = row.ordinal
  // An answered proposal keeps its row and says what was answered. Removing it would make a reply
  // look like it did nothing; re-offering the buttons would invite a second, duplicate answer.
  return (
    <li className="flex flex-col gap-s rounded-lg bg-surface-high px-m py-s sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="truncate text-on-surface text-[0.8125rem]">
          <span className="mr-1 text-on-surface-low">#{n}</span>
          <span style={fvs(600)}>{verbFor(row.action_type)}</span> {row.title || `item ${n}`}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-s text-[0.75rem]">
          <TierBadge tier={row.tier} clamped={row.clamped} />
          {row.source && <span className="text-on-surface-low">{row.source}</span>}
          {/* Same reason as the install-hint link above: this sits in a `<p>` beside the tier badge
              and the source label, so it IS inside a text block and needs the rest-state underline. */}
          {row.item_permalink && (
            <a href={row.item_permalink} className="text-primary-emphasis underline">the item</a>
          )}
        </p>
      </div>
      {row.answered ? (
        <span className="shrink-0 text-on-surface-low text-[0.75rem]">
          You answered <span style={fvs(600)}>{row.answer || 'this'}</span>
        </span>
      ) : (
        <div className="flex shrink-0 flex-wrap gap-1">
          <Button size="xs" variant="primary" loading={busy === `${n} yes`} onClick={() => onReply(`${n} yes`)}>Yes</Button>
          <Button size="xs" variant="secondary" loading={busy === `${n} no`} onClick={() => onReply(`${n} no`)}>No</Button>
          {/* "Always" is offered ONLY when the run recorded a pattern to teach. Without one there
              is nothing narrow to remember, and inventing a pattern from the action type would
              teach a rule far broader than the one thing the user is looking at. */}
          {row.pattern_key ? (
            <>
              <Button size="xs" variant="ghost" loading={busy === `always yes ${n}`} onClick={() => onReply(`always yes ${n}`)}
                title={`Always allow ${row.pattern_key}`}>Always</Button>
              <Button size="xs" variant="ghost" loading={busy === `always no ${n}`} onClick={() => onReply(`always no ${n}`)}
                title={`Never allow ${row.pattern_key}`}>Never</Button>
            </>
          ) : (
            <span className="self-center text-on-surface-low text-[0.75rem]">no pattern to remember</span>
          )}
        </div>
      )}
    </li>
  )
}

const TIER_LABEL: Record<string, string> = {
  trivial: 'trivial',
  low: 'low risk',
  medium: 'needs a look',
  high: 'high risk',
}

function TierBadge({ tier, clamped }: { tier: string; clamped: boolean }) {
  const tone = tier === 'high' ? 'danger' : tier === 'medium' ? 'warn' : tier === 'trivial' ? 'muted' : 'primary'
  // An UNSCORED tier is not a trivial one. A blank tier renders as "untiered", never as the
  // cheapest badge — a badge is a permission cue and the safe default is to say we don't know.
  const label = tier ? (TIER_LABEL[tier] || tier) : 'untiered'
  return (
    <Badge tone={tier ? tone : 'warn'}>
      {label}{clamped ? ' (raised)' : ''}
    </Badge>
  )
}

function Badge({ tone, children }: { tone: 'primary' | 'warn' | 'danger' | 'muted'; children: React.ReactNode }) {
  const cls = tone === 'danger' ? 'bg-danger/15 text-on-danger-tint'
    : tone === 'warn' ? 'bg-warn/15 text-warn'
      : tone === 'primary' ? 'bg-primary/15 text-on-primary-tint'
        : 'bg-surface-highest text-on-surface-low'
  return <span className={`rounded-full px-2 py-0.5 text-[0.75rem] ${cls}`} style={fvs(500)}>{children}</span>
}

function Header({ title, trailing }: { title: string; trailing?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-m">
      <h2 className="min-w-0 truncate text-on-surface text-[0.9375rem]" style={fvs(600)}>{title}</h2>
      {trailing}
    </div>
  )
}

function SectionHead({ icon: Icon, title, count }: { icon: typeof CheckCheck; title: string; count?: number }) {
  return (
    <p className="mb-s mt-l flex items-center gap-s text-on-surface text-[0.8125rem]" style={fvs(600)}>
      <Icon size={14} className="shrink-0 text-on-surface-low" aria-hidden="true" />
      {title}
      {count !== undefined && count > 0 && <span className="text-on-surface-low" style={fvs(400)}>{count}</span>}
    </p>
  )
}

const ACTION_VERB: Record<string, string> = {
  archive: 'Archived',
  mark_read: 'Marked read',
  mute_thread: 'Muted',
  dismiss: 'Dismissed',
  reply_draft: 'Drafted a reply to',
  create_task: 'Filed a task for',
}

/** The action word, or the raw type when we have no phrasing for it — never a guess that reads
 *  gentler than the thing it names. */
function verbFor(actionType: string): string {
  return ACTION_VERB[actionType] || actionType || 'Acted on'
}
