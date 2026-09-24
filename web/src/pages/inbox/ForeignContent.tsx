import { Quote } from 'lucide-react'
import { fvs } from '../../design/fontWeight'
import { Markdown } from '../../ui/Markdown'
import { type InboxItem } from '../../lib/api'
import { isForeignItem } from './inboxMeta'

/** TSE2-3 — the ONE way this app renders a foreign-attributed inbox item's content.
 *
 *  The rule comes from `docs/architecture/shared-store-provider-conformance.md` clause 2:
 *  a teammate's content is FENCED and LABELLED, never trusted as the owner's intent. The
 *  server does the prompt-side half (`inbox_service.fence_message_for_prompt` wraps it in
 *  `<untrusted_content>` and appends `identity.contributor_label`); this is the same
 *  obligation on the surface a PERSON reads, because the failure mode is symmetric. A
 *  teammate's message rendered flush with the owner's own reads as something the owner
 *  wrote or agreed to — and if the owner acts on it because of how it was framed, an
 *  unfenced UI has laundered foreign content into owner intent just as surely as an
 *  unfenced prompt would.
 *
 *  Three things make it a fence rather than a decoration:
 *
 *  1. a visible attribution label carrying the same `(from <handle>)` form the server and
 *     semantic memory use — one convention, not a third;
 *  2. a quote treatment (rule + inset + muted body) so the content reads as QUOTED, the
 *     visual equivalent of the `<untrusted_content>` markers;
 *  3. an accessible name on the region, so the fence exists for a screen-reader user too
 *     rather than being a purely visual cue. An unlabelled `blockquote` would leave exactly
 *     the users who cannot see the rule with no signal at all.
 *
 *  The owner's own content is rendered PLAIN, deliberately. Fencing every row would make
 *  the fence meaningless — the same reason `identity.contributor_label` labels only foreign
 *  records — so this component renders its children untouched when the item is not foreign.
 *  That also keeps a solo install pixel-identical to what it was before attribution existed.
 */
export function ForeignContent({ item, owner, children }: {
  item: InboxItem
  owner: string
  children: React.ReactNode
}) {
  if (!isForeignItem(item, owner)) return <>{children}</>
  const who = item.owner_username || 'someone else'
  return (
    <div role="group" aria-label={`Content from ${who} — quoted, not your own`}
      className="rounded-md border-l-2 border-warn/50 bg-surface-container/60 pl-m pr-m py-2">
      <div data-type="caption" className="mb-1 inline-flex items-center gap-1.5 text-on-surface-var" style={fvs(600)}>
        <Quote size={11} aria-hidden />
        {/* The exact form the server's `contributor_label` emits, so the same string a
            reviewer greps for appears on both sides of the wire. */}
        <span>(from {who})</span>
      </div>
      <div className="text-on-surface-var">{children}</div>
    </div>
  )
}

/** The row-level twin: a compact `(from X)` chip for a list row, or null when not foreign.
 *
 *  A list row shows a one-line PREVIEW rather than the content, so the full quote treatment
 *  above would be wrong at that size — but the row must still say whose item it is, or the
 *  label would only appear after the user opened it, which is after they have already read
 *  it as the owner's. */
export function ForeignBadge({ item, owner }: { item: InboxItem; owner: string }) {
  if (!isForeignItem(item, owner)) return null
  const who = item.owner_username || 'someone else'
  return (
    <span data-type="caption"
      className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-5 text-on-surface-var"
      title={`Attributed to ${who} — quoted, not your own`}>
      <Quote size={10} aria-hidden /> from {who}
    </span>
  )
}

/** The message body, fenced when foreign. Used by the detail panel so the panel and the
 *  server agree about which items get a fence. */
export function InboxMessageBody({ item, owner }: { item: InboxItem; owner: string }) {
  return (
    <ForeignContent item={item} owner={owner}>
      <Markdown>{item.message}</Markdown>
    </ForeignContent>
  )
}
