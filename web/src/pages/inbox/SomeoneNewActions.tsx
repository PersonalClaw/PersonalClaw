import { useState } from 'react'
import { UserPlus, XCircle } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { BUSY_REASON } from '../../ui/unavailable'
import { api, type InboxItem } from '../../lib/api'

/** A message from someone new, held by a channel that sends as you (your own mailbox).
 *
 *  Nothing was sent to them: such a channel never answers a stranger on its own, since an answer
 *  would go out in your name and tell them the address is read. Their message waits here. The
 *  reply below answers them, as you, when you press Send; Pair lets them talk to your agent on that
 *  channel from their next message on; Ignore dismisses this. */
export function SomeoneNewActions({ item, onChanged, onIgnore, busy = false }: {
  item: InboxItem
  onChanged: () => void
  onIgnore: () => void
  busy?: boolean
}) {
  const refs = (item.refs ?? {}) as Record<string, unknown>
  const where = String(refs.channel_name || refs.someone_new || 'this channel')
  const who = item.sender_name || item.sender_id
  const [pairing, setPairing] = useState(false)
  const [err, setErr] = useState('')

  if (refs.paired) {
    return <p data-type="body-s" className="text-on-surface-var">Paired. {who} can talk to your agent on {where} now.</p>
  }

  async function pair() {
    setPairing(true)
    setErr('')
    try {
      await api.pairInboxSender(item.id)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not pair them')
    } finally {
      setPairing(false)
    }
  }

  const blocked = pairing || busy
  return (
    <div className="flex flex-col gap-m">
      <p data-type="body-s" className="text-on-surface-var">
        {who} wrote to you on {where}, and nothing was sent to them. Reply below to answer them as
        you, pair them so they can talk to your agent there, or ignore this.
      </p>
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" onClick={pair} loading={pairing} disabled={blocked} disabledReason={BUSY_REASON}>
          <UserPlus size={14} /> Pair
        </Button>
        <Button size="sm" variant="ghost" onClick={onIgnore} disabled={blocked} disabledReason={BUSY_REASON}>
          <XCircle size={14} /> Ignore
        </Button>
      </div>
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}
