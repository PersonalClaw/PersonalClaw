import { useEffect, useRef, useState } from 'react'
import { CornerDownLeft, FileText, Folder } from 'lucide-react'
import { api, type FsEntry } from '../../../lib/api'
import { useSyncedDraft } from '../../../ui/forms'
import { resolveTypedPath } from '../filesData'

/** Type/paste a path — a folder's or a file's — with autocomplete (file-complete). Enter or a
 *  picked suggestion goes there (`onNavigate`, which opens a file and lists a folder). A path that
 *  is not absolute is relative to the folder the box shows (`value`), for the suggestions and for
 *  the go alike (`resolveTypedPath`). */
export function PathBar({ value, onNavigate }: { value: string; onNavigate: (path: string) => void }) {
  // Follows the folder shown when it moves, never on mount (`useSyncedDraft`).
  const [draft, setDraft] = useSyncedDraft(value)
  const [open, setOpen] = useState(false)
  const [suggestions, setSuggestions] = useState<FsEntry[]>([])
  const [activeIdx, setActiveIdx] = useState(-1)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || !draft) { setSuggestions([]); return }
    const t = setTimeout(() => {
      api.fileComplete(resolveTypedPath(draft, value)).then((r) => { setSuggestions(r.suggestions); setActiveIdx(-1) }).catch(() => setSuggestions([]))
    }, 180)
    return () => clearTimeout(t)
  }, [draft, open, value])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const go = (typed: string) => {
    const path = resolveTypedPath(typed, value)
    onNavigate(path.replace(/\/+$/, '') || path)
    setOpen(false)
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); activeIdx >= 0 && suggestions[activeIdx] ? go(suggestions[activeIdx].path) : go(draft) }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => Math.min(i + 1, suggestions.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => Math.max(i - 1, -1)) }
    else if (e.key === 'Escape') setOpen(false)
  }

  return (
    <div ref={boxRef} className="relative">
      <div className="flex items-center gap-1.5 rounded-md bg-surface-high px-2.5 h-8 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
        <Folder size={13} className="shrink-0 text-on-surface-low" />
        <input value={draft} onChange={(e) => { setDraft(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onKeyDown={onKey}
          name="workspace-path" aria-label="Go to path"
          placeholder="Go to path…" className="min-w-0 flex-1 bg-transparent font-mono text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none" />
        <CornerDownLeft size={12} className="shrink-0 text-on-surface-low opacity-50" />
      </div>
      {open && suggestions.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-outline/40 bg-surface-container py-1 shadow-lg">
          {suggestions.map((s, i) => (
            <button key={s.path} onMouseEnter={() => setActiveIdx(i)} onClick={() => go(s.path)} type="button"
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[0.75rem]"
              style={{ background: i === activeIdx ? 'var(--color-surface-high)' : undefined }}>
              {s.is_dir
                ? <Folder size={12} className="shrink-0 text-primary" />
                : <FileText size={12} className="shrink-0 text-on-surface-low" />}
              <span className="truncate font-mono text-on-surface">{s.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
