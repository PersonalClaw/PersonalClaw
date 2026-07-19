import { useEffect, useState } from 'react'
import { api, type DashboardConfig } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useAgentCatalog, ensureBindableAgentName } from '../../lib/agents'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SegPills, SavedToast } from './settingsUI'
import { Combobox } from '../../ui/Combobox'
import { FormSkeleton } from '../../ui/ListScaffold'

const RESTORE_WINDOWS = [
  { key: '15', label: '15 min' }, { key: '30', label: '30 min' },
  { key: '60', label: '1 hour' }, { key: '240', label: '4 hours' }, { key: '0', label: 'All' },
]

/** Chat settings — session restore + message display (server-stored so behavior
 *  is identical across browsers) + context lifecycle (auto-compact, idle timeout,
 *  warm pool). Dashboard prefs persist via /api/dashboard/config; the session.*
 *  lifecycle knobs via the config PATCH allowlist. */
export function ChatPanel() {
  const [cfg, setCfg] = useState<DashboardConfig | null>(null)
  const [session, setSession] = useState<Record<string, unknown> | null>(null)
  const { options: agentOptions, discovered } = useAgentCatalog()

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from a
  // single cached snapshot of both fetches, revalidating in the background. The
  // editable form state below is seeded/rehydrated from this read-only `data`.
  const { data } = useCachedData('settings:chat', async () => {
    const [dash, plaw] = await Promise.all([
      api.dashboardConfig().catch(() => null),
      api.personalclawConfig().then((c) => (c.session ?? {}) as Record<string, unknown>).catch(() => ({} as Record<string, unknown>)),
    ])
    return { cfg: dash, session: plaw }
  }, { persist: true })

  useEffect(() => {
    if (data) { setCfg(data.cfg); setSession(data.session) }
  }, [data])

  if (!data || !cfg || !session) return <FormSkeleton sections={3} />

  return (
    <div>
      <PanelHeader title="Chat" hint="How sessions restore, how messages display, and how long context lives. These follow you across browsers." />

      <SessionsSection cfg={cfg} setCfg={setCfg} />
      <MessagesSection cfg={cfg} setCfg={setCfg} />
      <LifecycleSection session={session} setSession={setSession} agentOptions={agentOptions} discovered={discovered} />
    </div>
  )
}

// ── Sessions (dashboard config) ──────────────────────────────────────────────
function SessionsSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch).then(flash).catch(() => {})
  }
  return (
    <Section title="Sessions" hint="What happens to your chats on restart, and while the agent is busy.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Restore sessions on startup" hint="Re-open recently active sessions when the app starts.">
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={cfg.restore_sessions} onChange={(v) => save({ restore_sessions: v })} label="Restore sessions" /></div>
        </Row>
        {cfg.restore_sessions && (
          <Row label="Restore window" hint="How recently active a session must be to re-open.">
            <SegPills value={String(cfg.restore_window_minutes)} onChange={(v) => save({ restore_window_minutes: Number(v) })} options={RESTORE_WINDOWS} />
          </Row>
        )}
        <Row label="Merge queued messages" hint="While the agent is busy, combine follow-ups into one labeled prompt instead of queueing separately.">
          <Toggle on={cfg.merge_queued_messages} onChange={(v) => save({ merge_queued_messages: v })} label="Merge queued messages" />
        </Row>
        <Row label="Auto-tag new chats" hint="When a chat's title is generated, also propose and assign tags in the same pass. Never touches chats you've tagged yourself, or incognito/temporary chats.">
          <Toggle on={cfg.auto_tag_sessions} onChange={(v) => save({ auto_tag_sessions: v })} label="Auto-tag new chats" />
        </Row>
      </div>
    </Section>
  )
}

// ── Messages (dashboard config display prefs) ────────────────────────────────
function MessagesSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch).then(flash).catch(() => {})
  }
  return (
    <Section title="Messages" hint="How messages and tool activity render in the chat.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Send on Enter" hint={cfg.send_on_enter ? 'Enter sends · Shift+Enter for a newline.' : 'Enter inserts a newline · Cmd/Ctrl+Enter sends.'}>
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={cfg.send_on_enter} onChange={(v) => save({ send_on_enter: v })} label="Send on Enter" /></div>
        </Row>
        <Row label="Show timestamps" hint="Display a time on each message.">
          <Toggle on={cfg.show_timestamps} onChange={(v) => save({ show_timestamps: v })} label="Show timestamps" />
        </Row>
        <Row label="Show thinking inline" hint="Show intermediate reasoning between tool calls instead of collapsing it.">
          <Toggle on={cfg.show_thinking_inline} onChange={(v) => save({ show_thinking_inline: v })} label="Show thinking inline" />
        </Row>
        <Row label="Simplified tool names" hint="Tool pills show a simplified purpose instead of the exact command.">
          <Toggle on={cfg.simplified_tool_names} onChange={(v) => save({ simplified_tool_names: v })} label="Simplified tool names" />
        </Row>
        <Row label="Widget density" hint="How aggressively the agent uses inline widgets for visual content.">
          <SegPills value={cfg.widget_density} onChange={(v) => save({ widget_density: v as 'more' | 'less' })}
            options={[{ key: 'more', label: 'More' }, { key: 'less', label: 'Less' }]} />
        </Row>
        <Row label="Confirm before closing a session" hint="Ask for confirmation when closing a session from the sidebar.">
          <Toggle on={cfg.confirm_close_session} onChange={(v) => save({ confirm_close_session: v })} label="Confirm before closing" />
        </Row>
      </div>
    </Section>
  )
}

// ── Context & lifecycle (session.* config) ───────────────────────────────────
function LifecycleSection({ session, setSession, agentOptions, discovered }: {
  session: Record<string, unknown>; setSession: (s: Record<string, unknown>) => void
  agentOptions: import('../../lib/agents').AgentOption[]; discovered: Record<string, import('../../lib/api').DiscoveredAgent[]>
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown) => {
    const prev = session[key]
    setSession({ ...session, [key]: value })
    api.patchConfig(`session.${key}`, value).then(flash).catch((e) => {
      setSession({ ...session, [key]: prev })
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const poolSize = Number(session.pool_size ?? 0)
  return (
    <Section title="Context & lifecycle" hint="Keep long sessions productive and control how warm sessions are kept ready.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <NumberRow label="Auto-compact threshold" hint="Context-usage % that triggers compaction. Lower = more frequent." value={Number(session.autocompact_pct ?? 90)} min={5} max={90} step={1} suffix="%" onCommit={(n) => patch('autocompact_pct', n)} saved={saved} />
        <NumberRow label="Idle timeout" hint="Auto-close an idle session after this long. 0 = never." value={Number(session.timeout_secs ?? 0)} min={0} max={86400} step={60} suffix="s" onCommit={(n) => patch('timeout_secs', n)} saved={saved} />

        <Row label="Warm pool size" hint="Pre-started sessions kept ready for an instant first turn. 0 = off.">
          <NumberInput value={poolSize} min={0} max={10} step={1} onCommit={(n) => patch('pool_size', n)} ariaLabel="Warm pool size" />
        </Row>
        {poolSize > 0 && (
          <>
            <Row label="Warm pool agent" hint="Which agent the warm sessions pre-start as (native or a connected ACP-runtime agent). Empty uses the default agent.">
              <div className="w-56">
                <Combobox
                  value={String(session.pool_agent ?? '')}
                  options={[{ value: '', label: '— default —' }, ...agentOptions,
                    ...(session.pool_agent && !agentOptions.some((o) => o.value === session.pool_agent) ? [{ value: String(session.pool_agent), label: String(session.pool_agent), group: 'Current' }] : [])]}
                  placeholder="— default —" emptyText="No agents"
                  onChange={async (v) => { const name = v ? await ensureBindableAgentName(v, discovered) : ''; patch('pool_agent', name) }} />
              </div>
            </Row>
            <NumberRow label="Warm pool TTL" hint="Recycle a warm session after this long unused." value={Number(session.pool_ttl_secs ?? 1800)} min={0} max={7200} step={60} suffix="s" onCommit={(n) => patch('pool_ttl_secs', n)} saved={saved} />
          </>
        )}
      </div>
    </Section>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────
function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function NumberRow({ label, hint, value, min, max, step, suffix, onCommit, saved }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix?: string
  onCommit: (n: number) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberInput value={value} min={min} max={max} step={step} onCommit={onCommit} ariaLabel={label} />
        {suffix && <span className="w-6 text-on-surface-low text-[0.75rem]">{suffix}</span>}
      </div>
    </Row>
  )
}

function NumberInput({ value, min, max, step, onCommit, ariaLabel }: {
  value: number; min: number; max: number; step?: number; onCommit: (n: number) => void; ariaLabel?: string
}) {
  const [local, setLocal] = useState(String(value))
  useEffect(() => { setLocal(String(value)) }, [value])
  const commit = () => {
    const n = Number(local)
    if (local === '' || Number.isNaN(n)) { setLocal(String(value)); return }
    const clamped = Math.min(max, Math.max(min, n))
    setLocal(String(clamped))
    if (clamped !== value) onCommit(clamped)
  }
  return (
    <input type="number" aria-label={ariaLabel} value={local} min={min} max={max} step={step ?? 1}
      onChange={(e) => setLocal(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      className="h-8 w-24 rounded-md bg-surface-high px-2 text-right text-[0.8125rem] text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
  )
}
