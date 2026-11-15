import { api } from '../lib/api'
import { useQuery } from '../lib/data'
import { PROJECT_LIST_KEY } from '../lib/loopProject'

// ── An item page's back control ───────────────────────────────────────────────
//
// A knowledge item's page and an artifact's page return to the library with that thing selected
// (`#/library?item=<ref>`), and say "Library". Opened from a project's page (`?back=project:<id>`) the
// control names the project and returns there.

/** Where an item page's back control goes, and what it says. */
export interface ItemBack { label: string; path: string }

/** The project a `back` value names, or ''. */
export const backProjectOf = (back: string | undefined): string => (back?.startsWith('project:') ? back.slice('project:'.length) : '')

/** The back control for the item `ref` (`knowledge:<id>`, `artifact:<slug>`), from the page's `back`. */
export function itemBackOf(ref: string, back: string | undefined, projectName: (id: string) => string): ItemBack {
  const project = backProjectOf(back)
  if (project) return { label: projectName(project) || 'Project', path: `projects/${encodeURIComponent(project)}` }
  return { label: 'Library', path: `library?item=${encodeURIComponent(ref)}` }
}

/** `itemBackOf`, naming the project from the projects read the shell already keeps. */
export function useItemBack(ref: string, back: string | undefined): ItemBack {
  // The shell's one projects read, under its own key and fetcher, so no reader of it sees a stand-in.
  const { data } = useQuery(PROJECT_LIST_KEY, async () => api.projects(), { persist: true })
  return itemBackOf(ref, back, (id) => (data ?? []).find((p) => p.id === id)?.name ?? '')
}
