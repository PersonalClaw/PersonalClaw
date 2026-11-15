import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'
import { MAX_NAME_LEN } from '../../lib/api'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { PageTitle } from '../../ui/PageTitle'
import { TextInput } from '../../ui/forms'
import { TOUCH_TARGET } from '../../ui/Toggle'

/** ── A project's page: its title, and renaming it ─────────────────────────────────────────────
 *
 *  The page's one `h1`, the project's name (it truncates, the whole name in its title). Rename, from
 *  the header's "…" or from a list row's Rename (`?rename=1`, which the page reads once), turns it
 *  into a field with the name in it: Enter or Save writes it, Esc or Cancel leaves it, and the field
 *  carries the name cap. Three deliberate writes, and no write on blur. Save is named "Save the
 *  project name"; it and Cancel take their 44 px boxes from 320 to 768 px. */
export function ProjectTitle({ name, editing, onEditingChange, onSave, disabledReason }: {
  name: string
  editing: boolean
  onEditingChange: (v: boolean) => void
  onSave: (name: string) => Promise<boolean>
  disabledReason?: string
}) {
  const [draft, setDraft] = useState(name)
  useEffect(() => { if (editing) setDraft(name) }, [editing, name])
  const next = draft.trim()
  const save = async () => {
    if (!next || disabledReason) return
    if (next === name || (await onSave(next))) onEditingChange(false)
  }
  if (!editing) return <PageTitle className="min-w-0 truncate"><span title={name}>{name}</span></PageTitle>
  return (
    <div className="flex min-w-0 items-center gap-2">
      <TextInput autoFocus ariaLabel="Rename this project" value={draft} onChange={setDraft} maxLength={MAX_NAME_LEN}
        onKeyDown={(e) => { if (e.key === 'Enter') void save(); else if (e.key === 'Escape') onEditingChange(false) }}
        size="sm" surface="high" />
      <Button size="sm" ariaLabel="Save the project name" onClick={() => void save()} disabled={!next || !!disabledReason}
        disabledReason={!next ? 'Enter a project name first' : disabledReason} className={TOUCH_TARGET}><Check size={14} /></Button>
      <IconButton icon={X} label="Cancel" size={24} iconSize={15} onClick={() => onEditingChange(false)} className={TOUCH_TARGET} />
    </div>
  )
}
