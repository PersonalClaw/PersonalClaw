import { useEffect, useState } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { motion } from 'framer-motion'
import { FolderTree, X } from 'lucide-react'
import { spring } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { api, type OrganizeProposal } from '../../lib/api'
import { notify } from '../../app/appSdk'

/** Suggested-organization chip (SESSION-MANAGEMENT T2.1) — a non-blocking pill above the
 *  composer proposing a folder and/or tags for a chat the user never organized.
 *
 *  The chip is a *proposal*: fetching and rendering it changes nothing about the session.
 *  "File it" is the only path that writes folder/tags (through the same endpoints the
 *  per-session menu uses), and ✕ records a decline so the identical suggestion never
 *  returns. Nothing here auto-applies — a user who ignores the chip keeps an untagged chat,
 *  which is a legitimate choice.
 *
 *  Suggestions come from deterministic signals (title keywords against the user's own
 *  folder/tag vocabulary, the workspace directory, channel origin); the backend consults a
 *  model only for genuinely ambiguous chats. */
export function OrganizeChip({ sessionKey, refreshKey, onApplied }: {
  sessionKey: string
  /** Bump to re-ask (e.g. after a turn completes and the title finally exists). */
  refreshKey?: number
  onApplied?: (applied: { folder_id: string; tags: string[] }) => void
}) {
  const [proposal, setProposal] = useState<OrganizeProposal | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!sessionKey) { setProposal(null); return }
    let live = true
    api.organizeSuggestion(sessionKey)
      .then((r) => { if (live) setProposal(r?.proposal ?? null) })
      .catch(() => { if (live) setProposal(null) })
    return () => { live = false }
  }, [sessionKey, refreshKey])

  if (!proposal) return null

  const accept = async () => {
    setBusy(true)
    try {
      const r = await api.organizeAccept(sessionKey, proposal)
      notify(proposal.folder_name ? `Filed in ${proposal.folder_name}` : 'Chat tagged', 'success')
      onApplied?.({ folder_id: r?.folder_id ?? '', tags: r?.tags ?? [] })
      setProposal(null)
    } catch (e) {
      notify(`Couldn't organize: ${String((e as Error)?.message || e)}`, 'error')
      setBusy(false)
    }
  }

  // 🪤 A DISMISSAL THAT DID NOT STICK FAILS LATER, NOT NOW. The decline is what suppresses the
  // proposal — `session_organize.record_decline` is documented "so it is never proposed again" — so a
  // swallowed rejection meant the chip vanished, the user believed it was settled, and the SAME
  // proposal returned on the next scan with no explanation. That is the nag the backend's own tests
  // call "worse than no feature", and its cause is invisible at the moment it is created.
  //
  // The chip still clears. A dismissal is a request to get something out of the way, so refusing to
  // hide it would fight the click; the report is what makes a later reappearance explicable. That is
  // the opposite ruling from a MIRROR of server state (`chat/approvalDecisionReported`), and
  // deliberately so — this control is not claiming a server fact, it is hiding a suggestion.
  const decline = () => {
    void reportingWrite('decline that suggestion', () => api.organizeDecline(sessionKey, proposal))
    setProposal(null)
  }

  // What is being proposed, in the user's own words: folder, tags, or both.
  const parts: string[] = []
  if (proposal.folder_name) parts.push(proposal.folder_name)
  if (proposal.tags.length) parts.push(proposal.tags.join(', '))

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
      transition={spring.spatialFast}
      className="inline-flex items-center gap-2 rounded-pill border border-outline-variant/50 bg-surface-container pl-3 pr-1.5 h-8 text-[0.8125rem]"
      role="status">
      <FolderTree size={14} style={{ color: 'var(--color-primary)' }} className="shrink-0" />
      <span className="text-on-surface-var">
        Organize this chat under{' '}
        <span className="text-on-surface" style={fvs(600)}>{parts.join(' · ')}</span>
        {proposal.reason ? ` — ${proposal.reason}?` : '?'}
      </span>
      <Button variant="secondary" size="xs" onClick={accept} loading={busy} className="h-6 px-3">File it</Button>
      <IconButton icon={X} label="No thanks (won't suggest this again)" onClick={decline} size={24} iconSize={13} />
    </motion.div>
  )
}
