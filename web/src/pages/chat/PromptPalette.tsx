import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Search, ChevronLeft, FileText, CornerDownLeft } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { api, type PromptItem, type PromptVariable } from '../../lib/api'
import { seedRenderValues } from '../prompts/promptMeta'

/** Composer prompt palette — pick a USER prompt, fill its variables (the merged
 *  set, including any from snippets it pulls in), preview the rendered text, then
 *  Insert it into the composer or Send it straight away. The power-user path
 *  (`@name key=value`) stays in chat_runner; this is the guided UI.
 *
 *  Two steps: (1) search + pick; (2) fill-in (skipped when the prompt has no
 *  variables — its rendered content inserts immediately). */
export function PromptPalette({ onInsert, onSend, onClose }: {
  onInsert: (text: string) => void
  onSend?: (text: string) => void
  onClose: () => void
}) {
  const [items, setItems] = useState<PromptItem[] | null>(null)
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<PromptItem | null>(null)  // full detail (with merged_variables)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.prompts('user').then(setItems).catch(() => setItems([]))
  }, [])

  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return n ? items.filter((p) => `${p.name} ${p.title ?? ''} ${p.description ?? ''} ${(p.tags ?? []).join(' ')}`.toLowerCase().includes(n)) : items
  }, [items, q])

  async function pick(p: PromptItem) {
    setErr(''); setLoadingDetail(true)
    try {
      const full = await api.prompt(p.name)
      const vars = full.merged_variables?.length ? full.merged_variables : (full.variables ?? [])
      if (vars.length === 0) {
        // No variables → render + insert straight away (no fill-in step).
        const r = await api.renderPrompt(p.name, {})
        onInsert(r.rendered)
        onClose()
        return
      }
      setPicked(full)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load that prompt')
    } finally { setLoadingDetail(false) }
  }

  return (
    <Modal title={picked ? (picked.title || picked.name) : 'Insert a prompt'}
      icon={picked ? <FileText size={18} className="text-primary" /> : <Search size={18} className="text-primary" />}
      onClose={onClose}>
      {picked ? (
        <FillIn prompt={picked} onBack={() => setPicked(null)} onInsert={(t) => { onInsert(t); onClose() }} onSend={onSend ? (t) => { onSend(t); onClose() } : undefined} />
      ) : (
        <div className="flex min-h-[320px] flex-col gap-3">
          <div className="flex items-center gap-2 rounded-md bg-surface-high px-2.5 py-1.5">
            <Search size={14} className="shrink-0 text-on-surface-low" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your prompts…" aria-label="Search prompts"
              onKeyDown={(e) => {
                // Enter picks the first match — the common search → Enter flow (mirrors
                // the composer's @-mention menu), so a user need not reach for the mouse.
                if (e.key === 'Enter' && filtered && filtered.length > 0 && !loadingDetail) { e.preventDefault(); void pick(filtered[0]) }
                else if (e.key === 'Escape' && q) { e.preventDefault(); setQ('') }
              }}
              className="min-w-0 flex-1 bg-transparent text-[0.875rem] text-on-surface outline-none placeholder:text-on-surface-low" />
            {loadingDetail && <Loader2 size={14} className="animate-spin text-on-surface-low" />}
          </div>
          {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
          <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40">
            {filtered === null ? (
              <div className="flex h-40 items-center justify-center"><Loader2 size={18} className="animate-spin text-on-surface-low" /></div>
            ) : filtered.length === 0 ? (
              <div className="flex h-40 flex-col items-center justify-center gap-1 px-4 text-center text-on-surface-low text-[0.8125rem]">
                {q ? `No prompts match “${q.trim()}”.` : 'No user prompts yet. Create one on the Prompts page.'}
              </div>
            ) : (
              <div className="flex flex-col">
                {filtered.map((p) => (
                  <button key={p.name} type="button" onClick={() => pick(p)}
                    className="flex items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-high">
                    <FileText size={15} className="mt-0.5 shrink-0 text-on-surface-low" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-on-surface text-[0.875rem]">{p.title || p.name}</span>
                      {p.description && <span className="block truncate text-on-surface-low text-[0.75rem]">{p.description}</span>}
                    </span>
                    {(p.variables?.length ?? 0) > 0 && <span className="shrink-0 rounded-pill bg-surface-highest px-1.5 py-0.5 text-on-surface-low text-[0.65rem]">{p.variables!.length} var{p.variables!.length > 1 ? 's' : ''}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}

/** Step 2 — one typed input per (merged) variable + a live preview of the rendered
 *  prompt, then Insert / Send. */
function FillIn({ prompt, onBack, onInsert, onSend }: {
  prompt: PromptItem
  onBack: () => void
  onInsert: (text: string) => void
  onSend?: (text: string) => void
}) {
  const vars: PromptVariable[] = prompt.merged_variables?.length ? prompt.merged_variables : (prompt.variables ?? [])
  const [values, setValues] = useState<Record<string, unknown>>(() => seedRenderValues(vars))
  const [preview, setPreview] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const debTimer = useRef<number | null>(null)

  // Live preview: debounce-render through the server engine so includes + the full
  // mini-language resolve exactly as they will at send time (no client re-impl).
  useEffect(() => {
    if (debTimer.current) window.clearTimeout(debTimer.current)
    debTimer.current = window.setTimeout(() => {
      api.renderPrompt(prompt.name, values)
        .then((r) => { setPreview(r.rendered); setErr('') })
        .catch((e) => setErr(e instanceof Error ? e.message : 'render failed'))
    }, 250)
    return () => { if (debTimer.current) window.clearTimeout(debTimer.current) }
  }, [prompt.name, values])

  const missingRequired = vars.some((v) => v.required && !String(values[v.name] ?? '').trim())

  async function finalize(send: boolean) {
    setBusy(true); setErr('')
    try {
      const r = await api.renderPrompt(prompt.name, values)
      if (send && onSend) onSend(r.rendered)
      else onInsert(r.rendered)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'render failed'); setBusy(false)
    }
  }

  return (
    <div className="flex min-h-[320px] flex-col gap-3"
      onKeyDown={(e) => {
        // ⌘/Ctrl+↵ submits the fill-in (Send if available, else Insert) — matches the
        // app-wide textarea-submit convention so the user needn't reach for the mouse.
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !busy && !missingRequired) {
          e.preventDefault(); void finalize(!!onSend)
        }
      }}>
      <button type="button" onClick={onBack} className="inline-flex items-center gap-1 self-start text-on-surface-low text-[0.8125rem] hover:text-on-surface">
        <ChevronLeft size={14} /> All prompts
      </button>
      {prompt.description && <p className="text-on-surface-var text-[0.8125rem]">{prompt.description}</p>}

      <div className="flex flex-col gap-2">
        {vars.map((v) => (
          <label key={v.name} className="flex flex-col gap-1">
            <span className="text-on-surface-var text-[0.75rem]">{v.name}{v.required && <span className="text-danger"> *</span>}{v.description && <span className="text-on-surface-low"> — {v.description}</span>}</span>
            <VarInput v={v} value={values[v.name]} onChange={(val) => setValues((s) => ({ ...s, [v.name]: val }))} />
          </label>
        ))}
      </div>

      <div>
        <div className="mb-1 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Preview</div>
        <pre className="max-h-44 overflow-y-auto rounded-md bg-surface-container px-3 py-2 text-on-surface-var text-[0.8125rem] whitespace-pre-wrap break-words">{preview || '…'}</pre>
      </div>

      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      <div className="flex items-center justify-end gap-2 border-t border-outline-variant/40 pt-3">
        <Button variant="ghost" size="sm" onClick={onBack}>Cancel</Button>
        <Button variant="secondary" size="sm" disabled={busy || missingRequired} onClick={() => finalize(false)}>Insert</Button>
        {onSend && <Button size="sm" disabled={busy || missingRequired} onClick={() => finalize(true)}><CornerDownLeft size={14} /> Send</Button>}
      </div>
    </div>
  )
}

function VarInput({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (v: unknown) => void }) {
  const base = 'w-full rounded-md bg-surface-container px-2.5 py-1.5 text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  if (v.type === 'boolean') {
    return <Toggle on={!!value} onChange={(val) => onChange(val)} size="sm" />
  }
  if (v.type === 'select') {
    return (
      <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={`${base} [color-scheme:dark]`}>
        <option value="">—</option>
        {(v.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  if (v.type === 'textarea') {
    return <textarea value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} rows={3} className={`${base} resize-y`} />
  }
  if (v.type === 'number') {
    return <input type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} className={base} />
  }
  return <input value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={base} />
}
