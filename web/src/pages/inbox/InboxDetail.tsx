import { useEffect, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Sparkles, Send, Check, XCircle, BellOff, Loader2, Star, ExternalLink } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FeedbackThumbs } from '../../ui/FeedbackThumbs'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { Markdown } from '../../ui/Markdown'
import { TextArea, Segmented } from '../../ui/forms'
import { api, type InboxItem, type InboxClassification, type SkillProposalDetail } from '../../lib/api'
import { classMeta, confMeta, statusMeta, kindMeta, channelLabel, sourceLabel, relPast, CLASSIFICATIONS, NON_CHANNEL_ITEM_KINDS, refTarget, refLabel } from './inboxMeta'

/** Inbox item triage panel: the full message + thread context, the triage
 *  verdict (classification + confidence), the AI-drafted reply (generate / edit),
 *  and triage actions. Sending a reply depends on the source provider supporting
 *  it (filesystem/Slack-bot don't here) — Send is shown but gated. */
export function InboxDetail({ item, onChanged, navigate }: { item: InboxItem; onChanged: () => void; navigate: (path: string) => void }) {
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
  // A non-channel item (needs_input, proposal, …) was never triaged by the AI layer and has
  // no channel to reply into. Showing it a classification verdict, a Reclassify control, a
  // thumbs pair, a draft box or Mute thread would all be controls over something that does
  // not exist — and the thumbs would attribute a judgment no prompt ever made.
  const channelBacked = !NON_CHANNEL_ITEM_KINDS.includes(item.item_kind || 'message')
  const km = kindMeta(item.item_kind)
  const target = refTarget(item)

  return (
    <div className="flex flex-col gap-l">
      {/* triage verdict — the classification is an AI judgment: thumbs attribute
          to its bound prompt (plan 58). Digest items judge the digest instead. */}
      <div className="flex flex-wrap items-center gap-s">
        {channelBacked ? (
          <>
            <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${cm.tone} 16%, transparent)`, color: cm.tone }}><cm.icon size={13} /> {cm.label}</span>
            <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${cf.tone} 16%, transparent)`, color: cf.tone }}><cf.icon size={13} /> {cf.label}</span>
            {item.source === 'digest' ? (
              <FeedbackThumbs targetKind="inbox_digest" targetId={item.id}
                producer={item.feedback_producers?.digest}
                snapshot={{ classification: item.classification }} />
            ) : (
              <FeedbackThumbs targetKind="inbox_classification" targetId={item.id}
                producer={item.feedback_producers?.classification}
                snapshot={{ classification: item.classification, confidence: item.confidence }} />
            )}
          </>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${km.tone} 16%, transparent)`, color: km.tone }}><km.icon size={13} /> {km.label}</span>
        )}
        <span className="ml-auto inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]">{(() => { const sm = statusMeta(item.status); return <><sm.icon size={13} style={{ color: sm.tone }} /> {sm.label}</> })()}</span>
        {/* Investigate (plan 60): open a chat pre-loaded with this item's full
            context (fenced, ask mode) — "what does this message need from me?" */}
        <InvestigateButton kind="inbox_item" id={item.id} backLink="#/inbox" />
      </div>

      {/* Provenance. For a channel item that's sender + #channel; for a non-channel item
          the "sender" is the emitting subsystem, so showing it twice (as sender AND as
          "via X") plus a fake #channel would be three labels for one fact. */}
      <div className="flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low text-[0.8125rem]">
        {channelBacked && <span className="text-on-surface" style={fvs(600)}>{item.sender_name || item.sender_id}</span>}
        {channelBacked && channelLabel(item) && <span>{channelLabel(item)}</span>}
        <span className="inline-flex items-center rounded-pill bg-surface-high px-2 h-5 text-[0.75rem] text-on-surface-var">via {sourceLabel(item.source)}</span>
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
                <div className="text-on-surface-var text-[0.75rem] mb-0.5" style={fvs(600)}>{t.sender_name || 'Unknown'}</div>
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

      {/* A proposal is ANSWERABLE here — that's the point of folding it in. Approving from
          the inbox runs the same accept path the skills page uses, so there is one
          installation code path, not a second one that could drift. */}
      {item.item_kind === 'proposal' && item.refs?.skill_proposal && (
        <ProposalActions pid={item.refs.skill_proposal} onChanged={onChanged} navigate={navigate} />
      )}

      {/* Deep link — for a non-channel item this REPLACES the reply machinery as the
          primary action: the answer to "a loop needs your input" is to go to the loop.
          Skipped for proposals, which have their own actions above. */}
      {!channelBacked && target && item.item_kind !== 'proposal' && (
        <Section label="Source">
          <Button size="sm" variant="secondary" onClick={() => navigate(target)}>
            <ExternalLink size={14} /> {refLabel(item)}
          </Button>
        </Section>
      )}

      {/* Reclassify + draft are CHANNEL-message machinery: classification is the triage
          layer's verdict on an incoming message, and a draft is a reply to a sender. A
          needs_input item has neither, so these are hidden rather than shown inert. */}
      {channelBacked && (
        <>
          <Section label="Reclassify">
            <Segmented options={CLASSIFICATIONS.map((c) => ({ key: c.key, label: c.label, tone: c.tone, icon: c.icon }))}
              value={item.classification} onChange={(v) => patch({ classification: v as InboxClassification }, 'class')} />
          </Section>

          {/* drafted reply — the draft is an AI judgment: thumbs attribute to the
              inbox_draft prompt binding (plan 58). Only shown once a draft exists. */}
          <Section label="Drafted reply"
            right={item.draft ? (
              <FeedbackThumbs targetKind="inbox_draft" targetId={item.id}
                producer={item.feedback_producers?.draft}
                snapshot={{ draft_preview: (item.draft ?? '').slice(0, 200) }} />
            ) : undefined}>
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
        </>
      )}

      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {/* triage actions */}
      <div className="flex flex-wrap items-center gap-s border-t border-outline-variant/40 pt-l">
        <Button size="sm" variant="secondary" onClick={() => patch({ status: 'handled' }, 'handled')} disabled={!!busy}><Check size={14} /> Mark handled</Button>
        <Button size="sm" variant="ghost" onClick={() => patch({ status: 'dismissed' }, 'dismiss')} disabled={!!busy}><XCircle size={14} /> Dismiss</Button>
        {/* Mute thread writes to the muted-THREADS set, keyed off a channel thread id. A
            non-channel item has no thread, so the button would silently do nothing. */}
        {channelBacked && <Button size="sm" variant="ghost" onClick={() => patch({ mute_thread: true }, 'mute')} disabled={!!busy}><BellOff size={14} /> Mute thread</Button>}
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
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</span>
        {right}
      </div>
      {children}
    </div>
  )
}

/** Accept / reject a skill proposal from its inbox row.
 *
 *  Loads the FULL proposal (the list summary truncates the procedure at 280 chars) because
 *  approving something whose body you can't read is not a review. Runs the same
 *  accept/reject endpoints the skills page uses — one installation path, not a second one
 *  that could drift from it. */
function ProposalActions({ pid, onChanged, navigate }: { pid: string; onChanged: () => void; navigate: (path: string) => void }) {
  const [detail, setDetail] = useState<SkillProposalDetail | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [gone, setGone] = useState(false)

  useEffect(() => {
    let alive = true
    setErr(''); setGone(false); setDetail(null)
    api.skillProposalDetail(pid)
      .then((d) => { if (alive) setDetail(d) })
      // A 404 means it was already answered elsewhere (the skills page, or another tab).
      // That is not an error to shout about — it's a stale row, so say so plainly.
      .catch(() => { if (alive) setGone(true) })
    return () => { alive = false }
  }, [pid])

  async function act(kind: 'accept' | 'reject') {
    setBusy(kind); setErr('')
    try {
      if (kind === 'accept') await api.acceptSkillProposal(pid)
      else await api.rejectSkillProposal(pid)
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : `${kind} failed`)
    } finally { setBusy(null) }
  }

  if (gone) {
    return (
      <Section label="Proposal">
        <p className="text-on-surface-low text-[0.8125rem]">
          This proposal was already answered. <button type="button" onClick={() => navigate('skills')} className="text-primary hover:underline">Open Skills</button>
        </p>
      </Section>
    )
  }
  return (
    <Section label={detail?.kind === 'refine' ? 'Refine a skill' : 'New skill'}>
      {detail === null ? (
        <p className="text-on-surface-low text-[0.8125rem]">Loading the proposal…</p>
      ) : (
        <div className="flex flex-col gap-m">
          <div className="flex flex-wrap items-center gap-x-m gap-y-1 text-[0.8125rem]">
            <span className="text-on-surface" style={fvs(600)}>{detail.slug}</span>
            {detail.refine_target && <span className="text-on-surface-low">refines {detail.refine_target}</span>}
            {detail.triggers && <span className="text-on-surface-low">triggers: {detail.triggers}</span>}
          </div>
          {/* The full procedure — the thing actually being approved. */}
          <div className="max-h-64 overflow-auto rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.8125rem]">
            <Markdown>{detail.procedure_md}</Markdown>
          </div>
          {/* Provenance is a FENCED excerpt of the driving trace: untrusted text rendered
              for review only, so it stays visually distinct from the procedure above. */}
          {detail.source_excerpt && (
            <details>
              <summary className="cursor-pointer text-on-surface-low text-[0.75rem]">Why this was proposed</summary>
              <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface-container/60 px-m py-2 text-on-surface-low text-[0.75rem]">{detail.source_excerpt}</pre>
            </details>
          )}
          <div className="flex flex-wrap items-center gap-s">
            <Button size="sm" onClick={() => act('accept')} disabled={!!busy}>
              {busy === 'accept' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Install skill
            </Button>
            <Button size="sm" variant="ghost" onClick={() => act('reject')} disabled={!!busy}>
              <XCircle size={14} /> Reject
            </Button>
            {/* Editing before approving lives on the skills page, which has the editor. */}
            <Button size="sm" variant="ghost" onClick={() => navigate('skills')} disabled={!!busy}>
              <ExternalLink size={14} /> Edit first
            </Button>
          </div>
          {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        </div>
      )}
    </Section>
  )
}
