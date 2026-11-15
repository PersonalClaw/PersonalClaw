import { useState } from 'react'
import { ChevronRight, File as FileIcon, Folder } from 'lucide-react'
import { api, type AlwaysOnDocument, type AlwaysOnResponse, type FsEntry } from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { rebaseText, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { notify } from '../../app/appSdk'
import { copyText } from '../../app/clipboard'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { ListRow } from '../../ui/ListScaffold'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { SAVED_LINES } from './ProjectAbout'

/** ── A project's page: the details it opens beside its work ────────────────────────────────────
 *
 *  The folder's tree (`?open=folder`) and what a new chat here is told (`?open=told`). A waiting
 *  item's detail is Home's (`NeedDetail`), a task's is the Tasks page's (`TaskDetail`), and the new
 *  task form is the create page's body (`TaskCreateForm`). The library's preview and forms are the
 *  library's (commit 2). */

/** Why a folder read failed, in words: a 404 says the folder is gone, anything else that it
 *  could not be read. */
function dirErrorMessage(e: unknown): string {
  const status = (e as { status?: number } | null)?.status
  if (status === 404) return 'This folder no longer exists on disk.'
  return "PersonalClaw can't read this folder right now. It may still be there."
}

/** The bound folder's tree: a folder opens in place, with a way back up to the root; each entry
 *  also opens Files at its own path (a file in its folder, opened), never at the root. */
export function FolderTree({ path, navigate }: { path: string; navigate: (to: string) => void }) {
  const [cur, setCur] = useState(path)
  const { data, loading, error } = useQuery(`dirtree:${cur}`, () => api.fileList(cur))
  const rel = cur.startsWith(path) ? cur.slice(path.length).replace(/^\//, '') : ''
  const crumbs = rel ? rel.split('/') : []
  const entries = data?.entries ?? []
  const dirs = entries.filter((e) => e.is_dir)
  const files = entries.filter((e) => !e.is_dir)
  const openInFiles = (e?: FsEntry) => navigate(e && !e.is_dir
    ? `files?dir=${encodeURIComponent(cur)}&file=${encodeURIComponent(e.path)}`
    : `files?dir=${encodeURIComponent(e?.path ?? cur)}`)
  return (
    <div className="flex min-w-0 flex-col gap-s px-m py-s">
      <div>
        <Button size="sm" variant="ghost" onClick={() => openInFiles()} className={TOUCH_TARGET}>Open in Files</Button>
      </div>
      <nav aria-label="Where in the folder" className="flex min-w-0 flex-wrap items-center gap-xs">
        <Button size="xs" variant="ghost" onClick={() => setCur(path)} className={TOUCH_TARGET}>
          {path.split('/').pop() || path}
        </Button>
        {crumbs.map((seg, i) => (
          <span key={`${i}:${seg}`} className="inline-flex items-center gap-xs">
            <ChevronRight aria-hidden size={12} className="shrink-0 text-on-surface-low" />
            <Button size="xs" variant="ghost" onClick={() => setCur(`${path}/${crumbs.slice(0, i + 1).join('/')}`)}
              className={TOUCH_TARGET}>{seg}</Button>
          </span>
        ))}
      </nav>
      {loading && !data ? <p data-type="body-s" className="text-on-surface-low">Loading…</p>
        : error ? <p data-type="body-s" className="text-on-surface">{dirErrorMessage(error)}</p>
        : entries.length === 0 ? <p data-type="body-s" className="text-on-surface-low">This folder is empty.</p>
        : (
          <ul className="flex min-w-0 flex-col gap-0.5">
            {dirs.map((e) => (
              <ListRow key={e.path} label={`Folder: ${e.name}`} onClick={() => setCur(e.path)}>
                <span className="flex min-w-0 items-center gap-xs"><Folder aria-hidden size={14} className="shrink-0 text-primary" /><span className="truncate">{e.name}</span></span>
              </ListRow>
            ))}
            {files.map((e) => (
              <ListRow key={e.path} label={`File: ${e.name}`} onClick={() => openInFiles(e)}>
                <span className="flex min-w-0 items-center gap-xs"><FileIcon aria-hidden size={14} className="shrink-0 text-on-surface-low" /><span className="truncate">{e.name}</span></span>
              </ListRow>
            ))}
          </ul>
        )}
    </div>
  )
}

/** The parts of a new chat's block, each by the line its writer starts it with
 *  (`chat_utils.project_context_block`); a line that starts none (a part's own bullets) belongs to
 *  the part above it, and the framing lines before the first part are unlabelled. */
const PART_STARTS: { label: string; starts: string }[] = [
  { label: 'Brief', starts: '- Project brief' },
  { label: 'Overview', starts: '- Project overview' },
  { label: 'Decisions', starts: '- Decisions so far' },
  { label: 'Open questions', starts: '- Not yet specified' },
  { label: 'Out of scope', starts: '- Out of scope' },
  { label: 'Folder', starts: '- Workspace:' },
  { label: 'Notes folder', starts: '- Project context directory' },
  { label: 'Loops run here', starts: '- Loops' },
]

export interface BlockLine { text: string; label: string; first: boolean }

/** The block's lines, each with the part it belongs to ('' for the framing lines), and whether it
 *  starts that part (where its label is drawn). */
export function blockLines(text: string): BlockLine[] {
  let label = ''
  return text.split('\n').map((line) => {
    const part = PART_STARTS.find((p) => line.startsWith(p.starts))
    if (part) { label = part.label; return { text: line, label, first: true } }
    if (!line.startsWith(' ') && !line.startsWith('\t') && line.trim() !== '') label = ''
    return { text: line, label, first: false }
  })
}

const OVERVIEW_ID = 'project_instruction:overview.md'

/** What a new chat in the project reads before the first message, word for word, each part
 *  labelled in the gutter. The overview has Edit, saved over its revision; the ledgers are a record
 *  of what happened and are not edited here. */
export function ToldDetail({ projectId, projectName, read, failed, onRetry }: {
  projectId: string
  projectName: string
  read: AlwaysOnResponse | undefined
  failed: boolean
  onRetry: () => void
}) {
  const block = read?.project_block ?? null
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [base, setBase] = useState<Revisioned<string>>({ value: '', revision: '' })
  const [saved, setSaved] = useState<string | null>(null)
  const guard = useStaleWriteGuard<string>({
    read: () => api.alwaysOnDoc(OVERVIEW_ID, projectId).then((d: AlwaysOnDocument) => ({ value: d.body, revision: d.revision })),
    write: (next, revision) => api.saveAlwaysOnDoc(OVERVIEW_ID, projectId, next, revision),
    onSaved: () => {
      invalidateKeys(`projects:told:${projectId}`)
      setEditing(false)
      setSaved(SAVED_LINES.overview)
    },
  })
  const edit = async () => {
    try {
      const d = await api.alwaysOnDoc(OVERVIEW_ID, projectId)
      setBase({ value: d.body, revision: d.revision })
      setDraft(d.body)
    } catch (e) {
      // A doc with nothing in it yet is not in the inventory: it is written from empty.
      if ((e as { status?: number } | null)?.status !== 404) {
        notify(`Couldn't read the overview, so nothing was opened: ${(e as Error).message || 'the request failed'}`, 'error')
        return
      }
      setBase({ value: '', revision: '' })
      setDraft('')
    }
    setSaved(null)
    setEditing(true)
  }
  const copy = async () => {
    if (!block) return
    await copyText(block.text, 'what a new chat here is told')
  }
  if (failed) {
    return (
      <div className="flex flex-col gap-s px-m py-s">
        <p data-type="body-s" className="text-on-surface">{"Couldn't load what a new chat here is told."}</p>
        <div><Button size="xs" variant="ghost-accent" onClick={onRetry} className={TOUCH_TARGET}>Try again</Button></div>
      </div>
    )
  }
  return (
    <div className="flex min-w-0 flex-col gap-s px-m py-s">
      <p data-type="body-s" className="text-on-surface-var">This is what a new chat here reads before your first message, word for word.</p>
      {!read ? <p data-type="body-s" className="text-on-surface-low">Loading…</p>
        : !block || !block.text.trim() ? (
          <p data-type="body-s" className="text-on-surface-low">{`Nothing yet. Once you write the brief, every new chat in ${projectName} starts with it.`}</p>
        ) : (
          <div role="group" aria-label={`What a new chat in ${projectName} is told`}
            className="grid min-w-0 grid-cols-[6.5rem_minmax(0,1fr)] gap-x-s rounded-md border border-outline-variant/40 bg-surface-low p-s">
            {blockLines(block.text).map((l, i) => (
              <div key={i} className="contents">
                <span data-type="caption" className="text-on-surface-low">{l.first ? l.label : ''}</span>
                <span data-type="body-s" className="whitespace-pre-wrap break-words font-mono text-on-surface-var">{l.text || ' '}</span>
              </div>
            ))}
          </div>
        )}
      {editing ? (
        <div className="flex flex-col gap-xs">
          <TextArea autoFocus ariaLabel={`${projectName}'s overview`} value={draft} onChange={setDraft} rows={8} size="sm" surface="high" />
          <StaleWriteNotice guard={guard} what={`${projectName}'s overview`} />
          <div className="flex flex-wrap gap-xs">
            <Button size="sm" onClick={() => void guard.save(base, draft, rebaseText(base.value, draft))} loading={guard.busy} className={TOUCH_TARGET}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} className={TOUCH_TARGET}>Cancel</Button>
          </div>
        </div>
      ) : read ? (
        <div><Button size="xs" variant="ghost-accent" ariaLabel={`Edit ${projectName}'s overview`} onClick={() => void edit()} className={TOUCH_TARGET}>Edit</Button></div>
      ) : null}
      <p role="status" aria-live="polite" data-type="body-s" className="text-on-surface-var empty:hidden">{saved ?? ''}</p>
      <p data-type="body-s" className="text-on-surface-low">
        {"Decisions, open questions and what is out of scope are a record of what happened, so they aren't edited here."}
      </p>
      <p data-type="body-s" className="text-on-surface-low">
        {"Not in it: the instructions, which an agent gets when it loads this project's context, and the knowledge, which an agent looks up when it needs it."}
      </p>
      <p data-type="body-s" className="text-on-surface-low">Chats already going keep what they started with.</p>
      {block?.text ? <div><Button size="xs" variant="ghost-accent" onClick={() => void copy()} className={TOUCH_TARGET}>Copy</Button></div> : null}
    </div>
  )
}
