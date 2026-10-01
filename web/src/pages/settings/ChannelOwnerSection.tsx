import { useCallback, useEffect, useState } from 'react'
import { Copy, KeyRound, Loader2, UserCheck, UserX } from 'lucide-react'
import { api, type ChannelOwnerStatus } from '../../lib/api'
import { untilSentence } from '../../lib/epoch'
import { copyText } from '../../app/clipboard'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { FieldError } from '../../ui/forms'
import { channelPerson, ownerWho } from './channelPerson'

/** How often the page re-reads the pairing while a code is showing. */
const POLL_MS = 2000

const msg = (e: unknown) => String((e as Error)?.message || e)

const ownerOf = (s: ChannelOwnerStatus) => ({ id: s.owner_id, source: s.source, name: s.owner_name })

/** What the channel knows about you, in words: who it reaches you as, or that it can't. */
function ownerLine(s: ChannelOwnerStatus): string {
  const channel = s.display_name
  if (!s.owner_id) return `${channel} doesn't know who you are yet, so nothing your agent sends you can reach you there.`
  const who = ownerWho(channel, ownerOf(s))
  if (s.source === 'shared') {
    // A shared id this channel knows a name for is someone here, so it is not "another app's".
    return s.owner_name
      ? `${channel} reaches you as ${who}, by the owner id every channel used to share.`
      : `${channel} reaches you as ${who}, the owner id every channel used to share. It may be another app's id.`
  }
  return `${channel} reaches you as ${who}.`
}

/** Why the last pairing ended, when it did not end in a pairing. */
function endedLine(s: ChannelOwnerStatus): string {
  const p = s.pairing
  // The owner line above already says who; this is the confirmation that the pairing did it.
  if (p.ended === 'paired') return 'Paired.'
  if (p.ended === 'expired') return 'The code expired before it was sent. Pair again for a new one.'
  if (p.ended === 'cancelled') return 'Pairing cancelled.'
  if (p.ended === 'too_many_attempts') return 'The code was cancelled after too many wrong codes were sent to the bot. Pair again for a new one.'
  return ''
}

/** A chat channel's owner, on its Configure page.
 *
 *  The owner id is who the gateway sends your results, scheduled messages and approval prompts to on
 *  this channel. It was set only by `personalclaw setup`, so a channel set up here reached nobody.
 *  Pairing shows a code; whoever sends it to the bot in a direct message becomes the owner, stored
 *  under this channel's own key, and the page follows the pairing until it ends. A channel where
 *  the code is sent some other way (a mail to the mailbox) says how (`pairing_hint`).
 *
 *  `channel` is the channel's runtime name (`telegram`), not its app's. */
export function ChannelOwnerSection({ channel, onChanged }: { channel: string; onChanged?: () => void }) {
  const [status, setStatus] = useState<ChannelOwnerStatus | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState<'' | 'start' | 'cancel'>('')
  const [said, setSaid] = useState('')

  const load = useCallback(async () => {
    try {
      const s = await api.channelOwner(channel)
      setStatus(s); setLoadErr('')
      return s
    } catch (e) {
      setLoadErr(msg(e))
      return null
    }
  }, [channel])

  useEffect(() => { void load() }, [load])

  // While a code is showing, follow the pairing until it ends — paired, expired, cancelled.
  useEffect(() => {
    if (!code) return
    const timer = setInterval(() => {
      void load().then((s) => {
        if (!s || s.pairing.active) return
        setCode('')
        setSaid(endedLine(s))
        if (s.pairing.ended === 'paired') onChanged?.()
      })
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [code, load, onChanged])

  const start = async () => {
    setBusy('start'); setErr(''); setSaid('')
    try {
      const r = await api.startChannelOwnerPairing(channel)
      setCode(r.code)
      setStatus((s) => (s ? { ...s, pairing: r.pairing } : s))
    } catch (e) {
      setErr(`Couldn't start pairing: ${msg(e)}`)
    } finally {
      setBusy('')
    }
  }

  const cancel = async () => {
    setBusy('cancel'); setErr('')
    try {
      await api.cancelChannelOwnerPairing(channel)
      setCode('')
      const s = await load()
      if (s) setSaid(endedLine(s))
    } catch (e) {
      setErr(`Couldn't cancel pairing: ${msg(e)}`)
    } finally {
      setBusy('')
    }
  }

  if (!status && loadErr) return <FieldError>{`Couldn't read this channel's owner: ${loadErr}`}</FieldError>
  if (!status) return <div data-type="body-s" className="text-on-surface-low">Checking who this channel reaches…</div>

  const who = status.display_name
  const Icon = status.owner_id ? UserCheck : UserX
  const detail = status.owner_id ? channelPerson(who, status.owner_id, status.owner_name).detail : ''
  return (
    <section role="region" aria-label={`${who} owner`} className="flex flex-col gap-s rounded-md border border-outline-variant bg-surface-high p-m">
      <div data-type="label-l" className="text-on-surface">Owner</div>
      <div data-type="body-s" className="flex items-start gap-s text-on-surface-var">
        <Icon size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
        <div className="flex min-w-0 flex-col">
          <span>{ownerLine(status)}</span>
          {/* With a name to lead with, the platform's id for the owner is the quieter detail under
              it: the name is the one the sender chose, the id is who the channel actually reaches.
              With no name, the sentence above already says the id. */}
          {detail && (
            <span data-type="caption" className="flex items-center gap-xs text-on-surface-low">
              <span className="break-all">{detail}</span>
              <IconButton icon={Copy} size={24} iconSize={12} label={`Copy your ${who} id`}
                onClick={() => void copyText(status.owner_id, `your ${who} id`)} />
            </span>
          )}
        </div>
      </div>

      {code ? (
        <div className="flex flex-col gap-s rounded-md bg-surface-container p-m">
          <div data-type="body-s" className="text-on-surface">
            {status.pairing_hint ? `${status.pairing_hint}:` : `Send this code to your bot in a direct message on ${who}:`}
          </div>
          <div className="flex items-center gap-s">
            <span role="img" data-type="headline-s" className="font-mono tracking-[0.2em] text-on-surface" aria-label={`Pairing code ${code.split('').join(' ')}`}>{code}</span>
            <Button size="xs" variant="ghost" onClick={() => void copyText(code, 'the pairing code')} ariaLabel="Copy the pairing code">
              <Copy size={12} /> Copy
            </Button>
          </div>
          <div data-type="caption" className="text-on-surface-low">
            {untilSentence('It works once', status.pairing.expires_at)} Whoever sends it becomes {who}'s owner
            {status.owner_id ? `, in place of ${ownerWho(who, ownerOf(status))}` : ''}.
          </div>
          <div className="flex items-center gap-s">
            <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low">
              <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Waiting for your message…
            </span>
            <Button size="xs" variant="ghost" onClick={cancel} loading={busy === 'cancel'} className="ml-auto">Cancel</Button>
          </div>
        </div>
      ) : status.pairing_supported ? (
        <div className="flex flex-col gap-s">
          {/* The code is shown once, so a page opened while one is outstanding cannot show it
              again — it says so, and offers the two ways out. */}
          {status.pairing.active && (
            <div data-type="caption" className="text-on-surface-low">
              {untilSentence('A pairing code is still waiting to be sent', status.pairing.expires_at)}
              {' '}Pairing again replaces it.
            </div>
          )}
          <div className="flex items-center gap-s">
            <Button size="sm" variant="tonal" onClick={start} loading={busy === 'start'}>
              <KeyRound size={14} /> {status.owner_id ? 'Pair a new owner' : 'Pair as owner'}
            </Button>
            {status.pairing.active && (
              <Button size="sm" variant="ghost" onClick={cancel} loading={busy === 'cancel'}>Cancel that code</Button>
            )}
          </div>
        </div>
      ) : (
        <div data-type="caption" className="text-on-surface-low">{who} can't pair its owner from here.</div>
      )}

      {/* How the pairing ended, said ONCE: this live region is also the line you see. Always
          mounted, since a live region created with its text is not reliably announced, and kept
          out of the layout while it is empty. (A code on show always has `said` empty.) */}
      <div role="status" aria-live="polite" data-type="caption" className={said ? 'text-on-surface-var' : 'sr-only'}>{said}</div>
      {err && <FieldError>{err}</FieldError>}
    </section>
  )
}
