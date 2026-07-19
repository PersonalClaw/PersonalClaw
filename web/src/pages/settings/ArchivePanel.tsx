import { useEffect, useState } from 'react'
import { Archive, Search, FileText, Loader2 } from 'lucide-react'
import { api, type SessionArchive } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader } from './settingsUI'
import { ListSkeleton } from '../../ui/ListScaffold'

/** Archive — browse archived chat-session transcripts (read-only). Each row is an
 *  archived `.jsonl`; click to inspect its messages. Backed by
 *  /api/session/archive (list) + /api/session/archive/{name} (read). */
export function ArchivePanel() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  // Archives change slowly — persist for instant paint on revisit + reload.
  const { data: archives } = useCachedData(
    'settings:archives', () => api.sessionArchives().catch(() => [] as SessionArchive[]), { persist: true },
  )
  if (!archives) return <ListSkeleton rows={6} />

  const needle = q.trim().toLowerCase()
  const shown = needle ? archives.filter((a) => `${a.key} ${a.name}`.toLowerCase().includes(needle)) : archives

  return (
    <div>
      <PanelHeader title="Archive" hint="Archived chat sessions. Browse and inspect past transcripts — read-only." />

      {archives.length > 0 && (
        <div className="relative mb-3">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by session key" aria-label="Filter archived sessions"
            className="h-9 w-full rounded-md bg-surface-high pl-8 pr-2 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>
      )}

      {shown.length === 0 ? (
        <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-8 text-center">
          <Archive size={22} className="mx-auto mb-2 text-on-surface-low" />
          <p className="text-on-surface-low text-[0.82rem]">{q ? 'No archives match.' : 'No archived sessions yet. Closed sessions are archived here.'}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {shown.map((a) => <ArchiveRow key={a.name} a={a} open={open === a.name} onToggle={() => setOpen(open === a.name ? null : a.name)} />)}
        </div>
      )}
    </div>
  )
}

function ArchiveRow({ a, open, onToggle }: { a: SessionArchive; open: boolean; onToggle: () => void }) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!open || content !== null) return
    setLoading(true)
    api.sessionArchiveRead(a.name)
      .then((d) => setContent(d))
      .catch((e) => setContent(`(failed to read archive: ${(e as Error)?.message || e})`))
      .finally(() => setLoading(false))
  }, [open, a.name, content])

  return (
    <div className="rounded-lg bg-surface-container px-4 py-2.5">
      <button type="button" onClick={onToggle} className="flex w-full items-center gap-3 text-left">
        <FileText size={16} className="shrink-0 text-on-surface-low" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-on-surface text-[0.82rem]">{a.key}</div>
          <div className="text-on-surface-low text-[0.7rem]">{fmtMtime(a.mtime)} · {fmtSize(a.size)}</div>
        </div>
      </button>
      {open && (
        <div className="mt-2 border-t border-outline-variant/30 pt-2">
          {loading ? <div className="py-2 text-on-surface-low text-[0.78rem]"><Loader2 size={12} className="inline animate-spin" /> Loading…</div>
            : <pre className="max-h-80 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface text-[0.72rem] whitespace-pre-wrap">{content}</pre>}
        </div>
      )}
    </div>
  )
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
// mtime is epoch seconds; render Y-M-D H:M in the user's local timezone
// (toISOString showed UTC — hours off from the archive's actual local write time).
function fmtMtime(epoch: number): string {
  try {
    const d = new Date(epoch * 1000)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  } catch { return '' }
}
