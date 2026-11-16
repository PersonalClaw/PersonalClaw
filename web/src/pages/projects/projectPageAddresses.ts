/** ── A project's page: what its address opens ──────────────────────────────────────────────────
 *
 *  `#/projects/<id>?open=<what>` opens one detail beside the work (pushed, so Back closes it):
 *  `need:<key>` the waiting item's detail, `task:<id>` a task and `task:new` the task form starting
 *  in this project, `folder` the folder's tree, `told` what a new chat here reads, `artifact:<slug>`
 *  and `knowledge:<id>` the library's preview of a result or a knowledge item, and `note:new`,
 *  `link:new` and `audio:new` the library's add forms, filing into this project.
 *
 *  Retired values are mapped by the page as it opens, before its project read answers, each
 *  replacing its history entry (`information-architecture.md:89-91`):
 *  `?panel=tasks:<list>` → `?tab=tasks&list=<list>`, `?panel=dir:workspace` → `?open=folder`,
 *  `?panel=dir:context` → `?open=told`. Any other `?panel=` value is dropped. */

export type ProjectDetail =
  | { kind: 'need'; key: string }
  | { kind: 'task'; id: string }
  | { kind: 'new-task' }
  | { kind: 'folder' }
  | { kind: 'told' }
  | { kind: 'artifact'; slug: string }
  | { kind: 'knowledge'; id: string }
  | { kind: 'new-note' | 'new-link' | 'new-audio' }

/** The detail `?open=` names, or null for none (or a value the page does not open). */
export function projectDetail(open: string | undefined): ProjectDetail | null {
  const v = (open ?? '').trim()
  if (!v) return null
  if (v === 'folder') return { kind: 'folder' }
  if (v === 'told') return { kind: 'told' }
  if (v === 'task:new') return { kind: 'new-task' }
  if (v === 'note:new') return { kind: 'new-note' }
  if (v === 'link:new') return { kind: 'new-link' }
  if (v === 'audio:new') return { kind: 'new-audio' }
  const at = v.indexOf(':')
  if (at <= 0 || at === v.length - 1) return null
  const [kind, rest] = [v.slice(0, at), v.slice(at + 1)]
  if (kind === 'need') return { kind: 'need', key: rest }
  if (kind === 'task') return { kind: 'task', id: rest }
  if (kind === 'artifact') return { kind: 'artifact', slug: rest }
  if (kind === 'knowledge') return { kind: 'knowledge', id: rest }
  return null
}

/** The `?open=` value for *detail*. */
export function openValue(detail: ProjectDetail): string {
  switch (detail.kind) {
    case 'need': return `need:${detail.key}`
    case 'task': return `task:${detail.id}`
    case 'new-task': return 'task:new'
    case 'folder': return 'folder'
    case 'told': return 'told'
    case 'artifact': return `artifact:${detail.slug}`
    case 'knowledge': return `knowledge:${detail.id}`
    case 'new-note': return 'note:new'
    case 'new-link': return 'link:new'
    case 'new-audio': return 'audio:new'
  }
}

/** The query patch a retired `?panel=` value maps to (always dropping `panel`), or null when the
 *  address names none. Written with a replace as the page opens. */
export function retiredPanelPatch(panel: string | undefined): Record<string, string | null> | null {
  if (panel === undefined) return null
  const v = panel.trim()
  if (v.startsWith('tasks:') && v.length > 'tasks:'.length) return { panel: null, tab: 'tasks', list: v.slice('tasks:'.length) }
  if (v === 'dir:workspace') return { panel: null, open: 'folder' }
  if (v === 'dir:context') return { panel: null, open: 'told' }
  return { panel: null }
}
