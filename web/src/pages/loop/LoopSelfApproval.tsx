import { useState } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { useSyncedDraft } from '../../ui/forms'
import { Popover } from '../../ui/Popover'
import { Toggle } from '../../ui/Toggle'
import { notify } from '../../app/appSdk'
import { failureSentence } from '../../app/reportingWrite'
import { api, type LoopAgentCliSelfApproval } from '../../lib/api'
import { ConsentDeclined } from '../../lib/securityConsent'

/** Whether an Unattended loop's agent CLI asks PersonalClaw about its calls (the default) or
 *  approves its own, beside the chip that says what the loop runs on.
 *
 *  Asking, each call the CLI asks about meets PersonalClaw's safety rules: the loop's grant answers
 *  what they allow, and a call nothing approves is refused. Its owner may let this one loop's CLI
 *  approve its own calls instead: the gateway asks her consent first, in its own words, and records
 *  the change (`agent_cli_self_approval`). Shown only for an Unattended loop on an agent CLI, where
 *  that is the question; a CLI PersonalClaw has not measured keeps asking, and the switch says why. */
export function LoopSelfApproval({ loop }: {
  loop: { id: string; attended: boolean; provider?: string; agent_cli_self_approval?: LoopAgentCliSelfApproval }
}) {
  // A fresh read of the loop is the server's word on it: it replaces what a change here left.
  const [choice, setChoice] = useSyncedDraft(loop.agent_cli_self_approval)
  const [busy, setBusy] = useState(false)
  if (loop.attended || !loop.provider?.startsWith('acp') || !choice) return null
  const cli = choice.cli || 'Its agent CLI'
  const change = async (next: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      setChoice((await api.setLoopAgentCliSelfApproval(loop.id, next)).agent_cli_self_approval)
    } catch (e) {
      if (e instanceof ConsentDeclined) notify(e.message)
      else notify(failureSentence(next ? `let ${cli} approve its own calls` : `make ${cli} ask PersonalClaw again`, e), 'error')
    } finally {
      setBusy(false)
    }
  }
  const allowed = choice.allowed
  const label = allowed ? 'Approves its own calls' : 'Asks PersonalClaw'
  const says = allowed
    ? `${cli} approves its own calls in this loop, so PersonalClaw's safety rules don't check them.`
    : `${cli} asks PersonalClaw about each call it makes: the loop's grant answers what your safety rules allow, and a call nothing approves is refused.`
  return (
    <Popover portal width={300} placement="bottom" trigger={(open, toggle) => (
      <button type="button" data-type="caption" onClick={toggle} aria-expanded={open} title={says}
        aria-label={`Its calls: ${label}`}
        className={`inline-flex h-5 max-w-[16rem] items-center gap-xs rounded-pill px-s hover:brightness-110 ${allowed ? 'bg-warn/15 text-warn' : 'bg-surface-high text-on-surface-var'}`}>
        {allowed ? <ShieldAlert size={11} className="shrink-0" /> : <ShieldCheck size={11} className="shrink-0" />}
        <span className="truncate">{label}</span>
      </button>
    )}>
      {() => (
        <div className="flex items-start gap-m px-m py-s">
          <div className="min-w-0 flex-1">
            <p data-type="body-s" className="text-on-surface">Let {cli} approve its own calls</p>
            <p data-type="caption" className="mt-xs text-on-surface-low">{choice.available ? says : choice.unavailable}</p>
          </div>
          <Toggle on={allowed} onChange={(next) => { void change(next) }} label={`Let ${cli} approve its own calls`}
            disabled={busy || !choice.available} disabledReason={choice.available ? undefined : choice.unavailable} size="sm" />
        </div>
      )}
    </Popover>
  )
}
