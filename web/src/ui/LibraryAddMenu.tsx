import { useId, useRef, useState } from 'react'
import { ArrowLeft, CalendarClock, Link2, Mic, NotebookPen, Plus, Rss, Target, Upload } from 'lucide-react'
import { invalidateKeys } from '../lib/data'
import { notify } from '../app/appSdk'
import { createKnowledge, uploadKnowledgeFile } from '../pages/knowledge/knowledgeStore'
import { AudioRecorder } from '../pages/knowledge/AudioRecorder'
import { HeaderMenu } from './HeaderActions'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { ChipInput, Field, Select, TextArea, TextInput } from './forms'
import { FormFooter } from './FormFooter'
import { TOUCH_TARGET } from './Toggle'
import { BUSY_REASON } from './unavailable'

// ── Add, and its forms ──────────────────────────────────────────────────────────
//
// One door: Upload files, Add a link, Write a note, Record audio, then (in the library) the three
// set-up rows. With a `project` (the Projects page) it holds the first four only, each filing into
// that project, since no feed, intent or report belongs to a project.

/** The forms Add opens in the pane, at `?open=<form>:new`. */
export type AddForm = 'link' | 'note' | 'audio'

/** The set-up forms Add's last three rows open, at `?setup=<form>:new`. */
export type SetupForm = 'source' | 'intent' | 'report'

/** Upload `files` into the library (and `project`, when there is one), each saying how it went in the
 *  upload policy's own sentence when it is refused. */
export async function uploadFiles(files: readonly File[], project?: { id: string; name: string } | null): Promise<void> {
  for (const f of files) {
    try {
      const r = await uploadKnowledgeFile(f, undefined, undefined, project?.id)
      notify(r.deduped ? `“${f.name}” is already in your library.` : project ? `Added “${f.name}” to ${project.name}.` : `Added “${f.name}” to your library.`, 'success')
    } catch (e) {
      notify(e instanceof Error && e.message ? e.message: `Couldn't add “${f.name}”.`, 'error')
    }
  }
  invalidateKeys('knowledge', true)
}

export function LibraryAddMenu({ project, onOpen, onSetup, phoneSheet = false, disabled, disabledReason, className }: {
  /** The project it files into (the Projects page): its four add rows only. */
  project?: { id: string; name: string } | null
  /** Open an add form in the pane. */
  onOpen: (form: AddForm) => void
  /** Open a set-up form in the pane; the library's Add only. */
  onSetup?: (form: SetupForm) => void
  /** From 320 to 768 px, a phone sheet with 44 px rows, its break kept. */
  phoneSheet?: boolean
  /** Off for now, saying why in `disabledReason` (the page can't reach the gateway). */
  disabled?: boolean
  disabledReason?: string
  /** Added to the trigger: the compact widths' 44 px box (`TOUCH_TARGET`). */
  className?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const setup = !project && onSetup
  const items = [
    { label: 'Upload files', icon: Upload, hint: 'They stay on this computer.', group: 'add', onSelect: () => input.current?.click() },
    { label: 'Add a link', icon: Link2, group: 'add', onSelect: () => onOpen('link') },
    { label: 'Write a note', icon: NotebookPen, hint: 'Note, journal, quick note or code snippet', group: 'add', onSelect: () => onOpen('note') },
    { label: 'Record audio', icon: Mic, group: 'add', onSelect: () => onOpen('audio') },
    ...(setup ? [
      { label: 'Follow a feed or folder', icon: Rss, hint: 'New items come in on their own.', group: 'setup', onSelect: () => onSetup('source') },
      { label: 'Look out for a topic', icon: Target, hint: 'The agent checks what you add and keeps what it finds.', group: 'setup', onSelect: () => onSetup('intent') },
      { label: 'Schedule a report', icon: CalendarClock, hint: 'A question answered on a schedule.', group: 'setup', onSelect: () => onSetup('report') },
    ] : []),
  ]
  return (
    <>
      <HeaderMenu icon={Plus} label="Add" variant="primary" priority="primary" className={className} width={setup ? 384: 300} phoneSheet={phoneSheet}
        items={items} disabled={disabled} title={disabled ? disabledReason: undefined} />
      {/* Raw: no primitive draws a file picker. Out of the tab order on purpose: Add › Upload files
          is its keyboard-reachable control, and forwards the click here. */}
      <input ref={input} type="file" multiple className="hidden" data-library-upload tabIndex={-1} aria-hidden
        onChange={(e) => { const files = [...(e.target.files ?? [])]; e.target.value = ''; void uploadFiles(files, project) }} />
    </>
  )
}

/** The kinds Write a note makes. */
const NOTE_KINDS = [
  { value: 'note', label: 'Note' },
  { value: 'journal', label: 'Journal' },
  { value: 'fleeting', label: 'Quick note' },
  { value: 'gist', label: 'Code snippet' },
] as const

const LANGUAGES = ['typescript', 'javascript', 'python', 'rust', 'go', 'bash', 'sql', 'json', 'yaml', 'markdown', 'text']

/** An add form in the pane (`?open=link:new | note:new | audio:new`): what it asks, then Add, which
 *  opens what it made. */
export function LibraryAddForm({ form, project, onClose, onAdded }: {
  form: AddForm
  project?: { id: string; name: string } | null
  onClose: () => void
  /** Added: the new item's ref. */
  onAdded: (ref: string) => void
}) {
  const headingId = useId()
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [content, setContent] = useState('')
  const [kind, setKind] = useState<string>('note')
  const [language, setLanguage] = useState('typescript')
  const [tags, setTags] = useState<string[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const heading = form === 'link' ? 'Add a link' : form === 'note' ? 'Write a note' : 'Record audio'
  const where = project ? ` It goes in ${project.name}.` : ''
  const urlOk = /^https?:\/\/\S+$/.test(url.trim())
  const ready = form === 'link' ? urlOk: form === 'note' ? !!(content.trim() || title.trim()) : !!file
  const why = form === 'link' ? 'Enter an address starting with http:// or https://' : form === 'note' ? 'Write something first.' : 'Record something first.'

  async function add() {
    setErr(''); setSaving(true)
    try {
      if (form === 'audio' && file) {
        const r = await uploadKnowledgeFile(file, undefined, undefined, project?.id)
        invalidateKeys('knowledge', true)
        notify(project ? `Added the recording to ${project.name}.` : 'Added the recording to your library.', 'success')
        onAdded(r.item_id ? `knowledge:${r.item_id}` : '')
        return
      }
      const made = await createKnowledge({
        type: form === 'link' ? 'bookmark' : kind as 'note',
        title: title.trim(), content: form === 'note' ? content: '', url: form === 'link' ? url.trim() : '', tags,
        ...(form === 'note' && kind === 'gist' ? { gist_language: language } : {}),
        ...(project ? { project_id: project.id } : {}),
      })
      invalidateKeys('knowledge', true)
      onAdded(`knowledge:${made.id}`)
    } catch (e) {
      setErr(e instanceof Error && e.message ? e.message: "Couldn't add it.")
    } finally { setSaving(false) }
  }

  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex items-start gap-s">
        <IconButton icon={ArrowLeft} label="Back" onClick={onClose} className={TOUCH_TARGET} />
        <div className="min-w-0 flex-1">
          <h2 id={headingId} data-type="title-l" className="text-on-surface">{heading}</h2>
          {where && <p data-type="body-s" className="mt-xs text-on-surface-var">{where.trim()}</p>}
        </div>
      </header>
      {form === 'link' && (
        <>
          <Field label="Address">
            <TextInput value={url} onChange={setUrl} required autoFocus placeholder="https://example.com/article" />
          </Field>
          <Field label="Title" hint="Leave it empty to use the page's own.">
            <TextInput value={title} onChange={setTitle} />
          </Field>
        </>
      )}
      {form === 'note' && (
        <>
          <Field label="Kind">
            <Select value={kind} onChange={setKind} options={NOTE_KINDS.map((k) => ({ value: k.value, label: k.label }))} />
          </Field>
          <Field label="Title">
            <TextInput value={title} onChange={setTitle} autoFocus ariaLabel="Title" />
          </Field>
          {kind === 'gist' && (
            <Field label="Language">
              <Select value={language} onChange={setLanguage} options={LANGUAGES.map((l) => ({ value: l, label: l }))} />
            </Field>
          )}
          <Field label={kind === 'gist' ? 'Code' : 'Text'}>
            <TextArea value={content} onChange={setContent} rows={10} mono={kind === 'gist'} />
          </Field>
        </>
      )}
      {form === 'audio' && (
        <AudioRecorder onRecorded={(f) => { setFile(f); setErr('') }} onClear={() => setFile(null)} />
      )}
      {form !== 'audio' && (
        <Field label="Tags">
          <ChipInput values={tags} onChange={setTags} placeholder="Add a tag…" ariaLabel="Tags" />
        </Field>
      )}
      <FormFooter error={err}>
        <Button variant="ghost" onClick={onClose} disabled={saving} disabledReason={BUSY_REASON}>Cancel</Button>
        <Button onClick={() => void add()} loading={saving} loadingLabel="Adding…" disabled={!ready} disabledReason={why}>Add</Button>
      </FormFooter>
    </section>
  )
}
