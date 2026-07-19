import { useEffect, useRef, useState } from 'react'
import { Eye, AlertTriangle, Loader2, Puzzle } from 'lucide-react'
import { api, type PromptVariable } from '../../lib/api'
import type { PromptDraft } from './PromptForm'

/** Live preview pane — renders the draft's CURRENT (unsaved) content through the
 *  real backend engine (POST /api/prompts/preview), so what the author sees is
 *  exactly what the model receives. Debounced; shows a sample-values form (typed
 *  widgets) that drives the render, plus inline render errors and the snippet
 *  includes pulled in. This is the "preview through the real engine" pattern —
 *  never a JS reimplementation that could drift from runtime. */
export function PromptPreviewPane({ draft }: { draft: PromptDraft }) {
  const vars = draft.variables.filter((v) => v.name.trim())
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [rendered, setRendered] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [includes, setIncludes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)

  // Seed sample values for any newly-declared variable (keep what the user typed).
  useEffect(() => {
    setValues((prev) => {
      const next = { ...prev }
      for (const v of vars) {
        if (!(v.name in next)) next[v.name] = v.default ?? (v.type === 'boolean' ? false : '')
      }
      return next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vars.map((v) => `${v.name}:${v.type}`).join('|')])

  const valuesKey = JSON.stringify(values)
  useEffect(() => {
    if (!draft.content.trim()) { setRendered(''); setError(null); setIncludes([]); return }
    const id = ++seq.current
    setBusy(true)
    const t = window.setTimeout(async () => {
      try {
        const r = await api.previewPrompt({ content: draft.content, variables: vars, values })
        if (id !== seq.current) return  // a newer request superseded this one
        setIncludes(r.includes ?? [])
        if (r.ok) { setRendered(r.rendered ?? ''); setError(null) }
        else { setError(r.error ?? 'Could not render'); }
      } catch (e) {
        if (id === seq.current) setError((e as Error).message)
      } finally {
        if (id === seq.current) setBusy(false)
      }
    }, 250)  // matches the app's debounce cadence
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.content, valuesKey, vars.map((v) => `${v.name}:${v.type}`).join('|')])

  const setVal = (name: string, v: unknown) => setValues((p) => ({ ...p, [name]: v }))

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-1.5 text-on-surface-var">
        <Eye size={14} /> <span data-type="title-s">Live preview</span>
        {busy && <Loader2 size={13} className="animate-spin text-on-surface-low" />}
      </div>

      {vars.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg bg-surface-container p-2.5">
          <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Sample values</div>
          {vars.map((v) => (
            <SampleField key={v.name} v={v} value={values[v.name]} onChange={(x) => setVal(v.name, x)} />
          ))}
        </div>
      )}

      {includes.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-on-surface-low text-[0.75rem]">
          <Puzzle size={12} /> includes:
          {includes.map((n) => <code key={n} className="rounded bg-surface-high px-1 font-mono">{n}</code>)}
        </div>
      )}

      {error ? (
        <div role="alert" className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>Couldn't render this template: {error}</span>
        </div>
      ) : (
        <pre className="min-h-[120px] flex-1 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container p-3 font-mono text-[0.8125rem] leading-relaxed text-on-surface">
          {rendered || <span className="text-on-surface-low">Type a template to see the assembled output.</span>}
        </pre>
      )}
    </div>
  )
}

function SampleField({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (v: unknown) => void }) {
  const base = 'w-full rounded-md bg-surface px-2.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  const label = (
    <label className="flex items-center gap-1.5 text-on-surface-var text-[0.75rem]">
      <code className="font-mono text-on-surface">{v.name}</code>
      {v.required && <span className="text-danger">*</span>}
    </label>
  )
  const sval = value == null ? '' : String(value)
  if (v.type === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-2">
        {label}
        <button type="button" onClick={() => onChange(!value)} aria-label={v.name}
          className="inline-flex items-center gap-1.5 rounded-pill px-2.5 h-7 text-[0.75rem] transition-colors"
          style={value ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          {value ? 'true' : 'false'}
        </button>
      </div>
    )
  }
  if (v.type === 'select') {
    return (
      <div className="flex flex-col gap-1">{label}
        <select value={sval} onChange={(e) => onChange(e.target.value)} aria-label={v.name} className={`${base} h-8 [color-scheme:dark]`}>
          <option value="">—</option>
          {(v.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    )
  }
  if (v.type === 'textarea') {
    return <div className="flex flex-col gap-1">{label}<textarea value={sval} onChange={(e) => onChange(e.target.value)} rows={2} aria-label={v.name} className={`${base} py-1.5 resize-y`} /></div>
  }
  return (
    <div className="flex flex-col gap-1">{label}
      <input type={v.type === 'number' ? 'number' : 'text'} value={sval} onChange={(e) => onChange(e.target.value)} aria-label={v.name} className={`${base} h-8`} />
    </div>
  )
}
