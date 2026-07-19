import { useMemo, useRef, useEffect } from 'react'
import { X, Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptSnippet, PromptVariable, PromptVarType } from '../../lib/api'
import { Field, TextInput, ChipInput } from '../tasks/formControls'
import { VAR_TYPES, detectPlaceholders, detectIncludes, promptVars } from './promptMeta'

export type SnippetDraft = { name: string; title: string; description: string; content: string; variables: PromptVariable[]; tags: string[]; source?: string }

export function emptySnippetDraft(): SnippetDraft {
  return { name: '', title: '', description: '', content: '', variables: [], tags: [] }
}
export function toSnippetDraft(s: PromptSnippet): SnippetDraft {
  return { name: s.name, title: s.title ?? '', description: s.description ?? '', content: s.content ?? '', variables: promptVars(s), tags: s.tags ?? [], source: s.source }
}
export function snippetDraftToPayload(d: SnippetDraft): Record<string, unknown> {
  return {
    name: d.name.trim(),
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
  }
}

/** Snippet authoring form — a reusable fragment: name + title + description +
 *  body (with {{var}} placeholders + nested {{> snippet}} includes) + typed vars. */
export function SnippetForm({ draft, onChange, nameLocked, registerInsert }: { draft: SnippetDraft; onChange: (d: SnippetDraft) => void; nameLocked?: boolean; registerInsert?: (fn: (text: string) => void) => void }) {
  const set = <K extends keyof SnippetDraft>(k: K, v: SnippetDraft[K]) => onChange({ ...draft, [k]: v })
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!registerInsert) return
    registerInsert((text: string) => {
      const ta = taRef.current
      if (!ta) { set('content', draft.content + text); return }
      const start = ta.selectionStart ?? draft.content.length
      const end = ta.selectionEnd ?? start
      set('content', draft.content.slice(0, start) + text + draft.content.slice(end))
      requestAnimationFrame(() => { ta.focus(); const pos = start + text.length; ta.setSelectionRange(pos, pos) })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.content, registerInsert])

  const undeclared = useMemo(() => {
    const declared = new Set(draft.variables.map((v) => v.name))
    return detectPlaceholders(draft.content).filter((n) => !declared.has(n))
  }, [draft.content, draft.variables])
  const includes = useMemo(() => detectIncludes(draft.content), [draft.content])

  const addVar = (name = '') => set('variables', [...draft.variables, { name, type: 'text', description: '', required: false }])
  const addVars = (names: string[]) => set('variables', [...draft.variables, ...names.map((name) => ({ name, type: 'text' as PromptVarType, description: '', required: false }))])
  const updateVar = (i: number, patch: Partial<PromptVariable>) => set('variables', draft.variables.map((v, idx) => idx === i ? { ...v, ...patch } : v))
  const removeVar = (i: number) => set('variables', draft.variables.filter((_, idx) => idx !== i))

  return (
    <div className="flex flex-col gap-l">
      <Field label="Name" hint="The id other prompts include with {{> name}}.">
        <TextInput value={draft.name} onChange={(v) => set('name', nameLocked ? draft.name : v)} placeholder="signature" autoFocus={!nameLocked} />
      </Field>
      <Field label="Title" hint="A human-readable label (optional)."><TextInput value={draft.title} onChange={(v) => set('title', v)} placeholder="Signature" /></Field>
      <Field label="Description"><TextInput value={draft.description} onChange={(v) => set('description', v)} placeholder="One line: what this fragment is" /></Field>

      <Field label="Content" hint="The fragment body. {{variable}} placeholders, logic, functions, and nested {{> snippet}} includes.">
        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={8}
          spellCheck={false} placeholder={'— {{author}}, {{role}}'}
          className="w-full rounded-lg bg-surface-container px-3 py-2.5 font-mono text-[0.8125rem] leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 resize-y" />
      </Field>

      {includes.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
          <div className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] mb-1.5"><Puzzle size={13} className="text-info" /> Includes these snippets:</div>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => <span key={n} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface-var text-[0.75rem]"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>)}
          </div>
        </div>
      )}

      {undeclared.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <div className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] mb-1.5"><Wand2 size={13} className="text-primary" /> Placeholders not yet declared:</div>
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

      <Field label="Variables" hint="Typed inputs — they merge into any prompt that includes this snippet.">
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} />
          ))}
          <button type="button" onClick={() => addVar()} className="inline-flex items-center gap-1.5 self-start rounded-md bg-surface-container px-m h-9 text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors"><Plus size={14} /> Add variable</button>
        </div>
      </Field>

      <Field label="Tags"><ChipInput values={draft.tags} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>
    </div>
  )
}

function VariableRow({ v, onChange, onRemove }: { v: PromptVariable; onChange: (patch: Partial<PromptVariable>) => void; onRemove: () => void }) {
  return (
    <div className="rounded-md bg-surface-container p-2 flex flex-col gap-2">
      <div className="flex items-center gap-s">
        <input value={v.name} onChange={(e) => onChange({ name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '_') })} placeholder="variable_name"
          className="flex-1 h-8 rounded-md bg-surface px-m font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <select value={v.type} onChange={(e) => onChange({ type: e.target.value as PromptVarType })}
          className="h-8 appearance-none rounded-md bg-surface pl-m pr-7 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
          {VAR_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <button type="button" onClick={() => onChange({ required: !v.required })} className="rounded-pill px-2 h-7 text-[0.7rem] transition-colors" style={v.required ? { background: 'color-mix(in srgb, var(--color-danger) 18%, transparent)', color: 'var(--color-danger)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>{v.required ? 'required' : 'optional'}</button>
        <button type="button" onClick={onRemove} className="text-on-surface-low hover:text-danger px-1"><X size={14} /></button>
      </div>
      <div className="flex items-center gap-s">
        <input value={v.description ?? ''} onChange={(e) => onChange({ description: e.target.value })} placeholder="Description"
          className="flex-1 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <input value={v.default == null ? '' : String(v.default)} onChange={(e) => onChange({ default: e.target.value })} placeholder="default"
          className="w-28 h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      </div>
      {v.type === 'select' && (
        <input value={(v.options ?? []).join(', ')} onChange={(e) => onChange({ options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} placeholder="Choices, comma-separated"
          className="h-8 rounded-md bg-surface px-m text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      )}
    </div>
  )
}
