import { useEffect, useMemo, useState } from 'react'
import { api, type AutonomyLadder, type AutonomyReversal, type AutonomyType, type ProviderHealth } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, SegPills, Toggle, SavedToast } from './settingsUI'
import { NumberField } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { RungChip } from '../../ui/RungChip'
import { rungMeta } from '../../lib/rungs'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

/** Guardrails — the personal safety floor (AUTONOMY-GUARDRAILS). Four groups:
 *  the incident kill switch, daily spend budgets, the outbound secret/PII scan mode,
 *  and the per-provider circuit-breaker tuning — plus a derived provider-health view.
 *  Budget/scan/breaker controls PATCH allowlisted guardrails.* config paths; incident
 *  is its own endpoint (not config). */
type GuardrailsCfg = {
  budgets?: { max_tokens_per_run?: number; max_tokens_per_day?: number; max_dollars_per_day?: number }
  breaker?: { failure_threshold?: number; recovery_secs?: number }
  scan_mode?: string
}

export function GuardrailsPanel() {
  const [cfg, setCfg] = useState<GuardrailsCfg | null>(null)

  const { data, error: loadErr, refresh } = useCachedData('settings:guardrails', () =>
    api.personalclawConfig().then((c) => (c.guardrails ?? {}) as GuardrailsCfg),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  // 🔴 A settings panel must not present FABRICATED values as saved state. `.catch(() => ({}))` made a
  // failed config read resolve with an empty section, so every control below rendered at its fallback —
  // indistinguishable from "this is what you saved" — and the panel offered to edit values it had never
  // loaded. Measured on `#/settings/agent` with `/api/config` at 500: the form rendered in full with no
  // error anywhere. Now the rejection reaches the hook and the form is replaced by the failure.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={3} what="settings" />

  const patchNum = (path: string, value: number) =>
    api.patchConfig(`guardrails.${path}`, value).catch((e) => {
      notify(`Couldn't save ${path}: ${String((e as Error)?.message || e)}`, 'error')
    })

  return (
    <div>
      <PanelHeader title="Guardrails" hint="The personal safety floor for unattended work — a daily spend ceiling, an outbound secret scan, provider circuit breakers, and a kill switch. Interactive chat is never affected by these." />

      <IncidentSection />

      <Section title="Daily budget" hint="Cap what your automations spend in a day. At the ceiling, further unattended runs are skipped (a cron fire is paused, a subagent spawn refused) and resume automatically the next day. 0 = unlimited.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Max tokens / day" hint="Across every trigger. 0 = unlimited."
            value={cfg.budgets?.max_tokens_per_day ?? 0} min={0} step={1000}
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_tokens_per_day: v } })); return patchNum('budgets.max_tokens_per_day', v) }} />
          <NumberRow label="Max dollars / day" hint="Estimated from per-model pricing. 0 = unlimited."
            value={cfg.budgets?.max_dollars_per_day ?? 0} min={0} step={1} dollars
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_dollars_per_day: v } })); return patchNum('budgets.max_dollars_per_day', v) }} />
          <NumberRow label="Max tokens / run" hint="Per single unattended run (a goal-loop cycle, a cron fire). 0 = unlimited."
            value={cfg.budgets?.max_tokens_per_run ?? 0} min={0} step={1000}
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_tokens_per_run: v } })); return patchNum('budgets.max_tokens_per_run', v) }} />
        </div>
      </Section>

      <Section title="Outbound scan" hint="How a prompt bound for a REMOTE model provider is handled when it contains secrets or PII. Local models always warn (their content never leaves your machine).">
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <Field label="Scan mode" hint="warn = log & send · redact = substitute & send · block = refuse the call.">
            <SegPills value={String(cfg.scan_mode ?? 'redact')}
              onChange={(v) => { setCfg((c) => ({ ...c, scan_mode: v })); api.patchConfig('guardrails.scan_mode', v).catch((e) => notify(`Couldn't save scan mode: ${String((e as Error)?.message || e)}`, 'error')) }}
              options={[{ key: 'warn', label: 'Warn' }, { key: 'redact', label: 'Redact' }, { key: 'block', label: 'Block' }]} />
          </Field>
        </div>
      </Section>

      <Section title="Circuit breaker" hint="Per-provider fail-fast: after N consecutive failures a provider's breaker opens, so unattended runs fail in microseconds during an outage instead of stacking timeouts.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Failure threshold" hint="Consecutive failures before the breaker opens."
            value={cfg.breaker?.failure_threshold ?? 5} min={1} step={1}
            onSave={(v) => { setCfg((c) => ({ ...c, breaker: { ...c?.breaker, failure_threshold: v } })); return patchNum('breaker.failure_threshold', v) }} />
          <NumberRow label="Recovery seconds" hint="How long an open breaker waits before a half-open probe."
            value={cfg.breaker?.recovery_secs ?? 30} min={0} step={5}
            onSave={(v) => { setCfg((c) => ({ ...c, breaker: { ...c?.breaker, recovery_secs: v } })); return patchNum('breaker.recovery_secs', v) }} />
        </div>
      </Section>

      <AutonomyLadderSection />

      <ProviderHealthSection />
    </div>
  )
}

// ── Earned autonomy: the rung ladder (AUTONOMY-GUARDRAILS §5-§6.1) ──────────
/** The ladder panel: what each automated action may do on its own, what it has earned, and
 *  the two things a user can do about it — promote (a click, never automatic) or hand the
 *  autonomy back. Plus the undo list for actions that already ran at the with-undo rung.
 *
 *  This is the surface that OWNS the ladder read, so it is the one that must state a failed
 *  read out loud: a chip elsewhere can go quiet, but a panel that silently rendered an empty
 *  ladder would say "no automation has any autonomy" — a reassuring claim about a safety
 *  control, produced by a failed request. */
function AutonomyLadderSection() {
  const { data: ladder, error: loadErr, refresh } = useCachedData('autonomy:ladder', () => api.autonomyLadder(), { persist: true })
  const [busy, setBusy] = useState('')
  const reload = () => { invalidateCache('autonomy:ladder'); refresh() }

  // Eligible first (the only rows with a decision to make), then least autonomy first — a
  // type that drafts or asks is one the user may want to promote, while a row that already
  // runs on its own needs no attention. Alphabetical inside each tier so the list is stable.
  const types = useMemo(() => {
    const order = ladder?.rungs ?? []
    return [...(ladder?.types ?? [])].sort((a, b) =>
      Number(b.eligible) - Number(a.eligible) ||
      order.indexOf(a.resolved_rung) - order.indexOf(b.resolved_rung) ||
      a.key.localeCompare(b.key),
    )
  }, [ladder])

  const promote = async (t: AutonomyType) => {
    setBusy(t.key)
    try {
      const r = await api.autonomyGrant(t.key, t.next_rung)
      notify(`${t.key} now ${rungMeta(r.rung, ladder ?? null).label}.`, 'success')
      reload()
    } catch (e) {
      notify(`Couldn't promote ${t.key}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy('') }
  }
  const handBack = async (t: AutonomyType) => {
    setBusy(t.key)
    try {
      await api.autonomyDemote(t.key)
      notify(`${t.key} is back at ${rungMeta(t.floor, ladder ?? null).label}.`, 'success')
      reload()
    } catch (e) {
      notify(`Couldn't change ${t.key}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy('') }
  }

  return (
    <Section title="Earned autonomy" hint="Every automated action starts at the rung it was declared with and can only climb when you say so. A single rejection, thumbs-down or undo drops it back immediately and starts a cooldown.">
      {!ladder && loadErr ? (
        <LoadError what="the autonomy ladder" error={loadErr} onRetry={reload} />
      ) : !ladder ? (
        <div className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">Loading…</div>
      ) : (
        <div className="flex flex-col gap-l">
          {ladder.incident_active && (
            <div role="alert" className="rounded-lg px-4 py-3 text-[0.8125rem] bg-error/10 ring-1 ring-error/40 text-on-surface-var">
              Incident mode is active, so nothing runs above “asks first” — a granted rung shows as held until you resume.
            </div>
          )}
          <div className="rounded-lg bg-surface-container px-4 py-1">
            {types.map((t) => (
              <div key={t.key} className="flex items-start justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-on-surface text-[0.8125rem]">{t.key}</span>
                    <RungChip type={t} ladder={ladder} />
                    {t.leaves_machine && (
                      <span className="text-on-surface-low text-[0.6875rem]" title="Its effect is visible outside this machine, so a track record can never propose full autonomy for it.">leaves this machine</span>
                    )}
                  </div>
                  {/* WHY it runs at this rung, then WHAT it has earned. Both, always: the first
                      is the answer to "why is this allowed to run by itself", the second is the
                      only thing that decides whether a promote button appears — and when it does
                      not appear, the record says what is missing rather than leaving a user to
                      guess why a row is inert. */}
                  <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{t.authority}</div>
                  <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{t.record}</div>
                  {t.demotions.length > 0 && (
                    <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
                      Last demoted {t.demotions[t.demotions.length - 1].at.slice(0, 10)} — {t.demotions[t.demotions.length - 1].cause}
                      {t.demotions.length > 1 && ` (${t.demotions.length} demotions on record)`}
                    </div>
                  )}
                </div>
                <div className="shrink-0 flex items-center gap-2">
                  {t.eligible && t.next_rung && (
                    <Button size="xs" variant="secondary" disabled={busy === t.key} onClick={() => promote(t)}>
                      Promote to {rungMeta(t.next_rung, ladder).label}
                    </Button>
                  )}
                  {t.granted_at && (
                    <Button size="xs" variant="ghost" disabled={busy === t.key} onClick={() => handBack(t)}
                      title={`Drop ${t.key} back to ${rungMeta(t.floor, ladder).label} and start its cooldown.`}>
                      Hand back
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <UndoList ladder={ladder} onChange={reload} />
        </div>
      )}
    </Section>
  )
}

/** Actions that ran at the with-undo rung and can still be taken back.
 *
 *  The undo is offered from a PERSISTED record, so this list is the same source the
 *  notification's undo button uses — one place decides whether an undo is still available,
 *  rather than a page holding a handle it hopes is still good. */
function UndoList({ ladder, onChange }: { ladder: AutonomyLadder; onChange: () => void }) {
  const [busy, setBusy] = useState('')
  const pending = ladder.reversals.filter((r) => !r.reversed_at)

  const undo = async (r: AutonomyReversal) => {
    setBusy(r.id)
    try {
      // A refused undo comes back as a non-2xx and lands in `catch` carrying the server's
      // named reason, so there is no in-band failure branch to write here — and the list
      // reloads either way, because the record's state is what decides whether the button
      // should still be offered.
      await api.autonomyUndo(r.id)
      notify(`Undone. ${r.action_type} will ask again from now on.`, 'success')
    } catch (e) {
      notify(`Couldn't undo: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(''); onChange() }
  }

  return (
    <div>
      <div className="mb-s text-on-surface-low text-[0.75rem] uppercase tracking-wide">Automatic actions you can still undo</div>
      <div className="rounded-lg bg-surface-container px-4 py-1">
        {pending.length === 0 ? (
          <div className="py-3 text-on-surface-low text-[0.8125rem]">Nothing is waiting to be undone — no action has run at the “runs with undo” rung yet.</div>
        ) : pending.map((r) => (
          <Row key={r.id} label={r.label || r.action_type}
            hint={`Ran ${r.created_at.slice(0, 16).replace('T', ' ')}. Undoing it also stops ${r.action_type} from doing this on its own.`}>
            <Button size="xs" variant="secondary" disabled={busy === r.id} onClick={() => undo(r)}>Undo</Button>
          </Row>
        ))}
      </div>
    </div>
  )
}

// ── Incident kill switch ────────────────────────────────────────────────────
function IncidentSection() {
  const [state, setState] = useState<{ active: boolean; reason: string; started_at: string } | null>(null)
  const [busy, setBusy] = useState(false)
  // 🔴 A failed read used to substitute `{ active: false }`, i.e. it FABRICATED "no incident is
  // active" — a claim about a safety control. Measured with `/api/incident` at 500: this row rendered
  // "Incident mode · Off — automation runs normally", with the toggle ENABLED and pointing the wrong
  // way, and nothing said anywhere. Now the state stays `null` (unknown), which already disables the
  // toggle via `!state`, and the hint says what happened instead of guessing.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const load = () => api.incident().then((s) => { setState(s); setLoadErr(null) }).catch(setLoadErr)
  useEffect(() => { load() }, [])

  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) setState(await api.incidentOn('Activated from Settings'))
      else { await api.incidentResume(); setState({ active: false, reason: '', started_at: '' }) }
    } catch (e) {
      notify(`Couldn't ${on ? 'activate' : 'resume'} incident mode: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }

  return (
    <Section title="Incident kill switch" hint="Suspend ALL unattended work — cron jobs, hooks, event triggers, subagent spawns — at once. Interactive chat keeps working. Resume is explicit.">
      <div className={`rounded-lg px-4 py-1 ${state?.active ? 'bg-error/10 ring-1 ring-error/40' : 'bg-surface-container'}`}>
        <Row label={state?.active ? 'Incident mode is ACTIVE' : 'Incident mode'}
          hint={loadErr
            ? `Couldn't check whether incident mode is active: ${String((loadErr as Error)?.message || loadErr)}`
            : state?.active ? `Unattended work suspended${state.reason ? ` — ${state.reason}` : ''}. Turn off to resume.`
            : state ? 'Off — automation runs normally.'
            : 'Checking…'}>
          <Toggle on={Boolean(state?.active)} disabled={busy || !state} onChange={toggle} label="Incident mode" />
        </Row>
      </div>
    </Section>
  )
}

// ── Provider health (derived) ───────────────────────────────────────────────
function ProviderHealthSection() {
  const [rows, setRows] = useState<ProviderHealth[] | null>(null)
  // Same shape as the incident read above: `.catch(() => setRows([]))` turned "we could not check" into
  // "no background model calls recorded yet" — a reassuring sentence about a health surface, produced by
  // a failed request. The rows stay `null` and the failure is stated.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const refresh = () => api.modelsHealth().then((r) => { setRows(r.providers); setLoadErr(null) }).catch(setLoadErr)
  useEffect(() => { refresh() }, [])

  return (
    <Section title="Provider health" hint="Derived from the model-call audit — breaker state, latency, and recent failures per provider. No data leaves your machine.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        {loadErr ? (
          <div role="alert" className="text-on-surface-low text-[0.8125rem]">
            Couldn't check provider health: {String((loadErr as Error)?.message || loadErr)}
          </div>
        ) : rows === null ? (
          <div className="text-on-surface-low text-[0.8125rem]">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">No background model calls recorded yet.</div>
        ) : (
          <div className="flex flex-col gap-2">
            {rows.map((p) => <HealthRow key={p.name} p={p} />)}
          </div>
        )}
      </div>
    </Section>
  )
}

/** Exported for test: jsdom reports every box as 0, so the only way to pin this row's derived
 *  values (mode ordering, the p99 threshold) is to render the component directly. */
export function HealthRow({ p }: { p: ProviderHealth }) {
  // Dominant mode first — with a handful of modes the ordering is what makes the row scannable.
  const failureModes = Object.entries(p.failure_modes ?? {}).sort((a, b) => b[1] - a[1])
  const stateColor = p.breaker_state === 'open' ? 'var(--color-error)'
    : p.breaker_state === 'half_open' ? 'var(--color-warning)' : 'var(--color-success)'
  const stateLabel = p.breaker_state === 'open' ? 'Open' : p.breaker_state === 'half_open' ? 'Half-open' : 'Closed'
  return (
    <div className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-2 last:border-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-on-surface text-[0.8125rem]">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: stateColor }} />
          <span className="truncate">{p.name}</span>
          <span className="text-on-surface-low">· {stateLabel}</span>
          {p.degraded && <span style={{ color: 'var(--color-warning)' }}>· degraded</span>}
        </div>
        <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
          {p.calls} calls · {p.pass_rate === null ? '—' : `${Math.round(p.pass_rate * 100)}% ok`}
          {p.p90_ms > 0 && ` · p90 ${Math.round(p.p90_ms)}ms`}
          {/* The tail, only when it diverges from p90. `provider_health` computes p50/p90/p99 and
              this row rendered p90 alone, so a provider that is usually fast but occasionally
              stalls looked identical to one that is uniformly fast — the exact case a circuit
              breaker exists for. Shown only when p99 is materially worse, so a healthy provider
              keeps a one-line summary instead of two numbers that always agree. */}
          {p.p99_ms > 0 && p.p99_ms >= p.p90_ms * 1.5 && ` (p99 ${Math.round(p.p99_ms)}ms)`}
          {p.failed > 0 && ` · ${p.failed} failed`}
        </div>
        {/* WHY it failed, not just how often. `failure_modes` is the per-mode tally behind the
            `failed` count above; without it "12 failed" gives no way to tell a rate limit from a
            bad key from a timeout, which are three different user actions. Ordered by frequency
            so the dominant mode reads first. */}
        {failureModes.length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {failureModes.map(([mode, n]) => (
              <span key={mode}
                className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.6875rem] tabular-nums">
                {mode.replace(/_/g, ' ')} ×{n}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── number field renderer (built on the shared NumberField stepper) ─────────
function NumberRow({ label, hint, value, min, step, dollars, onSave }: {
  label: string; hint?: string; value: number; min: number; step: number; dollars?: boolean
  onSave: (v: number) => Promise<unknown>
}) {
  const [saved, setSaved] = useState(false)
  const commit = (n: number) => {
    if (n === value) return
    onSave(n).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) })
  }
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {dollars && <span className="text-on-surface-low text-[0.8125rem]">$</span>}
        <NumberField value={value} min={min} step={step} onChange={commit} ariaLabel={label} />
      </div>
    </Row>
  )
}
