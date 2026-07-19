import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Plus, Workflow as WorkflowIcon, AlertTriangle, Search } from 'lucide-react'
import type { WorkflowItem, WorkflowStep, WorkflowScope } from '../../lib/api'
import { useAgentCatalog } from '../../lib/agents'
import { Combobox } from '../../ui/Combobox'
import { Bud } from '../../ui/motion'
import { spring } from '../../design/motion'
import { Field, TextInput, TextArea, Segmented, ChipInput } from '../tasks/formControls'
import { SCOPES, scopeNeedsRef, scopeMeta } from './workflowMeta'
import { refMap, wouldCycle } from './workflowDag'

export type WorkflowDraft = Partial<WorkflowItem> & { name: string; steps: WorkflowStep[] }

export function emptyDraft(): WorkflowDraft {
  return { name: '', description: '', scope: 'global', scope_ref: '', tags: [], match_text: '', enabled: true, steps: [] }
}
export function toDraft(w: WorkflowItem): WorkflowDraft {
  return { ...w, tags: w.tags ?? [], steps: w.steps ?? [], scope: w.scope ?? 'global' }
}

/** Shared workflow form behind the create PAGE and the in-panel edit. Captures
 *  name, description, scope (+ scope_ref when workspace/session), tags,
 *  match_text (the intent that auto-triggers the SOP), and the ordered steps
 *  (each: title + optional instruction). */
export function WorkflowForm({ draft, onChange, compact, allWorkflows = [] }: { draft: WorkflowDraft; onChange: (d: WorkflowDraft) => void; compact?: boolean; allWorkflows?: WorkflowItem[] }) {
  const set = <K extends keyof WorkflowDraft>(k: K, v: WorkflowDraft[K]) => onChange({ ...draft, [k]: v })
  const needsRef = scopeNeedsRef(draft.scope)
  const sm = scopeMeta(draft.scope)
  const { options: agentOptions } = useAgentCatalog()

  return (
    <div className={`flex flex-col ${compact ? 'gap-l' : 'gap-xl'}`}>
      <Field label="Name" hint="Lowercase, hyphenated — e.g. ship-feature">
        <TextInput value={draft.name} onChange={(v) => set('name', v.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/-+/g, '-'))} placeholder="commit-changes" autoFocus />
      </Field>
      <Field label="Description"><TextInput value={draft.description ?? ''} onChange={(v) => set('description', v)} placeholder="One line: what this SOP is for" /></Field>

      <Field label="Scope"><Segmented options={SCOPES.map((s) => ({ key: s.key, label: s.label, tone: s.tone }))} value={draft.scope ?? 'global'} onChange={(v) => set('scope', v as WorkflowScope)} /></Field>
      {draft.scope === 'agent' && (
        <Field label="Agent" hint="Tie this SOP to a specific agent — native or from an ACP runtime. Offered only to that agent.">
          <Combobox options={agentOptions} value={draft.scope_ref ?? ''} onChange={(v) => set('scope_ref', v)} placeholder="Select an agent…" emptyText="No agents found" />
        </Field>
      )}
      {needsRef && <Field label={sm.refLabel ?? 'Scope reference'} hint={sm.refHint}><TextInput value={draft.scope_ref ?? ''} onChange={(v) => set('scope_ref', v)} placeholder={draft.scope === 'workspace' ? '/abs/path' : 'session-key'} /></Field>}

      <Field label="Fires when (match intent)" hint="Natural-language intent that should auto-surface this SOP mid-turn.">
        <TextArea value={draft.match_text ?? ''} onChange={(v) => set('match_text', v)} placeholder="e.g. committing changes, writing a commit message, staging files" rows={compact ? 2 : 3} />
      </Field>
      <Field label="Tags"><ChipInput values={draft.tags ?? []} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>

      <Field label="Steps" hint="Ordered checklist the agent follows. A step can be inline (title + detail) or a whole other workflow added as a step — composed, reusable SOPs.">
        <StepEditor steps={draft.steps} onChange={(v) => set('steps', v)} selfId={draft.id} allWorkflows={allWorkflows} />
      </Field>
    </div>
  )
}

function StepEditor({ steps, onChange, selfId, allWorkflows }: { steps: WorkflowStep[]; onChange: (s: WorkflowStep[]) => void; selfId?: string; allWorkflows: WorkflowItem[] }) {
  const [picking, setPicking] = useState(false)
  const byId = useMemo(() => new Map(allWorkflows.map((w) => [w.id, w])), [allWorkflows])
  const update = (i: number, patch: Partial<WorkflowStep>) => onChange(steps.map((s, idx) => idx === i ? { ...s, ...patch } : s))
  const remove = (i: number) => onChange(steps.filter((_, idx) => idx !== i))
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir; if (j < 0 || j >= steps.length) return
    const next = [...steps];[next[i], next[j]] = [next[j], next[i]]; onChange(next)
  }
  return (
    <div className="flex flex-col gap-s">
      <AnimatePresence initial={false}>
      {steps.map((s, i) => s.ref ? (
        <motion.div key={s.ref ? `ref-${s.ref}-${i}` : `step-${i}`} layout
          initial={{ opacity: 0, scaleY: 0.3, borderRadius: 'var(--radius-pill)' }}
          animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
          exit={{ opacity: 0, scaleY: 0.3 }}
          transition={spring.spatialDefault}
          style={{ originY: 0, outline: '1px solid color-mix(in srgb, var(--color-primary) 30%, transparent)' }}
          className="group flex items-center gap-s rounded-md bg-surface-container p-2" >
          <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill text-[0.7rem] tabular-nums" style={{ background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)' }}>{i + 1}</span>
          <WorkflowIcon size={14} className="text-primary shrink-0" />
          <span className="flex-1 truncate text-on-surface text-[0.875rem]">
            Run workflow <span style={{ fontVariationSettings: '"wght" 600' }}>{byId.get(s.ref)?.name ?? s.ref}</span>
            {!byId.has(s.ref) && <span className="text-danger text-[0.7rem] ml-1">(missing)</span>}
          </span>
          <div className="flex shrink-0 items-center opacity-0 group-hover:opacity-100 transition-opacity">
            <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="text-on-surface-low hover:text-on-surface disabled:opacity-30 px-1">↑</button>
            <button type="button" onClick={() => move(i, 1)} disabled={i === steps.length - 1} className="text-on-surface-low hover:text-on-surface disabled:opacity-30 px-1">↓</button>
            <button type="button" onClick={() => remove(i)} className="text-on-surface-low hover:text-danger px-1"><X size={14} /></button>
          </div>
        </motion.div>
      ) : (
        <motion.div key={`step-${i}`} layout
          initial={{ opacity: 0, scaleY: 0.3, borderRadius: 'var(--radius-pill)' }}
          animate={{ opacity: 1, scaleY: 1, borderRadius: 'var(--radius-md)' }}
          exit={{ opacity: 0, scaleY: 0.3 }}
          transition={spring.spatialDefault}
          style={{ originY: 0 }}
          className="group rounded-md bg-surface-container p-2">
          <div className="flex items-center gap-s mb-1.5">
            <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill text-[0.7rem] tabular-nums" style={{ background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)' }}>{i + 1}</span>
            <input value={s.title} onChange={(e) => update(i, { title: e.target.value })} placeholder="Step title (imperative)"
              className="flex-1 h-8 rounded-md bg-surface px-m text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            <div className="flex shrink-0 items-center opacity-0 group-hover:opacity-100 transition-opacity">
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="text-on-surface-low hover:text-on-surface disabled:opacity-30 px-1">↑</button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === steps.length - 1} className="text-on-surface-low hover:text-on-surface disabled:opacity-30 px-1">↓</button>
              <button type="button" onClick={() => remove(i)} className="text-on-surface-low hover:text-danger px-1"><X size={14} /></button>
            </div>
          </div>
          <textarea value={s.instruction} onChange={(e) => update(i, { instruction: e.target.value })} rows={2} placeholder="Optional: how-to detail injected with this step (markdown)"
            className="ml-7 w-[calc(100%-1.75rem)] rounded-md bg-surface px-m py-1.5 text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </motion.div>
      ))}
      </AnimatePresence>

      {/* The workflow-ref picker buds OUT of the Add-buttons row (a liquid droplet
          splitting off the button) instead of appearing from nowhere; the buttons
          and the picker occupy the same slot, so the picker grows from the top edge
          where the buttons sat. AnimatePresence lets it bud back in on cancel/pick. */}
      <AnimatePresence mode="wait" initial={false}>
        {picking ? (
          <Bud key="picker" from="top">
            <WorkflowRefPicker selfId={selfId} all={allWorkflows} currentRefs={steps.map((s) => s.ref).filter((r): r is string => !!r)}
              onPick={(id) => { onChange([...steps, { title: '', instruction: '', ref: id }]); setPicking(false) }} onCancel={() => setPicking(false)} />
          </Bud>
        ) : (
          <motion.div key="addbtns" className="flex items-center gap-s"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
            <button type="button" onClick={() => onChange([...steps, { title: '', instruction: '' }])} className="inline-flex items-center gap-1.5 rounded-md bg-surface-container px-m h-9 text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors"><Plus size={14} /> Add step</button>
            <button type="button" onClick={() => setPicking(true)} className="inline-flex items-center gap-1.5 rounded-md bg-surface-container px-m h-9 text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors"><WorkflowIcon size={14} /> Add a workflow</button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Pick another workflow to embed as a step. Choices that would form a cycle
 *  (the candidate already references this one, transitively) are disabled. */
function WorkflowRefPicker({ selfId, all, currentRefs, onPick, onCancel }: {
  selfId?: string; all: WorkflowItem[]; currentRefs: string[]; onPick: (id: string) => void; onCancel: () => void
}) {
  const [q, setQ] = useState('')
  const m = useMemo(() => refMap(all, selfId, currentRefs), [all, selfId, currentRefs])
  const candidates = useMemo(() => {
    const n = q.trim().toLowerCase()
    return all
      .filter((w) => w.id !== selfId && !currentRefs.includes(w.id))
      .filter((w) => !n || w.name.toLowerCase().includes(n))
      .map((w) => ({ w, cyclic: wouldCycle(m, selfId, w.id) }))
      .slice(0, 30)
  }, [all, selfId, currentRefs, q, m])

  return (
    <div className="rounded-md bg-surface-container p-2">
      <div className="relative mb-1.5">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a workflow to add" className="w-full h-8 rounded-md bg-surface pl-8 pr-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      <div className="max-h-52 overflow-y-auto flex flex-col gap-0.5">
        {candidates.length === 0 ? <div className="px-2 py-3 text-on-surface-low text-[0.8125rem]">No workflows to reference.</div> : candidates.map(({ w, cyclic }) => (
          <button key={w.id} type="button" disabled={cyclic} onClick={() => onPick(w.id)}
            className="flex items-center gap-s rounded-md px-2 py-1.5 text-left transition-colors enabled:hover:bg-surface-high disabled:opacity-45 disabled:cursor-not-allowed">
            <WorkflowIcon size={14} className="text-primary shrink-0" />
            <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{w.name}</span>
            {cyclic && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.7rem]" title="Would create a reference cycle"><AlertTriangle size={11} /> cycle</span>}
          </button>
        ))}
      </div>
      <div className="mt-1.5 flex justify-end"><button type="button" onClick={onCancel} className="text-on-surface-low text-[0.8125rem] hover:text-on-surface px-2 py-1">Cancel</button></div>
    </div>
  )
}

/** Strip a draft to the create/update payload. */
export function draftToPayload(d: WorkflowDraft): Record<string, unknown> {
  return {
    name: d.name.trim(),
    description: d.description ?? '',
    scope: d.scope ?? 'global',
    scope_ref: (scopeNeedsRef(d.scope) || d.scope === 'agent') ? (d.scope_ref ?? '') : '',
    tags: d.tags ?? [],
    match_text: d.match_text ?? '',
    enabled: d.enabled ?? true,
    // keep ref-steps (no title needed); keep inline steps with a title
    steps: (d.steps ?? [])
      .filter((s) => s.ref || s.title.trim())
      .map((s) => s.ref ? { ref: s.ref, title: s.title ?? '', instruction: s.instruction ?? '' } : { title: s.title.trim(), instruction: s.instruction ?? '' }),
  }
}
