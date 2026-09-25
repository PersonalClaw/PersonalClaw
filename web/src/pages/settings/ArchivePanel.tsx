import { useEffect, useState } from 'react'
import { ResultAnnouncement } from '../../ui/ListControls'
import { Archive, Search, FileText, Loader2 } from 'lucide-react'
import { api, type SessionArchive } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { PanelHeader } from './settingsUI'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { TextInput } from '../../ui/forms'

/** Archive — browse the message lines that EARLIER versions trimmed out of a chat
 *  (read-only). A row is one `archive/{key}__{stamp}.jsonl` batch of trimmed lines, NOT a
 *  closed or whole session. Nothing writes this directory any more — background compression
 *  and the 2 MB size rotation both used to, and neither shortens a chat now — and nothing
 *  prunes it, because for a chat trimmed before that a batch can be the only copy of its
 *  lines. A batch is removed only when its chat is deleted (`ConversationLog.delete_session`),
 *  so this panel must never imply batches expire or that new ones will appear. Backed by
 *  /api/session/archive (list) + /api/session/archive/{name} (read). */
export function ArchivePanel() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  // Archives change slowly — persist for instant paint on revisit + reload.
  const { data: archives, error, refresh } = useQuery(
    'settings:archives', () => api.sessionArchives(), { persist: true },
  )
  // The fetcher used to carry `.catch(() => [] as SessionArchive[])`, so a 500 resolved as an empty
  // list and this panel answered with its empty state ("nothing dropped yet") — and `{ persist: true }`
  // wrote that fiction to sessionStorage, where it survived a reload. The rejection has to REACH the
  // hook before `error` can be read at all.
  if (!archives && error) return <LoadError what="trimmed messages" error={error} onRetry={refresh} />
  if (!archives) return <ListSkeleton rows={6} what="trimmed messages" />

  const needle = q.trim().toLowerCase()
  const shown = needle ? archives.filter((a) => `${a.key} ${a.name}`.toLowerCase().includes(needle)) : archives

  return (
    <div>
      <PanelHeader title="Archive" hint="Earlier versions of PersonalClaw trimmed older messages out of long or idle chats. Those lines are kept here so you can still read them — read-only, one batch of lines per entry rather than a whole chat. Chats are no longer trimmed, and deleting a chat deletes its entries here too." />

      {archives.length > 0 && (
        <div className="mb-3">
          <TextInput value={q} onChange={setQ} placeholder="Filter by session key" ariaLabel="Filter trimmed messages by session key"
            size="md" surface="high" leadingIcon={<Search size={14} />} />
          {/* `shown` is the array the rows map over, and `needle` is what the empty state below
              already uses to tell "no archives yet" from "none match" — same two facts, announced. */}
          <ResultAnnouncement count={shown.length} noun="trimmed messages" active={!!needle} />
        </div>
      )}

      {shown.length === 0 ? (
        <div className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-8 text-center">
          <Archive size={22} className="mx-auto mb-2 text-on-surface-low" />
          <p data-type="body-s" className="text-on-surface-low">{q ? 'No trimmed messages match.' : 'Nothing here. Chats are no longer trimmed; this only lists lines an earlier version trimmed.'}</p>
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
      {/* `aria-expanded` on the row itself, matching `AuditPanel`'s event row — the same shape in the
          same area (a card whose button reveals detail beneath it). The toggle arriving as a PROP is
          why the cycle-127 disclosure census could not see this one. */}
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-3 text-left">
        <FileText size={16} className="shrink-0 text-on-surface-low" />
        <div className="min-w-0 flex-1">
          <div data-type="body-s" className="truncate font-mono text-on-surface">{a.key}</div>
          <div data-type="caption" className="text-on-surface-low">{fmtMtime(a.mtime)} · {fmtSize(a.size)}</div>
        </div>
      </button>
      {open && (
        <div className="mt-2 border-t border-outline-variant/30 pt-2">
          {loading ? <div data-type="caption" className="py-2 text-on-surface-low"><Loader2 size={12} className="inline animate-spin" /> Loading…</div>
            /* The transcript overflows its own cap (measured: 394px of content in a 320px box), so it is
               a scroll region — `tabIndex={0}` + `role="group"` + a short `aria-label` is this repo's
               canonical trio for one. Without the name Chrome computes it from the subtree, which here
               is the whole JSONL transcript announced as the region's name. Static, not the session key:
               a name must stay a name, and a key is unbounded. */
            : <pre tabIndex={0} role="group" aria-label="Trimmed messages from this session"
                data-type="caption" className="max-h-80 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface whitespace-pre-wrap">{content}</pre>}
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
