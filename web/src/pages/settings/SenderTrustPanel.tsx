import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, KeyRound, Loader2, MessageCircle, ShieldCheck, UserCheck, UserX, Users } from 'lucide-react'
import { api } from '../../lib/api'
import { expiryStamp, untilSentence } from '../../lib/epoch'
import type { ChannelSenderPairing, ChannelTrustProvider, ChannelTrustSeenSender, ChannelTrustSender } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { copyText } from '../../app/clipboard'
import { ConsentDeclined } from '../../lib/securityConsent'
import { confirm } from '../../ui/dialog'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, Row, SegPills } from './settingsUI'
import { Button } from '../../ui/Button'
import { EmptyState, FormSkeleton, ListRow, LoadError } from '../../ui/ListScaffold'
import { channelPerson } from './channelPerson'

const CACHE_KEY = 'settings:sender-trust'

/** How often the page re-reads the list while a pairing code is showing. */
const POLL_MS = 2000

/** How long after a code's time the page reads again: past it, so the gateway's clock agrees. */
const EXPIRY_GRACE_MS = 1000

/** The house form for a CAUGHT error (`lib/errText` takes a `Response`, not an exception). */
const msg = (e: unknown) => String((e as Error)?.message || e)

/** The channel's own name for itself, from the API. Core never names a vendor, so a channel the
 *  API has no name for renders as its key rather than as blank. */
const labelOf = (p: ChannelTrustProvider) => p.display_name || p.provider

/** How this sender came to be trusted. Rendered rather than assumed: the store records
 *  provenance precisely so the list can say *why* someone has access, and an unrecognized
 *  value renders as itself. */
function viaLabel(via: string): string {
  if (via === 'owner') return 'You allowed them'
  if (via === 'pairing') return 'Redeemed a pairing code'
  if (via === 'owner_pairing') return 'Paired as your owner'
  if (!via) return 'Source unrecorded'
  return via
}

/** The DM posture, in the owner's words. This is what happens to someone NOT on the list, and it
 *  depends on what the channel is: a bot answers a stranger with the pairing note, while a channel
 *  that sends as you sends them nothing and keeps their message in your Inbox. */
function dmPolicyLabel(p: ChannelTrustProvider, policy: string): string {
  if (p.speaks_as_owner) {
    if (policy === 'pairing') return 'A stranger is sent nothing: their message waits in your Inbox, and a pairing code you give them lets them in'
    if (policy === 'owner_only') return 'A stranger is sent nothing: their message waits in your Inbox, and only you can let them in'
    if (policy === 'open') return 'Anyone may talk to your agent, and it answers them as you'
    return policy
  }
  if (policy === 'pairing') return 'Strangers must redeem a pairing code'
  if (policy === 'owner_only') return 'Strangers are ignored silently'
  if (policy === 'open') return 'Anyone may talk to your agent'
  return policy
}

function groupPolicyLabel(policy: string): string {
  if (policy === 'tracked_only') return 'Only tracked groups are read'
  if (policy === 'off') return 'Group messages are ignored'
  return policy
}

/** The choices for people you haven't paired. A bot asks a stranger for a code; a channel that
 *  sends as you asks nobody anything, so its choices say who can let them in. */
function dmOptions(p: ChannelTrustProvider): { key: string; label: string }[] {
  return p.speaks_as_owner
    ? [
        { key: 'pairing', label: 'A code lets them in' },
        { key: 'owner_only', label: 'Only you let them in' },
        { key: 'open', label: 'Anyone' },
      ]
    : [
        { key: 'pairing', label: 'Ask for a code' },
        { key: 'owner_only', label: 'Ignore them' },
        { key: 'open', label: 'Anyone' },
      ]
}

/** Whether the page shows a channel's group rule and its groups: when the channel carries groups,
 *  or when a group is on record for it anyway, so nothing the store holds is ever hidden. */
const showsGroups = (p: ChannelTrustProvider) =>
  p.groups || (p.tracked_channels ?? []).length > 0 || (p.seen_channels ?? []).length > 0

/** The sentence over a sender's code: the channel's own when it gave one (a mail, say), else a
 *  message to you on a channel that sends as you, else a DM to the bot. */
function codeSentence(p: ChannelTrustProvider, label: string): string {
  if (p.pairing_hint) return `${p.pairing_hint}:`
  return p.speaks_as_owner
    ? `Have them send you this code on ${label}:`
    : `Have them send this code to your bot in a direct message on ${label}:`
}
// Each choice says what it does. "Tracked groups" and "None" side by side read as a label and its
// value, "Tracked groups: None", right above the groups the channel does track.
const GROUP_OPTIONS: { key: string; label: string }[] = [
  { key: 'tracked_only', label: 'Only tracked groups' },
  { key: 'off', label: 'Ignore all groups' },
]

/** An ISO-8601 timestamp as a date, or a distinct word when the store had none.
 *
 *  These are ISO STRINGS, not epoch seconds — the trust store writes `datetime.isoformat()`,
 *  so the epoch-second helpers the Devices panel uses do not apply. An empty or unparseable
 *  value reads as "date unknown" rather than being backfilled to today, which would make an
 *  ancient grant look fresh. */
function addedLabel(iso: string): string {
  if (!iso) return 'date unknown'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return 'date unknown'
  return new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** What someone unpaired sent, counted: how many messages, since when, and the last one's time.
 *  Their words reached nobody, and the page never shows them either. */
function wroteLabel(s: ChannelTrustSeenSender): string {
  const last = expiryStamp(s.last_seen) || 'time unknown'
  if (s.count <= 1) return `1 message, ${last} · not read`
  return `${s.count} messages since ${addedLabel(s.since)}, the last ${last} · not read`
}

export function SenderTrustPanel({ navigate }: { navigate?: (path: string) => void } = {}) {
  const { data, error: loadErr, refresh } = useQuery(CACHE_KEY, () => api.channelTrust(), { persist: true })
  const [revoking, setRevoking] = useState<string | null>(null)
  const [said, setSaid] = useState('')

  const revoke = async (p: ChannelTrustProvider, sender: ChannelTrustSender) => {
    const where = labelOf(p)
    const who = channelPerson(where, sender.sender_id, sender.name).name
    const ok = await confirm({
      title: `Revoke ${who}?`,
      // The claim this body makes is about what the backend actually does: `deny_sender` drops
      // the sender from `allowed_senders` and writes a `sender_denied` audit row. It does NOT
      // end a conversation already in flight, so this does not promise that.
      body: `${who} will be dropped from your ${where} allowlist and their next message will be treated as a stranger's. Any turn already running is not interrupted. They can be let back in with a new pairing code.`,
      danger: true,
      confirmLabel: 'Revoke access',
    })
    if (!ok) return
    const tag = `${p.provider}:${sender.sender_id}`
    setRevoking(tag)
    try {
      await api.revokeChannelSender(p.provider, sender.sender_id)
      notify(`${who} can no longer talk to your agent on ${where}.`, 'success')
      setSaid(`${who} revoked on ${where}.`)
      refresh()
    } catch (e) {
      notify(`Couldn't revoke ${who}: ${msg(e)}`, 'error')
    } finally {
      setRevoking(null)
    }
  }

  // Error BEFORE loading: a failed read must not shimmer forever, and must never render as
  // "nobody can reach your agent" — on a security page that reads as an all-clear.
  if (!data && loadErr) return <LoadError what="sender trust" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={2} what="sender trust" />

  const providers = data.providers
  const total = providers.reduce((n, p) => n + p.allowed_senders.length, 0)

  return (
    <div className="space-y-2xl">
      <PanelHeader
        title="Sender trust"
        hint="Who can talk to your agent through each chat channel, and what happens to someone it doesn't know. Each channel below says what a stranger gets there and how you let someone in."
      />

      {providers.length === 0 ? (
        <EmptyState
          icon={MessageCircle}
          title="No chat channel is set up"
          hint="Install one from Apps, and its people and groups show up here."
          action={navigate ? { label: 'Open Apps', onClick: () => navigate('apps') } : undefined}
        />
      ) : (
        providers.map((p) => (
          <ProviderSection
            key={p.provider}
            p={p}
            revoking={revoking}
            onRevoke={revoke}
            onChanged={refresh}
            onSaid={setSaid}
          />
        ))
      )}

      {/* Always mounted, empty at rest — a live region created together with its text is not
          reliably announced. */}
      <div role="status" aria-live="polite" className="sr-only">{said}</div>
      <div className="sr-only">{total === 1 ? '1 trusted sender in total' : `${total} trusted senders in total`}</div>
    </div>
  )
}

function ProviderSection({ p, revoking, onRevoke, onChanged, onSaid }: {
  p: ChannelTrustProvider
  revoking: string | null
  onRevoke: (p: ChannelTrustProvider, sender: ChannelTrustSender) => void
  onChanged: () => void
  onSaid: (s: string) => void
}) {
  const label = labelOf(p)
  const senders = p.allowed_senders
  const [code, setCode] = useState<ChannelSenderPairing | null>(null)
  const [busy, setBusy] = useState('')
  // Whether the list has shown this code as outstanding yet. The code goes when the list says it
  // is spent or gone, which only means something once the list has seen it at all.
  const seenLive = useRef(false)

  // While a code is showing, re-read the list until it is spent or gone: the new sender appears.
  useEffect(() => {
    if (!code) return
    const timer = setInterval(onChanged, POLL_MS)
    return () => clearInterval(timer)
  }, [code, onChanged])
  // A code made elsewhere is shown as outstanding until its time. The page reads the list again
  // then, so the line goes when the code stops working rather than at the next reload.
  useEffect(() => {
    if (code || !p.pairing_active) return
    const until = Date.parse(p.pairing_expires_at)
    if (Number.isNaN(until)) return
    const timer = setTimeout(onChanged, Math.max(0, until - Date.now()) + EXPIRY_GRACE_MS)
    return () => clearTimeout(timer)
  }, [code, p.pairing_active, p.pairing_expires_at, onChanged])
  useEffect(() => {
    if (!code) { seenLive.current = false; return }
    if (p.pairing_active) { seenLive.current = true; return }
    if (seenLive.current) {
      setCode(null)
      onSaid(`The pairing code for ${label} was used or ran out.`)
    }
  }, [code, p.pairing_active, label, onSaid])

  const setPolicy = useCallback(async (change: { dm?: string; group?: string }) => {
    setBusy(change.dm ? 'dm' : 'group')
    try {
      await api.setChannelTrustPolicies(p.provider, change)
      onSaid(change.dm
        ? `${label}: ${dmPolicyLabel(p, change.dm)}.`
        : `${label}: ${groupPolicyLabel(change.group ?? '')}.`)
      onChanged()
    } catch (e) {
      if (!(e instanceof ConsentDeclined)) notify(`Couldn't change ${label}'s rule: ${msg(e)}`, 'error')
    } finally {
      setBusy('')
    }
  }, [p, label, onChanged, onSaid])

  const pair = async () => {
    setBusy('pair')
    try {
      setCode(await api.startSenderPairing(p.provider))
      onChanged()
    } catch (e) {
      notify(`Couldn't make a pairing code for ${label}: ${msg(e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const cancelCode = async () => {
    setBusy('cancel')
    try {
      await api.cancelSenderPairing(p.provider)
      setCode(null)
      onSaid(`Pairing code for ${label} cancelled.`)
      onChanged()
    } catch (e) {
      notify(`Couldn't cancel the pairing code: ${msg(e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const groups = showsGroups(p)
  // A sender's code lets someone in only while the rule asks for one, so it is offered only then.
  const takesCodes = p.policies.dm === 'pairing'

  return (
    <Section
      title={`${label}${senders.length ? ` (${senders.length})` : ''}`}
      icon={ShieldCheck}
      // Muted, not coral: this glyph marks a CATEGORY (which channel this block is about), and
      // coral is reserved for something live or active. `sectionHeadingScale.test.tsx` holds the line.
      iconTone="muted"
      hint={`${dmPolicyLabel(p, p.policies.dm)}.${groups ? ` ${groupPolicyLabel(p.policies.group)}.` : ''}`}
    >
      <div className="space-y-3">
        <RowGroup>
          <Row
            label="People you haven't paired"
            hint={p.speaks_as_owner
              ? `Someone who writes to you on ${label} and isn't on the list below.`
              : "Someone who messages the bot directly and isn't on the list below."}
          >
            <SegPills
              value={p.policies.dm}
              onChange={(dm) => { if (dm !== p.policies.dm && !busy) void setPolicy({ dm }) }}
              options={dmOptions(p)}
              ariaLabel={`${label} ${p.speaks_as_owner ? 'messages' : 'DMs'} from people you haven't paired`}
            />
          </Row>
          {groups && (
            <Row label="Group chats" hint={`${p.speaks_as_owner ? `A group on ${label}.` : 'A group the bot is in.'} What other people write there reaches your agent as their words, not your instructions.`}>
              <SegPills
                value={p.policies.group}
                onChange={(group) => { if (group !== p.policies.group && !busy) void setPolicy({ group }) }}
                options={GROUP_OPTIONS}
                ariaLabel={`${label} group chats`}
              />
            </Row>
          )}
        </RowGroup>

        {code ? (
          <div className="flex flex-col gap-s rounded-md bg-surface-container p-m">
            <div data-type="body-s" className="text-on-surface">
              {codeSentence(p, label)}
            </div>
            <div className="flex items-center gap-s">
              <span role="img" data-type="headline-s" className="font-mono tracking-[0.2em] text-on-surface" aria-label={`Pairing code ${code.code.split('').join(' ')}`}>{code.code}</span>
              <Button size="xs" variant="ghost" onClick={() => void copyText(code.code, 'the pairing code')} ariaLabel="Copy the pairing code">
                <Copy size={12} /> Copy
              </Button>
            </div>
            <div data-type="caption" className="text-on-surface-low">
              {untilSentence('It works once', code.expires_at)} Whoever sends it can talk to your agent on {label}.
            </div>
            <div className="flex items-center gap-s">
              <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low">
                <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Waiting for it…
              </span>
              <Button size="xs" variant="ghost" onClick={cancelCode} loading={busy === 'cancel'} className="ml-auto">Cancel</Button>
            </div>
          </div>
        ) : p.pairing_active ? (
          // A code minted elsewhere (or before a reload) cannot be shown again: say it is out
          // there, and offer the way to close it.
          <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low">
            <KeyRound size={16} className="shrink-0" aria-hidden="true" />
            {/* Until a TIME: a sender's code lasts ten minutes, and a date said nothing about when. */}
            <span>
              {untilSentence(`A pairing code is outstanding for ${label}`, p.pairing_expires_at, ' until ')}
              {' '}{takesCodes
                ? 'Anyone who sends it becomes a trusted sender.'
                : `It lets nobody in while ${label}'s rule for strangers doesn't ask for a code.`}
            </span>
            <Button size="xs" variant="ghost" onClick={cancelCode} loading={busy === 'cancel'} className="ml-auto">Cancel it</Button>
          </div>
        ) : null}

        {senders.length === 0 ? (
          <EmptyState
            icon={UserCheck}
            title={`Nobody is trusted on ${label}`}
            hint={`${dmPolicyLabel(p, p.policies.dm)}.`}
            action={!code && takesCodes ? { label: 'Pair someone', onClick: () => void pair() } : undefined}
          />
        ) : (
          <>
            <RowGroup>
              {senders.map((s, i) => {
                const { name: who, detail } = channelPerson(label, s.sender_id, s.name)
                const tag = `${p.provider}:${s.sender_id}`
                return (
                  // `ListRow`, not a hand-rolled flex row: this is a RECORD row (a glyph plus two
                  // sublines plus a control), and the old bespoke container string is a ratchet
                  // pinned at its three remaining sites — a fourth copy is how that shape creeps
                  // back. The `label` is what keeps the row's accessible name the sender rather
                  // than its whole subtree.
                  <ListRow key={s.sender_id} index={i} label={who}>
                    <div className="flex items-start justify-between gap-l py-2">
                    <div className="flex min-w-0 items-start gap-3">
                      <UserCheck size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                      <div className="min-w-0">
                        <div data-type="body-s" className="truncate text-on-surface">{who}</div>
                        {/* The id is shown even when a display name exists: on most channels the
                            name is chosen by the sender, so the id is the part that identifies
                            who you are actually revoking. */}
                        {detail ? (
                          <div data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{detail}</div>
                        ) : null}
                        <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
                          {viaLabel(s.via)} · added {addedLabel(s.added_at)}
                        </div>
                      </div>
                    </div>
                    {/* The name identifies the ROW and the channel: the same sender id can be
                        trusted on two providers, and N buttons all named "Revoke" would make the
                        action ambiguous to anyone navigating by name. */}
                    <Button
                      size="xs"
                      variant="danger"
                      onClick={() => onRevoke(p, s)}
                      loading={revoking === tag}
                      ariaLabel={`Revoke ${who} on ${label}`}
                    >
                      Revoke
                    </Button>
                    </div>
                  </ListRow>
                )
              })}
            </RowGroup>
            {!code && takesCodes && (
              <Button size="sm" variant="tonal" onClick={pair} loading={busy === 'pair'}>
                <KeyRound size={14} /> Pair someone else
              </Button>
            )}
          </>
        )}

        <UnpairedSenders p={p} label={label} />

        {groups && <GroupsBlock p={p} label={label} onChanged={onChanged} onSaid={onSaid} />}
      </div>
    </Section>
  )
}

/** The people who messaged the agent and aren't paired, newest first. In the day after someone
 *  first writes they are answered at most once and you are told once (the pairing note, the
 *  notice), so what they write after that is counted here, and nobody who keeps writing goes unseen.
 *  There is nothing to grant from this list: someone is let in by a pairing code or by Allow on the
 *  notice about them. */
function UnpairedSenders({ p, label }: { p: ChannelTrustProvider; label: string }) {
  const people = p.seen_senders ?? []
  if (people.length === 0) return null
  return (
    <div role="group" aria-label={`People who messaged your agent on ${label} and aren't paired`} className="space-y-xs">
      <div data-type="label-m" className="text-on-surface-var">People who messaged your agent and aren't paired</div>
      <RowGroup>
        {people.map((s, i) => {
          const { name: who, detail } = channelPerson(label, s.sender_id, s.name)
          return (
            <ListRow key={s.sender_id} index={i} label={who}>
              <div className="flex min-w-0 items-start gap-m py-s">
                <UserX size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                <div className="min-w-0">
                  <div data-type="body-s" className="truncate text-on-surface">{who}</div>
                  {detail ? (
                    <div data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{detail}</div>
                  ) : null}
                  <div data-type="caption" className="mt-0.5 text-on-surface-low/80">{wroteLabel(s)}</div>
                </div>
              </div>
            </ListRow>
          )
        })}
      </RowGroup>
    </div>
  )
}

/** The groups a channel's agent reads, and the ones that tried. Tracking is offered only for a
 *  group that already messaged the agent: its id is the vendor's, and a text field for it would
 *  be a grant nobody can check. */
function GroupsBlock({ p, label, onChanged, onSaid }: {
  p: ChannelTrustProvider
  label: string
  onChanged: () => void
  onSaid: (s: string) => void
}) {
  const tracked = p.tracked_channels ?? []
  const seen = p.seen_channels ?? []
  const [busy, setBusy] = useState('')
  const groupsOff = p.policies.group === 'off'

  const track = async (channelId: string, name: string) => {
    const group = name || channelId
    const ok = await confirm({
      title: `Track ${group}?`,
      body: `Messages in ${group} will reach your agent on ${label}. What other people write there is passed to it as their words, not your instructions.`,
      confirmLabel: 'Track group',
    })
    if (!ok) return
    setBusy(channelId)
    try {
      await api.trackChannelGroup(p.provider, channelId, name)
      onSaid(`${group} is tracked on ${label}.`)
      onChanged()
    } catch (e) {
      notify(`Couldn't track ${group}: ${msg(e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  const untrack = async (channelId: string, name: string) => {
    const group = name || channelId
    const ok = await confirm({
      title: `Stop tracking ${group}?`,
      body: `Messages in ${group} will stop reaching your agent. Anything already running is not interrupted.`,
      danger: true,
      confirmLabel: 'Stop tracking',
    })
    if (!ok) return
    setBusy(channelId)
    try {
      await api.untrackChannelGroup(p.provider, channelId)
      onSaid(`${group} is no longer tracked on ${label}.`)
      onChanged()
    } catch (e) {
      notify(`Couldn't stop tracking ${group}: ${msg(e)}`, 'error')
    } finally {
      setBusy('')
    }
  }

  if (tracked.length === 0 && seen.length === 0) {
    return (
      <div data-type="body-s" className="flex items-start gap-2 text-on-surface-low">
        <Users size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
        <span>
          No group has messaged your agent on {label} yet.{' '}
          {p.speaks_as_owner
            ? 'When one does, it shows up here to track.'
            : 'Add the bot to a group and send a message there, and the group shows up here to track.'}
        </span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {tracked.length > 0 && (
        <div role="group" aria-label={`Tracked groups on ${label}`} className="space-y-xs">
          <div data-type="label-m" className="text-on-surface-var">Tracked groups</div>
          <RowGroup>
            {tracked.map((g, i) => (
              <ListRow key={g.channel_id} index={i} label={g.name || g.channel_id}>
                <div className="flex items-center justify-between gap-l py-2">
                  <div className="min-w-0">
                    <div data-type="body-s" className="truncate text-on-surface">{g.name || g.channel_id}</div>
                    <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
                      {g.name ? `${g.channel_id} · ` : ''}tracked {addedLabel(g.added_at)}{groupsOff ? ' · groups are off, so it is not read' : ''}
                    </div>
                  </div>
                  <Button size="xs" variant="ghost" onClick={() => void untrack(g.channel_id, g.name)} loading={busy === g.channel_id}
                    ariaLabel={`Stop tracking ${g.name || g.channel_id} on ${label}`}>
                    Stop tracking
                  </Button>
                </div>
              </ListRow>
            ))}
          </RowGroup>
        </div>
      )}
      {seen.length > 0 && (
        <div role="group" aria-label={`Groups that messaged your agent on ${label}`} className="space-y-xs">
          <div data-type="label-m" className="text-on-surface-var">Groups that messaged your agent</div>
          <RowGroup>
            {seen.map((g, i) => (
              <ListRow key={g.channel_id} index={i} label={g.name || g.channel_id}>
                <div className="flex items-center justify-between gap-l py-2">
                  <div className="min-w-0">
                    <div data-type="body-s" className="truncate text-on-surface">{g.name || g.channel_id}</div>
                    <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
                      {g.name ? `${g.channel_id} · ` : ''}last message {addedLabel(g.last_seen)} · not read
                    </div>
                  </div>
                  <Button size="xs" variant="tonal" onClick={() => void track(g.channel_id, g.name)} loading={busy === g.channel_id}
                    ariaLabel={`Track ${g.name || g.channel_id} on ${label}`}>
                    Track
                  </Button>
                </div>
              </ListRow>
            ))}
          </RowGroup>
        </div>
      )}
    </div>
  )
}
