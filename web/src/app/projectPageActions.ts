import { useEffect, useRef, useSyncExternalStore } from 'react'

/** ── What a project's page offers the palette and ⌘N while it is open ─────────────────────────
 *
 *  A project's page publishes its own actions while it is mounted: the project it is, and how to
 *  start a chat in it (focus its start box), start a goal loop, add a task, edit its brief or its
 *  instructions, open its folder, and show what a new chat there is told. The palette reads them
 *  (`paletteProjectRows`): while the page is open its seven rows are listed once each, named for the
 *  project, and run; away from it the six page-bound rows are listed once each, dimmed, with "Open a
 *  project to use this.". ⌘N, the global New chat, focuses the start box while the page is open and
 *  keeps its global meaning everywhere else. One page at a time is open, so one set is published. */

export interface ProjectPageActions {
  id: string
  name: string
  startChat: () => void
  newGoalLoop: () => void
  newTask: () => void
  editBrief: () => void
  editInstructions: () => void
  openFolder: () => void
  told: () => void
}

let current: ProjectPageActions | null = null
const watchers = new Set<() => void>()
const changed = () => { for (const w of watchers) w() }
const watch = (w: () => void) => { watchers.add(w); return () => { watchers.delete(w) } }

/** Publish *actions* while the caller is mounted; the latest of each is the one run. */
export function useProjectPageActions(actions: ProjectPageActions | null): void {
  const latest = useRef(actions)
  latest.current = actions
  const key = actions ? `${actions.id}\u0000${actions.name}` : ''
  useEffect(() => {
    if (!key || !latest.current) return
    const { id, name } = latest.current
    const own: ProjectPageActions = {
      id, name,
      startChat: () => latest.current?.startChat(),
      newGoalLoop: () => latest.current?.newGoalLoop(),
      newTask: () => latest.current?.newTask(),
      editBrief: () => latest.current?.editBrief(),
      editInstructions: () => latest.current?.editInstructions(),
      openFolder: () => latest.current?.openFolder(),
      told: () => latest.current?.told(),
    }
    current = own
    changed()
    return () => { if (current === own) { current = null; changed() } }
  }, [key])
}

/** The open project page's actions, or null while no project's page is open. */
export function useOpenProjectPage(): ProjectPageActions | null {
  return useSyncExternalStore(watch, () => current, () => null)
}
