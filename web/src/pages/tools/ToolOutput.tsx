import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Markdown } from '../../ui/Markdown'

/** Renders a tool's invoke output intelligently by detected SHAPE. Tools carry
 *  no output schema (the backend returns a plain string), so rather than dump
 *  raw text we sniff the content: JSON → table/tree, markdown → rendered, else
 *  → clean monospace text. This is the honest stand-in for schema-driven output
 *  UI until tools declare an output schema (then this can switch on it). */
export function ToolOutput({ text }: { text: string }) {
  const trimmed = (text ?? '').trim()
  if (!trimmed) return <p className="text-on-surface-low text-[0.8125rem]">(no output)</p>

  const json = tryParseJson(trimmed)
  if (json !== undefined) return <JsonValue value={json} top />

  if (looksMarkdown(trimmed)) return <div className="text-[0.875rem]"><Markdown>{trimmed}</Markdown></div>

  return <pre className="text-on-surface-var text-[0.8125rem] font-mono whitespace-pre-wrap break-words">{trimmed}</pre>
}

function tryParseJson(s: string): unknown {
  if (!/^[[{]/.test(s)) return undefined // only attempt for object/array roots
  try { return JSON.parse(s) } catch { return undefined }
}

function looksMarkdown(s: string): boolean {
  return /(^|\n)#{1,6}\s|\n[-*]\s|\n\d+\.\s|```|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|(^|\n)\|.*\|/.test(s)
}

function isPrimitive(v: unknown): v is string | number | boolean | null {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v)
}

function JsonValue({ value, top }: { value: unknown; top?: boolean }) {
  // array of objects → table
  if (Array.isArray(value) && value.length > 0 && value.every((r) => r && typeof r === 'object' && !Array.isArray(r))) {
    return <JsonTable rows={value as Record<string, unknown>[]} />
  }
  // array of primitives → bullet list
  if (Array.isArray(value) && value.every(isPrimitive)) {
    return <ul className="flex flex-col gap-0.5">{value.map((v, i) => <li key={i} className="text-on-surface text-[0.8125rem] flex gap-s"><span className="text-on-surface-low tabular-nums">{i + 1}.</span><Scalar v={v} /></li>)}</ul>
  }
  // mixed array → indexed rows
  if (Array.isArray(value)) {
    return <div className="flex flex-col gap-1">{value.map((v, i) => <Row key={i} label={`[${i}]`} value={v} />)}</div>
  }
  // object → key/value rows
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return <span className="text-on-surface-low text-[0.8125rem]">{'{}'}</span>
    return <div className={`flex flex-col gap-1 ${top ? '' : 'pl-3 border-l border-outline-variant/30'}`}>{entries.map(([k, v]) => <Row key={k} label={k} value={v} />)}</div>
  }
  return <Scalar v={value} />
}

/** One key → value row; nested objects/arrays collapse. */
function Row({ label, value }: { label: string; value: unknown }) {
  const nested = value !== null && typeof value === 'object'
  const [open, setOpen] = useState(true)
  return (
    <div className="text-[0.8125rem]">
      <div className="flex items-start gap-s">
        {nested ? (
          <button onClick={() => setOpen((v) => !v)} className="inline-flex items-center gap-1 text-on-surface-var hover:text-on-surface shrink-0">
            <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
            <span className="font-mono">{label}</span>
            <span className="text-on-surface-low">{Array.isArray(value) ? `[${(value as unknown[]).length}]` : '{…}'}</span>
          </button>
        ) : (
          <>
            <span className="font-mono text-on-surface-low shrink-0">{label}</span>
            <Scalar v={value} />
          </>
        )}
      </div>
      {nested && open && <div className="mt-1 ml-3"><JsonValue value={value} /></div>}
    </div>
  )
}

function Scalar({ v }: { v: unknown }) {
  if (v === null) return <span className="text-on-surface-low italic">null</span>
  if (typeof v === 'boolean') return <span style={{ color: v ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>{String(v)}</span>
  if (typeof v === 'number') return <span className="text-primary-emphasis tabular-nums">{v}</span>
  return <span className="text-on-surface break-words whitespace-pre-wrap">{String(v)}</span>
}

/** Array-of-objects → table. Columns = union of keys (capped); cells render
 *  scalars inline and nest objects/arrays as compact JSON. */
function JsonTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols: string[] = []
  for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k)
  const shown = cols.slice(0, 8)
  return (
    <div className="overflow-x-auto rounded-md border border-outline-variant/30">
      <table className="w-full border-collapse text-[0.75rem]">
        <thead>
          <tr>{shown.map((c) => <th key={c} className="border-b border-outline-variant/40 bg-surface-high px-2 py-1.5 text-left font-mono text-on-surface-var" style={{ fontVariationSettings: '"wght" 500' }}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-surface-high/40">
              {shown.map((c) => <td key={c} className="border-b border-outline-variant/20 px-2 py-1.5 align-top max-w-[260px]"><Cell v={r[c]} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Cell({ v }: { v: unknown }) {
  if (v === undefined) return <span className="text-on-surface-low">—</span>
  if (isPrimitive(v)) return <Scalar v={v} />
  if (Array.isArray(v) && v.every(isPrimitive)) return <span className="text-on-surface-var break-words">{v.join(', ')}</span>
  return <span className="font-mono text-on-surface-low break-words">{JSON.stringify(v)}</span>
}
