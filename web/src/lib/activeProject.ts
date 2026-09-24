/** The user's currently-active Project — a lightweight, client-side "what am I
 *  working on" pointer shared across the create surfaces (Goal Loop, Code) and the
 *  manual task form, so picking a project once carries to the next thing you start.
 *
 *  Deliberately localStorage-only: it's a UI convenience (a default selection), not
 *  authoritative state — the binding that matters is persisted per work unit
 *  (loop.project_id / code.tasks_project_id / task_list.project_id) at create time.
 *  "" means "no active project" → surfaces default to New/auto or the Personal
 *  catch-all, exactly as before.
 */
const KEY = 'active-project'

export function getActiveProject(): string {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}

export function setActiveProject(projectId: string): void {
  try {
    if (projectId) localStorage.setItem(KEY, projectId)
    else localStorage.removeItem(KEY)
  } catch { /* private mode / quota — the pointer is best-effort */ }
  // Notify same-tab listeners (storage events only fire cross-tab).
  try { window.dispatchEvent(new CustomEvent('ne:active-project', { detail: projectId })) } catch { /* SSR guard */ }
}
