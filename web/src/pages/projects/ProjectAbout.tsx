import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { api, type AlwaysOnResponse, type ProjectItem } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { rebaseText, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { reportingWrite } from '../../app/reportingWrite'
import { notify } from '../../app/appSdk'
import { Button } from '../../ui/Button'
import { TextArea, useSyncedDraft } from '../../ui/forms'
import { Modal } from '../../ui/Modal'
import { StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { TrustFolderButton } from '../triggers/HeldBackNote'
import type { WorkspaceState } from '../../lib/useWorkspaceMissing'

/** ── About this project: the parts every chat and loop here works from ─────────────────────────
 *
 *  One card with hairlines between its parts, beside the work from 60rem of content, else after it.
 *  Each part says what it is and has one way to change it: the brief (inline), the instructions (a
 *  window over the revision it read), the folder and what automations may do there, what a new chat
 *  here is told (opened beside), and the project's secrets. The knowledge is the library's
 *  (commit 2). After a save each part says, once and politely, who gets the change (§1.4 item 3). */

/** What each part says after a save: who gets the change, never that work already going keeps
 *  the old one where it does not. */
export const SAVED_LINES = {
  brief: "Saved. New chats and loops get it. A chat already going gets it the next time it loads this project's context, and a loop already going when it resumes or starts its next stage.",
  instructions: "Saved. An agent gets them the next time it loads this project's context.",
  overview: 'Saved. New chats get it. Ones already going keep what they started with.',
} as const

function Part({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs px-m py-s">
      <h3 id={id} data-type="title-s" className="text-on-surface">{title}</h3>
      {children}
    </section>
  )
}

/** A part's saved line: polite status, said once. */
function Saved({ text }: { text: string | null }) {
  return <p role="status" aria-live="polite" data-type="body-s" className="text-on-surface-var empty:hidden">{text ?? ''}</p>
}

/** An ask from outside the part to open its editor (the palette's "Edit Garden's brief"): a count
 *  that goes up once per ask. */
function useEditAsk(ask: number | undefined, open: () => void) {
  const latest = useRef(open)
  latest.current = open
  useEffect(() => { if (ask) latest.current() }, [ask])
}

export function BriefPart({ project, offReason, onSave, editAsk }: {
  project: Pick<ProjectItem, 'name' | 'brief'>
  offReason?: string
  onSave: (brief: string) => Promise<boolean>
  editAsk?: number
}) {
  const brief = (project.brief ?? '').trim()
  const [editing, setEditing] = useState(false)
  const [all, setAll] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [draft, setDraft] = useSyncedDraft(brief)
  useEditAsk(editAsk, () => { if (!offReason) { setSaved(null); setEditing(true) } })
  const cancel = () => { setDraft(brief); setEditing(false) }
  const save = async () => {
    if (await onSave(draft.trim())) { setEditing(false); setSaved(SAVED_LINES.brief) }
  }
  return (
    <Part title="Brief">
      {editing ? (
        <div className="flex flex-col gap-xs">
          <TextArea autoFocus ariaLabel={`${project.name}'s brief`} value={draft} onChange={setDraft} rows={4} size="sm" surface="high"
            placeholder="What is this project for? What does done look like?"
            onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); cancel() } }} />
          <div className="flex flex-wrap items-center gap-xs">
            <Button size="sm" onClick={() => void save()} className={TOUCH_TARGET}>Save brief</Button>
            <Button size="sm" variant="ghost" onClick={cancel} className={TOUCH_TARGET}>Cancel</Button>
          </div>
        </div>
      ) : brief ? (
        <>
          <p data-type="body-s" className={`whitespace-pre-line text-on-surface-var ${all ? '' : 'line-clamp-4'}`}>{brief}</p>
          <div className="flex flex-wrap items-center gap-s">
            {brief.split('\n').length > 4 || brief.length > 280 ? (
              <Button size="xs" variant="ghost-accent" ariaExpanded={all} onClick={() => setAll((v) => !v)} className={TOUCH_TARGET}>
                {all ? 'Show less' : 'Show all'}
              </Button>
            ) : null}
            <Button size="xs" variant="ghost-accent" onClick={() => { setSaved(null); setEditing(true) }}
              disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Edit</Button>
          </div>
        </>
      ) : (
        <>
          <p data-type="body-s" className="text-on-surface-low">{`Say what ${project.name} is for. Every chat and loop here reads it.`}</p>
          <div>
            <Button size="xs" variant="ghost-accent" onClick={() => { setSaved(null); setEditing(true) }}
              disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Write the brief</Button>
          </div>
        </>
      )}
      <Saved text={saved} />
    </Part>
  )
}

/** The project's instructions as the window edits them, with the revision the same read reported. */
const instructionsOf = (p: ProjectItem): Revisioned<string> => ({
  value: p.agent_instructions_template ?? '',
  revision: p.revisions?.agent_instructions_template ?? '',
})

export function InstructionsPart({ project, proposals, adapters, offReason, onReview, editAsk }: {
  project: ProjectItem
  /** How many of Learning's proposals change this project's instructions. */
  proposals: number
  /** Whether Settings › Legibility writes the context into files in its folder, with a folder bound. */
  adapters: boolean
  offReason?: string
  onReview: () => void
  editAsk?: number
}) {
  const text = (project.agent_instructions_template ?? '').trim()
  const [open, setOpen] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [updating, setUpdating] = useState(false)
  const [draft, setDraft] = useState('')
  const [base, setBase] = useState<Revisioned<string>>({ value: '', revision: '' })
  const guard = useStaleWriteGuard<string>({
    read: () => api.project(project.id).then(instructionsOf),
    write: (next, revision) => api.setProjectInstructions(project.id, next, revision),
    onSaved: () => { invalidateKeys(`projects:detail:${project.id}`); setOpen(false); setSaved(SAVED_LINES.instructions) },
  })
  const edit = () => {
    const current = instructionsOf(project)
    setBase(current)
    setDraft(current.value)
    setSaved(null)
    setOpen(true)
  }
  useEditAsk(editAsk, () => { if (!offReason) edit() })
  const updateFiles = async () => {
    setUpdating(true)
    try {
      const r = await api.regenerateContextAdapters(project.id)
      if (r.errors.length) notify(`Wrote ${r.written.length}, ${r.errors.length} failed: ${r.errors[0].file}`, 'error')
      else notify(`Updated ${r.written.length} file${r.written.length === 1 ? '' : 's'} in ${r.workspace_dir}`, 'success')
    } catch (e) {
      notify((e as Error).message || "Couldn't update them", 'error')
    } finally {
      setUpdating(false)
    }
  }
  return (
    <Part title="Instructions">
      {text ? (
        <p data-type="body-s" className="line-clamp-4 whitespace-pre-line text-on-surface-var">{text}</p>
      ) : (
        <p data-type="body-s" className="text-on-surface-low">Tell agents how to work here: rules to follow, things to avoid.</p>
      )}
      <div className="flex flex-wrap items-center gap-s">
        <Button size="xs" variant="ghost-accent" onClick={edit} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>
          {text ? 'Edit' : 'Add instructions'}
        </Button>
        {proposals > 0 ? (
          <span className="flex items-center gap-xs">
            <span data-type="body-s" className="text-on-surface-var">{`${proposals} ${proposals === 1 ? 'change' : 'changes'} proposed`}</span>
            <Button size="xs" variant="ghost-accent" onClick={onReview} className={TOUCH_TARGET}>Review</Button>
          </span>
        ) : null}
      </div>
      {adapters ? (
        <div className="flex flex-wrap items-center gap-s">
          <p data-type="body-s" className="text-on-surface-low">Also written into files in its folder that other AI tools read.</p>
          <Button size="xs" variant="ghost-accent" onClick={() => void updateFiles()} loading={updating}
            disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Update them</Button>
        </div>
      ) : null}
      <Saved text={saved} />
      {open ? (
        <Modal size="compact" title={`Instructions for ${project.name}`} onClose={() => setOpen(false)}>
          <div className="flex flex-col gap-s">
            <p data-type="body-s" className="text-on-surface-var">
              {"An agent gets these when it loads this project's context. Write them as plain rules."}
            </p>
            <TextArea autoFocus ariaLabel={`Instructions for ${project.name}`} value={draft} onChange={setDraft} rows={8} size="sm" surface="high" />
            <StaleWriteNotice guard={guard} what={`${project.name}'s instructions`} />
            <div className="flex justify-end gap-xs">
              <Button variant="ghost" onClick={() => setOpen(false)} className={TOUCH_TARGET}>Cancel</Button>
              <Button onClick={() => void guard.save(base, draft, rebaseText(base.value, draft))} loading={guard.busy} className={TOUCH_TARGET}>Save</Button>
            </div>
          </div>
        </Modal>
      ) : null}
    </Part>
  )
}

export function FolderPart({ project, state, offReason, onOpen, onChange, onRemove, onTrustChanged }: {
  project: ProjectItem
  /** What the disk says of the bound folder (`useWorkspaceState`). */
  state: WorkspaceState
  offReason?: string
  onOpen: () => void
  onChange: () => void
  onRemove: () => void
  onTrustChanged: () => void
}) {
  const dir = (project.workspace_dir ?? '').trim()
  const folder = project.folder ?? null
  const [stopping, setStopping] = useState(false)
  const stopTrusting = async () => {
    if (!folder) return
    setStopping(true)
    try {
      if (await reportingWrite(`stop trusting ${folder.resolved}`, () => api.distrustProjectFolder(folder.resolved))) onTrustChanged()
    } finally {
      setStopping(false)
    }
  }
  if (!dir) {
    return (
      <Part title="Folder">
        <p data-type="body-s" className="text-on-surface-low">No folder. Chats here use your usual workspace.</p>
        <div>
          <Button size="xs" variant="ghost-accent" onClick={onChange} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Choose a folder</Button>
        </div>
      </Part>
    )
  }
  if (state === 'missing') {
    return (
      <Part title="Folder">
        <p data-type="body-s" className="text-on-surface">
          This folder is missing. It was at <span className="break-all font-mono">{dir}</span>. Restore it there, or choose another.
        </p>
        <div className="flex flex-wrap gap-xs">
          <Button size="xs" variant="ghost-accent" onClick={onChange} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Choose another folder</Button>
          <Button size="xs" variant="ghost" onClick={onRemove} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Remove folder</Button>
        </div>
      </Part>
    )
  }
  return (
    <Part title="Folder">
      <p data-type="body-s" className="break-all font-mono text-on-surface-var">{dir}</p>
      {state === 'unreadable' ? (
        <p data-type="body-s" className="text-on-surface-var">{"PersonalClaw can't read this folder right now. It may still be there."}</p>
      ) : folder?.trust === 'trusted' ? (
        <p data-type="body-s" className="text-on-surface-var">Automations here may change files and run commands.</p>
      ) : (
        <p data-type="body-s" className="text-on-surface-var">Automations here can only read, until you trust this folder.</p>
      )}
      <div className="flex flex-wrap items-center gap-xs">
        {state !== 'unreadable' ? (
          <Button size="xs" variant="ghost-accent" onClick={onOpen} className={TOUCH_TARGET}>Open</Button>
        ) : null}
        <Button size="xs" variant="ghost-accent" onClick={onChange} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Change</Button>
        <Button size="xs" variant="ghost" onClick={onRemove} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Remove folder</Button>
        {state !== 'unreadable' && folder ? (
          folder.trust === 'trusted' ? (
            <Button size="xs" variant="ghost" onClick={() => void stopTrusting()} loading={stopping}
              disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>Stop trusting</Button>
          ) : (
            <TrustFolderButton folder={folder.resolved} onTrusted={onTrustChanged} className={TOUCH_TARGET} />
          )
        ) : null}
      </div>
    </Part>
  )
}

/** The owner's parts a new chat here starts with, in words: "the brief, the overview and 8
 *  decisions". Counted by the code that writes each line (`project_block.parts`), never here. */
export function toldSummary(parts: NonNullable<AlwaysOnResponse['project_block']>['parts']): string {
  const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`
  const words = [
    ...(parts.brief ? ['the brief'] : []),
    ...(parts.overview ? ['the overview'] : []),
    ...(parts.decisions ? [n(parts.decisions, 'decision', 'decisions')] : []),
    ...(parts.open_questions ? [n(parts.open_questions, 'open question', 'open questions')] : []),
    ...(parts.out_of_scope ? [n(parts.out_of_scope, 'thing out of scope', 'things out of scope')] : []),
  ]
  if (words.length === 0) return ''
  const list = words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
  return `Each new chat here starts with ${list}.`
}

export function ToldPart({ read, failed, onSee, onRetry }: {
  read: AlwaysOnResponse | undefined
  failed: boolean
  onSee: () => void
  onRetry: () => void
}) {
  const summary = read?.project_block ? toldSummary(read.project_block.parts) : ''
  return (
    <Part title="What a new chat here is told">
      {failed ? (
        <div className="flex flex-wrap items-center gap-s">
          <p data-type="body-s" className="text-on-surface">{"Couldn't load what a new chat here is told."}</p>
          <Button size="xs" variant="ghost-accent" onClick={onRetry} className={TOUCH_TARGET}>Try again</Button>
        </div>
      ) : !read ? (
        <p data-type="body-s" className="text-on-surface-low">Loading…</p>
      ) : summary ? (
        <>
          <p data-type="body-s" className="text-on-surface-var">{summary}</p>
          <div>
            <Button size="xs" variant="ghost-accent" onClick={onSee} className={TOUCH_TARGET}>See it</Button>
          </div>
        </>
      ) : (
        <p data-type="body-s" className="text-on-surface-low">Nothing yet. Once you write the brief, every new chat here starts with it.</p>
      )}
    </Part>
  )
}

export function SecretsPart({ project }: { project: Pick<ProjectItem, 'id' | 'name'> }) {
  return (
    <Part title="Secrets">
      <div>
        <TextLink href={`#/settings/secrets?project=${encodeURIComponent(project.id)}`} className={TOUCH_TARGET}>
          {`Secrets for ${project.name}`}
        </TextLink>
      </div>
    </Part>
  )
}

/** The aside itself: one card, its parts in order, hairlines between them. */
export function ProjectAbout({ id, children }: { id: string; children: ReactNode }) {
  return (
    <aside id={id} aria-labelledby={`${id}-title`} className="flex min-w-0 flex-col">
      <h2 id={`${id}-title`} data-type="title-m" className="px-m pb-xs text-on-surface">About this project</h2>
      <div className="flex min-w-0 flex-col divide-y divide-outline-variant/40 rounded-lg border border-outline-variant/40 bg-surface-low">
        {children}
      </div>
    </aside>
  )
}
