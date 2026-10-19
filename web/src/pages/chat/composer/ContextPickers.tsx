import { useEffect, useState } from 'react'
import { BookText, Boxes, Check, Loader2, X } from 'lucide-react'
import { fvs } from '../../../design/fontWeight'
import { api } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { Button } from '../../../ui/Button'
import { FieldError } from '../../../ui/forms'
import { Meter } from '../../../ui/Meter'
import { Modal } from '../../../ui/Modal'
import { MenuRow } from '../../../ui/Popover'
import { SearchField } from '../../../ui/SearchField'
import { ResultAnnouncement } from '../../../ui/ListControls'

/** "Add knowledge to prompt" — a search picker over the library that shows each
 *  result's token cost against a budget, so the user can attach relevant context
 *  without blowing the window. Selecting toggles the item into mentionedKnowledge
 *  (the same pipeline as an @-mention); the backend inlines it at send. */
/** Pick artifacts to ground the next turn in. Unlike the knowledge picker there is no
 *  token budget meter: an artifact is one whole document the user names deliberately,
 *  not a set of search fragments competing for a context allowance. The list is the
 *  library itself, filtered client-side — an artifact library is small enough that a
 *  server round-trip per keystroke would be the slower option. */
export function ArtifactContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { slug: string; name: string }[]
  onPick: (a: { slug: string; name: string }) => void
  onRemove: (slug: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  // The swallow made a failed read indistinguishable from an empty library, and this picker's
  // empty state TEACHES ("Ask in chat for a widget…") — so a 500 told a user with artifacts to go
  // make their first one. Same shape as #1162's chat history, one surface down.
  const { data, loading, error: artifactsError } = useQuery('artifacts:chat-picker', () => api.artifacts())
  const all = data ?? []
  const attachedSlugs = new Set(attached.map((a) => a.slug))
  const n = q.trim().toLowerCase()
  const shown = (n ? all.filter((a) => `${a.name} ${a.slug} ${a.kind}`.toLowerCase().includes(n)) : all).slice(0, 40)
  return (
    <Modal title="Reference an artifact" icon={<Boxes size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <SearchField value={q} onChange={setQ} autoFocus placeholder="Search your artifacts…"
          ariaLabel="Search your artifacts"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {/* `shown` is capped at 40, and the count follows the CAP rather than the match total —
            the announcement describes what is on screen, which is the only number a user can act
            on. (That the cap itself is silent is a separate, visible-copy question.) */}
        <ResultAnnouncement count={shown.length} noun="artifacts" active={!!n} />
        {attached.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {attached.map((a) => (
              <Button key={a.slug} variant="ghost" size="xs" onClick={() => onRemove(a.slug)}
                title="Remove from this prompt">
                <Boxes size={11} /> {a.name} <X size={11} />
              </Button>
            ))}
          </div>
        )}
        {data === undefined && artifactsError ? (
          // The error branch comes FIRST: `data === undefined` is true for loading, failure AND an
          // empty library, so a failure test placed after them is unreachable. `FieldError`
          // announces (role=alert), which a teaching empty state deliberately does not.
          <FieldError>Couldn't load your artifacts — {(artifactsError as Error)?.message || 'the server did not respond'}</FieldError>
        ) : all.length === 0 && !loading ? (
          <p className="text-on-surface-low text-[0.8125rem]">
            No artifacts yet. Ask in chat for a widget or a document and it lands here.
          </p>
        ) : (
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
            {shown.map((a) => {
              const on = attachedSlugs.has(a.slug)
              return (
                <MenuRow key={a.slug} icon={<Boxes size={14} />} label={a.name}
                  hint={`${a.kind} · v${a.version} · ${a.slug}`} selected={on}
                  onClick={() => (on ? onRemove(a.slug) : onPick({ slug: a.slug, name: a.name }))} />
              )
            })}
            {n && shown.length === 0 && (
              <p className="px-2 py-2 text-on-surface-low text-[0.8125rem]">No artifact matches that.</p>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

export function KnowledgeContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { id: string; name: string }[]
  onPick: (item: { id: string; name: string }) => void
  onRemove: (id: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<import('../../../lib/api').KnowledgeContextResult | null>(null)
  const [loading, setLoading] = useState(false)
  const MAX = 4000
  const attachedIds = new Set(attached.map((a) => a.id))
  // A query's synchronous consequences happen in the handler that changes it, batched into the
  // keystroke's own render; the effect below only runs the debounced search. `setLoading(true)`
  // used to sit in that effect, where it scheduled a render from inside every keystroke's commit —
  // typed fast, ~50 keys threw React's #185 (the mechanism: `ui/composer/MarkdownInput`). Clearing
  // here also clears `loading`, which the effect never did: emptying the box while a search was
  // pending left the spinner turning where "Type to search" belongs.
  const search = (v: string) => {
    setQ(v)
    if (v.trim()) setLoading(true)
    else { setRes(null); setLoading(false) }
  }
  useEffect(() => {
    const query = q.trim()
    if (!query) return
    let alive = true
    const t = window.setTimeout(() => {
      api.knowledgeSearchForContext(query, MAX).then((r) => { if (alive) setRes(r) }).catch(() => { if (alive) setRes(null) }).finally(() => { if (alive) setLoading(false) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [q])
  // Running budget = sum of tokens of currently-attached results the search surfaced.
  const attachedTokens = (res?.results ?? []).filter((r) => attachedIds.has(r.id)).reduce((n, r) => n + r.tokens, 0)
  const pct = Math.min(100, Math.round((attachedTokens / MAX) * 100))
  return (
    <Modal title="Add knowledge to prompt" icon={<BookText size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <SearchField value={q} onChange={search} autoFocus placeholder="Search your knowledge library…"
          ariaLabel="Search your knowledge library"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {/* Remote, and debounced 250ms: `active` waits for `loading` to clear so the count is the
            one for the query the user has actually finished typing, and for `res` to exist so an
            unrun search is not reported as "no matches". */}
        <ResultAnnouncement count={res?.results.length ?? 0} noun="knowledge items"
          active={!!q.trim() && !loading && res !== null} />
        {attached.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[0.75rem] text-on-surface-low">
              <span>{attached.length} attached{attachedTokens ? ` · ~${attachedTokens} tokens` : ''}</span>
              {attachedTokens > 0 && <span className="tabular-nums">{pct}% of {MAX}</span>}
            </div>
            {attachedTokens > 0 && (
              <Meter size="thin" label="Prompt budget used by attached knowledge" pct={pct}
                tone={pct > 90 ? 'var(--color-warn)' : 'var(--color-primary)'} />
            )}
          </div>
        )}
        {/* 🔴 Attached-or-not was announced by COLOUR alone — a tinted row, a coral outline and a
            check glyph, none of which reaches the accessibility tree. `aria-pressed` is the state;
            the group carries the dimension so a row announces what it is being attached TO. No tab
            stop is needed on the scroller because every row in it is a button. */}
        <div role="group" aria-label="Knowledge to attach" className="max-h-[46vh] overflow-y-auto flex flex-col gap-1.5">
          {loading && !res ? <div className="grid place-items-center py-6 text-on-surface-low"><Loader2 size={16} className="animate-spin" /></div>
            : !q.trim() ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">Type to search notes, gists, bookmarks, docs…</p>
            : (res?.results.length ?? 0) === 0 ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">No matches for “{q}”.</p>
            : res!.results.map((r) => {
              const on = attachedIds.has(r.id)
              // Raw: a full-row selectable result card with its own check mark, which no primitive draws.
              return (
                <button key={r.id} type="button" aria-pressed={on}
                  onClick={() => on ? onRemove(r.id) : onPick({ id: r.id, name: r.title })}
                  className="flex items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors"
                  style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)', outline: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' } : { background: 'var(--color-surface-container)' }}>
                  <span className="mt-0.5 shrink-0 grid size-4 place-items-center rounded border" style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>{on && <Check size={11} />}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{r.title}</span>
                      <span className="ml-auto shrink-0 tabular-nums text-on-surface-low text-[0.75rem]">~{r.tokens} tok</span>
                    </div>
                    {r.summary && <div className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem]">{r.summary}</div>}
                  </div>
                </button>
              )
            })}
        </div>
        <div className="flex justify-end"><Button size="sm" onClick={onClose}>Done</Button></div>
      </div>
    </Modal>
  )
}
