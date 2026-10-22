import { useId } from 'react'
import { WebLink } from '../../../ui/WebLink'
import type { TurnSource } from '../turnSources'

/** The answer's sources (#3820), under it: each link it cites that its own turn retrieved, by the
 *  number the text badges it with and the title the search or the fetch came back with
 *  (`turnSources`). It shows what the turn recorded and fetches nothing: no preview, no favicon.
 *  Each links out the way the answer's own links do (`WebLink`): the same schemes, the same new tab
 *  with no opener, the same hand-off to the system browser on the desktop. */
export function SourcesRow({ sources }: { sources: TurnSource[] }) {
  const id = useId()
  if (!sources.length) return null
  return (
    <section aria-labelledby={id} className="mt-s flex flex-wrap items-baseline gap-x-m gap-y-xs">
      <h3 id={id} data-type="label-s" className="text-on-surface-low">Sources</h3>
      <ol className="flex min-w-0 flex-wrap gap-x-m gap-y-xs">
        {sources.map((s) => (
          <li key={s.url} data-type="body-s" className="flex min-w-0 max-w-full items-baseline gap-xs">
            <span data-type="caption" aria-hidden className="tabular-nums text-on-surface-low">{s.n}</span>
            <WebLink href={s.url} title={s.url} className="min-w-0 truncate rounded-pill bg-surface-container px-s text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface">
              {s.title}
              {s.title !== s.host && <span className="text-on-surface-low"> · {s.host}</span>}
            </WebLink>
          </li>
        ))}
      </ol>
    </section>
  )
}
