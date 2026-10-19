import { useCallback, useEffect, useState } from 'react'
import { Check, Repeat, X } from 'lucide-react'
import { api, type NudgeLoop } from '../../../lib/api'
import { Button } from '../../../ui/Button'
import { Modal } from '../../../ui/Modal'
import { MenuRow } from '../../../ui/Popover'
import { FieldError, NumberField, TextArea } from '../../../ui/forms'
import { BUSY_REASON } from '../../../ui/unavailable'

/** Auto-nudge entry for the composer "+" menu — a MenuRow that reflects the
 *  on/off state and opens the config in a Modal. Arms / edits / stops a reactive
 *  same-session loop (when a turn finishes and the user is idle for idle_secs,
 *  the service re-injects `message` into THIS session; survives reload/restart).
 *  On by default; PERSONALCLAW_AUTONUDGE=0 disables it (then the modal says so).
 *  `onOpen` closes the parent "+" menu when the row is chosen. */
export function AutoNudgeMenuItem({ session, onOpen }: { session: string; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [loop, setLoop] = useState<NudgeLoop | null>(null)
  const [msg, setMsg] = useState('')
  const [idle, setIdle] = useState(60)
  const [maxCycles, setMaxCycles] = useState(0)
  const [busy, setBusy] = useState(false)

  // 🔴 THE CATCH USED TO `setEnabled(false)`, and the `!enabled` branch below names a specific
  // environment variable as the cause — so an unreachable `/api/autonudge` told the user
  // "Disabled on this server (PERSONALCLAW_AUTONUDGE=0)" about a server that might have it on.
  // A confident wrong answer about a setting is worse than a silent one; the rejection is recorded
  // and rendered as itself.
  const [cfgErr, setCfgErr] = useState<unknown>(null)
  const load = useCallback(() => {
    api.autonudgeGet(session).then((r) => {
      setCfgErr(null)
      setEnabled(r.enabled)
      setLoop(r.loop)
      if (r.loop) { setMsg(r.loop.message); setIdle(r.loop.idle_secs); setMaxCycles(r.loop.max_cycles) }
    }).catch((e) => setCfgErr(e))
  }, [session])
  useEffect(load, [load])

  async function arm() {
    if (!msg.trim() || busy) return
    setBusy(true)
    try {
      if (loop) await api.autonudgeUpdate(loop.id, { message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      else await api.autonudgeStart({ session_name: session, message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      load(); setOpen(false)
    } finally { setBusy(false) }
  }
  async function stop() {
    if (!loop || busy) return
    setBusy(true)
    try { await api.autonudgeDelete(loop.id); setLoop(null) } finally { setBusy(false) }
  }

  return (
    <>
      <MenuRow icon={<Repeat size={16} />} label="Auto-nudge" hint={loop?.active ? 'On — keeps this chat working when idle' : 'Keep this chat working when idle'}
        onClick={() => { onOpen(); load(); setOpen(true) }} />
      {open && (
        <Modal title="Auto-nudge" icon={<Repeat size={18} className="text-primary" />} onClose={() => setOpen(false)}>
          <div className="flex flex-col gap-2">
            {cfgErr ? (
              // Tested BEFORE `!enabled`: `enabled` is unchanged by a failed read, so an error
              // branch after it would be unreachable for whichever value it happens to hold.
              <FieldError>Couldn't load your auto-nudge setting — {(cfgErr as Error)?.message || 'the server did not respond'}. Reopen this panel to try again.</FieldError>
            ) : !enabled ? (
              <p className="text-[0.8125rem] text-on-surface-low">Disabled on this server (<code className="font-mono">PERSONALCLAW_AUTONUDGE=0</code>).</p>
            ) : (<>
              <p className="text-[0.8125rem] text-on-surface-low">When a turn finishes and you're idle, this message is re-injected into this chat to keep it working on its own.</p>
              <TextArea value={msg} onChange={setMsg} rows={3} autoFocus size="sm" surface="high" ariaLabel="Auto-nudge message"
                placeholder="e.g. Continue toward the goal; if done, write a summary and stop." />
              <div className="flex items-center gap-3 text-[0.8125rem] text-on-surface-var">
                <label className="flex items-center gap-1">Idle
                  <NumberField value={idle} onChange={setIdle} min={15} width="w-16" />s</label>
                <label className="flex items-center gap-1">Max cycles
                  <NumberField value={maxCycles} onChange={setMaxCycles} min={0} width="w-14" /></label>
              </div>
              {loop && <p className="text-[0.75rem] text-on-surface-low">Active · {loop.cycle_count} cycle{loop.cycle_count === 1 ? '' : 's'} fired{loop.max_cycles ? ` / ${loop.max_cycles}` : ''}.</p>}
              <div className="flex justify-end gap-2 mt-1">
                {loop && <Button variant="ghost" size="sm" onClick={stop} disabled={busy} disabledReason={BUSY_REASON}><X size={14} /> Stop</Button>}
                <Button size="sm" onClick={arm} disabled={busy || !msg.trim()}
                  disabledReason={!msg.trim() ? 'Write the message first' : BUSY_REASON}><Check size={14} /> {loop ? 'Update' : 'Arm'}</Button>
              </div>
            </>)}
          </div>
        </Modal>
      )}
    </>
  )
}
