import { useEffect, useState } from 'react'
import { api, type ProviderHealth } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, SegPills, Toggle, SavedToast } from './settingsUI'
import { NumberField } from '../../ui/forms'
import { FormSkeleton } from '../../ui/ListScaffold'

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

  const { data } = useCachedData('settings:guardrails', () =>
    api.personalclawConfig().then((c) => (c.guardrails ?? {}) as GuardrailsCfg).catch(() => ({} as GuardrailsCfg)),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data || !cfg) return <FormSkeleton sections={3} />

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

      <ProviderHealthSection />
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
