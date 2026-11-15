// ── Where you were last ───────────────────────────────────────────────────────
//
// The shell writes, from the route, the chat you opened last and the project of the last project page
// or project chat, on this device. The library's Use in chat offers "Attach to <chat>" and "Add to
// <project>" from them. Neither is the default project.

/** The keys, on this device. */
export const LAST_CHAT_KEY = 'library:last-chat'
export const LAST_PROJECT_KEY = 'library:last-project'

/** The segments a chat's route names that are not one chat: a new one, and the history list. */
const NOT_A_CHAT = new Set(['', 'new', 'history', 'room'])

function keep(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* storage off: the menu leaves its quick row out */ }
}

/** Remember `route`'s chat or project. `project` is the address's `project` key, which a project chat
 *  carries. */
export function rememberPlace(route: string, sub: string, project?: string): void {
  const first = (sub || '').split('/')[0] ?? ''
  if (route === 'chat' && !NOT_A_CHAT.has(first)) {
    keep(LAST_CHAT_KEY, first)
    if (project) keep(LAST_PROJECT_KEY, project)
  } else if (route === 'projects' && first && first !== 'new') keep(LAST_PROJECT_KEY, first)
}
