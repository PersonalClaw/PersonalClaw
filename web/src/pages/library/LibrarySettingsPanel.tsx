import type { ReactNode } from 'react'
import { api, type ReindexJob } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { KINDS } from './libraryMeta'
import { n } from './libraryWords'

// ── Library settings, the Power user view's last tab ──────────────────────────────────────────────
//
// What is in the library, how it is found, and anything that needs a look: one row each of a title, a
// sentence and one action. A row that would read 0 is not drawn, and an empty library draws its empty
// state instead of rows.

interface Row { title: string; says: ReactNode; action?: { label: string; run: () => void; off?: string } }

/** Each kind's words in a count, one and many: "1 document", "6 notes". */
export const KIND_WORDS: Record<string, readonly [string, string]> = {
  note: ['note', 'notes'], document: ['document', 'documents'], link: ['link', 'links'],
  media: ['image or media file', 'images and media files'], page: ['page or app', 'pages and apps'], report: ['report', 'reports'],
}

/** A kind's count in words; a kind with no words of its own is an item, never its key or a blank. */
export function kindCount(kind: string, count: number): string {
  const [one, many] = KIND_WORDS[kind] ?? ['item', 'items']
  return `${n(count)} ${count === 1 ? one : many}`
}

/** The page's one embedding re-index (`useEmbeddingReindex`), shared with its "…" row. */
export interface Reindex { job: ReindexJob | null; start: () => void; starting: boolean }

export function LibrarySettingsPanel({ navigate, openTab, filterItems, processAll, reindex }: {
  navigate: (path: string) => void
  openTab: (tab: 'graph' | 'conflicts') => void
  /** Open Items narrowed to these states. */
  filterItems: (state: string) => void
  processAll: () => void
  reindex: Reindex
}) {
  const { data: stats, loading, error, refresh } = useQuery('knowledge:stats', () => api.knowledgeStats())
  const library = useQuery('knowledge:library:{}', () => api.library({})).data
  const facets = library?.facets
  const conflictsRead = useQuery('knowledge:conflicts:500', () => api.knowledgeConflicts(500))
  const conflicts = conflictsRead.data
  const { job, start, starting } = reindex
  // A read that failed says so: it never reads as "Off" or as an empty library.
  if (!stats) {
    return (
      <section aria-labelledby="lib-settings-title" className="flex flex-col gap-m p-l">
        <h2 id="lib-settings-title" data-type="title-l" className="text-on-surface">Library settings</h2>
        {error && !loading ? <LoadError what="library's settings" error={error} onRetry={refresh} /> : <ListSkeleton rows={4} what="library's settings" />}
      </section>
    )
  }
  // What the library holds is its own count: the knowledge store's notes, files and links, and the pages
  // the agent made, which the store holds only while they are indexed. Until it reads, the store's.
  const total = library?.total ?? stats.items
  if (!total) {
    return (
      <section aria-labelledby="lib-settings-title" className="flex flex-col gap-m p-l">
        <h2 id="lib-settings-title" data-type="title-l" className="text-on-surface">Library settings</h2>
        <p data-type="body-m" className="text-on-surface">Nothing in your library yet.</p>
        <div><Button variant="secondary" disabled disabledReason="There is nothing to read yet." className={TOUCH_TARGET}>Process everything again…</Button></div>
      </section>
    )
  }
  // Every kind the library counts, the Filter's in its order and any other after them, most first: the
  // parts add up to the library's own total, which counts a findings log as the report it is.
  const order = (kind: string) => { const at = KINDS.findIndex((k) => k.key === kind); return at < 0 ? KINDS.length : at }
  const byKind = Object.entries(facets?.kind ?? {}).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1] || order(a[0]) - order(b[0]))
  const e = stats.embeddings
  const ready = e.embedded_items ?? 0
  const stale = e.stale_items ?? Math.max(0, stats.items - ready)
  const ent = stats.enrichment?.entities
  const waiting = ent ? ent.not_run + ent.running : 0
  const failed = ent?.failed ?? 0
  const undecided = new Set((conflicts?.conflicts ?? []).filter((c) => !c.choice).map((c) => c.id)).size
  const running = job?.status === 'running'
  const rows: Row[] = [
    { title: 'Items', says: `${n(total)} in your library${byKind.length ? `: ${byKind.map(([k, c]) => kindCount(k, c)).join(', ')}` : ''}` },
    e.enabled
      ? {
        title: 'Search by meaning',
        says: running ? `Re-indexing · ${n(job!.done)} of ${n(job!.total)}`
          : job?.status === 'error' ? (job.id ? `The re-index stopped: ${job.error}` : `Re-index not started: ${job.error}`)
          : `On · ${n(ready)} of ${n(stats.items)} ready.${stale ? ` ${n(stale)} are waiting.` : ''}`,
        action: { label: 'Re-index', run: start, off: starting ? 'Starting…' : running ? "It's re-indexing now." : undefined },
      }
      : { title: 'Search by meaning', says: 'Off. Search matches words only.', action: { label: 'Set up', run: () => navigate('settings/models') } },
    ...(stats.entities ? [{
      title: 'Found in your items',
      says: `${n(stats.entities)} people, places and ideas${stats.relations ? `, with ${n(stats.relations)} ${stats.relations === 1 ? 'link' : 'links'} between them` : ''}`,
      action: { label: 'Open graph', run: () => openTab('graph') },
    }] : []),
    ...(waiting || failed ? [{
      title: 'Being read',
      says: [waiting ? `${n(waiting)} waiting` : '', failed ? `${n(failed)} couldn't be read` : ''].filter(Boolean).join(' · '),
      action: { label: 'Show them', run: () => filterItems('being_read,unreadable') },
    }] : []),
    ...(undecided ? [{
      title: 'Conflicts', says: `${n(undecided)} ${undecided === 1 ? 'place where items disagree' : 'places where items disagree'}`,
      action: { label: 'Review', run: () => openTab('conflicts') },
    }] : conflictsRead.error && !conflicts ? [{
      // Unread, it says so rather than claim there are none; the Conflicts tab says why.
      title: 'Conflicts', says: "Couldn't check for conflicts.", action: { label: 'Review', run: () => openTab('conflicts') },
    }] : []),
    {
      title: 'What search can see', says: 'Pages and documents the agent made are searched with your notes and files.',
      action: { label: 'Change', run: () => navigate('settings/automations?section=sources') },
    },
    {
      title: 'Everything again', says: 'Read every item again: tags, topics, the graph and search · Uses your model for a while. You can keep working.',
      action: { label: 'Process everything again…', run: processAll, off: stats.items ? undefined : 'There is nothing to read yet.' },
    },
  ]
  return (
    <section aria-labelledby="lib-settings-title" className="flex flex-col gap-m p-l">
      <header>
        <h2 id="lib-settings-title" data-type="title-l" className="text-on-surface">Library settings</h2>
        <p data-type="body-s" className="mt-xs text-on-surface-var">What is in your library, how it is found, and anything that needs a look.</p>
      </header>
      <dl className="flex flex-col divide-y divide-outline-variant/40">
        {rows.map((r) => (
          <div key={r.title} className="flex flex-wrap items-center gap-m py-m">
            <div className="min-w-0 flex-1">
              <dt data-type="label-l" className="text-on-surface">{r.title}</dt>
              <dd data-type="body-s" className="text-on-surface-var">{r.says}</dd>
            </div>
            {r.action && (
              <Button variant="secondary" size="sm" onClick={r.action.run} disabled={!!r.action.off} disabledReason={r.action.off} className={TOUCH_TARGET}>
                {r.action.label}
              </Button>
            )}
          </div>
        ))}
      </dl>
    </section>
  )
}
