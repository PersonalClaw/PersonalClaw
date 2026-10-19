import { useCallback, useEffect, useState } from 'react'
import { api, type ReplyStyle, type ReplyStyleList } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { Field, FieldError, TextArea, TextInput } from '../../ui/forms'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

type Draft = { name: string; title: string; description: string; text: string; editing: boolean }

const EMPTY: Draft = { name: '', title: '', description: '', text: '', editing: false }

/** `own:formal` → `formal`, the file name under `styles/`. */
const nameOf = (id: string) => id.replace(/^own:/, '')

/** Your own reply styles, and the ones enabled apps add.
 *
 *  Your own are Markdown files under the home (`styles/<name>.md`), offered everywhere a style is
 *  chosen as `own:<name>`. What is accepted is the server's call (the checks a style file meets at
 *  load: the one fence, the size cap, and the bundled styles' content rail over the text, title and
 *  description, `reply_style.BEHAVIOUR_PATTERNS`), so a refused save shows the server's reason and
 *  keeps what was typed. A file refused at load is listed with its reason,
 *  so a broken file is never silent. An app's styles are listed with the app; they ship with the
 *  app and are not edited here. The bundled styles are edited in Settings › Prompts. */
export function OwnReplyStyles() {
  const [list, setList] = useState<ReplyStyleList | null>(null)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saveErr, setSaveErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setLoadErr(null)
    api.replyStyles().then(setList).catch((e) => setLoadErr(e))
  }, [])
  useEffect(() => { load() }, [load])

  if (!list && loadErr) return <LoadError what="reply styles" error={loadErr} onRetry={load} />
  if (!list) return <FormSkeleton sections={1} rows={2} title={false} what="reply styles" />

  const own = list.styles.filter((s) => s.source === 'own')
  const apps = list.styles.filter((s) => s.source === 'app')
  const cap = Math.round((list.size_cap ?? 4096) / 1024)

  const edit = (s: ReplyStyle) => {
    setSaveErr('')
    setDraft({ name: nameOf(s.id), title: s.title, description: s.description, text: s.text ?? '', editing: true })
  }

  const save = () => {
    if (!draft) return
    setBusy(true)
    setSaveErr('')
    api.saveOwnReplyStyle(draft.name.trim(), { title: draft.title, description: draft.description, text: draft.text })
      .then(() => { setDraft(null); load() })
      .catch((e) => setSaveErr(String((e as Error)?.message || e)))
      .finally(() => setBusy(false))
  }

  const remove = (s: ReplyStyle) => {
    api.deleteOwnReplyStyle(nameOf(s.id))
      .then(load)
      .catch((e) => notify(`Couldn't delete the ${s.title} style: ${String((e as Error)?.message || e)}`, 'error'))
  }

  return (
    <Section title="Your reply styles"
      hint={`Styles of your own, offered beside the bundled ones in the composer, on an agent and here. Tone guidance only, up to ${cap} KB: a style that names tools, permissions, approvals or the sandbox, speaks of the prompt or the rules around it, or opens or closes a style section, is refused. Changes style only, never a fact, a caveat or a refusal.`}>
      <ul className="flex flex-col">
        {own.map((s) => (
          <li key={s.id} className="flex items-center gap-m border-b border-outline-variant/30 py-2 last:border-0">
            <div className="min-w-0 flex-1">
              <div data-type="body-s" className="text-on-surface">{s.title}</div>
              <div data-type="caption" className="text-on-surface-low">{s.description}</div>
            </div>
            <Button variant="ghost" size="sm" ariaLabel={`Edit ${s.title}`} onClick={() => edit(s)}>Edit</Button>
            <Button variant="ghost" size="sm" ariaLabel={`Delete ${s.title}`} onClick={() => remove(s)}>Delete</Button>
          </li>
        ))}
        {apps.map((s) => (
          <li key={s.id} className="flex items-center gap-m border-b border-outline-variant/30 py-2 last:border-0">
            <div className="min-w-0 flex-1">
              <div data-type="body-s" className="text-on-surface">{s.title}</div>
              <div data-type="caption" className="text-on-surface-low">{s.description} · added by {s.by ?? s.app}</div>
            </div>
          </li>
        ))}
      </ul>
      {(list.refused ?? []).length > 0 && (
        <div className="mt-s flex flex-col gap-1">
          <span data-type="caption" className="text-on-surface-low">Not loaded:</span>
          {(list.refused ?? []).map((r) => (
            <span key={r.id} data-type="caption" className="text-warn">{r.reason}</span>
          ))}
        </div>
      )}
      {draft ? (
        <div className="mt-m flex flex-col gap-m">
          <Field label="Name" hint="Lowercase letters, digits and dashes. The file is styles/<name>.md.">
            <TextInput value={draft.name} onChange={(v) => setDraft({ ...draft, name: v })} disabled={draft.editing}
              disabledReason="A style's name is its file name; delete it and add a new one to rename it." />
          </Field>
          <Field label="Title">
            <TextInput value={draft.title} onChange={(v) => setDraft({ ...draft, title: v })} />
          </Field>
          <Field label="Description">
            <TextInput value={draft.description} onChange={(v) => setDraft({ ...draft, description: v })} />
          </Field>
          <Field label="Style text">
            <TextArea value={draft.text} onChange={(v) => setDraft({ ...draft, text: v })} rows={6} />
          </Field>
          {saveErr && <FieldError>{saveErr}</FieldError>}
          <div className="flex gap-s">
            <Button size="sm" loading={busy} onClick={save}>Save style</Button>
            <Button variant="ghost" size="sm" onClick={() => { setDraft(null); setSaveErr('') }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="mt-m">
          <Button variant="tonal" size="sm" onClick={() => { setSaveErr(''); setDraft({ ...EMPTY }) }}>New style</Button>
        </div>
      )}
    </Section>
  )
}
