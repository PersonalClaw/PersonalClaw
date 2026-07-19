import { useEffect, useState } from 'react'
import { Sparkles, Send, Check, XCircle, BellOff, Loader2, Star } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { TextArea, Segmented } from '../tasks/formControls'
import { api, type InboxItem, type InboxClassification } from '../../lib/api'
import { classMeta, confMeta, statusMeta, channelLabel, sourceLabel, relPast, CLASSIFICATIONS } from './inboxMeta'

/** Inbox item triage panel: the full message + thread context, the triage
 *  verdict (classification + confidence), the AI-drafted reply (generate / edit),
 *  and triage actions. Sending a reply depends on the source provider supporting
 *  it (filesystem/Slack-bot don't here) — Send is shown but gated. */
export function InboxDetail({ item, onChanged }: { item: InboxItem; onChanged: () => void }) {
  const [draft, setDraft] = useState(item.draft ?? '')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const cm = classMeta(item.classification)
  const cf = confMeta(item.confidence)

  useEffect(() => { setDraft(item.draft ?? ''); setErr('') }, [item.id])

  async function patch(body: Record<string, unknown>, tag: string) {
    setBusy(tag); setErr('')
    try { await api.updateInboxItem(item.id, body); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Update failed') } finally { setBusy(null) }
  }
  async function generate() {
    setBusy('draft'); setErr('')
    try { const u = await api.draftInboxReply(item.id); setDraft(u.draft ?? ''); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Draft failed') } finally { setBusy(null) }
  }
  async function send() {
    if (!draft.trim()) { setErr('Write a reply first'); return }
    setBusy('send'); setErr('')
    try { await api.sendInboxReply(item.id, draft.trim()); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Send failed') } finally { setBusy(null) }
  }
  async function fav() {
    setBusy('fav'); setErr('')
    try { await api.favoriteInboxItem(item.id, !item.favorited); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Favorite failed') } finally { setBusy(null) }
  }

  const dirtyDraft = draft !== (item.draft ?? '')
  const canReply = item.can_reply ?? false

  return (
    <div className="flex flex-col gap-l">
      {/* triage verdict */}
      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${cm.tone} 16%, transparent)`, color: cm.tone }}><cm.icon size={13} /> {cm.label}</span>
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${cf.tone} 16%, transparent)`, color: cf.tone }}><cf.icon size={13} /> {cf.label}</span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]">{(() => { const sm = statusMeta(item.status); return <><sm.icon size={13} style={{ color: sm.tone }} /> {sm.label}</> })()}</span>
      </div>

      {/* sender + channel + source */}
      <div className="flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low text-[0.8125rem]">
        <span className="text-on-surface" style={{ fontVariationSettings: '"wght" 600' }}>{item.sender_name || item.sender_id}</span>
        {channelLabel(item) && <span>{channelLabel(item)}</span>}
        <span className="inline-flex items-center rounded-pill bg-surface-high px-2 h-5 text-[0.7rem] text-on-surface-var">via {sourceLabel(item.source)}</span>
        {item.created_at && <span>{relPast(item.created_at)}</span>}
      </div>

      {/* the message */}
      <div className="rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.9375rem] leading-relaxed"><Markdown>{item.message}</Markdown></div>

      {/* thread context */}
      {(item.thread_context?.length ?? 0) > 0 && (
        <Section label={`Thread context · ${item.thread_context!.length}`}>
          <div className="flex flex-col gap-2">
            {item.thread_context!.map((t, i) => (
              <div key={i} className="rounded-md bg-surface-container/60 px-m py-1.5">
                <div className="text-on-surface-var text-[0.75rem] mb-0.5" style={{ fontVariationSettings: '"wght" 600' }}>{t.sender_name || 'Unknown'}</div>
                <div className="text-on-surface-low text-[0.8125rem] leading-relaxed">{t.text}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {item.context_summary && (
        <Section label="Context the agent used">
          <p className="text-on-surface-var text-[0.8125rem] leading-relaxed italic">{item.context_summary}</p>
        </Section>
      )}

      {/* reclassify */}
      <Section label="Reclassify">
        <Segmented options={CLASSIFICATIONS.map((c) => ({ key: c.key, label: c.label, tone: c.tone, icon: c.icon }))}
          value={item.classification} onChange={(v) => patch({ classification: v as InboxClassification }, 'class')} />
      </Section>

      {/* drafted reply */}
      <Section label="Drafted reply">
        <TextArea value={draft} onChange={setDraft} rows={5} placeholder="No draft yet — generate one or write your own." ariaLabel="Drafted reply" />
        <div className="mt-2 flex flex-wrap items-center gap-s">
          <Button size="sm" variant="secondary" onClick={generate} disabled={busy === 'draft'}>{busy === 'draft' ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} {item.draft ? 'Regenerate' : 'Generate draft'}</Button>
          {dirtyDraft && <Button size="sm" variant="ghost" onClick={() => patch({ draft }, 'savedraft')} disabled={busy === 'savedraft'}><Check size={14} /> Save draft</Button>}
          {canReply ? (
            <Button size="sm" onClick={send} disabled={busy === 'send' || !draft.trim()}>{busy === 'send' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Send reply</Button>
          ) : (
            <span title="This item's source doesn't support replies (notifications are read-only)." className="inline-flex"><Button size="sm" variant="ghost" disabled><Send size={14} /> Send reply</Button></span>
          )}
        </div>
      </Section>

      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {/* triage actions */}
      <div className="flex flex-wrap items-center gap-s border-t border-outline-variant/40 pt-l">
        <Button size="sm" variant="secondary" onClick={() => patch({ status: 'handled' }, 'handled')} disabled={!!busy}><Check size={14} /> Mark handled</Button>
        <Button size="sm" variant="ghost" onClick={() => patch({ status: 'dismissed' }, 'dismiss')} disabled={!!busy}><XCircle size={14} /> Dismiss</Button>
        <Button size="sm" variant="ghost" onClick={() => patch({ mute_thread: true }, 'mute')} disabled={!!busy}><BellOff size={14} /> Mute thread</Button>
        {/* P11: favorite toggle — a strong engagement signal (boosts this channel/sender
            in the ranking when engagement ranking is enabled) + a persisted star. Uses the
            dedicated /favorite endpoint so the signal is recorded, not just the flag set. */}
        <Button size="sm" variant="ghost" onClick={fav} disabled={!!busy}>
          <Star size={14} className={item.favorited ? 'fill-current text-warning' : ''} />
          {item.favorited ? 'Favorited' : 'Favorite'}
        </Button>
      </div>
    </div>
  )
}

function Section({ label, right, children }: { label: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-s">
        <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{label}</span>
        {right}
      </div>
      {children}
    </div>
  )
}
