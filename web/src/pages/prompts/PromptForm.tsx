import { useId, useMemo, useRef, useEffect } from 'react'
import { X, Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptItem, PromptKind, PromptVariable, PromptVarType, LaunchSpec } from '../../lib/api'
import { Field, TextInput, ChipInput } from '../tasks/formControls'
import { VAR_TYPES, detectPlaceholders, detectIncludes, promptVars } from './promptMeta'
import { RunnableTemplateField } from './RunnableTemplateField'

// Runnable template (#17): the draft carries a launch_spec (undefined = plain prompt).
export type PromptDraft = { name: string; kind: PromptKind; title: string; description: string; content: string; variables: PromptVariable[]; tags: string[]; source?: string; launchSpec?: LaunchSpec }

export function emptyDraft(kind: PromptKind = 'user'): PromptDraft {
  return { name: '', kind, title: '', description: '', content: '', variables: [], tags: [] }
}
export function toDraft(p: PromptItem): PromptDraft {
  return { name: p.name, kind: p.kind ?? 'user', title: p.title ?? '', description: p.description ?? '', content: p.content ?? '', variables: promptVars(p), tags: p.tags ?? [], source: p.source, launchSpec: p.launch_spec }
}
export function draftToPayload(d: PromptDraft): Record<string, unknown> {
  return {
    name: d.name.trim(),
    kind: d.kind,
    title: d.title.trim(),
    description: d.description.trim(),
    content: d.content,
    tags: d.tags,
    variables: d.variables.filter((v) => v.name.trim()).map((v) => ({
      name: v.name.trim(), type: v.type, description: v.description ?? '',
      required: !!v.required,
      ...(v.default !== undefined && v.default !== '' ? { default: v.default } : {}),
      ...(v.type === 'select' ? { options: v.options ?? [] } : {}),
    })),
    // A runnable template persists its launch_spec; {} clears it (back to plain).
    launch_spec: d.launchSpec ?? {},
  }
}

/** Shared prompt form: name + title + description + tags + the template body
 *  (with {{var}} placeholders + {{> snippet}} includes) + a TYPED variable
 *  editor. Undeclared placeholders are surfaced with one-click "add as variable";
 *  snippet includes are listed so the author sees what's pulled in. */
export function PromptForm({ draft, onChange, compact, nameLocked, registerInsert }: { draft: PromptDraft; onChange: (d: PromptDraft) => void; compact?: boolean; nameLocked?: boolean; registerInsert?: (fn: (text: string) => void) => void }) {
  const set = <K extends keyof PromptDraft>(k: K, v: PromptDraft[K]) => onChange({ ...draft, [k]: v })
  const taRef = useRef<HTMLTextAreaElement>(null)

  // Expose an insert-at-cursor handle so a syntax-reference palette can drop
  // constructs/functions/snippets straight into the template at the caret.
  useEffect(() => {
    if (!registerInsert) return
    registerInsert((text: string) => {
      const ta = taRef.current
      if (!ta) { set('content', draft.content + text); return }
      const start = ta.selectionStart ?? draft.content.length
      const end = ta.selectionEnd ?? start
      const next = draft.content.slice(0, start) + text + draft.content.slice(end)
      set('content', next)
      // Restore focus + place the caret just after the inserted text.
      requestAnimationFrame(() => { ta.focus(); const pos = start + text.length; ta.setSelectionRange(pos, pos) })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.content, registerInsert])

  // placeholders in the body not yet declared as variables
  const undeclared = useMemo(() => {
    const declared = new Set(draft.variables.map((v) => v.name))
    return detectPlaceholders(draft.content).filter((n) => !declared.has(n))
  }, [draft.content, draft.variables])
  // snippet includes referenced in the body (informational — their vars merge in
  // server-side at render/fill-in time).
  const includes = useMemo(() => detectIncludes(draft.content), [draft.content])

  const addVar = (name = '') => set('variables', [...draft.variables, { name, type: 'text', description: '', required: false }])
  const addVars = (names: string[]) => set('variables', [...draft.variables, ...names.map((name) => ({ name, type: 'text' as PromptVarType, description: '', required: false }))])
  const updateVar = (i: number, patch: Partial<PromptVariable>) => set('variables', draft.variables.map((v, idx) => idx === i ? { ...v, ...patch } : v))
  const removeVar = (i: number) => set('variables', draft.variables.filter((_, idx) => idx !== i))

  return (
    <div className={`flex flex-col ${compact ? 'gap-l' : 'gap-xl'}`}>
      <Field label="Name" hint="The stable id — referenced by @name and {{> name}}.">
        <TextInput value={draft.name} onChange={(v) => set('name', nameLocked ? draft.name : v)} placeholder="summarize-thread" autoFocus={!nameLocked} />
      </Field>
      <Field label="Title" hint="A human-readable label (optional — defaults from the name)."><TextInput value={draft.title} onChange={(v) => set('title', v)} placeholder="Summarize Thread" /></Field>
      <Field label="Description"><TextInput value={draft.description} onChange={(v) => set('description', v)} placeholder="One line: what this prompt does" /></Field>

      <Field label="Template" hint="The prompt body. {{variable}} placeholders, {% if %}/{% for %} logic, {{ fn() }} functions, and {{> snippet}} includes.">
        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={compact ? 6 : 12}
          spellCheck={false} name="prompt-template" aria-label="Prompt template body"
          placeholder={'Summarize the thread {{thread_url}} in {{style}} style.\n{{> signature}}'}
          className="w-full rounded-lg bg-surface-container px-3 py-2.5 font-mono text-[0.8125rem] leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 resize-y" />
      </Field>

      {includes.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
          <div className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] mb-1.5"><Puzzle size={13} className="text-info" /> Includes these snippets (their variables merge in automatically):</div>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => (
              <span key={n} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface-var text-[0.75rem]"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>
            ))}
          </div>
        </div>
      )}

      {undeclared.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <div className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] mb-1.5"><Wand2 size={13} className="text-primary" /> Placeholders in the template not yet declared:</div>
          <div className="flex flex-wrap gap-1.5">
            {undeclared.map((n) => (
              <button key={n} type="button" onClick={() => addVar(n)} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface text-[0.75rem] hover:bg-surface-highest transition-colors">
                <Plus size={12} /> <span className="font-mono">{n}</span>
              </button>
            ))}
            {undeclared.length > 1 && <button type="button" onClick={() => addVars(undeclared)} className="rounded-pill px-2 h-7 text-primary text-[0.75rem] hover:underline">Add all</button>}
          </div>
        </div>
      )}

      <Field label="Variables" hint="Typed inputs collected when the prompt is invoked.">
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} />
          ))}
          <button type="button" onClick={() => addVar()} className="inline-flex items-center gap-1.5 self-start rounded-md bg-surface-container px-m h-9 text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors"><Plus size={14} /> Add variable</button>
        </div>
      </Field>

      <Field label="Tags"><ChipInput values={draft.tags} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>

      <Field label="Runnable template" hint="Make this a fill-and-launch “campaign template” — its rendered body becomes a Project/Loop task you start with one click.">
        <RunnableTemplateField spec={draft.launchSpec} onChange={(s) => set('launchSpec', s)} />
      </Field>
    </div>
  )
}

function VariableRow({ v, onChange, onRemove }: { v: PromptVariable; onChange: (patch: Partial<PromptVariable>) => void; onRemove: () => void }) {
  const rid = useId()
  return (
    <div className="rounded-md bg-surface-container p-2 flex flex-col gap-2">
      <div className="flex items-center gap-s">
        <input value={v.name} onChange={(e) => onChange({ name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '_') })} placeholder="variable_name" aria-label="Variable name" name={`var-name-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <div className="relative">
          <select value={v.type} onChange={(e) => onChange({ type: e.target.value as PromptVarType })} aria-label="Variable type" name={`var-type-${rid}`}
            className="h-8 appearance-none rounded-md bg-surface pl-m pr-7 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
            {VAR_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
        </div>
        <button type="button" onClick={() => onChange({ required: !v.required })} className="rounded-pill px-2 h-7 text-[0.7rem] transition-colors" style={v.required ? { background: 'color-mix(in srgb, var(--color-danger) 18%, transparent)', color: 'var(--color-danger)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>{v.required ? 'required' : 'optional'}</button>
        <button type="button" onClick={onRemove} className="text-on-surface-low hover:text-danger px-1"><X size={14} /></button>
      </div>
      <div className="flex items-center gap-s">
        <input value={v.description ?? ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="Description (shown when invoked)" aria-label="Variable description" name={`var-desc-${rid}`}
          className="flex-1 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <input value={v.default == null ? '' : String(v.default)} onChange={(e) => onChange({ default: e.target.value })} placeholder="default" aria-label="Variable default value" name={`var-default-${rid}`}
          className="w-28 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      {v.type === 'select' && (
        <input value={(v.options ?? []).join(', ')} onChange={(e) => onChange({ options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} placeholder="Choices, comma-separated" aria-label="Variable choices" name={`var-opts-${rid}`}
          className="h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      )}
    </div>
  )
}
