import { useId } from 'react'
import { X } from 'lucide-react'
import { invalidateKeys } from '../../lib/data'
import { IconButton } from '../../ui/IconButton'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { TagManager } from '../knowledge/TagManager'

/** Manage tags, in the pane (`?manage=tags`): the tag tree, to rename, nest, merge and
 *  delete, each tag's count opening the list filtered by it, and the reports that read a tag named
 *  before a change to it. */
export function TagsPane({ onClose, onFilter }: { onClose: () => void; onFilter: (tag: string) => void }) {
  const headingId = useId()
  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex items-start gap-s">
        <h2 id={headingId} data-type="title-l" className="min-w-0 flex-1 text-on-surface">Manage tags</h2>
        <IconButton icon={X} label="Close Manage tags" onClick={onClose} className={TOUCH_TARGET} />
      </header>
      <TagManager onFilter={onFilter} onChanged={() => { invalidateKeys('knowledge:library', true); invalidateKeys('knowledge:reports') }} />
    </section>
  )
}
