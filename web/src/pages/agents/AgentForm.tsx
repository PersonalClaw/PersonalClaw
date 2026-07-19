import { useEffect, useMemo, useState } from 'react'
import { Check } from 'lucide-react'
import type { SavedAgent } from '../../lib/api'
import { api } from '../../lib/api'
import { useActiveChatModelOptions } from '../../lib/agents'
import { Combobox } from '../../ui/Combobox'
import { Field, TextInput, TextArea, Segmented } from '../tasks/formControls'
import { APPROVAL_MODES } from './agentMeta'

export interface AgentDraft {
  name: string; description: string; model: string; system_prompt: string; voice: string
  approval_mode: string; skills: string[]; tools: string[]; triggers: string[]
  default_dir: string; memory_store: string
}

export function emptyDraft(): AgentDraft {
  return { name: '', description: '', model: '', system_prompt: '', voice: '', approval_mode: '', skills: [], tools: [], triggers: [], default_dir: '', memory_store: '' }
}
export function toDraft(a: SavedAgent): AgentDraft {
  return {
    name: a.name, description: a.description ?? '', model: a.model ?? '', system_prompt: a.system_prompt ?? '', voice: a.voice ?? '',
    approval_mode: a.approval_mode ?? '', skills: a.skills ?? [], tools: a.tools ?? [], triggers: a.triggers ?? [],
    default_dir: a.default_dir ?? '', memory_store: a.memory_store ?? '',
  }
}
export function draftToPayload(d: AgentDraft): Record<string, unknown> {
  // NB: there is no `workflows` binding on an agent. A workflow scopes itself to
  // an agent at THEIR creation (scope='agent', scope_ref=<agent>), not from the
  // agent side — eligibility is resolved by that scope_ref match at surfacing.
  return {
    name: d.name.trim(), description: d.description.trim(), provider: 'native', model: d.model,
    system_prompt: d.system_prompt, voice: d.voice, approval_mode: d.approval_mode,
    skills: d.skills, tools: d.tools, triggers: d.triggers,
    default_dir: d.default_dir.trim(), memory_store: d.memory_store.trim(),
    source: 'personalclaw',
  }
}

/** The native-agent builder. ACP agents are owned + invoked by their runtime
 *  and aren't authored here — so this is native-only: model, system prompt, and
 *  the PClaw capability bindings (skills / tools / triggers).
 *
 *  NB: workflows are NOT bound from here — a workflow scopes itself to an agent
 *  at the workflow's own creation, so the agent side has no workflow picker. */
export function AgentForm({ draft, onChange, nameLocked, compact }: { draft: AgentDraft; onChange: (d: AgentDraft) => void; nameLocked?: boolean; compact?: boolean }) {
  const set = <K extends keyof AgentDraft>(k: K, v: AgentDraft[K]) => onChange({ ...draft, [k]: v })
  // Constrain to ACTIVE chat models so an agent can't pin a model that isn't
  // bound (which would go stale when the active set changes). 'Auto' = inherit.
  const { options: modelOptions } = useActiveChatModelOptions()

  // capability catalogs
  const [skills, setSkills] = useState<CheckOption[]>([])
  const [tools, setTools] = useState<CheckOption[]>([])
  const [lifecycleTriggers, setLifecycleTriggers] = useState<CheckOption[]>([])
  useEffect(() => {
    api.skills().then((s) => setSkills(s.map((x) => ({ value: x.key ?? x.name, label: x.name, hint: x.description })))).catch(() => {})
    api.tools().then((t) => setTools(t.map((x) => ({ value: x.name, label: x.name, hint: x.provider, risk: x.risk_level })))).catch(() => {})
    api.hooks().then((h) => setLifecycleTriggers(h.map((x) => ({ value: x.id, label: x.name, hint: x.event })))).catch(() => {})
  }, [])

  const modelOpts = useMemo(() => [{ value: '', label: 'Auto — provider default' }, ...modelOptions], [modelOptions])

  return (
    <div className={`flex flex-col ${compact ? 'gap-l' : 'gap-xl'}`}>
      <Field label="Name" hint="Lowercase, hyphenated — e.g. research-assistant">
        <TextInput value={draft.name} onChange={(v) => set('name', nameLocked ? draft.name : v.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/-+/g, '-'))} placeholder="research-assistant" autoFocus={!nameLocked} />
      </Field>
      <Field label="Description"><TextInput value={draft.description} onChange={(v) => set('description', v)} placeholder="One line: what this agent is for" /></Field>

      <Field label="Model" hint="The model this agent runs on. Auto uses the provider default.">
        <Combobox options={modelOpts} value={draft.model} onChange={(v) => set('model', v)} placeholder="Auto — provider default" emptyText="No models" />
      </Field>

      <Field label="System prompt" hint="The agent's standing instructions — WHAT it does (operating rules).">
        <TextArea value={draft.system_prompt} onChange={(v) => set('system_prompt', v)} rows={compact ? 5 : 8} placeholder="You are a focused research assistant. …" />
      </Field>
      <Field label="Voice" hint="WHO it is — tone, opinions, bluntness, persona. Kept separate from the rules and injected high-priority so personality survives long prompts.">
        <TextArea value={draft.voice} onChange={(v) => set('voice', v)} rows={compact ? 3 : 4} placeholder="Blunt and witty. Has strong opinions and states them. No hedging or filler." />
      </Field>

      <Field label="Approval mode" hint="How tool calls are approved during this agent's runs.">
        <Segmented options={APPROVAL_MODES.map((m) => ({ key: m.key || 'default', label: m.label }))} value={draft.approval_mode || 'default'} onChange={(v) => set('approval_mode', v === 'default' ? '' : v)} />
      </Field>

      <CheckList label="Skills" hint="Skills surfaced to this agent." options={skills} value={draft.skills} onChange={(v) => set('skills', v)} />
      <CheckList label="Tools" hint="Tools this agent may call. None selected = all available tools." options={tools} value={draft.tools} onChange={(v) => set('tools', v)} />
      <CheckList label="Triggers" hint="Lifecycle triggers that fire for this agent (the agent-scoped allow-list)." options={lifecycleTriggers} value={draft.triggers} onChange={(v) => set('triggers', v)} />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-l">
        <Field label="Default directory" hint="Optional working dir for this agent."><TextInput value={draft.default_dir} onChange={(v) => set('default_dir', v)} placeholder="/abs/path (optional)" /></Field>
        <Field label="Memory store" hint="Optional named memory namespace."><TextInput value={draft.memory_store} onChange={(v) => set('memory_store', v)} placeholder="(default)" /></Field>
      </div>
    </div>
  )
}

/** A multiselect-driven checkbox list — every option is a toggleable row (no
 *  dropdown). Searchable when the list is long; shows a selected-count + select-
 *  all/clear. */
interface CheckOption { value: string; label: string; hint?: string; risk?: 'safe' | 'caution' | 'destructive' }

function CheckList({ label, hint, options, value, onChange }: {
  label: string; hint?: string; options: CheckOption[]; value: string[]; onChange: (v: string[]) => void
}) {
  const [q, setQ] = useState('')
  const selected = new Set(value)
  const n = q.trim().toLowerCase()
  const filtered = n ? options.filter((o) => `${o.label} ${o.hint ?? ''}`.toLowerCase().includes(n)) : options
  const toggle = (v: string) => onChange(selected.has(v) ? value.filter((x) => x !== v) : [...value, v])

  return (
    <Field label={`${label}${value.length ? ` · ${value.length}` : ''}`} hint={hint}>
      <div className="rounded-md bg-surface-container">
        {options.length > 8 && (
          <div className="border-b border-outline-variant/30 p-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${label.toLowerCase()}…`}
              className="w-full h-8 rounded-md bg-surface px-m text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          </div>
        )}
        {options.length === 0 ? (
          <div className="px-m py-3 text-on-surface-low text-[0.8125rem]">No {label.toLowerCase()} available.</div>
        ) : (
          <>
            <div className="max-h-56 overflow-y-auto p-1">
              {filtered.length === 0 ? <div className="px-2 py-2 text-on-surface-low text-[0.8125rem]">No matches.</div> : filtered.map((o) => {
                const on = selected.has(o.value)
                return (
                  <button key={o.value} type="button" onClick={() => toggle(o.value)}
                    className="flex w-full items-center gap-s rounded-md px-2 py-1.5 text-left hover:bg-surface-high transition-colors">
                    <span className="shrink-0 inline-flex size-4 items-center justify-center rounded-sm border transition-colors" style={{ borderColor: on ? 'var(--color-primary)' : 'var(--color-outline-variant)', background: on ? 'var(--color-primary)' : 'transparent' }}>
                      {on && <Check size={12} className="text-on-primary" />}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-on-surface text-[0.8125rem]">{o.label}</span>
                        <RiskTag risk={o.risk} />
                      </span>
                      {o.hint && <span className="block truncate text-on-surface-low text-[0.7rem]">{o.hint}</span>}
                    </span>
                  </button>
                )
              })}
            </div>
            {value.length > 0 && (
              <div className="border-t border-outline-variant/30 px-2 py-1.5 flex items-center justify-between text-[0.75rem]">
                <span className="text-on-surface-low">{value.length} selected</span>
                <button type="button" onClick={() => onChange([])} className="text-on-surface-low hover:text-on-surface">Clear</button>
              </div>
            )}
          </>
        )}
      </div>
    </Field>
  )
}

/** Risk indicator on a tool option in the agent's tool picker (tool risk
 *  taxonomy). Surfaces which tools are caution/destructive so a capability grant
 *  is security-informed. SAFE is the norm → no tag (matches the Tools-page badge). */
function RiskTag({ risk }: { risk?: 'safe' | 'caution' | 'destructive' }) {
  if (!risk || risk === 'safe') return null
  const color = risk === 'destructive' ? 'var(--color-danger)' : 'var(--color-warn)'
  const label = risk === 'destructive' ? 'Destructive' : 'Caution'
  return (
    <span className="shrink-0 rounded-pill px-1.5 py-0.5 text-[0.6rem]" title={`Risk: ${label}`}
      style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, color, fontVariationSettings: '"wght" 600' }}>
      {label}
    </span>
  )
}
