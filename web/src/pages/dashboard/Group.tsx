import type { ReactNode } from 'react'

/** A group's heading row and its body, as Needs you, Home's Overview and a project's page draw it:
 *  an h2 under the page's h1 with its count in the heading's own text (a colour cannot say "how
 *  many"), a `label-s` low-ink span with no dot, none at 0 and none while the count isn't known, then
 *  a hairline across the width. */
export function Group({ id, label, count, children }: {
  /** The heading's id, which names the section. */
  id: string
  label: string
  count: number | null
  children: ReactNode
}) {
  return (
    <section aria-labelledby={id} data-group={id} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <h2 id={id} data-type="label-l" className="text-on-surface-var">
          {label}{count ? <> <span data-type="label-s" data-group-count className="text-on-surface-low">{count}</span></> : null}
        </h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {children}
    </section>
  )
}
