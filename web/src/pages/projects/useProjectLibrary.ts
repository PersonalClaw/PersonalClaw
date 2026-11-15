import { useMemo } from 'react'
import type { LibraryItem } from '../../lib/api'
import { useLibrary } from '../library/useLibrary'

/** ── A project's page: what its work made and what its agents know, in one library read ─────────
 *
 *  `GET /api/library?project=<id>`, once, split by store: Results are the artifacts, About ›
 *  Knowledge the knowledge items (reports and findings have no project, so they are in neither).
 *  Each count is the read's store facet, never the rows drawn. A store the read names in `failed`
 *  is that store's own failure: it draws its line and no count while the other store draws its
 *  rows, and a failed request is both stores' failure. */

export type LibraryStore = 'artifacts' | 'knowledge'

export interface ProjectLibraryPart {
  /** The store's rows, in the read's order (its latest activity first). */
  rows: LibraryItem[]
  /** How many the store holds for the project (its facet), or null while unknown or failed. */
  count: number | null
  /** This store could not be read. */
  failed: boolean
}

export interface ProjectLibrary {
  results: ProjectLibraryPart
  knowledge: ProjectLibraryPart
  /** No answer yet. */
  loading: boolean
  refresh: () => void
}

export function useProjectLibrary(projectId: string): ProjectLibrary {
  const read = useLibrary({ project: projectId })
  return useMemo(() => {
    const data = read.data
    const failedRequest = !data && !!read.error
    const failedStores = new Set(Array.isArray(data?.failed) ? data.failed : [])
    const facet = data?.facets?.store ?? {}
    const items = Array.isArray(data?.items) ? data.items : []
    const part = (store: LibraryStore): ProjectLibraryPart => {
      const failed = failedRequest || failedStores.has(store)
      const n = facet[store]
      return {
        rows: failed ? [] : items.filter((i) => i.store === store),
        count: failed || typeof n !== 'number' ? null : n,
        failed,
      }
    }
    return { results: part('artifacts'), knowledge: part('knowledge'), loading: !data && !read.error, refresh: read.refresh }
  }, [read.data, read.error, read.refresh])
}
