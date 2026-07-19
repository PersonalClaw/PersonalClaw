/** Shared primitives for the tool render registry (tool-io-rendering).
 *
 * RawBlock — the always-correct monospace fallback (today's look).
 * KeyValueFields — schema-driven input: each arg as a labeled field, type-aware.
 * ContentTypeOutput — output rendered by its content_type (diff/json/log/csv/
 *   markdown), composing the existing Markdown (incl. its diff block) + ToolOutput
 *   JSON tree. Everything degrades to RawBlock.
 */
import { type ReactNode } from 'react'
import { Markdown } from '../../../ui/Markdown'
import { ToolOutput } from '../../tools/ToolOutput'
import type { ToolSegment } from '../chatTypes'

/** The raw monospace block — the floor every renderer falls back to. Accepts a
 *  string or a rendered child (so richer renderers can nest under the label). */
export function RawBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-1.5 last:mb-0">
      <div className="mb-0.5 text-on-surface-low text-[0.6rem] uppercase tracking-wide">{label}</div>
      {typeof children === 'string'
        ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-2 py-1.5 font-mono text-on-surface-var text-[0.7rem] leading-relaxed">{children}</pre>
        : <div className="max-h-96 overflow-auto rounded-md bg-surface-low px-2 py-1.5">{children}</div>}
    </div>
  )
}

/** Schema-driven input: render a structured args object as labeled fields.
 *  String/number/bool → inline value; object/array → compact JSON. Long strings
 *  (code/content) get a scrollable mono block. This is the default when a tool
 *  ships no native override but we DO have the structured input. */
export function KeyValueFields({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj)
  if (entries.length === 0) return <RawBlock label="Input">(no arguments)</RawBlock>
  return (
    <div className="mb-1.5">
      <div className="mb-1 text-on-surface-low text-[0.6rem] uppercase tracking-wide">Input</div>
      <div className="flex flex-col gap-1.5 rounded-md bg-surface-low px-2.5 py-2">
        {entries.map(([k, v]) => (
          <div key={k} className="flex flex-col gap-0.5">
            <span className="font-mono text-on-surface-low text-[0.65rem] uppercase tracking-wide">{k}</span>
            <FieldValue v={v} />
          </div>
        ))}
      </div>
    </div>
  )
}

function FieldValue({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className="text-on-surface-low italic text-[0.78rem]">—</span>
  if (typeof v === 'boolean') return <span className="text-[0.78rem]" style={{ color: v ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>{String(v)}</span>
  if (typeof v === 'number') return <span className="text-primary-emphasis tabular-nums text-[0.78rem]">{v}</span>
  if (typeof v === 'string') {
    // multi-line / long → scrollable mono; short → inline
    if (v.includes('\n') || v.length > 120) {
      return <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-surface px-2 py-1 font-mono text-on-surface-var text-[0.7rem]">{v}</pre>
    }
    return <span className="text-on-surface text-[0.8125rem] break-words">{v}</span>
  }
  // object / array → compact JSON
  return <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-surface px-2 py-1 font-mono text-on-surface-var text-[0.7rem]">{safeJson(v)}</pre>
}

function safeJson(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

/** Output rendered by content_type. Falls back to ToolOutput's sniff if the type
 *  has no dedicated renderer. */
export function ContentTypeOutput({ seg }: { seg: ToolSegment }) {
  const text = seg.output ?? ''
  const ct = seg.contentType
  if (ct === 'diff') {
    // Markdown renders a ```diff fence as its DiffBlock (+/- coloring).
    return <RawBlock label="Diff"><Markdown>{`\`\`\`diff\n${text}\n\`\`\``}</Markdown></RawBlock>
  }
  if (ct === 'markdown') {
    return <RawBlock label="Result"><div className="text-[0.8125rem]"><Markdown>{text}</Markdown></div></RawBlock>
  }
  if (ct === 'json') {
    return <RawBlock label="Result"><ToolOutput text={text} /></RawBlock>
  }
  if (ct === 'log' || ct === 'test') {
    return <RawBlock label="Result"><LogView text={text} /></RawBlock>
  }
  if (ct === 'csv') {
    return <RawBlock label="Result"><CsvTable text={text} /></RawBlock>
  }
  // unknown declared type → sniff
  return <RawBlock label="Result"><ToolOutput text={text} /></RawBlock>
}

/** Monospace log with error/warn/fail lines highlighted (the signal in a log). */
const _ERR_RE = /(error|warn|fail|exception|traceback|fatal|denied|assertionerror|✗|❌|\bE\s)/i
function LogView({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-[0.7rem] leading-relaxed">
      {lines.map((ln, i) => (
        <div key={i} className={_ERR_RE.test(ln) ? 'text-danger' : 'text-on-surface-var'}>{ln || ' '}</div>
      ))}
    </pre>
  )
}

/** Minimal CSV table (header + rows). Fails soft to mono on irregular shape. */
function CsvTable({ text }: { text: string }) {
  const rows = text.trim().split('\n').filter(Boolean).map((r) => r.split(','))
  if (rows.length < 2) return <pre className="font-mono text-[0.7rem] whitespace-pre-wrap">{text}</pre>
  const [head, ...body] = rows
  return (
    <div className="overflow-x-auto rounded-md border border-outline-variant/30">
      <table className="w-full border-collapse text-[0.72rem]">
        <thead><tr>{head.map((c, i) => <th key={i} className="border-b border-outline-variant/40 bg-surface-high px-2 py-1 text-left font-mono text-on-surface-var">{c}</th>)}</tr></thead>
        <tbody>{body.slice(0, 200).map((r, i) => (
          <tr key={i} className="hover:bg-surface-high/40">{r.map((c, j) => <td key={j} className="border-b border-outline-variant/20 px-2 py-1 align-top">{c}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  )
}
