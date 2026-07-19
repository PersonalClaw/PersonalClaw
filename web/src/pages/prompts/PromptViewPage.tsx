import { useEffect, useState } from 'react'
import { ArrowLeft, FileText, Puzzle, Loader2 } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { api, type PromptItem, type PromptSnippet } from '../../lib/api'
import { sourceTone } from './promptMeta'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'
import { useEditFlag, type RouteProps } from '../../app/useQueryState'

/** Dedicated full-page view/edit for a single prompt or snippet — opened from the
 *  list's side panel ("Open full page"). Same TopBar-back + centered-body shell as
 *  the create page, so the two feel consistent; the body reuses PromptDetail /
 *  SnippetDetail (which already handle view ↔ in-place edit). Edit mode is the same
 *  ?edit=1 URL flag the side-panel uses, so it survives refresh + Back/forward. */
export function PromptViewPage({ kind, name, onBack, navigate, query, setQuery }: {
  kind: 'system' | 'user' | 'snippets'
  name: string
  onBack: () => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const isSnippet = kind === 'snippets'
  const [prompt, setPrompt] = useState<PromptItem | null | 'missing'>(null)
  const [snippet, setSnippet] = useState<PromptSnippet | null | 'missing'>(null)
  // Bumped on save to refetch the record. The detail components skip their own
  // hydration fetch when the prop already carries `content` (this page's fetch
  // does), so without a parent refetch the view would keep showing the PRE-save
  // record after Save — stale content until a full reload.
  const [saveTick, setSaveTick] = useState(0)

  useEffect(() => {
    let alive = true
    if (isSnippet) {
      api.snippet(name).then((s) => { if (alive) setSnippet(s) }).catch(() => { if (alive) setSnippet('missing') })
    } else {
      api.prompt(name).then((p) => { if (alive) setPrompt(p) }).catch(() => { if (alive) setPrompt('missing') })
    }
    return () => { alive = false }
  }, [isSnippet, name, saveTick])

  const loaded = isSnippet ? snippet : prompt
  const tone = sourceTone(typeof loaded === 'object' && loaded ? loaded.source : undefined)

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s">
        <IconButton icon={ArrowLeft} label="Back to prompts" size={40} onClick={onBack} />
        {isSnippet ? <Puzzle size={18} style={{ color: tone }} /> : <FileText size={18} style={{ color: tone }} />}
        <span data-type="title-l" className="text-on-surface truncate">{name}</span>
      </div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {loaded === null ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
          ) : loaded === 'missing' ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center text-on-surface-low">
              {isSnippet ? <Puzzle size={26} className="opacity-40" /> : <FileText size={26} className="opacity-40" />}
              <p className="text-[0.9375rem] text-on-surface">This {isSnippet ? 'snippet' : 'prompt'} no longer exists</p>
              <p className="text-[0.8125rem]">It may have been deleted, or the link is stale.</p>
            </div>
          ) : isSnippet ? (
            <SnippetDetail snippet={loaded as PromptSnippet} editing={editing} onEditingChange={setEditing} onSaved={() => setSaveTick((t) => t + 1)} onDeleted={onBack} />
          ) : (
            <PromptDetail prompt={loaded as PromptItem} editing={editing} onEditingChange={setEditing} onSaved={() => setSaveTick((t) => t + 1)} onDeleted={onBack} onNavigate={navigate} />
          )}
        </div>
      </div>
    </div>
  )
}
