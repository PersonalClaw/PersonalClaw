/** A project's mark: a rounded tile with its initials, beside its name wherever the name is drawn.
 *
 *  One word gives its first letter, two or more the first letters of the first two words, upper
 *  case: Garden G, Trip planning TP, Taxes 2026 T2. A neutral surface step with secondary ink, and
 *  no hue, since a colour that says nothing would decorate (`web/DESIGN.md`, "semantic colours
 *  never decorate"). `aria-hidden`: the name beside it carries the meaning.
 *
 *  Two sizes: `md`, 28 px, for a list's rows and a project's page title; `sm`, 18 px, for chips,
 *  picker rows and the library's rows. The corners keep the tile's proportion and follow the
 *  owner's roundness setting (`--radius-scale`, `--radius-density`). */

/** A project name's initials, as its mark draws them. */
export function projectInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  const first = (word: string) => Array.from(word)[0] ?? ''
  return words.slice(0, 2).map(first).join('').toLocaleUpperCase()
}

const SIZES = {
  md: 'size-7 rounded-[calc(9px*var(--radius-scale)*var(--radius-density))]',
  sm: 'size-[18px] rounded-[calc(6px*var(--radius-scale)*var(--radius-density))]',
} as const

export function ProjectMark({ name, size = 'md' }: {
  name: string
  size?: 'md' | 'sm'
}) {
  return (
    <span aria-hidden="true" data-type={size === 'md' ? 'label-s' : 'caption'}
      className={`inline-flex shrink-0 select-none items-center justify-center bg-surface-highest leading-none text-on-surface-var ${SIZES[size]}`}>
      {projectInitials(name)}
    </span>
  )
}
