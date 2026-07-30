import { useEffect, useState } from 'react'
import { api, type DashboardConfig, type SessionTemplate } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useAgentCatalog, ensureBindableAgentName } from '../../lib/agents'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SegPills, SavedToast } from './settingsUI'
import { Combobox } from '../../ui/Combobox'
import { NumberField } from '../../ui/forms'
import { IconButton } from '../../ui/IconButton'
import { confirmDelete } from '../../ui/dialog'
import { Trash2 } from 'lucide-react'
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
  const [routing, setRouting] = useState<Record<string, unknown> | null>(null)
  const [resilience, setResilience] = useState<Record<string, unknown> | null>(null)
  const { options: agentOptions, discovered } = useAgentCatalog()

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from a
  // single cached snapshot of both fetches, revalidating in the background. The
  // editable form state below is seeded/rehydrated from this read-only `data`.
  const { data } = useCachedData('settings:chat', async () => {
    const [dash, plaw] = await Promise.all([
      api.dashboardConfig().catch(() => null),
      api.personalclawConfig().catch(() => ({} as Record<string, unknown>)),
    ])
    return {
      cfg: dash,
      session: (plaw.session ?? {}) as Record<string, unknown>,
      routing: (plaw.agents_routing ?? {}) as Record<string, unknown>,
      resilience: (plaw.resilience ?? {}) as Record<string, unknown>,
    }
  }, { persist: true })

  useEffect(() => {
    if (data) {
      setCfg(data.cfg); setSession(data.session); setRouting(data.routing)
      setResilience(data.resilience)
    }
  }, [data])

  if (!data || !cfg || !session || !routing || !resilience) return <FormSkeleton sections={3} />

  return (
    <div>
      <PanelHeader title="Chat" hint="How sessions restore, how messages display, and how long context lives. These follow you across browsers." />

      <SessionsSection cfg={cfg} setCfg={setCfg} />
      <MessagesSection cfg={cfg} setCfg={setCfg} />
      <MidTurnSection resilience={resilience} setResilience={setResilience} />
      <RoutingSection routing={routing} setRouting={setRouting} />
      <LifecycleSection session={session} setSession={setSession} agentOptions={agentOptions} discovered={discovered} />
      <StartersSection />
    </div>
  )
}

/** Saved chat starters (SESSION-MANAGEMENT S3 T3.2) — the management surface.
 *
 *  Starters are CREATED from a chat's header ("Save as starter"), because that's where
 *  the setup being saved actually exists. This section is where they're reviewed and
 *  removed: without it a starter would be creatable and never deletable, which is how
 *  a picker fills up with stale entries nobody can clear. */
function StartersSection() {
  const [items, setItems] = useState<SessionTemplate[] | null>(null)

  useEffect(() => {
    let live = true
    api.sessionTemplates()
      .then((t) => { if (live) setItems(t) })
      .catch(() => { if (live) setItems([]) })
    return () => { live = false }
  }, [])

  async function remove(t: SessionTemplate) {
    if (!(await confirmDelete('starter', t.name))) return
    try {
      await api.deleteSessionTemplate(t.id)
    } catch (e) {
      notify(`Couldn't delete this starter: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setItems((prev) => (prev ?? []).filter((x) => x.id !== t.id))
    // The chat page caches the starter list for instant paint; drop it so the picker
    // doesn't keep offering something that no longer exists.
    invalidateCache('chat:starters')
  }

  return (
    <Section title="Chat starters" hint="Reusable setups — agent, model and reasoning effort. Save one from a chat's header; they appear on the new-chat screen.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        {items === null ? (
          <p className="py-3 text-[0.8125rem] text-on-surface-low">Loading…</p>
        ) : items.length === 0 ? (
          <p className="py-3 text-[0.8125rem] text-on-surface-low">
            No starters yet. Open a chat, set it up how you like, then use “Save as starter” in its header.
          </p>
        ) : items.map((t) => (
          <Row key={t.id} label={t.name} hint={[t.agent, t.model, t.reasoning_effort].filter(Boolean).join(' · ') || 'Uses your defaults'}>
            <IconButton icon={Trash2} label={`Delete ${t.name}`} iconSize={16} size={32} onClick={() => remove(t)} />
          </Row>
        ))}
      </div>
    </Section>
  )
}

const MID_TURN_POLICIES = [
  { key: 'queue', label: 'Queue' },
  { key: 'steer', label: 'Steer' },
  { key: 'cancel_and_replace', label: 'Replace' },
] as const

// ── Mid-turn messages (personalclaw config: resilience.mid_turn_policy) ──────
// The field shipped with PLATFORM-RESILIENCE S3 but had no frontend control, so
// the config round-trip contract's fifth point was unmet — file-editable only.
function MidTurnSection({ resilience, setResilience }: {
  resilience: Record<string, unknown>; setResilience: (r: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const policy = String(resilience.mid_turn_policy ?? 'queue')
  const patch = (value: string) => {
    const prev = resilience.mid_turn_policy
    setResilience({ ...resilience, mid_turn_policy: value })
    api.patchConfig('resilience.mid_turn_policy', value).then(flash).catch((e) => {
      setResilience({ ...resilience, mid_turn_policy: prev })
      notify(`Couldn't save mid-turn policy: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Mid-turn messages" hint="What happens when you send something while an answer is still being written.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Default handling"
          hint="Queue: deliver it as the next turn. Steer: fold it into the answer being written, where the running agent supports that — otherwise it queues. Replace: stop the current answer and start over with the new message. Unattended work (loops, cron, subagents) always queues.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <SegPills value={policy} onChange={patch} options={[...MID_TURN_POLICIES]} />
          </div>
        </Row>
        {policy === 'steer' && (
          <p className="pb-3 text-[0.75rem] text-on-surface-low">
            Steering reaches the running answer on the built-in agent. Connected CLI
            agents (ACP) don't expose a mid-turn seam yet, so a message there queues
            instead — either way it appears above the composer, never dropped.
          </p>
        )}
      </div>
    </Section>
  )
}

// ── Agent routing (personalclaw config: agents_routing.*) ────────────────────
function RoutingSection({ routing, setRouting }: { routing: Record<string, unknown>; setRouting: (r: Record<string, unknown>) => void }) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown) => {
    const prev = routing[key]
    setRouting({ ...routing, [key]: value })
    api.patchConfig(`agents_routing.${key}`, value).then(flash).catch(() => {
      setRouting({ ...routing, [key]: prev })
      notify(`Couldn't save ${key}`, 'error')
    })
  }
  const enabled = routing.enabled !== false
  return (
    <Section title="Agent routing" hint="Suggest a better-fit specialist agent when a message matches one — you always confirm before it re-targets the chat.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Suggest specialists" hint="When a message in a default-agent chat fits an installed specialist, show a one-click 'route to <agent>?' chip. Never routes silently.">
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={enabled} onChange={(v) => patch('enabled', v)} label="Suggest specialists" /></div>
        </Row>
        {enabled && (
          <NumberRow label="Confidence threshold" hint="Minimum match confidence before a routing chip appears. Higher = fewer, surer suggestions." value={Number(routing.min_confidence ?? 0.62)} min={0.3} max={0.95} step={0.01} onCommit={(n) => patch('min_confidence', n)} saved={saved} />
        )}
        {enabled && (
          <NumberRow label="Dismiss cooldown" hint="After you dismiss a suggestion for an agent, suppress it for this long (three dismissals mute it until you re-enable)." value={Number(routing.cooldown_hours ?? 24)} min={0} max={720} step={1} suffix="h" onCommit={(n) => patch('cooldown_hours', n)} saved={saved} />
        )}
      </div>
    </Section>
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
        <Row label="Follow-up suggestions" hint="After each reply, show 2-3 suggested next messages (one small background call; never blocks the turn). Skipped for temporary/incognito chats; silent with no model bound.">
          <Toggle on={cfg.followup_chips} onChange={(v) => save({ followup_chips: v })} label="Follow-up suggestions" />
        </Row>
        <Row label="Streaming text reveal" hint="Smooth: steady word-by-word reveal decoupled from network chunks (never lags). Immediate: render each chunk the instant it arrives.">
          <SegPills value={cfg.stream_reveal} onChange={(v) => save({ stream_reveal: v as 'smooth' | 'immediate' })}
            options={[{ key: 'smooth', label: 'Smooth' }, { key: 'immediate', label: 'Immediate' }]} />
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
        <AutoArchiveRow days={Number(session.auto_archive_days ?? 30)} onCommit={(n) => patch('auto_archive_days', n)} saved={saved} />

        <Row label="Warm pool size" hint="Pre-started sessions kept ready for an instant first turn. 0 = off.">
          <NumberField value={poolSize} min={0} max={10} step={1} onChange={(n) => patch('pool_size', n)} ariaLabel="Warm pool size" />
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

/** The auto-archive threshold, plus what it would actually do right now.
 *
 *  The rule has been running on the heartbeat since S2 with no way to see or change
 *  it: chats silently left the list after 30 days and the only evidence was a shorter
 *  list. A retention rule the user can't inspect is indistinguishable from data loss,
 *  so the count is fetched from the existing dry-run preview — the same call the sweep
 *  makes, so the number shown IS the number that would move, not an estimate. */
function AutoArchiveRow({ days, onCommit, saved }: {
  days: number; onCommit: (n: number) => void; saved: boolean
}) {
  const [pending, setPending] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ count: number; enabled: boolean } | null>(null)
  const shown = pending ?? days

  useEffect(() => {
    // Only meaningful while the rule is on; 0 = off has nothing to preview.
    if (shown <= 0) { setPreview(null); return }
    let live = true
    api.autoArchiveSessions({ dry_run: true })
      .then((r) => { if (live) setPreview({ count: r.count, enabled: r.enabled }) })
      .catch(() => { if (live) setPreview(null) })
    return () => { live = false }
  }, [shown])

  return (
    <Row
      label="Auto-archive after"
      hint="Archive chats with no activity for this long. Archived chats stay searchable and restore in one click — nothing is deleted. 0 = off."
    >
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {shown > 0 && preview?.enabled && (
          <span className="text-xs text-on-surface-variant tabular-nums">
            {preview.count === 0 ? 'none stale now' : `${preview.count} stale now`}
          </span>
        )}
        <NumberField
          value={shown} min={0} max={3650} step={1} ariaLabel="Auto-archive after (days)"
          onChange={(n) => { setPending(n); onCommit(n) }}
        />
        <span className="text-xs text-on-surface-variant">{shown > 0 ? 'days' : 'off'}</span>
      </div>
    </Row>
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
        <NumberField value={value} min={min} max={max} step={step} onChange={onCommit} ariaLabel={label} />
        {suffix && <span className="w-6 text-on-surface-low text-[0.75rem]">{suffix}</span>}
      </div>
    </Row>
  )
}
