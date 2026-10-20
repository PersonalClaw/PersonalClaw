import { useState } from 'react'
import { KeyRound, Trash2 } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FieldError, TextInput } from '../../ui/forms'
import { confirmDestructive } from '../../ui/dialog'
import { copyText } from '../../app/clipboard'
import { api, type WebhookDoor, type WebhookInstructions, type WebhookSender } from '../../lib/api'
import { absTime, relPast } from '../schedule/scheduleMeta'

/** A webhook automation's door, on its page: the address a program posts to, where that address
 *  answers and what to send it, and the sender tokens made for it — where the owner makes one and
 *  revokes one. Everything it states is the server's (`inbound/webhook.py::door`): the address,
 *  the sentence for where it answers, the most it takes; a token is shown once, as Settings' route
 *  answers with it, together with a command that fires the automation with it. */
export function WebhookDoorSection({ automationId, automationName, door, onChanged }: {
  /** Its address's id (`store:webhook:<name>`), the one a sender token is pinned to. */
  automationId: string
  automationName: string
  door: WebhookDoor
  onChanged: () => void
}) {
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [made, setMade] = useState<{ label: string; token: string; notice: string; webhook: WebhookInstructions } | null>(null)

  async function make() {
    setBusy('make')
    setErr('')
    try {
      const r = await api.externalAccessCreateClient({
        label: label.trim() || `${automationName} sender`,
        surfaces: ['webhook'],
        scope: { trigger: automationId },
      })
      if (r.webhook) setMade({ label: r.label, token: r.token, notice: r.token_notice, webhook: r.webhook })
      setLabel('')
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not make a sender token')
    } finally {
      setBusy('')
    }
  }

  async function revoke(sender: WebhookSender) {
    const name = sender.label || sender.client_id
    if (!(await confirmDestructive(
      `Revoke the sender token “${name}”?`,
      'A program still sending it is refused, and told it was revoked. This cannot be undone.',
      { confirmLabel: 'Revoke' },
    ))) return
    setBusy(sender.client_id)
    setErr('')
    try {
      await api.externalAccessRevokeClient(sender.client_id)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not revoke this sender token')
    } finally {
      setBusy('')
    }
  }

  const kb = Math.floor(door.body_limit_bytes / 1024)
  return (
    <div className="flex flex-col gap-s">
      <div className="flex flex-wrap items-center gap-s">
        <code data-type="caption" className="min-w-0 flex-1 break-all font-mono text-on-surface">{door.url}</code>
        <Button size="xs" variant="secondary" onClick={() => copyText(door.url, 'the webhook address')}>
          Copy
        </Button>
      </div>
      <p data-type="body-s" className="text-on-surface-var">{door.reach}</p>
      <p data-type="body-s" className="text-on-surface-var">
        A program fires it by posting to this address with a sender token made for it, as the header{' '}
        <code className="font-mono">Authorization: Bearer &lt;sender token&gt;</code>. What it posts, up
        to {kb} KB, reaches what it runs as data, never as instructions. Each post is this
        automation firing, held to the rules its other fires keep: past its hourly cap, in its quiet
        hours or while a run of it is still going, nothing runs, and its history says why. A program
        that may send a post again names each one with an{' '}
        <code className="font-mono">Idempotency-Key</code> header, and the same name sent again
        within a week fires nothing.
      </p>

      {made && (
        <div role="status" data-type="body-s" className="flex flex-col gap-xs rounded-lg bg-surface-container px-m py-s">
          <div className="flex items-center gap-s text-on-surface">
            <KeyRound size={14} aria-hidden /> Sender token for “{made.label}”
          </div>
          <code data-type="caption" className="break-all font-mono text-on-surface">{made.token}</code>
          <p className="text-on-surface-low">{made.notice}</p>
          <p className="text-on-surface-low">To try it from this computer:</p>
          <code data-type="caption" className="break-all font-mono text-on-surface">{made.webhook.curl}</code>
          <div className="flex flex-wrap items-center gap-s">
            <Button size="xs" variant="secondary" onClick={() => copyText(made.token, 'the sender token')}>
              Copy token
            </Button>
            <Button size="xs" variant="secondary" onClick={() => copyText(made.webhook.curl, 'the command')}>
              Copy command
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setMade(null)}>Done</Button>
          </div>
        </div>
      )}

      {door.senders.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          No sender tokens yet, so nothing outside PersonalClaw can fire it.
        </p>
      ) : (
        <ul className="flex flex-col gap-xs">
          {door.senders.map((s) => <SenderRow key={s.client_id} sender={s} busy={busy === s.client_id} onRevoke={() => revoke(s)} />)}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-s">
        <div className="min-w-0 flex-1">
          <TextInput value={label} onChange={setLabel} size="sm" ariaLabel="What the sender token is for"
            placeholder={`What it is for (${automationName} sender)`} maxLength={80} />
        </div>
        <Button size="sm" variant="secondary" onClick={make} loading={busy === 'make'} loadingLabel="Making…">
          <KeyRound size={14} aria-hidden /> Make a sender token
        </Button>
      </div>
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}

function SenderRow({ sender, busy, onRevoke }: { sender: WebhookSender; busy: boolean; onRevoke: () => void }) {
  const ended = sender.expires_at > 0 && sender.expires_at * 1000 <= Date.now()
  const state = sender.disabled
    ? 'switched off in Settings → External Access'
    : ended ? `stopped working ${absTime(sender.expires_at)}` : `works until ${absTime(sender.expires_at)}`
  return (
    <li className="flex flex-wrap items-center gap-s rounded-lg bg-surface-container px-m py-s">
      <div className="min-w-0 flex-1">
        <div data-type="body-s" className="truncate text-on-surface">“{sender.label || sender.client_id}”</div>
        <div data-type="caption" className="text-on-surface-low">
          {state} · {sender.last_seen_at ? `last used ${relPast(sender.last_seen_at)}` : 'never used'}
        </div>
      </div>
      <Button size="xs" variant="ghost" onClick={onRevoke} loading={busy}>
        <Trash2 size={12} aria-hidden /> Revoke
      </Button>
    </li>
  )
}
