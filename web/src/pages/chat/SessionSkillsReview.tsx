import { useEffect, useState } from 'react'
import { useFocusTrap } from '../../ui/useFocusTrap'
import { fvs } from '../../design/fontWeight'
import { GraduationCap, Loader2, Check, X, Pencil } from 'lucide-react'
import { api, type EphemeralDraft } from '../../lib/api'
import { Button } from '../../ui/Button'

/** Session-skills review (skill-ephemeral-promotion).
 *
 *  When the agent captured skills this session via `skill_remember`, they live as
 *  drafts until reviewed. This surfaces a subtle "N session skills to review" chip
 *  once a turn settles; clicking opens a modal to promote each to a tier
 *  (this agent / all agents), edit it inline, or forget it. Nothing lands in the
 *  permanent library without this explicit choice. Renders nothing when there are
 *  no drafts, so it's invisible in the common case. */
export function SessionSkillsReview({ sessionKey, agent, refreshKey }: {
  sessionKey: string
  agent?: string
  refreshKey: number
}) {
  const [drafts, setDrafts] = useState<EphemeralDraft[]>([])
  const [open, setOpen] = useState(false)

  const load = () => {
    if (!sessionKey) return
    api.ephemeralSkills(sessionKey).then(setDrafts).catch(() => setDrafts([]))
  }
  // Re-check after each settled turn (refreshKey bumps when streaming ends).
  useEffect(load, [sessionKey, refreshKey])

  if (drafts.length === 0) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-pill bg-surface-container px-3 h-7 text-[0.75rem] text-on-surface-low hover:text-on-surface transition-colors"
        title="Review skills taught this session"
      >
        <GraduationCap size={14} style={{ color: 'var(--color-primary)' }} />
        {drafts.length} session skill{drafts.length === 1 ? '' : 's'} to review
      </button>
      {open && (
        <SessionSkillsModal
          sessionKey={sessionKey}
          agent={agent}
          drafts={drafts}
          onClose={() => setOpen(false)}
          onChanged={load}
        />
      )}
    </>
  )
}

function SessionSkillsModal({ sessionKey, agent, drafts, onClose, onChanged }: {
  sessionKey: string
  agent?: string
  drafts: EphemeralDraft[]
  onClose: () => void
  onChanged: () => void
}) {
  // The scrim closes this on click, which is a MOUSE-only exit. Escape gives the keyboard the same
  // way out — the convention 39 sites in this app already follow (`ui/Popover` documents it).
  //
  // 🔑 AND THE TRAP COMPLETES THAT CONTRACT. Escape gave the keyboard a way OUT; nothing kept it IN.
  // Measured before this: with the review open, Tab walked straight into the chat composer behind the
  // scrim — a user could type into a surface they cannot see. The ref goes on the CARD rather than the
  // scrim, because the card is the dialog; the scrim is only the click target that dismisses it.
  //
  // 🪤 The hook engages ONLY because this component mounts WITH the overlay (`{open && <…>}` above).
  // Its effect reads `ref.current`, so putting the same hook in an always-mounted parent that toggles
  // an `open` flag internally leaves it silently inert — that is exactly how `app/CommandPalette.tsx`
  // shipped an inert trap, and the reason this is a separate component and not inlined JSX.
  const trapRef = useFocusTrap<HTMLDivElement>()
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div ref={trapRef} className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center gap-2">
          <GraduationCap size={18} style={{ color: 'var(--color-primary)' }} />
          <h2 className="text-on-surface text-[1.0625rem]" style={fvs(600)}>Skills taught this session</h2>
        </div>
        <p className="mb-4 text-on-surface-low text-[0.8125rem]">
          Keep any of these for later? Save to just this agent, to all agents, or forget it.
        </p>
        <div className="flex flex-col gap-3">
          {drafts.map((d) => (
            <DraftCard key={d.slug} sessionKey={sessionKey} agent={agent} draft={d} onChanged={onChanged} />
          ))}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
  )
}

function DraftCard({ sessionKey, agent, draft, onChanged }: {
  sessionKey: string
  agent?: string
  draft: EphemeralDraft
  onChanged: () => void
}) {
  const [title, setTitle] = useState(draft.title)
  const [body, setBody] = useState(draft.body)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState('')

  const promote = async (scope: 'agent' | 'global') => {
    setBusy(scope)
    try {
      const r = await api.promoteEphemeralSkill(sessionKey, {
        slug: draft.slug, scope, agent: scope === 'agent' ? agent : undefined,
        title: title !== draft.title ? title : undefined,
        body: body !== draft.body ? body : undefined,
      })
      setDone(scope === 'agent' ? `Saved to ${agent || 'this agent'}` : 'Saved for all agents')
      void r; onChanged()
    } catch (e) {
      setDone(e instanceof Error ? e.message : 'Failed')
    }
    setBusy('')
  }
  const forget = async () => {
    setBusy('forget')
    try { await api.discardEphemeralSkill(sessionKey, draft.slug); setDone('Forgotten'); onChanged() }
    catch { setDone('Failed') }
    setBusy('')
  }

  if (done) {
    return (
      <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.8125rem] flex items-center gap-2">
        <Check size={14} className="text-ok" /> {title} — {done}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-3">
      {editing ? (
        <>
          <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Skill title"
            className="mb-2 h-8 w-full rounded-md bg-surface-high px-2.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} aria-label="Skill body"
            className="mb-2 w-full rounded-md bg-surface-high px-2.5 py-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </>
      ) : (
        <div className="mb-2">
          <div className="flex items-center gap-1.5">
            <span className="text-on-surface text-[0.9375rem]" style={fvs(500)}>{title}</span>
            <button type="button" onClick={() => setEditing(true)} className="text-on-surface-low hover:text-on-surface" title="Edit"><Pencil size={12} /></button>
          </div>
          <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-on-surface-low text-[0.75rem]">{body}</p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => promote('agent')} disabled={!!busy}>
          {busy === 'agent' ? <Loader2 size={13} className="animate-spin" /> : null} This agent
        </Button>
        <Button variant="secondary" size="sm" onClick={() => promote('global')} disabled={!!busy}>
          {busy === 'global' ? <Loader2 size={13} className="animate-spin" /> : null} All agents
        </Button>
        <Button variant="ghost" size="sm" onClick={forget} disabled={!!busy}>
          <X size={13} /> Forget
        </Button>
      </div>
    </div>
  )
}
