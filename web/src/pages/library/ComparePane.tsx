import { useId, useState } from 'react'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { api, type KnowledgeConflict } from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { epochSeconds } from '../../lib/epoch'
import { notify } from '../../app/appSdk'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { dayWords } from './libraryMeta'

// ── Compare: two items that disagree ─────────────────────────────────────────────────
//
// Reached from either item at `?item=<ref>&compare=<conflict id>`. The pair has one id whichever item
// holds the entry, and the owner's call is kept by it, naming an item, never a side. Both
// claims stay, whatever the call.

/** The conflict reads Compare looks through. */
const CONFLICTS_READ = 500

/** One side of a pair: its item and what it says. */
interface Side { id: string; claim: string }

/** The two sides of conflict `cid`, from its entries: the holder's claim and the other item's, for
 *  every claim the two disagree on. `null` when no two items disagree under that id now. */
export function sidesOf(rows: readonly KnowledgeConflict[], cid: string): { a: string; b: string; claims: { a: string; b: string }[]; choice: string; foundIn: string } | null {
  const mine = rows.filter((r) => r.id === cid)
  if (!mine.length) return null
  const first = mine[0]
  const holder = first.item_id
  const other = first.right_item && first.right_item !== holder ? first.right_item: first.left_item !== holder ? first.left_item: ''
  if (!other) return null
  const claims = mine.map((r) => {
    // `left` is the claim that was arriving when the reading found it, so it can be either item's.
    const holderLeft = !!r.right_item && r.right_item !== r.item_id
    const h: Side = { id: r.item_id, claim: holderLeft ? r.left_claim: r.right_claim }
    const o: Side = { id: holderLeft ? r.right_item: r.left_item, claim: holderLeft ? r.right_claim: r.left_claim }
    return h.id === holder ? { a: h.claim, b: o.claim } : { a: o.claim, b: h.claim }
  })
  return { a: holder, b: other, claims, choice: first.choice ?? '', foundIn: first.item_title }
}

export function ComparePane({ cid, fromTitle, onBack, onOpen, inList = false }: {
  cid: string
  /** The item the pane was opened from, for its back link. */
  fromTitle: string
  onBack: () => void
  /** One of the Conflicts tab's list: no back link, and its heading one level down. */
  inList?: boolean
  /** Open one of the two items at its own page. */
  onOpen: (itemId: string) => void
}) {
  const headingId = useId()
  const conflicts = useQuery(`knowledge:conflicts:${CONFLICTS_READ}`, () => api.knowledgeConflicts(CONFLICTS_READ))
  const pair = conflicts.data ? sidesOf(conflicts.data.conflicts, cid) : null
  const a = useQuery(`knowledge:item:${pair?.a ?? ''}`, () => (pair ? api.knowledgeItem(pair.a) : Promise.resolve(null)))
  const b = useQuery(`knowledge:item:${pair?.b ?? ''}`, () => (pair ? api.knowledgeItem(pair.b) : Promise.resolve(null)))
  const [changing, setChanging] = useState(false)
  const [busy, setBusy] = useState('')
  const back = inList ? null : (
    <TextLink onClick={onBack} className={TOUCH_TARGET}><ArrowLeft size={14} aria-hidden /> {fromTitle || 'Back'}</TextLink>
  )
  const Heading = inList ? 'h3' : 'h2'
  if (conflicts.loading && !conflicts.data) return <div className="p-l"><ListSkeleton rows={4} what="comparison" /></div>
  if (conflicts.error && !conflicts.data) return <div className="p-l"><LoadError what="comparison" error={conflicts.error} onRetry={conflicts.refresh} /></div>
  if (!pair) {
    return (
      <div className="flex flex-col items-start gap-s p-l">
        {back}
        <p data-type="body-m" className="text-on-surface">These two items don't disagree any more.</p>
      </div>
    )
  }
  const titleOf = (id: string, q: typeof a) => q.data?.title || q.data?.ai_title || (id === pair.a ? pair.foundIn: 'Another item')
  const ta = titleOf(pair.a, a)
  const tb = titleOf(pair.b, b)
  const added = (q: typeof a) => {
    const at = epochSeconds(q.data?.created_at)
    return at === undefined ? '' : `Added ${dayWords(at * 1000)}`
  }
  const decide = async (choice: string) => {
    setBusy(choice)
    try {
      await api.decideKnowledgeConflict(cid, choice)
      invalidateKeys('knowledge', true)
      setChanging(false)
    } catch { notify("Couldn't keep your choice.", 'error') } finally { setBusy('') }
  }
  const decided = pair.choice && !changing
  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      {back}
      <header>
        <Heading id={headingId} data-type={inList ? 'title-m' : 'title-l'} className="text-on-surface">Two items disagree</Heading>
        <p data-type="caption" className="text-on-surface-low">Found when {pair.foundIn} was added</p>
      </header>
      <div className="grid grid-cols-2 gap-m max-md:grid-cols-1">
        {([[pair.a, ta, a, 'a', !!a.error && !a.data], [pair.b, tb, b, 'b', !!b.error && !b.data]] as const).map(([id, title, q, side, unread]) => (
          <div key={id} className="flex flex-col gap-s rounded-lg bg-surface-high p-m">
            <p data-type="label-l" className="text-on-surface">{title}</p>
            {/* What each one says is the conflict's own; only the title and its date are the item's. */}
            {unread && <p data-type="caption" className="text-on-surface-low">Couldn't read this item. What it says here is the disagreement's own.</p>}
            {pair.claims.map((c, i) => (
              <blockquote key={i} data-type="body-s" className="text-on-surface">“{side === 'a' ? c.a: c.b}”</blockquote>
            ))}
            <div className="flex flex-wrap items-center gap-s">
              {added(q) && <span data-type="caption" className="flex-1 text-on-surface-low">{added(q)}</span>}
              <Button variant="secondary" size="sm" onClick={() => onOpen(id)} ariaLabel={`Open ${title}`} className={TOUCH_TARGET}>
                <ExternalLink size={14} aria-hidden /> Open
              </Button>
            </div>
          </div>
        ))}
      </div>
      {decided ? (
        <p data-type="body-s" className="text-on-surface">
          {pair.choice === 'both' ? 'You said both can be right.' : `You chose to go by “${pair.choice === pair.a ? ta: tb}”.`}{' '}
          <TextLink onClick={() => setChanging(true)} className={TOUCH_TARGET}>Change</TextLink>
        </p>
      ) : (
        <div className="flex flex-col gap-s">
          <p data-type="body-s" className="text-on-surface">Both are kept. Which one should the agent go by?</p>
          <div className="flex flex-wrap gap-s">
            <Button variant="secondary" onClick={() => void decide(pair.a)} loading={busy === pair.a} className={TOUCH_TARGET}>Go by {ta}</Button>
            <Button variant="secondary" onClick={() => void decide(pair.b)} loading={busy === pair.b} className={TOUCH_TARGET}>Go by {tb}</Button>
            <Button variant="secondary" onClick={() => void decide('both')} loading={busy === 'both'} className={TOUCH_TARGET}>Both can be right</Button>
          </div>
          <p data-type="caption" className="text-on-surface-low">Your choice is kept with the two items. You can change it any time.</p>
        </div>
      )}
    </section>
  )
}
