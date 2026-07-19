import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Markdown } from '../../ui/Markdown'

/** A second, expandable status bar (item 14): COLLAPSED shows the first line of the
 *  loop's prompt/task; EXPANDED reveals the full prompt as markdown. Shared by the
 *  Code + Design cockpits (the Loop cockpit has its own inline variant with sub-goals).
 *  Renders nothing when there is no prompt. */
export function CockpitPromptBar({ prompt }: { prompt: string }) {
  const [open, setOpen] = useState(false)
  const text = (prompt || '').trim()
  if (!text) return null
  const firstLine = text.split('\n').map((l) => l.trim()).find(Boolean) || '—'
  return (
    <div className="shrink-0 border-b border-outline-variant/30 px-2xl py-1.5">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-s text-left min-w-0">
        <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="shrink-0 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Prompt</span>
        {!open && <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.8125rem]">{firstLine}</span>}
      </button>
      {open && (
        <div className="mt-2 max-h-[40vh] overflow-y-auto text-on-surface text-[0.9375rem]">
          <Markdown>{text}</Markdown>
        </div>
      )}
    </div>
  )
}
