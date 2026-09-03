import type { ReactNode, ThHTMLAttributes, TdHTMLAttributes, HTMLAttributes } from 'react'
import { cx } from './cx'

/* The one canonical data-table family (audit AB-3). Eleven pages hand-rolled
 * `<table>` markup that drifted on the parts a reader depends on: some dropped
 * the sr-only caption (a screen reader announces an anonymous grid), some
 * dropped `scope="col"` (column headers stop associating with their cells),
 * and cell padding/type-size diverged file by file. This family carries the
 * sanctioned treatment — the shape `learning/AblationPanel.tsx` already had
 * right — in one place:
 *
 *   <Table caption="What this table holds">   ← caption REQUIRED, sr-only
 *     <THead>                                  ← muted header row
 *       <tr><Th>Name</Th><Th align="right">Count</Th></tr>
 *     </THead>
 *     <tbody>
 *       <tr><Td>…</Td><Td align="right">…</Td></tr>
 *     </tbody>
 *   </Table>
 *
 * Layout (which column is right-aligned, row inks, zebra striping) stays with
 * each consumer; the family owns only the semantics and the shared treatment.
 * The wrapper div provides `overflow-x-auto` so narrow viewports scroll the
 * table instead of breaking the page grid. */

export function Table({ caption, sized = true, className, wrapClassName, children }: {
  /** One sentence naming what the table holds — rendered sr-only, so a screen
   *  reader announces the table's purpose before its grid. Required: an
   *  anonymous data grid is the drift this family retires. */
  caption: string
  /** Emit the seed type size (text-[0.75rem]). Default true; set false when the
   *  table genuinely reads at another size and bring your own text utility —
   *  cx is a plain joiner, so two text-size utilities would race. */
  sized?: boolean
  /** Utilities for the <table> itself. Defaults carry the seed treatment:
   *  full width, caption-tier text. */
  className?: string
  /** Utilities for the scrolling wrapper (e.g. a surface fill or rounding). */
  wrapClassName?: string
  children: ReactNode
}) {
  return (
    <div className={cx('overflow-x-auto', wrapClassName)}>
      <table className={cx('w-full', sized && 'text-[0.75rem]', className)}>
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  )
}

/** Header block: emits <thead> with the muted header-row ink applied to its
 *  row(s). Put the <tr> inside so multi-row headers stay expressible. */
export function THead({ className, children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cx('text-on-surface-low', className)} {...rest}>{children}</thead>
}

/** Column header cell: always `scope="col"` (the association a hand-rolled
 *  <th> kept dropping), left-aligned by default, seed padding. A consumer with
 *  genuinely different cell metrics sets `pad={false}` and brings its own
 *  padding utilities — cx is a plain joiner, so emitting both paddings would
 *  let them race on stylesheet order. */
export function Th({ align = 'left', pad = true, className, children, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & {
  align?: 'left' | 'right' | 'center'
  pad?: boolean
}) {
  return (
    <th scope="col"
      className={cx(pad && 'px-m py-s', align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left', className)}
      {...rest}>
      {children}
    </th>
  )
}

/** Data cell: seed padding (opt out with `pad={false}`, mirroring Th), alignment
 *  mirroring its column's Th. */
export function Td({ align = 'left', pad = true, className, children, ...rest }: TdHTMLAttributes<HTMLTableCellElement> & {
  align?: 'left' | 'right' | 'center'
  pad?: boolean
}) {
  return (
    <td className={cx(pad && 'px-m py-s', align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left', className)}
      {...rest}>
      {children}
    </td>
  )
}
