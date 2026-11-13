import type { MouseEvent } from 'react'
import { useIsMobile } from '../app/useIsMobile'
import { ProjectMark } from './ProjectMark'
import { TOUCH_TARGET } from './Toggle'

/** The project a piece of work or an item is in, as a 24 px chip: its mark, then its name, cut at
 *  14rem with the whole name in its tooltip. MetaChip's pill, with the mark as its lead.
 *
 *  Two forms. **Open** (the default) is a link to the project's page, named "Open project Garden".
 *  **Filter** (`onFilter`) is a button that narrows the list it sits in to the project, named
 *  "Filter by project Garden". Both stop their click there, since every one sits in a row or a card
 *  whose own click opens the work.
 *
 *  **Phones.** From 320 to 768 px a chip `inControl` (inside a row or a card that is itself a
 *  control) is plain text, the mark and the name: the row opens its work and the project is a step
 *  away, so no surface gains a target under 44 px. A chip that is its surface's only way to the
 *  project (the chat header, a cockpit's) is no `inControl`, and keeps a 44 px box.
 *
 *  Never drawn for the project the view is already in (`current`), nor for an id that names no
 *  project the reader knows (no `name`). */
export function ProjectChip({ id, name, current, inControl = false, onFilter }: {
  /** The project's id; `""` for work in no project. */
  id: string
  /** The project's name, `""` while it is not known or for an id that names no project. */
  name: string
  /** The project the view is in, which no chip there names. */
  current?: string
  /** Inside a row or a card that is itself a control: plain text on a phone. */
  inControl?: boolean
  /** The filter form: narrows the list it sits in to this project. */
  onFilter?: (id: string) => void
}) {
  const phone = useIsMobile()
  if (!id || !name || id === current) return null
  const pill = 'inline-flex h-6 min-w-0 max-w-[14rem] shrink items-center gap-1 rounded-pill bg-surface-high pl-0.5 pr-2 text-on-surface-var'
  const body = (
    <>
      <ProjectMark name={name} size="sm" />
      <span className="min-w-0 truncate">{name}</span>
    </>
  )
  if (phone && inControl) {
    return <span data-type="caption" className={pill} title={name}>{body}</span>
  }
  // Inside a control the chip is its pill. Where it is its surface's only way to the project, the
  // control is a 44 px box below 1024 px, unpainted, around the pill, as `Toggle` holds its track:
  // the row it sits in keeps the pill's height.
  const box = inControl ? pill : `inline-flex h-6 min-w-0 max-w-[14rem] shrink items-center ${TOUCH_TARGET}`
  const inner = inControl ? body : <span className={pill}>{body}</span>
  const stop = (e: MouseEvent) => e.stopPropagation()
  if (onFilter) {
    return (
      <button type="button" data-type="caption" className={`${box} hover:brightness-125`} title={name}
        aria-label={`Filter by project ${name}`} onClick={(e) => { stop(e); onFilter(id) }}>
        {inner}
      </button>
    )
  }
  return (
    <a href={`#/projects/${encodeURIComponent(id)}`} data-type="caption" className={`${box} hover:brightness-125`}
      title={name} aria-label={`Open project ${name}`} onClick={stop}>
      {inner}
    </a>
  )
}
