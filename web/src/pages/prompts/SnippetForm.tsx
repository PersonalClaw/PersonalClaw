import { useMemo, useRef, useEffect } from 'react'
import { Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptSnippet, PromptVariable, PromptVarType } from '../../lib/api'
import { Field, TextInput, ChipInput } from '../../ui/forms'
import { AddItemButton } from '../../ui/AddItemButton'
import { detectPlaceholders, detectIncludes, promptVars } from './promptMeta'
import { VariableRow } from './VariableRow'
import { TextLink } from '../../ui/TextLink'

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
        {/* Stays a RAW textarea rather than `ui/forms`' TextArea, which would claim the Field's
            published label automatically: `taRef` drives cursor-position insertion for the snippet
            picker (registerInsert), and TextArea does not forward a ref. Adding ref forwarding to the
            primitive is a wider change than this pass — so it self-names instead. */}
        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={8}
          aria-label="Snippet content" spellCheck={false} placeholder={'— {{author}}, {{role}}'}
          className="w-full rounded-lg bg-surface-container px-3 py-2.5 font-mono text-[0.8125rem] leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-y" />
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
            {undeclared.length > 1 && <TextLink onClick={() => addVars(undeclared)} size="xs" className="rounded-pill px-2 h-7">Add all</TextLink>}
          </div>
        </div>
      )}

      <Field label="Variables" hint="Typed inputs — they merge into any prompt that includes this snippet.">
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} rowIndex={i} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} descriptionPlaceholder="Description" />
          ))}
          <AddItemButton className="self-start" onClick={() => addVar()}><Plus size={14} /> Add variable</AddItemButton>
        </div>
      </Field>

      <Field label="Tags"><ChipInput values={draft.tags} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>
    </div>
  )
}

