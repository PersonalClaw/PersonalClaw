import { useCallback, useEffect, useState } from 'react'
import { X, Plus } from 'lucide-react'
import { api, type RunnerRow } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useAgentCatalog, ensureBindableAgentName, type AgentOption } from '../../lib/agents'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, Row, Field, SegPills, SavedToast, ToggleRow } from './settingsUI'
import { Combobox } from '../../ui/Combobox'
import { FieldError, NumberField } from '../../ui/forms'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Button } from '../../ui/Button'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { accentChip } from '../../design/accent'

// The editable agent.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (types + ranges are the server's truth; we surface the same bounds).
type AgentCfg = Record<string, unknown>

/** Agent defaults — the default agent for new sessions + the agent-execution
 *  config (approval, subagents, advanced). Each control PATCHes a single
 *  allowlisted path via /api/config/personalclaw. Session/warm-pool settings
 *  live under Chat. */
export function AgentDefaultsPanel() {
  const [cfg, setCfg] = useState<AgentCfg | null>(null)
  const { options: agentOptions, loading: agentsLoading, discovered } = useAgentCatalog()
  const [defaultAgent, setDefaultAgent] = useState('')

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from one
  // cached snapshot. The editable form state (cfg/defaultAgent) is seeded and
  // rehydrated from this read-only `data`; mutations stay optimistic below.
  const { data, error: loadErr, refresh } = useQuery('settings:agent-defaults', async () => {
    const [plaw, agents] = await Promise.all([
      api.personalclawConfig().then((c) => (c.agent ?? {}) as AgentCfg),
      api.agents().then((d) => d.default_agent).catch(() => ''),
    ])
    return { cfg: plaw, defaultAgent: agents }
  }, { persist: true })

  useEffect(() => {
    if (data) { setCfg(data.cfg); setDefaultAgent(data.defaultAgent) }
  }, [data])

  // 🔴 A settings panel must not present FABRICATED values as saved state. `.catch(() => ({}))` made a
  // failed config read resolve with an empty section, so every control below rendered at its fallback —
  // indistinguishable from "this is what you saved" — and the panel offered to edit values it had never
  // loaded. Measured on `#/settings/agent` with `/api/config` at 500: the form rendered in full with no
  // error anywhere. Now the rejection reaches the hook and the form is replaced by the failure.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || agentsLoading) return <FormSkeleton sections={3} what="settings" />

  // Selecting a discovered ACP agent materializes a saved profile for it first,
  // then sets default_agent to that profile name (persistent bindings resolve a
  // saved profile, not an ephemeral discovered agent).
  const onPickDefault = async (value: string) => {
    const name = await ensureBindableAgentName(value, discovered)
    const prev = defaultAgent
    setDefaultAgent(name)
    api.setDefaultAgent(name).catch((e) => {
      setDefaultAgent(prev)
      notify(`Couldn't set the default agent: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  // local optimistic patch helper — updates state, fires the single-field PATCH.
  // A rejected PATCH (e.g. allowlist drift) rolls the optimistic value back and
  // surfaces the server error — a swallowed 400 here looks exactly like a save.
  const patch = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`agent.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Agent defaults" hint="The default agent for new sessions and how agents execute — approval, subagents, and advanced safety knobs. New sessions inherit these unless overridden." />

      <Section title="Default agent" hint="Which agent definition serves a new chat when none is chosen. Native agents and connected ACP-runtime agents are both selectable.">
        <DefaultAgentRow options={agentOptions} value={defaultAgent} onChange={onPickDefault} />
      </Section>

      <Section title="Defaults" hint="Baseline behavior for every session.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <EnumRow label="Approval mode" hint="When the agent must ask before running a tool." cfg={cfg} field="approval_mode" patch={patch}
            options={[{ key: 'auto', label: 'Auto' }, { key: 'interactive', label: 'Ask each time' }, { key: 'trust_reads', label: 'Trust reads' }]} />
          <EnumRow label="Sandbox" hint="Sandbox mode for the ACP provider." cfg={cfg} field="sandbox" patch={patch}
            options={[{ key: 'auto', label: 'Auto' }, { key: 'off', label: 'Off' }]} />
          <ToggleRow label="YOLO mode" cfg={cfg} field="yolo" patch={patch}
            hint="Skip every tool-approval confirmation — overrides approval mode. Only inside a sandbox or for trusted automation." danger />
        </div>
      </Section>

      <RunnersSection />

      <Section title="Subagents" hint="Limits for helper agents the main agent spawns.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Max concurrent subagents" hint="0 = auto-size from this host's CPU and memory." cfg={cfg} field="max_subagents" patch={patch} min={0} max={16} />
          <NumberRow label="Max turns per subagent" cfg={cfg} field="subagent_max_turns" patch={patch} min={1} max={200} />
          <NumberRow label="Subagent timeout" cfg={cfg} field="subagent_timeout_secs" patch={patch} min={60} max={7200} suffix="s" />
          <NumberRow label="Min free memory to spawn" cfg={cfg} field="spawn_min_memory_gb" patch={patch} min={0} max={64} step={0.5} suffix="GB" />
          <StrListField label="Allowed working directories" hint="Roots a subagent may run in." cfg={cfg} field="subagent_cwd_allowed_roots" patch={patch} />
        </div>
      </Section>

      <Section title="Advanced" hint="Delegation, safety enforcement, and diagnostics.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Orchestrator skill" cfg={cfg} field="orchestrator_skill" patch={patch}
            hint="Enable agent delegation — loads the orchestrator skill with the agent roster." />
          <ToggleRow label="Concurrent ACP sessions" cfg={cfg} field="acp_concurrent_sessions" patch={patch}
            hint="Run multiple ACP chats on ONE backend process (multiplexing) instead of one process per session. Only takes effect for backends that support session interleaving." />
          <ToggleRow label="Unattended runs need a verified adapter" cfg={cfg} field="unattended_requires_verified_adapter" patch={patch}
            hint="Refuse an unattended spawn — a cron fire, a loop-cycle worker, a subagent, the background session, an inbox or side sweep, a channel delivery, or a trigger dispatch — onto a runner whose ACP adapter has no verified provenance: an npx fetch-at-launch, an adapter that changed since it was provisioned, or a runner with no catalog row. Interactive chat is never gated. See each runner's adapter state under Runners." />
          <EnumRow label="Log level" hint="Backend logger level (overridden by --verbose)." cfg={cfg} field="log_level" patch={patch}
            options={[{ key: 'DEBUG', label: 'Debug' }, { key: 'INFO', label: 'Info' }, { key: 'WARNING', label: 'Warning' }, { key: 'ERROR', label: 'Error' }]} />
          <NumberRow label="Soft-stop budget" cfg={cfg} field="soft_stop_budget_secs" patch={patch} min={0.5} max={60} step={0.5} suffix="s"
            hint="Seconds to wait for a cooperative cancel before hard-killing a session." />
          <NumberRow label="Runner health check interval" cfg={cfg} field="runner_health_check_secs" patch={patch} min={60} max={86400} step={60} suffix="s"
            hint="How long a runner's measured health stays current. Past this, its row under Runners is marked check overdue rather than presenting an old reading as the present state. Nothing is probed automatically — use Re-check runners." />
        </div>
        {/* multi-agent space concurrency (max_spaces / max_space_agents) lives in
            Settings → Spaces, not here. */}
      </Section>
    </div>
  )
}

function DefaultAgentRow({ options, value, onChange }: { options: AgentOption[]; value: string; onChange: (v: string) => void }) {
  const [saved, setSaved] = useState(false)
  // The stored default is a profile NAME; surface it as a selectable option even
  // if it isn't (yet) in the catalog (e.g. a freshly-materialized ACP profile).
  const opts = options.some((o) => o.value === value) || !value
    ? options
    : [{ value, label: value, group: 'Current' }, ...options]
  return (
    <div className="rounded-lg bg-surface-container px-4 py-2">
      <Row label="Default agent" hint="Used for every new session.">
        <div className="flex items-center gap-2">
          <SavedToast show={saved} />
          <div className="w-56">
            <Combobox options={opts} value={value} placeholder="Select an agent…" emptyText="No agents"
              onChange={(v) => { onChange(v); setSaved(true); window.setTimeout(() => setSaved(false), 1500) }} />
          </div>
        </div>
      </Row>
    </div>
  )
}

// ── runner catalog (EXECUTION-ISOLATION §3.1) ───────────────────────────────

/** The runner catalog: one row per external agent CLI, with the health evidence
 *  actually measured for it. Every value shown here is a reading or an explicit
 *  "unknown" — an unprobed runner says so, an unparseable version says so, and a
 *  failed probe carries the CLI's own error text verbatim rather than a house
 *  paraphrase, because the verbatim text is what tells you WHICH thing is missing. */
function RunnersSection() {
  const [rows, setRows] = useState<RunnerRow[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [probing, setProbing] = useState(false)

  const load = useCallback((probe: boolean) => {
    if (probe) setProbing(true)
    api.agentRunners(probe)
      .then((r) => { setRows(r); setErr(null) })
      .catch((e) => setErr(e))
      .finally(() => setProbing(false))
  }, [])

  useEffect(() => { load(false) }, [load])

  return (
    <Section title="Runners"
      hint="External agent CLIs this install can drive. Health is measured by asking each CLI for its own version — nothing else is spawned."
      right={<Button size="xs" variant="secondary" loading={probing} onClick={() => load(true)}>Re-check runners</Button>}>
      {!rows && err ? <LoadError what="runners" error={err} onRetry={() => load(false)} />
        : !rows ? <FormSkeleton sections={1} rows={4} title={false} what="runners" />
        : rows.length === 0 ? (
          <p className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
            No runners in the catalog. Drop a definition into <code className="font-mono">runners/&lt;id&gt;.json</code> under your PersonalClaw home to add one.
          </p>
        ) : (
          <ul className="divide-y divide-outline-variant overflow-hidden rounded-lg bg-surface-container">
            {rows.map((r) => <RunnerRowItem key={r.id} row={r} />)}
          </ul>
        )}
    </Section>
  )
}

function Chip({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'ok' | 'bad' }) {
  // The healthy chip takes the SHARED accent-chip skin from design/accent.ts (the
  // container pair) rather than an accent ink over an accent tint, which fails AA in
  // light mode at every tint strength. The unhealthy chip keeps danger INK on the
  // neutral chip ground: the semantic tones have no `<tone>-container` sibling, so
  // inventing a red fill here would be a redesign, not a contrast fix.
  const base = 'inline-flex items-center rounded-pill px-2.5 py-1 text-[0.75rem]'
  if (tone === 'ok') return <span className={base} style={accentChip}>{children}</span>
  const toneCx = tone === 'bad' ? 'text-danger' : 'text-on-surface'
  return <span className={`${base} bg-surface-high ${toneCx}`}>{children}</span>
}

function RunnerRowItem({ row }: { row: RunnerRow }) {
  const h = row.health
  const caps = row.capabilities
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-on-surface text-[0.875rem]">{row.display_name}</span>
        <span className="font-mono text-on-surface-low text-[0.75rem]">{row.runtime_id}</span>
        {row.source === 'user' && <Chip>your definition</Chip>}
        {h === null ? <Chip>never probed</Chip> : h.ok ? <Chip tone="ok">healthy</Chip> : <Chip tone="bad">unhealthy</Chip>}
        {/* An overdue check is not a verdict on the runner — it says the reading you are
            looking at is older than the health-check interval, so "healthy" describes
            then, not now. Only shown for `true`: `null` means we do not know the age. */}
        {row.health_stale === true && <Chip>check overdue</Chip>}
      </div>

      {/* Health evidence. `version`/`latency_ms` can be null even on a healthy probe —
          the CLI answered but its output carried no version we could parse — so each is
          labelled unknown rather than shown as a zero or an em dash that reads like a
          measurement. */}
      {h !== null && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low text-[0.75rem]">
          <span>{h.version ? `v${h.version}` : 'version unknown'}</span>
          <span>{h.latency_ms === null ? 'latency unknown' : `${h.latency_ms} ms`}</span>
          <span>last {h.probe} probe {new Date(h.checked_at).toLocaleString()}</span>
        </div>
      )}
      {/* FieldError, not a hand-rolled danger line: an unhealthy runner is a failure the
          user did not ask for, so it has to announce (ui/fieldErrorAnnounced.test.tsx).
          The extra classes keep the CLI's text verbatim — wrapped, not truncated. */}
      {h !== null && !h.ok && h.error && (
        <FieldError className="mt-1.5 whitespace-pre-wrap break-words font-mono">{h.error}</FieldError>
      )}

      {/* Capability chips come ONLY from a recorded handshake. No handshake yet means
          the matrix is unknown — printing an assumed set would tell you this runner
          supports things nobody has ever seen it do. */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {caps === null ? (
          <span className="text-on-surface-low text-[0.75rem]">Capabilities unknown — no handshake recorded yet.</span>
        ) : (
          <>
            {caps.permission_modes.map((m) => <Chip key={`m-${m}`}>{m}</Chip>)}
            {caps.efforts.map((e) => <Chip key={`e-${e}`}>effort: {e}</Chip>)}
            {caps.models.length > 0 && <Chip>{caps.models.length} model{caps.models.length === 1 ? '' : 's'}</Chip>}
            {caps.permission_modes.length === 0 && caps.efforts.length === 0 && caps.models.length === 0 && (
              <span className="text-on-surface-low text-[0.75rem]">Handshake recorded no capabilities.</span>
            )}
          </>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Chip tone={row.adapter.verified ? 'ok' : 'bad'}>adapter {row.adapter.state}</Chip>
        <span className="text-on-surface-low text-[0.75rem]">{row.adapter.detail}</span>
      </div>
    </li>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────
function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function EnumRow({ label, hint, cfg, field, patch, options }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  options: { key: string; label: string }[]
}) {
  const [saved, flash] = useSavedFlash()
  const value = String(cfg[field] ?? options[0].key)
  // if the stored value isn't among options (legacy), show it but keep options.
  const opts = options.some((o) => o.key === value) ? options : [{ key: value, label: value }, ...options]
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <SegPills ariaLabel={label} value={value} onChange={(v) => patch(field, v, flash)} options={opts} />
      </div>
    </Row>
  )
}

function NumberRow({ label, hint, cfg, field, patch, min, max, step, suffix }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  min?: number; max?: number; step?: number; suffix?: string
}) {
  const [saved, flash] = useSavedFlash()
  // Every agent.* field is always serialized by the backend (asdict), so cfg[field]
  // is present — the `?? min ?? 0` fallback only guards a transient empty snapshot.
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={Number(cfg[field] ?? min ?? 0)} min={min} max={max} step={step}
          onChange={(n) => patch(field, n, flash)} ariaLabel={label} />
        {suffix && <span className="w-6 text-on-surface-low text-[0.75rem]">{suffix}</span>}
      </div>
    </Row>
  )
}

function StrListField({ label, hint, cfg, field, patch }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
}) {
  const [saved, flash] = useSavedFlash()
  const list = Array.isArray(cfg[field]) ? (cfg[field] as string[]) : []
  const [adding, setAdding] = useState('')
  const commit = (next: string[]) => patch(field, next, flash)
  return (
    <Field label={label} hint={hint}>
      <div className="flex flex-wrap items-center gap-1.5">
        {list.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface text-[0.75rem] font-mono">
            {v}
            <button type="button" onClick={() => commit(list.filter((x) => x !== v))} aria-label={`Remove ${v}`} className="text-on-surface-low hover:text-on-surface"><X size={12} /></button>
          </span>
        ))}
        {/* A RAW input inside settingsUI's Field cannot claim the Field's published label — only the
            form-family components read FieldLabelCtx. So it names itself, from `label`, which keeps
            it correct if this component gains a second call site. */}
        <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="Add path…"
          aria-label={`Add to ${label.toLowerCase()}`}
          onKeyDown={(e) => { if (e.key === 'Enter' && adding.trim()) { commit([...list, adding.trim()]); setAdding('') } }}
          className="h-8 w-40 rounded-md bg-surface-high px-2 text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        {adding.trim() && (
          <SquareIconButton icon={Plus} iconSize={15} label={`Add ${label.toLowerCase()}`} onClick={() => { commit([...list, adding.trim()]); setAdding('') }} />
        )}
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}
