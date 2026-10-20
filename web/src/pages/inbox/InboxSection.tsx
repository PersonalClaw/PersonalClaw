/** One labelled section of an Inbox item's detail panel: a small caps label, an optional control
 *  beside it, and its body. */
export function InboxSection({ label, right, children }: { label: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-s">
        <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">{label}</span>
        {right}
      </div>
      {children}
    </div>
  )
}
