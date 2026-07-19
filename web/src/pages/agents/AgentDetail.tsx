import { useEffect, useMemo, useState } from 'react'
import { Pencil, Trash2, Check, X, Star, Lock, Cpu, ShieldCheck, ChevronDown } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Combobox } from '../../ui/Combobox'
import { Markdown } from '../../ui/Markdown'
import { confirmDelete } from '../../ui/dialog'
import { Skeleton } from '../../ui/ListScaffold'
import { useCachedData } from '../../lib/useCachedData'
import { api, type SavedAgent, type DiscoveredAgent, type McpActiveServer, type AgentHook } from '../../lib/api'
import { useActiveChatModelOptions } from '../../lib/agents'
import { providerMeta, isReservedAgent } from './agentMeta'
import { AgentForm, toDraft, draftToPayload, type AgentDraft } from './AgentForm'

/** Native agent inspector: view ↔ in-panel edit (full builder), set-as-default,
 *  delete. */
export function NativeAgentDetail({ agent, isDefault, onSaved, onDeleted, onSetDefault, editing: editingProp, onEditingChange }: {
  agent: SavedAgent; isDefault: boolean; onSaved: () => void; onDeleted: () => void; onSetDefault: () => void; editing: boolean; onEditingChange: (v: boolean) => void
}) {
  const reserved = isReservedAgent(agent)
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled; a
  // reserved built-in is model-only so it can never enter the edit form.
  const editing = editingProp && !reserved
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<AgentDraft>(() => toDraft(agent))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  // The agent-scoped SOPs offered to this agent — workflows whose scope_ref
  // matches the agent binding id, resolved via the reverse-index endpoint
  // (used-by/{agent}). Computed server-side from the scope_ref model.
  const { data: workflowNames } = useCachedData<string[]>(`agent:workflows-usedby:${agent.name}`, () => api.workflowsUsedBy(agent.name).then((ws) => ws.map((w) => w.name)).catch(() => []), { persist: true })

  useEffect(() => { setDraft(toDraft(agent)) }, [agent.name])

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.updateAgent(agent.name, draftToPayload(draft)); onSaved(); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (isDefault) { setErr('Can’t delete the default agent — set another default first.'); return }
    if (!(await confirmDelete('agent', agent.name))) return
    try { await api.deleteAgent(agent.name); onDeleted() } catch { setErr('Delete failed') }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <AgentForm draft={draft} onChange={setDraft} nameLocked compact />
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <div className="sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(agent)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        {reserved ? (
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Lock size={13} /> Reserved built-in — model only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            {!isDefault && <Button size="sm" variant="ghost" onClick={onSetDefault}><Star size={14} /> Set default</Button>}
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        {isDefault && <span className="ml-auto inline-flex items-center gap-1 text-primary text-[0.75rem]"><Star size={12} fill="currentColor" /> Default</span>}
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {reserved && <p className="text-on-surface-low text-[0.8125rem] leading-relaxed">This is a built-in system agent (the background-chore worker, the goal-loop worker, or the goal-planner). Its definition is fixed, but you can swap which model it runs on.</p>}

      {reserved && <ReservedModelEditor agent={agent} onSaved={onSaved} />}

      <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
        <span className="inline-flex items-center gap-1 rounded-pill px-m h-7" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }}>{reserved && <ShieldCheck size={12} />}{reserved ? 'Built-in' : 'Native'}</span>
        {!reserved && agent.model && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center font-mono text-on-surface-var text-[0.75rem]">{agent.model}</span>}
        {agent.approval_mode && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var">{agent.approval_mode}</span>}
      </div>

      {agent.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{agent.description}</p>}

      {agent.system_prompt && (
        <Section label="System prompt"><div className="rounded-md bg-surface-container px-m py-2 max-h-72 overflow-y-auto text-on-surface-var text-[0.875rem] leading-relaxed"><Markdown>{agent.system_prompt}</Markdown></div></Section>
      )}

      <Caps label="Skills" items={agent.skills} />
      <Caps label="Tools" items={agent.tools} />
      <Caps label="Triggers" items={agent.triggers} />
      {workflowNames === undefined
        ? <Section label="Workflows"><Skeleton className="h-6 w-32 rounded-pill" /></Section>
        : <Caps label="Workflows" items={workflowNames} />}

      {!reserved && <AgentAdvanced agentName={agent.name} />}
    </div>
  )
}

/** Advanced per-agent config, collapsed by default: routing notes (editable — the
 *  "when to use this agent" hint the auto-router reads), the MCP servers this agent
 *  gets (read-only), and the lifecycle hooks in effect (read-only). Each block
 *  loads its data lazily only when the section is expanded. */
function AgentAdvanced({ agentName }: { agentName: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-outline-variant/40 pt-l">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] hover:text-on-surface">
        <ChevronDown size={15} className={`transition-transform ${open ? 'rotate-180' : ''}`} /> Advanced
      </button>
      {open && (
        <div className="mt-m flex flex-col gap-l">
          <RoutingNotesEditor agentName={agentName} />
          <AgentMcpView agentName={agentName} />
          <AgentHooksView />
        </div>
      )}
    </div>
  )
}

/** "When to use this agent" routing notes — feeds the orchestrator/auto-router. */
function RoutingNotesEditor({ agentName }: { agentName: string }) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    let alive = true
    api.agentMetadata(agentName).then((c) => { if (alive) { setContent(c); setDraft(c) } }).catch(() => { if (alive) { setContent(''); setDraft('') } })
    return () => { alive = false }
  }, [agentName])
  const dirty = content !== null && draft !== content
  const save = async () => {
    setBusy(true)
    try { await api.saveAgentMetadata(agentName, draft); setContent(draft); setSaved(true); setTimeout(() => setSaved(false), 1800) }
    catch { /* leave dirty */ }
    setBusy(false)
  }
  return (
    <Section label="Routing notes">
      <p className="mb-1.5 text-on-surface-low text-[0.78rem]">A short "when to use this agent" note the auto-router reads to pick between agents.</p>
      {content === null ? <Skeleton className="h-16 w-full rounded-md" /> : (
        <div className="flex flex-col gap-2">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} aria-label="Routing notes"
            placeholder="e.g. Use for deep code reviews and multi-file refactors; prefers a thorough, direct style."
            className="w-full resize-y rounded-md bg-surface-container px-3 py-2 text-[0.85rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={save} disabled={!dirty || busy}><Check size={14} /> {busy ? 'Saving…' : 'Save notes'}</Button>
            {saved && <span className="text-ok text-[0.75rem]">Saved ✓</span>}
          </div>
        </div>
      )}
    </Section>
  )
}

/** Read-only view of the MCP servers this agent is scoped to. */
function AgentMcpView({ agentName }: { agentName: string }) {
  const { data: servers } = useCachedData<McpActiveServer[]>(`agent:mcp:${agentName}`, () => api.mcpActive(agentName).catch(() => [] as McpActiveServer[]), { persist: false })
  if (servers === undefined) return <Section label="MCP servers"><Skeleton className="h-6 w-40 rounded-pill" /></Section>
  return (
    <Section label={`MCP servers · ${servers.length}`}>
      {servers.length === 0 ? <p className="text-on-surface-low text-[0.8rem] italic">No MCP servers scoped to this agent.</p> : (
        <div className="flex flex-wrap gap-1.5">
          {servers.map((s) => (
            <span key={s.name} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 text-[0.72rem]" style={{ color: s.enabled ? 'var(--color-on-surface-var)' : 'var(--color-on-surface-low)' }}>
              <span className="size-1.5 rounded-pill" style={{ background: s.enabled ? 'var(--color-ok)' : 'var(--color-outline)' }} />{s.name}
            </span>
          ))}
        </div>
      )}
    </Section>
  )
}

/** Read-only lifecycle hooks in effect (redacted commands), grouped by event. */
function AgentHooksView() {
  const { data: hooks } = useCachedData<Record<string, AgentHook[]>>('agent:hooks', () => api.agentHooks().catch(() => ({} as Record<string, AgentHook[]>)), { persist: false })
  if (hooks === undefined) return <Section label="Lifecycle hooks"><Skeleton className="h-6 w-40 rounded-md" /></Section>
  const events = Object.entries(hooks).filter(([, hs]) => hs.length > 0)
  return (
    <Section label="Lifecycle hooks">
      {events.length === 0 ? <p className="text-on-surface-low text-[0.8rem] italic">No lifecycle hooks configured.</p> : (
        <div className="flex flex-col gap-2">
          {events.map(([event, hs]) => (
            <div key={event}>
              <div className="mb-0.5 text-on-surface-var text-[0.72rem]" style={{ fontVariationSettings: '"wght" 600' }}>{event}</div>
              {hs.map((h, i) => (
                <div key={i} className="rounded-md bg-surface-container px-2.5 py-1.5 font-mono text-[0.72rem] text-on-surface-low overflow-x-auto" style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
                  {h.matcher && <span className="text-primary">[{h.matcher}] </span>}{h.command}{h.source && <span className="text-on-surface-low/60"> · {h.source}</span>}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}

/** Model-only editor for a reserved built-in agent: swap its model (constrained
 *  to active chat models + Auto) without touching its locked persona/tools. */
function ReservedModelEditor({ agent, onSaved }: { agent: SavedAgent; onSaved: () => void }) {
  const { options } = useActiveChatModelOptions()
  const [model, setModel] = useState(agent.model ?? '')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const opts = useMemo(() => [{ value: '', label: 'Auto — use chat binding' }, ...options], [options])
  useEffect(() => { setModel(agent.model ?? '') }, [agent.name])

  const dirty = model !== (agent.model ?? '')
  const save = async () => {
    setSaving(true); setErr('')
    try { await api.updateAgent(agent.name, { model }); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Model</div>
      <div className="flex items-center gap-s">
        <div className="min-w-0 flex-1"><Combobox options={opts} value={model} onChange={setModel} placeholder="Auto — use chat binding" emptyText="No active chat models" /></div>
        {dirty && <Button size="sm" onClick={save} disabled={saving}><Check size={14} /> {saving ? 'Saving…' : 'Save'}</Button>}
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
    </div>
  )
}

/** Read-only inspector for an ACP-runtime-discovered agent. */
export function DiscoveredAgentDetail({ agent, providerId }: { agent: DiscoveredAgent; providerId: string }) {
  const pm = providerMeta(providerId)
  return (
    <div className="flex flex-col gap-l">
      <div className="inline-flex items-center gap-1.5 self-start rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-on-surface-low) 14%, transparent)', color: 'var(--color-on-surface-var)' }}>
        <Lock size={13} /> {pm.label} — read-only
      </div>
      <p className="text-on-surface-low text-[0.8125rem]">This agent is defined and run by the {pm.label} runtime. It can't be edited here, but you can use it from the chat agent picker.</p>

      <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: `color-mix(in srgb, ${pm.tone} 16%, transparent)`, color: pm.tone }}><pm.icon size={13} /> {pm.label}</span>
        {agent.reasoning_effort && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var">{agent.reasoning_effort} effort</span>}
      </div>

      {agent.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{agent.description}</p>}

      {agent.provider_agent && <Section label="Runtime agent id"><span className="font-mono text-on-surface-var text-[0.8125rem]">{agent.provider_agent}</span></Section>}
      {(agent.models?.length ?? 0) > 0 && (
        <Section label="Models">
          <div className="flex flex-wrap gap-1.5">{agent.models.map((m) => <span key={m} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 font-mono text-on-surface-var text-[0.75rem]"><Cpu size={11} /> {m}</span>)}</div>
        </Section>
      )}
    </div>
  )
}

function Caps({ label, items }: { label: string; items?: string[] }) {
  if (!items || items.length === 0) return null
  return (
    <Section label={`${label} · ${items.length}`}>
      <div className="flex flex-wrap gap-1.5">{items.map((i) => <span key={i} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{i}</span>)}</div>
    </Section>
  )
}
function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
