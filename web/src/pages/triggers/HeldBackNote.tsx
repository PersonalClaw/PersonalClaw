import { useState } from 'react'
import { FolderLock } from 'lucide-react'
import { reportingWrite } from '../../app/reportingWrite'
import { api, type HeldBack } from '../../lib/api'
import { Button } from '../../ui/Button'
import { confirm } from '../../ui/dialog'
import { InlineConfirm } from '../../ui/InlineConfirm'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { BUSY_REASON } from '../../ui/unavailable'

/** What trusting a folder does, in the words its confirmation says, dialog or inline. */
const TRUST_WORDS = 'An automation that works in this folder then gets the write access its own step asks '
  + 'for: it may change files and run commands in it, the folder’s own scripts included. '
  + 'An automation that only reads is not affected, and no other folder is.'

/** Trust one project folder (`guardrails/project_trust.py`), asking first: an automation working
 *  in it then gets the write access its own step asks for, where Preview held it to reading.
 *
 *  Shown where a folder's Preview is said: the panel of a trigger it holds back (`HeldBackNote`) and
 *  the Inbox request the folder raised on its first such run. Until this control existed that
 *  request asked "Trust this project folder?" and offered nothing to answer it with. */
export function TrustFolderButton({ folder, onTrusted, busy = false, inline = false, className }: {
  /** The folder as the gateway resolved it (`HeldBack.folder`, a request's `refs.dir`). */
  folder: string
  onTrusted: () => void
  busy?: boolean
  /** Ask once more on the card, under the button (`InlineConfirm`, its yes "Yes, trust it"), rather
   *  than in a dialog: for a card in a list that holds still (Needs you). The button is a touch target
   *  there and never moves, and every press of it only asks again. Its other callers keep the dialog. */
  inline?: boolean
  /** The button's own box, for a host that needs it larger: a project's page passes
   *  `TOUCH_TARGET`, its 44 px box from 320 to 768 px. */
  className?: string
}) {
  const [trusting, setTrusting] = useState(false)
  const [asking, setAsking] = useState(false)
  async function write() {
    setTrusting(true)
    try {
      if (await reportingWrite(`trust ${folder}`, () => api.trustProjectFolder(folder))) onTrusted()
    } finally {
      setTrusting(false)
    }
  }
  async function trust() {
    if (inline) { setAsking(true); return }
    if (!(await confirm({
      title: `Trust ${folder}?`,
      body: TRUST_WORDS,
      confirmLabel: 'Trust this folder',
    }))) return
    await write()
  }
  const button = (
    <Button variant="secondary" size="sm" onClick={trust} loading={trusting} disabled={busy} disabledReason={BUSY_REASON}
      className={[inline ? TOUCH_TARGET : '', className ?? ''].filter(Boolean).join(' ') || undefined}>
      Trust this folder
    </Button>
  )
  if (!inline) return button
  return (
    <div className="flex min-w-0 flex-col items-start gap-s">
      {button}
      {asking && (
        <InlineConfirm sentence={`${TRUST_WORDS} Trust ${folder}?`} verb="trust it"
          onYes={() => { setAsking(false); void write() }} onBack={() => setAsking(false)} />
      )}
    </div>
  )
}

/** Why the agent a trigger starts may do less than its step asks, as things stand (`held_back`, the
 *  server's verdict): its working folder is in Preview until the owner trusts it, or it runs on an
 *  agent CLI no files to change can be held to. Its runs say the same in their history. Shown by
 *  both inspectors, a schedule and every other store kind, since either can start an agent. */
export function HeldBackNote({ heldBack, onTrusted, readOnly = false, busy = false }: {
  heldBack: HeldBack
  onTrusted: () => void
  /** Someone else's trigger: the folder is this owner's to trust from their own. */
  readOnly?: boolean
  busy?: boolean
}) {
  return (
    <div role="note" className="flex items-start gap-s text-warn">
      <FolderLock size={14} aria-hidden className="mt-0.5 shrink-0" />
      <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="label-m">Its agent does less than its step asks</p>
        <p className="break-words text-on-surface-var">{heldBack.why}</p>
        {heldBack.folder && !readOnly && (
          <div>
            <TrustFolderButton folder={heldBack.folder} onTrusted={onTrusted} busy={busy} />
          </div>
        )}
      </div>
    </div>
  )
}
