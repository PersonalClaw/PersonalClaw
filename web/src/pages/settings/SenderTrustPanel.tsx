import { useState } from 'react'
import { MessageCircle, ShieldCheck, KeyRound, UserCheck } from 'lucide-react'
import { api } from '../../lib/api'
import type { ChannelTrustProvider, ChannelTrustSender } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup } from './settingsUI'
import { Button } from '../../ui/Button'
import { EmptyState, FormSkeleton, ListRow, LoadError } from '../../ui/ListScaffold'

const CACHE_KEY = 'settings:sender-trust'

/** The house form for a CAUGHT error (`lib/errText` takes a `Response`, not an exception). */
const msg = (e: unknown) => String((e as Error)?.message || e)

/** Provider display copy, keyed off the backend's OPAQUE runtime key.
 *
 *  Core deliberately does not know these names — `provider` is a string the transport picked —
 *  so this map is presentation only and an unrecognized key renders as itself rather than as
 *  blank. Note the key is the runtime name (`telegram`), not the app name (`telegram-channel`). */
const PROVIDERS: Record<string, string> = {
  telegram: 'Telegram',
  discord: 'Discord',
  slack: 'Slack',
  email: 'Email',
  'reference-echo': 'Reference (Echo)',
}

const providerLabel = (p: string) => PROVIDERS[p] ?? p

/** How this sender came to be trusted. Rendered rather than assumed: the store records
 *  provenance precisely so the list can say *why* someone has access, and an unrecognized
 *  value renders as itself. */
function viaLabel(via: string): string {
  if (via === 'owner') return 'You allowed them'
  if (via === 'pairing') return 'Redeemed a pairing code'
  if (!via) return 'Source unrecorded'
  return via
}

/** The DM posture, in the owner's words. This is what happens to someone NOT on the list. */
function dmPolicyLabel(policy: string): string {
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

export function SenderTrustPanel() {
  const { data, error: loadErr, refresh } = useQuery(CACHE_KEY, () => api.channelTrust(), { persist: true })
  const [revoking, setRevoking] = useState<string | null>(null)
  const [said, setSaid] = useState('')

  const revoke = async (provider: string, sender: ChannelTrustSender) => {
    const who = sender.name || sender.sender_id
    const where = providerLabel(provider)
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
    const tag = `${provider}:${sender.sender_id}`
    setRevoking(tag)
    try {
      await api.revokeChannelSender(provider, sender.sender_id)
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
        hint="Who is allowed to talk to your agent through a messaging channel — and the switch that cuts one off. Access is granted by a pairing code or by your Allow on an unknown-sender notification; this page is where you review and revoke it."
      />

      {providers.length === 0 ? (
        <EmptyState
          icon={MessageCircle}
          title="No channel has any trust state yet"
          hint="A channel appears here once someone messages it or you pair a sender. Run `personalclaw pair <channel>` to mint an 8-digit code."
        />
      ) : (
        providers.map((p) => <ProviderSection key={p.provider} p={p} revoking={revoking} onRevoke={revoke} />)
      )}

      {/* Always mounted, empty at rest — a live region created together with its text is not
          reliably announced. */}
      <div role="status" aria-live="polite" className="sr-only">{said}</div>
      <div className="sr-only">{total === 1 ? '1 trusted sender in total' : `${total} trusted senders in total`}</div>
    </div>
  )
}

function ProviderSection({ p, revoking, onRevoke }: {
  p: ChannelTrustProvider
  revoking: string | null
  onRevoke: (provider: string, sender: ChannelTrustSender) => void
}) {
  const label = providerLabel(p.provider)
  const senders = p.allowed_senders
  return (
    <Section
      title={`${label}${senders.length ? ` (${senders.length})` : ''}`}
      icon={ShieldCheck}
      // Muted, not coral: this glyph marks a CATEGORY (which channel this block is about), and
      // coral is reserved for something live or active. `sectionHeadingScale.test.tsx` holds the line.
      iconTone="muted"
      hint={`${dmPolicyLabel(p.policies.dm)}. ${groupPolicyLabel(p.policies.group)}.`}
    >
      <div className="space-y-3">
        {p.pairing_active && (
          <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]">
            <KeyRound size={16} className="shrink-0" aria-hidden="true" />
            <span>
              A pairing code is outstanding for {label}
              {p.pairing_expires_at ? ` until ${addedLabel(p.pairing_expires_at)}` : ''}. Anyone who
              sends it becomes a trusted sender.
            </span>
          </div>
        )}

        {senders.length === 0 ? (
          <EmptyState
            icon={UserCheck}
            title={`Nobody is trusted on ${label}`}
            hint={dmPolicyLabel(p.policies.dm) + '.'}
          />
        ) : (
          <RowGroup>
            {senders.map((s, i) => {
              const who = s.name || s.sender_id
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
                      <div className="truncate text-on-surface text-[0.8125rem]">{who}</div>
                      {/* The id is shown even when a display name exists: on most channels the
                          name is chosen by the sender, so the id is the part that identifies
                          who you are actually revoking. */}
                      {s.name ? (
                        <div className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{s.sender_id}</div>
                      ) : null}
                      <div className="mt-0.5 text-on-surface-low/80 text-[0.75rem]">
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
                    onClick={() => onRevoke(p.provider, s)}
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
        )}
      </div>
    </Section>
  )
}
