import { LoadingStatus, Skeleton } from '../../../ui/ListScaffold'

/** Message-area skeleton shown while a chat session's history loads. Alternates a
 *  right-aligned user bubble and a left-aligned assistant block so the shape reads
 *  as a conversation the instant the page paints — no bare "Loading…" text. The
 *  chrome (header + composer) is already live around it; only this area is pending. It sits at
 *  the turn column's reading width, so the turns land where their placeholders were. */
export function MessagesSkeleton() {
  const rows = [
    { me: true, w: 'w-1/3' }, { me: false, w: 'w-3/4' },
    { me: true, w: 'w-2/5' }, { me: false, w: 'w-2/3' },
  ]
  return (
    <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--reading-width)' }}
      role="status" aria-busy="true" >
        <LoadingStatus what="conversation" />
      {rows.map((r, i) => (
        <div key={i} className={`flex flex-col gap-2 ${r.me ? 'items-end' : 'items-start'}`}>
          <Skeleton className={`h-4 ${r.w} ${r.me ? 'max-w-[70%]' : ''}`} />
          {!r.me && <><Skeleton className="h-4 w-11/12" /><Skeleton className="h-4 w-4/5" /></>}
        </div>
      ))}
    </div>
  )
}
