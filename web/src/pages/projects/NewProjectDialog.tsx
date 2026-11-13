import { useId, useState } from 'react'
import { Check, FolderKanban, FolderOpen, X } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { api, MAX_NAME_LEN, type ProjectItem } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { FieldHintProvider, FieldLabelProvider, TextInput } from '../../ui/forms'
import { IconButton } from '../../ui/IconButton'
import { InlineError } from '../../ui/InlineError'
import { Modal } from '../../ui/Modal'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { WorkspacePicker } from '../code/WorkspacePicker'

/** The New project dialog: a name and, optionally, a folder. The brief is written on the project's
 *  page and the default is set from its "…", so neither is asked here. Shared by the Projects list
 *  and every project picker's "New project…" (`ui/ProjectPicker`), which selects what it made.
 *
 *  Create stays reachable while the name is empty and says why on screen ("Give it a name first.").
 *  Choosing a folder while the name is empty fills the name with the folder's own name. A name the
 *  owner already has is said before the gateway refuses it. */
export function NewProjectModal({ onClose, onCreated, taken = [] }: {
  onClose: () => void
  /** The project the gateway made. */
  onCreated: (project: ProjectItem) => void
  /** The names the owner's projects already have, so a taken one is said as it is typed. */
  taken?: readonly string[]
}) {
  const [name, setName] = useState('')
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [pickWs, setPickWs] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const trimmed = name.trim()
  const clash = trimmed ? taken.find((t) => t.toLowerCase() === trimmed.toLowerCase()) : undefined
  const why = !trimmed ? 'Give it a name first.' : clash ? `You already have a project called “${clash}”.` : undefined
  const submit = async () => {
    if (why || busy) return
    setBusy(true); setErr(null)
    try {
      // The owner typed this name, so it is locked, as a rename locks it: it is not auto-named.
      const p = await api.createProject({ name: trimmed, name_locked: true, workspace_dir: workspaceDir.trim() || undefined })
      invalidateKeys('projects:list')
      onCreated(p)
    } catch (e) {
      const message = (e as Error).message || ''
      setErr(/already exists/i.test(message) ? `You already have a project called “${trimmed}”.` : message || 'Could not create the project')
    } finally { setBusy(false) }
  }
  const choose = (dir: string) => {
    setPickWs(false); setWorkspaceDir(dir)
    if (!name.trim()) setName(dir.replace(/\/+$/, '').split('/').pop() || '')
  }
  return (
    <Modal size="compact" title="New project" icon={<FolderKanban size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l">
        <p data-type="body-s" className="text-on-surface-var">A project keeps related chats, loops, tasks and files together.</p>
        <Field label="Name">
          <TextInput autoFocus value={name} onChange={setName}
            onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            placeholder="Project name"
            maxLength={MAX_NAME_LEN}
            surface="high" className={TOUCH_TARGET} />
        </Field>
        <Field label="Folder" hint="Optional. Chats and loops here work in this folder.">
          <div className="flex items-center gap-2">
            {/* A chosen path at the name field's height; with none, the button says it all. */}
            {workspaceDir && (
              <code data-chosen-folder className={cx('flex h-10 min-w-0 flex-1 items-center rounded-md bg-surface-high px-m font-mono text-[0.8125rem] text-on-surface-var', TOUCH_TARGET)} title={workspaceDir}><span className="min-w-0 truncate">{workspaceDir}</span></code>
            )}
            <Button size="sm" variant="secondary" onClick={() => setPickWs(true)} className={TOUCH_TARGET}><FolderOpen size={14} /> {workspaceDir ? 'Change' : 'Choose a folder'}</Button>
            {workspaceDir && <IconButton icon={X} label="Remove folder" size={32} onClick={() => setWorkspaceDir('')} className={TOUCH_TARGET} />}
          </div>
        </Field>
        {err && <InlineError onDismiss={() => setErr(null)}>{err}</InlineError>}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          {why && <span data-type="body-s" data-create-why className="mr-auto text-on-surface-low">{why}</span>}
          <Button variant="ghost" onClick={onClose} className={TOUCH_TARGET}>Cancel</Button>
          {/* The reason keeps the button reachable and says what is missing; it is on screen too. */}
          <Button onClick={() => void submit()} loading={busy} disabled={!!why || busy} className={TOUCH_TARGET}
            disabledReason={busy ? undefined : why}><Check size={15} /> Create project
          </Button>
        </div>
      </div>
      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)} onPick={choose} />
      )}
    </Modal>
  )
}

/** A labelled form field for the New-project modal.
 *
 *  KEPT, not replaced by `ui/forms`' Field, and the reason is a visible one: this layout puts the hint
 *  ABOVE the control in sentence case, while the shared Field puts it BELOW in uppercase. Swapping
 *  them here moved 27.9% of the modal's pixels — that is an open visual-language decision (the
 *  hint-placement taste call), and converging it inside an a11y fix
 *  would decide it by accident.
 *
 *  What WAS broken is the label CONTRACT, not the layout: this Field rendered a label but published no
 *  id, so no control inside it could claim one via aria-labelledby, and every child was unnamed. Same
 *  defect as ToolsPage's and settingsUI's local Fields. The fix is to publish — a second layout is
 *  allowed, a second layout that breaks the contract is not.
 *
 *  🔴 AND THE HINT WAS THE OTHER HALF OF THAT CONTRACT, still unpublished. The label pass fixed
 *  `FieldLabelProvider` and stopped there, so a control here resolved a NAME and no DESCRIPTION —
 *  while the sentence it needed was rendered two lines above it, in a `<span>` with no `id` for
 *  `aria-describedby` to point at. The shared `Field` publishes both, and the same six form-family
 *  primitives claim both. This is that half, mirrored exactly: the id is published only when there IS
 *  a hint, because an `aria-describedby` resolving to nothing is worse than none — it claims a
 *  description exists while giving the reader nothing. Zero pixels: an id and a context value have no
 *  layout, so the hint-placement taste call this layout exists to preserve is untouched. */
function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  const labelId = useId()
  const hintId = useId()
  return (
    <FieldLabelProvider value={labelId}>
      <FieldHintProvider value={hint ? hintId : undefined}>
        <div className="flex flex-col gap-1.5">
          <span id={labelId} className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{label}</span>
          {hint && <span id={hintId} className="text-on-surface-low text-[0.75rem] leading-snug">{hint}</span>}
          {children}
        </div>
      </FieldHintProvider>
    </FieldLabelProvider>
  )
}
