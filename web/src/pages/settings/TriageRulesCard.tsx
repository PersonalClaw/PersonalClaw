import { useState } from 'react'
import { ShieldCheck, Trash2 } from 'lucide-react'
import { api, type ApprovalRuleRow } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Section, Row, Toggle } from './settingsUI'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { fvs } from '../../design/fontWeight'

/** The triage rules manager (PROACTIVE-ASSISTANT §5.2).
 *
 *  The three routes behind this card — `GET`/`POST`/`DELETE /api/memory/approval-rules` — shipped
 *  with PA-1 and had NO frontend consumer at all, which is why a user could teach a rule by
 *  replying to a digest and then never see or revoke it. This card is that missing half.
 *
 *  🪤 Two honesty rules the endpoint made possible and this card must not throw away:
 *
 *  1. `unreadable` is a FIRST-CLASS list. The endpoint reports rows it could not decode instead of
 *     dropping them, because a rule the matcher ignores but the user believes in is exactly the
 *     confusion this surface exists to end. Rendering only `rules` would recreate it.
 *  2. A failed read is not "no rules yet". An empty rules list means the machine will propose
 *     everything; a failed read means we do not know what it will do. They get different UI.
 *
 *  The graduation toggle is deliberately labelled as intent rather than as a send switch: PA-3's
 *  `inbox-op` provider has no send path in it at all (asserted by a source scan in its own
 *  tests), so flipping this on does not start sending. It marks the rule as one a send-capable
 *  provider would be allowed to honour — and the warning badge says exactly that, because a
 *  toggle that read "send replies automatically" while nothing could send would be a lie in the
 *  reassuring direction. */
export function TriageRulesCard() {
  const { data, error, refresh } = useQuery<{ rules: ApprovalRuleRow[]; unreadable: string[] }>(
    'settings:approval-rules', () => api.approvalRules(), { persist: false })
  const [busy, setBusy] = useState('')

  const revoke = async (rule: ApprovalRuleRow) => {
    const ok = await confirm({
      title: 'Revoke this rule?',
      body: `The digest will propose “${rule.pattern}” again instead of ${rule.verdict === 'deny' ? 'silently skipping it' : 'acting on it automatically'}.`,
      confirmLabel: 'Revoke',
      danger: true,
    })
    if (!ok) return
    setBusy(rule.key)
    api.revokeApprovalRule(rule.key)
      .then(() => { notify('Rule revoked.', 'success'); refresh() })
      .catch((e) => notify(`Couldn't revoke that rule: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  const graduate = (rule: ApprovalRuleRow, on: boolean) => {
    setBusy(rule.key)
    // POST is an upsert on the rule's key, so re-teaching the same pattern with a new
    // `send_capable` edits the row rather than minting a second, conflicting one.
    api.saveApprovalRule({
      pattern: rule.pattern,
      verdict: rule.verdict === 'deny' ? 'deny' : 'approve',
      scope: rule.scope,
      expires_at: rule.expires_at ?? null,
      send_capable: on,
    })
      .then(() => { notify(on ? 'This rule may now be honoured by a send-capable provider.' : 'Send capability withdrawn.', 'success'); refresh() })
      .catch((e) => notify(`Couldn't update that rule: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  return (
    <Section
      title="Triage rules"
      icon={ShieldCheck}
      iconTone="muted"
      hint="What you taught the digest by answering “always” — one row per pattern. Deny beats approve, and the most specific pattern wins."
    >
      {error && data === undefined ? (
        // NOT an empty state. "You haven't taught any rules" and "we couldn't read your rules"
        // are opposite claims about what the machine is about to do on its own.
        <LoadError what="triage rules" error={error} onRetry={refresh} />
      ) : data === undefined ? (
        <ListSkeleton rows={2} what="triage rules" />
      ) : (
        <>
          {data.unreadable.length > 0 && (
            <div className="mb-m">
              <InlineError icon>
                {data.unreadable.length} rule row{data.unreadable.length === 1 ? '' : 's'} could not be decoded, so the
                digest ignores {data.unreadable.length === 1 ? 'it' : 'them'}:{' '}
                <code className="font-mono">{data.unreadable.join(', ')}</code>
              </InlineError>
            </div>
          )}
          {data.rules.length === 0 ? (
            <p className="text-on-surface-low text-[0.8125rem]">
              You haven't taught the digest any rules yet. Answer “Always” or “Never” on a digest proposal and the
              pattern shows up here, where you can revoke it.
            </p>
          ) : (
            <ul className="flex flex-col gap-s">
              {data.rules.map((rule) => (
                <li key={rule.key} className="rounded-lg bg-surface-high px-m py-s">
                  <div className="flex flex-wrap items-center gap-s">
                    <VerdictBadge verdict={rule.verdict} />
                    <code className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.8125rem]">{rule.pattern}</code>
                    <Button size="xs" variant="ghost" loading={busy === rule.key} onClick={() => revoke(rule)}
                      title={`Revoke ${rule.pattern}`}>
                      <Trash2 size={12} /> Revoke
                    </Button>
                  </div>
                  <p className="mt-1 flex flex-wrap gap-m text-on-surface-low text-[0.75rem]">
                    <span>scope {rule.scope || 'global'}</span>
                    {/* An UNCOUNTED hit count is not zero hits. `hit_count` absent from the row means
                        the rule was written before counting existed, which must not read as "this
                        rule has never matched" — that is the signal a user prunes on. */}
                    <span>{rule.hit_count === undefined || rule.hit_count === null ? 'hits not counted' : `${rule.hit_count} hit${rule.hit_count === 1 ? '' : 's'}`}</span>
                    <span>{rule.expires_at ? `expires ${String(rule.expires_at).slice(0, 10)}` : 'no expiry'}</span>
                    {rule.created_from_digest && <span>taught from {rule.created_from_digest}</span>}
                  </p>
                  {rule.verdict === 'approve' && (
                    <div className="mt-s border-outline-variant border-t pt-s">
                      <Row
                        label="May be honoured by a send-capable provider"
                        hint="Off, an approved reply is always a DRAFT — the inbox action provider has no send path in it. On, this rule is marked as one a future send-capable provider would be allowed to act on without asking again."
                      >
                        <div className="flex items-center gap-s">
                          {rule.send_capable && (
                            <span className="rounded-full bg-warn/15 px-2 py-0.5 text-warn text-[0.75rem]" style={fvs(500)}>
                              graduated
                            </span>
                          )}
                          <Toggle
                            on={Boolean(rule.send_capable)}
                            onChange={(v) => graduate(rule, v)}
                            label={`Send capability for ${rule.pattern}`}
                            disabled={busy === rule.key}
                          />
                        </div>
                      </Row>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  )
}

function VerdictBadge({ verdict }: { verdict: ApprovalRuleRow['verdict'] }) {
  // `suppressed` is a SHADOW row the digest maintains from declines, not something the user
  // taught. Labelling it "deny" would present a cooldown as a standing decision.
  const meta = verdict === 'deny'
    ? { label: 'never', cls: 'bg-danger/15 text-on-danger-tint' }
    : verdict === 'suppressed'
      ? { label: 'cooling off', cls: 'bg-surface-highest text-on-surface-low' }
      : { label: 'always', cls: 'bg-primary/15 text-on-primary-tint' }
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[0.75rem] ${meta.cls}`} style={fvs(500)}>{meta.label}</span>
}
