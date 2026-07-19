import { memo, useEffect, useMemo, useState } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { api } from '../../../lib/api'
import { extOf } from '../fileMeta'

/* ── Image ── */
export const ImagePreview = memo(function ImagePreview({ path, src }: { path?: string; src?: string }) {
  // Two sources: a workspace FILE (by path → /api/files raw) or a direct URL /
  // data-URI (a kind:image artifact, whose content is the /api/artifacts/<slug>/raw
  // ref). A graceful fallback when the fetch fails (deleted/moved/permission/decode)
  // — matches PdfPreview, which degrades to a link. Without it a failed load showed
  // the browser's broken-image glyph with no recourse.
  const [failed, setFailed] = useState(false)
  const url = src || (path ? api.fileRawUrl(path, true) : '')
  // Reset the error state when the source changes — else a reused instance (React
  // reconciliation across opens) keeps a prior image's failure + never shows the new.
  useEffect(() => { setFailed(false) }, [url])
  const label = (path || src || '').split('/').pop() || 'image'
  return (
    <div className="flex h-full items-center justify-center overflow-auto p-l">
      {failed || !url ? (
        <div className="flex flex-col items-center gap-3 text-on-surface-low">
          <span className="text-[0.8125rem]">Couldn’t load this image — it may have been moved or deleted.</span>
          {url && <a href={url} target="_blank" rel="noreferrer" className="rounded-md px-3 py-1.5 text-[0.8125rem]" style={{ background: 'var(--color-surface-high)' }}>Open directly</a>}
        </div>
      ) : (
        <img src={url} alt={label} onError={() => setFailed(true)}
          className="max-h-full max-w-full rounded-md object-contain" draggable={false} />
      )}
    </div>
  )
})

/* ── PDF ── */
export const PdfPreview = memo(function PdfPreview({ path }: { path: string }) {
  const url = api.fileRawUrl(path, true)
  return (
    <object data={url} type="application/pdf" className="h-full w-full">
      <div className="flex h-full flex-col items-center justify-center gap-3 text-on-surface-low">
        <span className="text-[0.8125rem]">PDF preview not supported in this browser</span>
        <a href={url} target="_blank" rel="noreferrer" className="rounded-md px-3 py-1.5 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>Open in browser</a>
      </div>
    </object>
  )
})

/* ── HTML (sandboxed, no scripts) ── */
export const HtmlPreview = memo(function HtmlPreview({ content }: { content: string }) {
  // Render the file as a REAL document: scripts run (so animations, canvas, and
  // interactivity work), served from a blob (null-origin) iframe with
  // sandbox="allow-scripts" — the same isolation the artifact/widget frames use,
  // so the page can't reach the app's origin, cookies, or storage. (A bare
  // sandbox="" blocked all scripts, leaving HTML static.)
  const blobUrl = useMemo(() => URL.createObjectURL(new Blob([content], { type: 'text/html;charset=utf-8' })), [content])
  useEffect(() => () => URL.revokeObjectURL(blobUrl), [blobUrl])
  return <iframe src={blobUrl} sandbox="allow-scripts" title="HTML preview" className="h-full w-full border-none bg-white" />
})

/* ── CSV / TSV table ── */
export const CsvPreview = memo(function CsvPreview({ content, name }: { content: string; name: string }) {
  const delimiter = extOf(name) === 'tsv' ? '\t' : ','
  const rows = useMemo(() => parseDelimited(content, delimiter), [content, delimiter])
  if (rows.length === 0) return <div className="p-l text-on-surface-low text-[0.8125rem]">Empty file</div>
  const [header, ...body] = rows
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-collapse font-mono text-[0.8125rem]">
        <thead className="sticky top-0" style={{ background: 'var(--color-surface-high)' }}>
          <tr>{header.map((h, i) => (
            <th key={i} className="whitespace-nowrap border-b border-outline/40 px-3 py-2 text-left text-on-surface-low text-[0.7rem] uppercase tracking-wide">{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {body.slice(0, 500).map((row, ri) => (
            <tr key={ri} className="hover:bg-surface-high">
              {row.map((cell, ci) => <td key={ci} className="whitespace-nowrap border-b border-outline/25 px-3 py-1.5 text-on-surface">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {body.length > 500 && <div className="border-t border-outline/25 py-2.5 text-center text-on-surface-low text-[0.7rem]">Showing 500 of {body.length} rows</div>}
    </div>
  )
})

function parseDelimited(content: string, delimiter: string): string[][] {
  // Single-pass scan over the WHOLE content (not line-by-line): a quoted field may
  // contain newlines (a valid CSV cell like "line1\nline2", common in exported data).
  // Splitting on \n first cut such a cell across rows + reset quote state each line,
  // corrupting every row after it. Here a newline INSIDE quotes is literal cell text;
  // only an UNQUOTED newline ends a row. Handles "" escapes + CRLF.
  const rows: string[][] = []
  let cells: string[] = []
  let cur = ''
  let inQuote = false
  const endCell = () => { cells.push(cur); cur = '' }
  const endRow = () => { endCell(); rows.push(cells); cells = [] }
  for (let i = 0; i < content.length; i++) {
    const ch = content[i]
    if (inQuote) {
      if (ch === '"') {
        if (content[i + 1] === '"') { cur += '"'; i++ }  // escaped quote
        else inQuote = false
      } else cur += ch
      continue
    }
    if (ch === '"') { inQuote = true; continue }
    if (ch === delimiter) { endCell(); continue }
    if (ch === '\n') { endRow(); continue }
    if (ch === '\r') continue  // CRLF → ignore the CR; the \n ends the row
    cur += ch
  }
  // Flush the trailing row unless the file ended on a clean newline (no dangling cell).
  if (cur !== '' || cells.length > 0) endRow()
  // Drop fully-blank rows (e.g. a trailing newline or blank separator lines) — but keep
  // rows that have real content, including a legitimately-empty quoted first cell.
  return rows.filter((r) => r.some((c) => c !== '') || r.length > 1)
}

/* ── JSON collapsible tree ── */
export const JsonPreview = memo(function JsonPreview({ content, name }: { content: string; name?: string }) {
  const isJsonl = extOf(name || '') === 'jsonl'
  const parsed = useMemo(() => {
    // JSONL is ONE json value per line, not a single document — JSON.parse of the whole
    // file always throws, so it used to show a bogus "Invalid JSON" for a format the app
    // explicitly routes here (fileMeta maps jsonl→json). Parse it line-by-line into an
    // array of records; a blank line is skipped, a bad line is kept as a raw string so a
    // partial file still previews.
    if (isJsonl) {
      const recs: unknown[] = []
      for (const line of content.split('\n')) {
        const t = line.trim()
        if (!t) continue
        try { recs.push(JSON.parse(t)) } catch { recs.push(t) }
      }
      return { ok: true as const, value: recs }
    }
    try { return { ok: true as const, value: JSON.parse(content) } } catch { return { ok: false as const, value: null } }
  }, [content, isJsonl])
  if (!parsed.ok) {
    // Don't dead-end on invalid JSON (a worker mid-write, a .jsonc/.json5 with comments
    // or trailing commas that strict JSON.parse rejects): show the reason AND the raw
    // text so the user can still read + fix it, instead of a bare "Invalid JSON".
    return (
      <div className="flex h-full flex-col">
        <div className="shrink-0 px-3 py-2 font-mono text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-error) 10%, transparent)', color: 'var(--color-error)' }}>
          Not valid JSON — showing the raw text. (Comments / trailing commas aren’t valid JSON; edit in the editor.)
        </div>
        <pre className="min-h-0 flex-1 overflow-auto p-m font-mono text-[0.8125rem] text-on-surface-var whitespace-pre-wrap break-words">{content}</pre>
      </div>
    )
  }
  return <div className="h-full overflow-auto p-m font-mono text-[0.8125rem]"><JsonNode value={parsed.value} depth={0} kName={null} /></div>
})

function JsonNode({ value, depth, kName }: { value: unknown; depth: number; kName: string | null }) {
  const [open, setOpen] = useState(depth < 2)
  const label = kName !== null ? <><span style={{ color: 'var(--color-primary)' }}>"{kName}"</span><span className="text-on-surface-low">: </span></> : null

  // JSON value colors follow the app's hljs syntax palette (tokens.css): strings
  // = ok/green, literals/numbers = primary-emphasis, so a JSON preview reads like
  // a code block rather than inventing its own colors.
  if (value === null) return <div>{label}<span className="text-on-surface-low">null</span></div>
  if (typeof value === 'boolean') return <div>{label}<span style={{ color: 'var(--color-primary-emphasis)' }}>{String(value)}</span></div>
  if (typeof value === 'number') return <div>{label}<span style={{ color: 'var(--color-primary)' }}>{value}</span></div>
  if (typeof value === 'string') return <div>{label}<span style={{ color: 'var(--color-ok)' }}>"{value.length > 200 ? value.slice(0, 200) + '…' : value}"</span></div>

  const isArr = Array.isArray(value)
  const entries = isArr ? (value as unknown[]).map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>)
  const [lb, rb] = isArr ? ['[', ']'] : ['{', '}']
  return (
    <div>
      <button onClick={() => setOpen(!open)} type="button" className="inline-flex items-center gap-0.5 text-on-surface-low hover:text-on-surface">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{label}{lb}{!open && <span className="text-on-surface-low"> {entries.length} {isArr ? 'items' : 'keys'} {rb}</span>}
      </button>
      {open && (
        <div style={{ paddingLeft: 16 }}>
          {entries.slice(0, 200).map(([k, v]) => <JsonNode key={k} value={v} depth={depth + 1} kName={isArr ? null : k} />)}
          {entries.length > 200 && <div className="text-on-surface-low">… {entries.length - 200} more</div>}
          <div className="text-on-surface-low">{rb}</div>
        </div>
      )}
    </div>
  )
}
