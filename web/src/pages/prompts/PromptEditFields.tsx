import { useMemo, useRef } from 'react'
import { Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptVariable, PromptVarType } from '../../lib/api'
import { AddItemButton } from '../../ui/AddItemButton'
import { ChipInput } from '../../ui/forms'
import { detectPlaceholders, detectIncludes } from './promptMeta'
import type { PromptDraft } from './PromptForm'
import { PromptPreviewPane } from './PromptPreviewPane'
import { SyntaxReference } from './SyntaxReference'
import { RunnableTemplateField } from './RunnableTemplateField'
import { VariableRow } from './VariableRow'
import { TextLink } from '../../ui/TextLink'

/** Edit-mode fields that mirror the view's section rhythm (Description → Tags →
 *  Variables → Template). Same `Section` wrapper as the read view, so toggling
 *  edit only swaps the section *contents* to editable controls — not the layout.
 *  Name is intentionally not editable here (renames go through delete+create). */
export function PromptEditFields({ draft, onChange, Section }: {
  draft: PromptDraft
  onChange: (d: PromptDraft) => void
  Section: (props: { label: string; children: React.ReactNode }) => React.ReactNode
}) {
  const set = <K extends keyof PromptDraft>(k: K, v: PromptDraft[K]) => onChange({ ...draft, [k]: v })
  const taRef = useRef<HTMLTextAreaElement>(null)
  const insertAtCursor = (text: string) => {
    const ta = taRef.current
    if (!ta) { set('content', draft.content + text); return }
    const start = ta.selectionStart ?? draft.content.length
    const end = ta.selectionEnd ?? start
    set('content', draft.content.slice(0, start) + text + draft.content.slice(end))
    requestAnimationFrame(() => { ta.focus(); const pos = start + text.length; ta.setSelectionRange(pos, pos) })
  }

  const undeclared = useMemo(() => {
    const declared = new Set(draft.variables.map((v) => v.name))
    return detectPlaceholders(draft.content).filter((n) => !declared.has(n))
  }, [draft.content, draft.variables])
  const includes = useMemo(() => detectIncludes(draft.content), [draft.content])

  const addVar = (name = '') => set('variables', [...draft.variables, { name, type: 'text', description: '', required: false }])
  const addVars = (names: string[]) => set('variables', [...draft.variables, ...names.map((name) => ({ name, type: 'text' as PromptVarType, description: '', required: false }))])
  const updateVar = (i: number, patch: Partial<PromptVariable>) => set('variables', draft.variables.map((v, idx) => idx === i ? { ...v, ...patch } : v))
  const removeVar = (i: number) => set('variables', draft.variables.filter((_, idx) => idx !== i))

  const inputCls = 'w-full rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'

  return (
    <div className="flex flex-col gap-l">
      <Section label="Title">
        <input value={draft.title} onChange={(e) => set('title', e.target.value)} aria-label="Prompt title"
          placeholder="A human-readable label" className={inputCls} />
      </Section>

      <Section label="Description">
        <input value={draft.description} onChange={(e) => set('description', e.target.value)} aria-label="Prompt description"
          placeholder="One line: what this prompt does" className={inputCls} />
      </Section>

      <Section label="Tags">
        {/* ChipInput, matching PromptForm's Tags field: it drafts locally and commits
            per chip. The raw input this replaces re-parsed itself on every keystroke
            (split → trim → filter(Boolean) → join), which ate the comma as soon as it
            was typed — "red,green" became the single tag "redgreen", so the field
            could never hold more than one tag. */}
        <ChipInput values={draft.tags} onChange={(v) => set('tags', v)}
          ariaLabel="Prompt tags" placeholder="Add a tag, Enter" />
      </Section>

      <Section label={`Variables · ${draft.variables.length}`}>
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} rowIndex={i} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} />
          ))}
          <AddItemButton className="self-start" onClick={() => addVar()}><Plus size={14} /> Add variable</AddItemButton>
        </div>
      </Section>

      <Section label="Template">
        {/* `Section` is injected as a PROP and renders a bare label div — it publishes no label id,
            so nothing here can claim one. Each control names itself after its section. */}
        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={12}
          aria-label="Prompt template" spellCheck={false} placeholder={'The prompt body. {{variable}} placeholders, {% if %}/{% for %} logic, {{ fn() }} functions, and {{> snippet}} includes.'}
          className="w-full rounded-lg bg-surface-container px-3 py-2.5 font-mono text-[0.8125rem] leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-y" />
        {includes.length > 0 && (
          <div className="mt-2 rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
            <div className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] mb-1.5"><Puzzle size={13} className="text-info" /> Includes snippets (their variables merge in):</div>
            <div className="flex flex-wrap gap-1.5">
              {includes.map((n) => <span key={n} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface-var text-[0.75rem]"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>)}
            </div>
          </div>
        )}
        {undeclared.length > 0 && (
          <div className="mt-2 rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
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
      </Section>

      <Section label="Runnable template">
        <RunnableTemplateField spec={draft.launchSpec} onChange={(s) => set('launchSpec', s)} />
      </Section>

      <Section label="Live preview">
        <PromptPreviewPane draft={draft} />
      </Section>

      <Section label="Syntax reference">
        <SyntaxReference onInsert={insertAtCursor} />
      </Section>
    </div>
  )
}

