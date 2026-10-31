import type { RouteProps } from '../../app/useQueryState'
import { MemoryPanel } from '../settings/MemoryPanel'

/** Library › Memory (#3815), at `#/memory`: the memory studio and its tools, which were Settings ›
 *  Memory. The page is the one Settings drew, in the same centred column; Library's strip is its
 *  header, so it draws no bar of its own. Its views and the record it opens ride the address as
 *  they did (`?tab=`, `?sel=`, `?pref=`, `?partition=`), and the old Settings address carries them
 *  here (`routeRedirects.ts`). */
export function MemoryPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  return (
    <div className="h-full min-w-0 overflow-y-auto">
      <div className="mx-auto px-2xl py-2xl">
        <MemoryPanel query={query} setQuery={setQuery} />
      </div>
    </div>
  )
}
