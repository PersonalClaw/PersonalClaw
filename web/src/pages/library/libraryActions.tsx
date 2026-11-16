import { createElement } from 'react'
import {
  Archive, ArchiveRestore, BookOpenCheck, CalendarClock, Copy, Download, Eraser, FolderInput, FolderOpen, Home, Library as LibraryIcon,
  Link as LinkIcon, MessageSquare, MessageSquarePlus, MessagesSquare, Pin, PinOff, Play, Sparkles, Telescope, Trash2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type LibraryBulkBody, type LibraryItem } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { investigate } from '../../lib/investigate'
import { downloadFrom } from '../../lib/download'
import { n } from './libraryWords'
import { chatLaunchPath } from '../../app/linkState'
import { LAST_CHAT_KEY, LAST_PROJECT_KEY } from '../../app/lastPlace'
import { notify, notifyWithAction } from '../../app/appSdk'
import { copyText } from '../../app/clipboard'
import { confirm } from '../../ui/dialog'
import type { ContextMenuItem } from '../../ui/motion'
import { refId, refKind, titleOf } from './libraryMeta'

// ── What you can do with a thing in the library ────────────────────────────────────────
//
// The same few actions on every item: Use in chat (a new chat with it, ask about it, attach it to a
// chat, add it to a project), Open, Pin and More. A kind that cannot do one leaves it out. Every
// write goes through `POST /api/library/bulk`, the one path both stores and the automations' logs
// answer, with one ref or many.

/** The navigation memory the shell keeps from the route: the chat you opened
 *  last and the project of the last project page or project chat, on this device. Neither is the
 *  default project. */
export { LAST_CHAT_KEY, LAST_PROJECT_KEY }
export const readLast = (key: string): string => { try { return localStorage.getItem(key) ?? '' } catch { return '' } }

/** One row of a menu the library draws. */
export interface ActionRow { label: string; hint?: string; icon: LucideIcon; onSelect: () => void; danger?: boolean; disabled?: boolean; disabledReason?: string; name?: string }
export interface ActionGroup { group: string; rows: ActionRow[] }

/** The knowledge item a report attaches and opens as: its write-up. */
export type WriteUps = ReadonlyMap<string, string>

/** The ref a chip holds for `item`: a report's write-up's; a findings log holds none. */
export function chipRefOf(item: LibraryItem, writeUps: WriteUps): string {
  const kind = refKind(item.ref)
  if (kind === 'findings') return ''
  if (kind === 'report') { const up = writeUps.get(refId(item.ref)); return up ? `knowledge:${up}` : '' }
  return item.ref
}

/** The page an item opens at: its own page, a report its write-up's; a findings log none. */
export function openPathOf(item: LibraryItem, writeUps: WriteUps, back = ''): string {
  const kind = refKind(item.ref)
  const id = refId(item.ref)
  const tail = back ? `?back=${encodeURIComponent(back)}` : ''
  if (kind === 'knowledge') return `knowledge/item/${encodeURIComponent(id)}${tail}`
  if (kind === 'artifact') return `artifacts/${encodeURIComponent(id)}${tail}`
  if (kind === 'report') { const up = writeUps.get(id); return up ? `knowledge/item/${encodeURIComponent(up)}${tail}` : '' }
  return ''
}

/** Whether a kind goes into a project: notes, files, links and artifacts; a report and a
 *  findings log have no project in v0.3.9. */
export const filesIntoProject = (item: LibraryItem) => refKind(item.ref) === 'knowledge' || refKind(item.ref) === 'artifact'

/** One operation on refs, then the library re-reads. Answers the refs that failed. */
export async function bulk(body: LibraryBulkBody): Promise<string[]> {
  const { results } = await api.libraryBulk(body)
  invalidateKeys('knowledge', true)
  invalidateKeys('artifacts', true)
  return results.filter((r) => !r.ok).map((r) => r.ref)
}

export interface ActionInputs {
  navigate: (path: string) => void
  writeUps: WriteUps
  /** The project the page is in (a project's page, or the library filtered to one). */
  project?: string
  projects: readonly { id: string; name: string }[]
  /** The last chat's title, by its key, from the sidebar's read. */
  chatTitle: (key: string) => string
  /** The chat picker and the project picker, which the host opens for the item. */
  pickChat: (item: LibraryItem) => void
  pickProject: (item: LibraryItem) => void
  /** The library is filtered to a manual collection: More offers to take an item out of it. */
  collection?: { id: string; name: string } | null
  /** Writes are off, with why (the gateway is not answering). */
  offReason?: string
  /** The collection picker (manual collections), which the host opens for the item. */
  pickCollection?: (item: LibraryItem) => void
  /** Whether the intent `id` offers to build a skill from what it finds. */
  offersSkill?: (id: string) => boolean
  /** A findings log's count, for Clear findings…'s sentence. */
  /** How many findings an intent kept: undefined while the intents read has no entry for it, so a
   *  confirm or a toast never states a count it doesn't have. */
  foundBy?: (id: string) => number | undefined
}

/** Use in chat ▾'s groups: New chat with this, Ask about this; Chats; Projects. */
export function useInChatGroups(item: LibraryItem, inp: ActionInputs): ActionGroup[] {
  const kind = refKind(item.ref)
  const chip = chipRefOf(item, inp.writeUps)
  const title = titleOf(item)
  const ask = (): ActionRow => ({
    label: 'Ask about this', hint: 'A new chat that reads it and changes nothing', icon: Telescope,
    onSelect: () => {
      const which = kind === 'artifact' ? 'artifact' : kind === 'findings' ? 'findings' : 'knowledge_item'
      const id = kind === 'report' ? (inp.writeUps.get(refId(item.ref)) ?? '') : refId(item.ref)
      if (!id) return
      investigate(which, id, { backLink: `#/library?item=${encodeURIComponent(item.ref)}` })
        .catch(() => notify(`Couldn't start a chat about “${title}”.`, 'error'))
    },
  })
  // A findings log is no chip: Ask about this only.
  if (kind === 'findings') return [{ group: '', rows: [ask()] }]
  const groups: ActionGroup[] = [{
    group: '',
    rows: [
      ...(chip ? [{
        label: 'New chat with this', hint: 'Opens a new chat with it attached. Nothing is sent.', icon: MessageSquarePlus,
        onSelect: () => inp.navigate(chatLaunchPath({ attach: [chip], project: inp.project })),
      }] : []),
      ask(),
    ],
  }]
  if (chip) {
    const last = readLast(LAST_CHAT_KEY)
    const lastTitle = last ? inp.chatTitle(last) : ''
    groups.push({
      group: 'Chats',
      rows: [
        ...(last && lastTitle ? [{
          label: `Attach to ${lastTitle}`, hint: 'The chat you were last in', icon: MessageSquare,
          onSelect: () => inp.navigate(chatLaunchPath({ session: last, attach: [chip] })),
        }] : []),
        { label: 'Attach to another chat…', icon: MessagesSquare, onSelect: () => inp.pickChat(item) },
      ],
    })
  }
  if (filesIntoProject(item)) {
    const lastId = readLast(LAST_PROJECT_KEY)
    const last = inp.projects.find((p) => p.id === lastId)
    const here = item.project?.id ?? ''
    const quick: ActionRow[] = last && last.id !== here ? [{
      label: here ? `Move to ${last.name}` : `Add to ${last.name}`, hint: here ? `Now in ${item.project!.name}` : 'The project you were last in',
      icon: FolderInput, disabled: !!inp.offReason, disabledReason: inp.offReason,
      onSelect: () => void fileInto(item, last, inp),
    }] : []
    groups.push({ group: 'Projects', rows: [...quick, { label: 'Add to project…', hint: 'Pick any project', icon: FolderOpen, onSelect: () => inp.pickProject(item) }] })
  }
  return groups.filter((g) => g.rows.length > 0)
}

/** File `item` in `to` (`""` takes it out of its project), with the toast and its Undo. */
export async function fileInto(item: LibraryItem, to: { id: string; name: string } | null, inp: Pick<ActionInputs, 'projects'>): Promise<void> {
  const title = titleOf(item)
  const from = item.project
  const failed = await bulk({ op: 'project', refs: [item.ref], project_id: to?.id ?? '' }).catch(() => [item.ref])
  if (failed.length) { notify(`Couldn't file “${title}”.`, 'error'); return }
  const undo = () => void bulk({ op: 'project', refs: [item.ref], project_id: from?.id ?? '' })
  if (!to) notifyWithAction(`Removed “${title}” from ${from?.name ?? 'its project'}.`, { label: 'Undo', run: undo })
  else if (from) notifyWithAction(`Moved “${title}” to ${to.name}.`, { label: 'Undo', run: undo })
  else notifyWithAction(`Added “${title}” to ${to.name}.`, { label: 'Undo', run: undo })
  void inp
}

const icon = (I: LucideIcon) => createElement(I, { size: 15 })

/** More's rows: the same as the row's ⋯, its long press and Shift+F10, which add Use in
 *  chat's rows at the top. A kind that cannot do one leaves it out. */
export function moreRows(item: LibraryItem, inp: ActionInputs): ContextMenuItem[] {
  const kind = refKind(item.ref)
  const title = titleOf(item)
  const off = inp.offReason
  const write = (body: LibraryBulkBody, done: string) => async () => {
    const failed = await bulk(body).catch(() => body.refs)
    notify(failed.length ? `Couldn't change “${title}”.` : done, failed.length ? 'error' : 'success')
  }
  const rows: ContextMenuItem[] = []
  const id = refId(item.ref)
  if (kind !== 'artifact') {
    rows.push({ icon: icon(Copy), label: 'Copy text', onSelect: () => void copyItemText(item, inp.writeUps) })
  }
  rows.push({ icon: icon(LinkIcon), label: 'Copy link', onSelect: () => void copy(`${location.origin}${location.pathname}#/library?item=${encodeURIComponent(item.ref)}`, 'the link', 'Link copied.') })
  const dl = downloadUrlOf(item)
  // Saved through an anchor with `download`, signed first where sign-ins are paired: opened in a tab,
  // the gateway's copy of a page or an image would be drawn as a page on its origin.
  rows.push({ icon: icon(Download), label: 'Download', onSelect: () => (dl ? downloadFrom(dl) : void downloadText(item, inp.writeUps)) })
  if (kind !== 'findings' && inp.pickCollection) {
    rows.push({ icon: icon(LibraryIcon), label: 'Add to collection…', disabled: !!off, hint: off, onSelect: () => inp.pickCollection!(item) })
  }
  if (inp.collection && kind !== 'findings') {
    rows.push({ icon: icon(FolderInput), label: `Remove from ${inp.collection.name}`, disabled: !!off, hint: off,
      onSelect: write({ op: 'uncollect', refs: [item.ref], collection_id: inp.collection.id }, `Removed “${title}” from ${inp.collection.name}.`) })
  }
  if (filesIntoProject(item)) {
    const inThis = !!inp.project && item.project?.id === inp.project
    if (item.project && !inThis) {
      rows.push({ icon: icon(FolderOpen), label: `Open project ${item.project.name}`, onSelect: () => inp.navigate(`projects/${encodeURIComponent(item.project!.id)}`) })
    } else if (!item.project) {
      rows.push({ icon: icon(FolderInput), label: 'Add to project…', onSelect: () => inp.pickProject(item) })
    }
  }
  if (item.from && refKind(item.from.ref) === 'chat' && item.from.title) {
    rows.push({ icon: icon(MessageSquare), label: 'Show the chat it came from', onSelect: () => inp.navigate(`chat/${encodeURIComponent(refId(item.from!.ref))}`) })
  }
  if (kind === 'artifact') {
    rows.push({
      icon: icon(Home), label: 'Show on Home', disabled: !!off, hint: off,
      onSelect: () => void api.pinArtifact(id, true).then(() => notify(`Showing “${title}” on Home.`, 'success'))
        .catch(() => notify(`Couldn't show “${title}” on Home.`, 'error')),
    })
  }
  if (kind === 'report') {
    rows.push({
      icon: icon(Play), label: 'Run now', disabled: !!off || item.running, hint: off ?? (item.running ? "It's already writing one" : undefined),
      onSelect: () => void api.runResearchReport(id).then(() => invalidateKeys('knowledge', true)).catch(() => notify(`Couldn't run “${title}”.`, 'error')),
    })
    rows.push({ icon: icon(CalendarClock), label: 'Edit report', onSelect: () => inp.navigate(`library?setup=${encodeURIComponent(`report:${id}`)}`) })
  }
  if (kind === 'findings' && inp.offersSkill?.(id)) {
    rows.push({
      icon: icon(Sparkles), label: 'Generate skill', disabled: !!off, hint: off,
      onSelect: () => void api.generateSkillFromIntent(id)
        .then((r) => notify(`Made the skill “${r.skill}”${findingsFrom(inp.foundBy?.(id))}. Find it under Skills.`, 'success'))
        .catch(() => notify(`Couldn't make a skill from “${title}”.`, 'error')),
    })
  }
  if (kind !== 'artifact') {
    const read = item.read === 'read'
    rows.push({ icon: icon(BookOpenCheck), label: read ? 'Mark as unread' : 'Mark as read', disabled: !!off, hint: off,
      onSelect: write({ op: 'read_state', refs: [item.ref], state: read ? 'unread' : 'read' }, read ? `Marked “${title}” as unread.` : `Marked “${title}” as read.`) })
  }
  rows.push(item.archived
    ? { icon: icon(ArchiveRestore), label: 'Restore', disabled: !!off, hint: off, onSelect: write({ op: 'restore', refs: [item.ref] }, `Restored “${title}”.`) }
    : { icon: icon(Archive), label: 'Archive', disabled: !!off, hint: off, onSelect: write({ op: 'archive', refs: [item.ref] }, `Archived “${title}”.`) })
  if (kind === 'findings') {
    rows.push({
      icon: icon(Eraser), label: 'Clear findings…', danger: true, disabled: !!off, hint: off,
      onSelect: async () => {
        // Unread, the count isn't known: the title asks without one rather than say 0.
        const count = inp.foundBy?.(id)
        const ok = await confirm({
          title: count === undefined ? "Clear this intent's findings?" : `Clear the ${n(count)} ${count === 1 ? 'finding' : 'findings'}?`,
          body: "Your intent keeps looking and starts again from nothing. You can't undo this.",
          confirmLabel: 'Clear findings', danger: true,
        })
        if (!ok) return
        try { await api.clearIntentFindings(id); invalidateKeys('knowledge', true); notify(`Cleared the findings of “${title}”.`, 'success') }
        catch { notify(`Couldn't clear the findings of “${title}”.`, 'error') }
      },
    })
  }
  if (kind === 'knowledge' || kind === 'artifact') {
    rows.push({
      icon: icon(Trash2), label: 'Delete…', danger: true, disabled: !!off, hint: off,
      onSelect: async () => {
        if (!(await confirm({ title: `Delete “${title}”?`, body: deleteSentence(item), confirmLabel: 'Delete', danger: true }))) return
        const failed = await bulk({ op: 'delete', refs: [item.ref], expect: 1 }).catch(() => [item.ref])
        notify(failed.length ? `Couldn't delete “${title}”.` : `Deleted “${title}”.`, failed.length ? 'error' : 'success')
      },
    })
  }
  if (kind === 'report') {
    const up = inp.writeUps.get(id)
    if (up) {
      rows.push({
        icon: icon(Trash2), label: 'Delete write-up…', danger: true, disabled: !!off, hint: off,
        onSelect: async () => {
          if (!(await confirm({ title: 'Delete this write-up?', body: `“${title}” keeps running and writes a new one next time.`, confirmLabel: 'Delete', danger: true }))) return
          const failed = await bulk({ op: 'delete', refs: [`knowledge:${up}`], expect: 1 }).catch(() => ['x'])
          notify(failed.length ? `Couldn't delete the write-up of “${title}”.` : `Deleted the write-up of “${title}”.`, failed.length ? 'error' : 'success')
        },
      })
    }
  }
  return rows
}

/** The approved delete sentence: a file's names the copy the library keeps, an artifact's its
 *  versions. */
export function deleteSentence(item: LibraryItem): string {
  if (refKind(item.ref) === 'artifact') {
    const v = item.version ?? 1
    return v > 1 ? `This deletes all ${v} versions. You can't undo this.` : "This deletes it. You can't undo this."
  }
  const file = item.kind !== 'note' && item.kind !== 'link'
  return file ? "This deletes it and the copy of the file your library keeps. You can't undo this." : "This deletes it. You can't undo this."
}

/** Pin or unpin: kept at the top of your library. */
export function pinRow(item: LibraryItem, offReason?: string): ActionRow {
  const title = titleOf(item)
  return {
    label: item.pinned ? 'Unpin' : 'Pin', icon: item.pinned ? PinOff: Pin, disabled: !!offReason, disabledReason: offReason,
    name: `${item.pinned ? 'Unpin' : 'Pin'} ${title}`,
    onSelect: async () => {
      const failed = await bulk({ op: 'pin', refs: [item.ref], value: !item.pinned }).catch(() => [item.ref])
      if (failed.length) notify(`Couldn't ${item.pinned ? 'unpin' : 'pin'} “${title}”.`, 'error')
    },
  }
}

/** Where a thing downloads from: a knowledge file's bytes, an artifact's raw body; '' for a note, a
 *  link, a report and a findings log, which download as `.md` built here (`downloadText`). */
function downloadUrlOf(item: LibraryItem): string {
  const kind = refKind(item.ref)
  if (kind === 'artifact') return `/api/artifacts/${encodeURIComponent(refId(item.ref))}/raw`
  if (kind === 'knowledge' && item.kind !== 'note' && item.kind !== 'link') return api.knowledgeItemFileUrl(refId(item.ref))
  return ''
}

/** A thing's text, as Copy text reads it: a knowledge item's, a report's write-up's, a findings log's
 *  findings as a list. */
async function textOf(item: LibraryItem, writeUps: WriteUps): Promise<string> {
  const kind = refKind(item.ref)
  if (kind === 'findings') {
    const lines: string[] = []
    let before = ''
    do {
      const page = await api.knowledgeIntentOutcomesPage(refId(item.ref), 200, before)
      lines.push(...page.outcomes.map((o) => `- ${o.takeaway}`))
      before = page.before
    } while (before)
    return lines.join('\n')
  }
  const id = kind === 'report' ? writeUps.get(refId(item.ref)) : refId(item.ref)
  if (!id) return ''
  return (await api.knowledgeItem(id)).content ?? ''
}

/** Download a note, a link, a report or a findings log as `.md`, built in the browser. */
async function downloadText(item: LibraryItem, writeUps: WriteUps) {
  try {
    const text = await textOf(item, writeUps)
    const url = URL.createObjectURL(new Blob([`# ${titleOf(item)}\n\n${text}\n`], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${titleOf(item).replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'item'}.md`
    a.click()
    URL.revokeObjectURL(url)
  } catch { notify(`Couldn't read “${titleOf(item)}”.`, 'error') }
}

/** " from 14 findings" for a skill's toast, or nothing while the count isn't known. */
function findingsFrom(count: number | undefined): string {
  return count === undefined ? '' : ` from ${n(count)} ${count === 1 ? 'finding' : 'findings'}`
}

/** Copy through the app's one clipboard writer, which says why when it can't, and say when it did. */
async function copy(text: string, what: string, done: string) {
  if (await copyText(text, what)) notify(done, 'success')
}

/** Copy a thing's text: a knowledge item's, a report's write-up's, a findings log's findings. */
async function copyItemText(item: LibraryItem, writeUps: WriteUps) {
  try {
    const findings = refKind(item.ref) === 'findings'
    await copy(await textOf(item, writeUps), findings ? 'the findings' : 'the text', findings ? 'Findings copied.' : 'Text copied.')
  } catch {
    notify(`Couldn't read “${titleOf(item)}”.`, 'error')
  }
}

