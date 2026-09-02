import { useState } from 'react'
import { StickyNote } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Field, FieldError, TextArea } from '../../ui/forms'
import { notify } from '../../app/appSdk'
import { api, type InboxItem } from '../../lib/api'

/** The longest note `POST /api/inbox/notes` accepts — mirrors `_NOTE_MAX_CHARS` in
 *  `dashboard/handlers_inbox.py`. Duplicated rather than fetched because the server is
 *  still the authority: this only lets the form say "too long" before spending a round
 *  trip, and the server's own refusal carries the real count either way. */
const MAX_CHARS = 4000

/** Where the counter starts being useful. Showing "12/4000" under a two-word note is noise;
 *  showing nothing as someone pastes an essay is a surprise 400. */
const COUNTER_FROM = Math.floor(MAX_CHARS * 0.9)

/** INU-9 — compose an inbox item from free text.
 *
 *  The dashboard half of the capability the desktop tray's quick capture promises. Both
 *  reach the SAME endpoint: the tray deep-links `#/inbox?capture=1`, `InboxPage` turns that
 *  flag into this modal, and the modal posts. There is no tray-only path.
 *
 *  Not optimistic, deliberately. Every settings toggle in this app flips local state first
 *  and PUTs after, which is right for a toggle whose value the user can see. A note is the
 *  opposite: the only copy of the text is in this textarea, so the modal stays open and
 *  keeps it until the server has confirmed the write. A failure leaves the words exactly
 *  where the user left them, which is what `note_not_saved`'s message promises.
 */
export function ComposeNoteModal({ onClose, onCreated }: {
  onClose: () => void
  onCreated: (item: InboxItem) => void
}) {
  const [text, setText] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  const trimmed = text.trim()
  const tooLong = trimmed.length > MAX_CHARS

  const save = async () => {
    if (!trimmed) { setErr('Type what you want to remember, then save.'); return }
    if (tooLong) {
      setErr(`That note is ${trimmed.length} characters; the limit is ${MAX_CHARS}. Shorten it and save again.`)
      return
    }
    setSaving(true); setErr('')
    try {
      const r = await api.createInboxNote(trimmed)
      notify('Note saved to your inbox.', 'success')
      onCreated(r.item)
    } catch (e) {
      // The message, not a generic sentence: the server's `note_too_long` carries the real
      // count and `note_not_saved` tells the user their text is still in the box. Replacing
      // either with "Save failed" would throw away the actionable half.
      setErr(e instanceof Error ? e.message : "Couldn't save this note.")
      setSaving(false)
    }
  }

  return (
    <Modal title="Capture a note" icon={<StickyNote size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Field
          label="Note"
          hint="Its first line becomes the subject in your inbox. Everything else is the body."
          right={trimmed.length >= COUNTER_FROM
            ? <span className="tabular-nums text-[0.75rem]" style={{ color: tooLong ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{trimmed.length}/{MAX_CHARS}</span>
            : undefined}>
          <TextArea value={text} onChange={(v) => { setText(v); setErr('') }} rows={7} autoFocus
            placeholder="Ask about the invoice discrepancy before Friday" />
        </Field>
        {err && <FieldError>{err}</FieldError>}
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save} loading={saving} disabled={saving || !trimmed || tooLong}
            disabledReason={saving ? undefined : !trimmed ? 'Write the note first' : 'Shorten the note to save it'}>
            {saving ? 'Saving…' : 'Save to inbox'}
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Modal>
  )
}
