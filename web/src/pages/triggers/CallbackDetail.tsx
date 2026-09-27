import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '../../ui/Button'
import { confirmDelete } from '../../ui/dialog'
import { FieldError } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { api, type CallbackRow } from '../../lib/api'
import { GrantNote } from './ReviewNote'
import { relPast } from './triggerMeta'

/** Inspector for a callback the agent registered (`hook_register`, `webhook_callbacks.py`).
 *
 *  An outside system's post to `/api/hooks/agent` naming this callback's session starts an agent
 *  turn, with the agent's tools, from the context the agent saved. So the context is shown in
 *  full — as text, since it is the agent's words — above the one control that matters: the switch,
 *  which is the owner's yes. Switching it on asks first (the gateway's question), and names the
 *  context this panel shows (`seal`), so a callback registered again with other context in the
 *  meantime is refused rather than allowed unseen. There is no editor: the agent that registered
 *  it owns its context. */
export function CallbackDetail({ callback, onChanged, onDeleted }: {
  callback: CallbackRow
  onChanged: () => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function setAllowed(on: boolean) {
    setBusy(true); setErr('')
    try { await api.toggleCallback(callback.raw_id, on, callback.seal); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not change this callback') }
    finally { setBusy(false) }
  }
  async function del() {
    if (!(await confirmDelete('callback', callback.name))) return
    try { await api.deleteCallback(callback.raw_id); onDeleted() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Delete failed') }
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label data-type="body-s" className="ml-auto inline-flex items-center gap-s cursor-pointer">
          <span className="text-on-surface-var">{callback.enabled ? 'Allowed to run' : 'Not allowed to run'}</span>
          <Toggle on={callback.enabled} onChange={() => setAllowed(!callback.enabled)} disabled={busy} label="Allow this callback to run" size="sm" />
        </label>
      </div>
      {err && <FieldError>{err}</FieldError>}
      {callback.needs_grant.length > 0 && (
        <GrantNote labels={callback.needs_grant} enabled={callback.enabled} busy={busy} onAllow={() => setAllowed(true)} />
      )}

      <Section label="What it does">
        <p data-type="body-s" className="text-on-surface-var">
          When an outside system that holds your webhook token posts to{' '}
          <span className="font-mono">/api/hooks/agent</span> with the session key below,
          PersonalClaw starts an agent turn, with the agent's tools, from this context.
        </p>
      </Section>

      <Section label="Context the agent saved">
        {callback.context_summary
          ? <pre data-type="caption" className="rounded-md bg-surface-container px-m py-s text-on-surface-var font-mono overflow-x-auto whitespace-pre-wrap break-words">{callback.context_summary}</pre>
          : <p data-type="body-s" className="text-on-surface-low">No context: the turn starts from the outside system's message alone.</p>}
      </Section>

      <Section label="Session key">
        <span data-type="caption" className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center font-mono text-on-surface-var">{callback.session_key}</span>
      </Section>

      <Section label="Registered">
        <span data-type="body-s" className="text-on-surface-var">By the agent, {relPast(callback.registered_at)}</span>
      </Section>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
