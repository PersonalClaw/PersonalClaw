import { useEffect, useMemo, useState } from 'react'
import { Pencil, Trash2, Check, X, Play, Loader2, Lock, Code2, Eye, Puzzle, Rocket } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { Markdown } from '../../ui/Markdown'
import { Skeleton } from '../../ui/ListScaffold'
import { confirmDelete } from '../../ui/dialog'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type PromptItem, type PromptVariable } from '../../lib/api'
import { Field } from '../tasks/formControls'
import { isReadOnly, sourceTone, sourceLabel, promptVars, seedRenderValues } from './promptMeta'
import { toDraft, draftToPayload, type PromptDraft } from './PromptForm'
import { PromptEditFields } from './PromptEditFields'

/** Substitute each variable's default into the template so the rendered view
 *  reads naturally (e.g. {{bot_name}} → "PersonalClaw"). Placeholders without a
 *  default are left as-is so the author still sees them. */
function fillDefaults(content: string, vars: PromptVariable[]): string {
  let out = content
  for (const v of vars) {
    if (v.default != null && v.default !== '') {
      out = out.split(`{{${v.name}}}`).join(String(v.default))
    }
  }
  return out
}

/** Prompt inspector: view ↔ in-panel edit, plus a "Try it" render panel with one
 *  typed input per variable that calls /api/prompts/{name}/render. Bundled/
 *  marketplace prompts are read-only (edit/delete hidden). */
export function PromptDetail({ prompt, onSaved, onDeleted, editing: editingProp, onEditingChange, onNavigate }: {
  prompt: PromptItem
  onSaved: (name: string) => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
  // Route to a launched run's cockpit through the hash router (never write
  // location.hash directly — the url-navigation doctrine forbids raw nav in pages).
  onNavigate: (path: string) => void
}) {
  const readOnly = isReadOnly(prompt.source)
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled;
  // marketplace/read-only prompts can never enter the edit form.
  const editing = editingProp && !readOnly
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<PromptDraft>(() => toDraft(prompt))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  // The list payload may omit `content`; hydrate the full template on open. When
  // the prop already carries content we skip the fetch (the prop IS the full).
  const { data: fetched, refresh: refetch } = useCachedData<PromptItem | undefined>(`prompt:${prompt.name}`, () => (prompt.content == null ? api.prompt(prompt.name) : Promise.resolve(undefined)), { persist: true })
  const full = prompt.content != null ? prompt : fetched

  useEffect(() => { if (full) setDraft(toDraft(full)) }, [full])

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    // invalidateCache alone is not enough: this component stays mounted after Save
    // (same list-row key), so the hydration hook never re-runs and the view keeps
    // showing the PRE-save record. Explicitly refetch after invalidating.
    try { const r = await api.savePrompt(prompt.name, draftToPayload(draft)); invalidateCache(`prompt:${prompt.name}`); refetch(); onSaved(r.prompt?.name ?? prompt.name); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('prompt', prompt.name))) return
    try { await api.deletePrompt(prompt.name); onDeleted() } catch { setErr('Delete failed') }
  }

  if (editing) {
    // Edit mode mirrors the view layout (same header row + Section rhythm); only
    // the section *contents* swap to editable controls.
    return (
      <div className="flex flex-col gap-l">
        <div className="flex items-center gap-s">
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Pencil size={13} /> Editing</span>
          <span className="ml-auto inline-flex items-center rounded-pill px-m h-6 text-[0.7rem]" style={{ background: `color-mix(in srgb, ${sourceTone(prompt.source)} 16%, transparent)`, color: sourceTone(prompt.source) }}>{sourceLabel(prompt.source)}</span>
        </div>
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <PromptEditFields draft={draft} onChange={setDraft} Section={Section} />
        <div className="sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={() => { if (full) setDraft(toDraft(full)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !draft.name.trim()}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    )
  }

  if (full === undefined) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  // The variable set to render/fill = the merged set from the API (own ∪ the vars
  // of every included snippet, host wins) when present; else the prompt's own.
  const ownVars = promptVars(full)
  const vars = full.merged_variables?.length ? full.merged_variables : ownVars
  const includes = full.includes ?? []
  return (
    <div className="flex flex-col gap-l">
      <div className="flex items-center gap-s">
        {readOnly ? (
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Lock size={13} /> {sourceLabel(prompt.source)} — read-only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        {full.kind && <span className="inline-flex items-center rounded-pill px-m h-6 text-[0.7rem]" style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>{full.kind} prompt</span>}
        {full.launch_spec && Object.keys(full.launch_spec).length > 0 && (
          <span className="inline-flex items-center gap-1 rounded-pill px-m h-6 text-[0.7rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }}><Rocket size={11} /> runnable</span>
        )}
        <span className="ml-auto inline-flex items-center rounded-pill px-m h-6 text-[0.7rem]" style={{ background: `color-mix(in srgb, ${sourceTone(prompt.source)} 16%, transparent)`, color: sourceTone(prompt.source) }}>{sourceLabel(prompt.source)}</span>
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {full.title && <h2 data-type="title-m" className="text-on-surface">{full.title}</h2>}
      {full.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{full.description}</p>}

      {(full.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">{full.tags!.map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{t}</span>)}</div>
      )}

      {includes.length > 0 && (
        <Section label={`Includes · ${includes.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => (
              <span key={n} className="inline-flex items-center gap-1 rounded-pill bg-surface-container px-2 h-7 font-mono text-on-surface-var text-[0.75rem]"><Puzzle size={12} className="text-info" /> {n}</span>
            ))}
          </div>
        </Section>
      )}

      {vars.length > 0 && (
        <Section label={`Variables · ${vars.length}`}>
          <div className="flex flex-col gap-1.5">
            {vars.map((v) => (
              <div key={v.name} className="rounded-md bg-surface-container px-m py-1.5">
                <div className="flex items-center gap-s">
                  <span className="font-mono text-on-surface text-[0.8125rem]">{v.name}</span>
                  <span className="text-on-surface-low text-[0.7rem]">{v.type}</span>
                  {v.required && <span className="text-danger text-[0.7rem]">required</span>}
                  {v.default != null && v.default !== '' && <span className="text-on-surface-low text-[0.7rem]">default: {String(v.default)}</span>}
                </div>
                {v.description && <p className="mt-0.5 text-on-surface-var text-[0.8125rem]">{v.description}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      <TemplateSection content={full.content || ''} vars={vars} />

      <RenderPanel name={prompt.name} vars={vars} onNavigate={onNavigate}
        launchable={!!full.launch_spec && Object.keys(full.launch_spec).length > 0}
        launchKind={full.launch_spec?.kind ?? 'goal'} />
    </div>
  )
}

/** "Try it" — one typed input per variable → POST /render → substituted output.
 *  For a RUNNABLE template (#17) it doubles as "Fill & launch": the same filled
 *  values create + start a Project/Loop run and navigate to its cockpit. */
function RenderPanel({ name, vars, launchable, launchKind, onNavigate }: { name: string; vars: PromptVariable[]; launchable?: boolean; launchKind?: string; onNavigate: (path: string) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>(() => seedRenderValues(vars))
  const [out, setOut] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [launching, setLaunching] = useState(false)

  useEffect(() => { setValues(seedRenderValues(vars)); setOut(null); setErr('') }, [name])

  // Required vars must be filled before a launch (render tolerates blanks; a run
  // shouldn't start half-specified). Mirrors the loop composer's min gate.
  const missing = vars.filter((v) => v.required && (values[v.name] == null || String(values[v.name]).trim() === '')).map((v) => v.name)

  async function render() {
    setLoading(true); setErr(''); setOut(null)
    try { const r = await api.renderPrompt(name, values); setOut(r.rendered) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Render failed') } finally { setLoading(false) }
  }

  async function launch() {
    if (missing.length) { setErr(`Fill the required variable${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}`); return }
    setLaunching(true); setErr('')
    try {
      const r = await api.launchCampaignTemplate(name, values)
      // Navigate to the launched run's cockpit (code loops live under /code/<id>).
      const path = (r.kind === 'code') ? `code/${r.loop_id}` : `loops/${r.loop_id}`
      onNavigate(path)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Launch failed'); setLaunching(false) }
  }

  return (
    <Section label={launchable ? 'Fill & launch' : 'Try it'}>
      <div className="flex flex-col gap-2">
        {vars.map((v) => (
          <Field key={v.name} label={`${v.name}${v.required ? ' *' : ''}`}>
            <RenderInput v={v} value={values[v.name]} onChange={(val) => setValues((s) => ({ ...s, [v.name]: val }))} />
          </Field>
        ))}
        <div className="flex items-center gap-2">
          <Button size="sm" variant={launchable ? 'secondary' : 'primary'} onClick={render} disabled={loading} className="self-start">{loading ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} {launchable ? 'Preview' : 'Render'}</Button>
          {launchable && (
            <Button size="sm" onClick={launch} disabled={launching} className="self-start">
              {launching ? <Loader2 size={15} className="animate-spin" /> : <Rocket size={15} />} {launching ? 'Launching…' : `Launch ${launchKind ?? 'goal'} run`}
            </Button>
          )}
        </div>
      </div>
      {err && <p className="mt-2 text-danger text-[0.8125rem]">{err}</p>}
      {out != null && (
        <div className="mt-2 rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{out}</Markdown></div>
      )}
    </Section>
  )
}

function RenderInput({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (v: unknown) => void }) {
  const base = 'w-full rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  // Stable id/name per variable so each Try-it field is identifiable to screen
  // readers + browser autofill (fixes the "form field should have an id or name"
  // a11y advisory).
  const fid = `prompt-var-${v.name}`
  if (v.type === 'boolean') {
    return <Toggle on={!!value} onChange={onChange} size="sm" />
  }
  if (v.type === 'select') {
    return (
      <select id={fid} name={v.name} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={`${base} [color-scheme:dark]`}>
        <option value="">—</option>
        {(v.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  if (v.type === 'textarea') {
    return <textarea id={fid} name={v.name} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} rows={3} placeholder={v.description} className={`${base} resize-y`} />
  }
  if (v.type === 'number') {
    return <input id={fid} name={v.name} type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} placeholder={v.description} className={base} />
  }
  return <input id={fid} name={v.name} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder={v.description} className={base} />
}

/** The template body — rendered (defaults substituted, Markdown) by default,
 *  with a toggle to the raw source. */
function TemplateSection({ content, vars }: { content: string; vars: PromptVariable[] }) {
  const [raw, setRaw] = useState(false)
  const rendered = useMemo(() => fillDefaults(content, vars), [content, vars])
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-s">
        <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Template</div>
        <button
          type="button"
          onClick={() => setRaw((r) => !r)}
          className="ml-auto inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 text-on-surface-low text-[0.7rem] hover:text-on-surface transition-colors"
          title={raw ? 'Show rendered' : 'Show raw template'}
        >
          {raw ? <><Eye size={12} /> Rendered</> : <><Code2 size={12} /> Raw</>}
        </button>
      </div>
      {raw ? (
        <pre className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono overflow-x-auto whitespace-pre-wrap break-words">{content || '—'}</pre>
      ) : (
        <div className="rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.875rem] leading-relaxed">
          <Markdown>{rendered || '—'}</Markdown>
        </div>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
