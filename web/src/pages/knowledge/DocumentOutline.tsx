import { useEffect, useRef } from 'react'
import { List } from 'lucide-react'
import { Button } from '../../ui/Button'
import type { OutlineEntry } from './readingOutline'

/** The reading view's document outline panel (KL-16).
 *
 *  Rows come from {@link parseOutline}, keyed by SOURCE OFFSET rather than by a slug of the
 *  rendered heading — `readingOutline`'s docstring carries the reasoning, and the short form
 *  is that two `## Setup` sections have to stay two rows. This component adds nothing to that
 *  contract: it displays entries, reports a click, and keeps the active row visible.
 *
 *  🔑 WHAT IT OWNS AND WHAT IT DOES NOT. `activeOffset` is decided OUTSIDE — by the reader's
 *  rect-based scroll spy — and scrolling the ARTICLE to a heading is the caller's `onSelect`.
 *  The only scrolling done here is of this panel's own list, so the row a reader is on cannot
 *  sit below the fold of the outline while the prose beside it is correct.
 *
 *  🔑 INDENT COMES FROM `depth`, WHICH IS ALREADY RELATIVE. The parser normalises to the
 *  shallowest heading present, so a document whose top level is `##` renders flat here with no
 *  special case — and the indent is one computed value, not a class per level (a `pl-{n}`
 *  ladder would be a dozen utilities that only differ by a number the data already carries).
 *
 *  🔑 THE ROW IS `ui/Button`, NOT A HAND-ROLLED BUTTON ELEMENT.
 *  `design/primitiveAdoption.test.ts` ratchets raw button elements outside `ui/` and its
 *  baseline sits AT the live count (265), so a bespoke row would turn that rail red on its
 *  own. The shape here is `workflows/OutboxPanel`'s selected-row idiom — the same problem,
 *  already solved with the primitive.
 *
 *  🪤 AND THAT RATCHET COUNTS PROSE. Its scan is a plain `text.match` over the whole file with
 *  no comment stripping, so an earlier draft of THIS paragraph — which spelled the tag it was
 *  explaining — pushed the count to 267 and failed the rail from a docstring. Hence the
 *  circumlocution above; it is deliberate, not squeamishness.
 *
 *  ⚠️ AND THAT IS WHY THE STATE IS `aria-pressed` RATHER THAN `aria-current`. `aria-current`
 *  is the honest attribute for "the section you are reading" — it is what `ui/NavRail` and
 *  `files/browse/FileTree` use for the thing you are on — but `ui/Button` declares its aria
 *  surface as `ariaLabel`/`ariaExpanded`/`ariaPressed` and spreads no rest props, so there is
 *  no route to it without editing the primitive. `ariaPressed` is the state the primitive
 *  does carry for "a list row that stays chosen" (its own doc's words), so the row announces
 *  its state instead of announcing nothing. Giving `ui/Button` an `ariaCurrent` prop is the
 *  follow-up that makes this exact, and it belongs in `ui/`, not here.
 */
export function DocumentOutline({ entries, activeOffset, onSelect }: {
  entries: OutlineEntry[]
  /** The offset of the entry the reader is currently in, or `null` when none is. */
  activeOffset: number | null
  onSelect: (entry: OutlineEntry) => void
}) {
  const rows = useRef(new Map<number, HTMLLIElement>())

  // 🔑 `[activeOffset]` IS THE GUARD, AND IT IS THE WHOLE GUARD. A rect-based scroll spy
  // re-renders its parent on every scroll frame, so an unguarded `scrollIntoView` here would
  // fire dozens of times a second and fight a reader scrolling the outline itself. The
  // dependency array already stops that: React skips the effect when the value is unchanged.
  //
  // 🪤 A `useRef` remembering "the offset we last scrolled for" was written here first and
  // DELETED after a falsification run — removing it changed no test, because the dependency
  // array had already made it unreachable. It read as the mechanism doing the work while doing
  // nothing, which is the shape worth not shipping.
  useEffect(() => {
    if (activeOffset === null) return
    // `block: 'nearest'` is the app's 13-site idiom (SlashMenu, MentionMenu, CommandPalette,
    // Combobox…): it moves the list by the minimum needed, so a row already in view does not
    // jump, and there is no motion to reduce. Optional-called because jsdom implements no
    // `scrollIntoView` — same guard `ui/Combobox` uses, so a caller's render test does not
    // crash on it.
    rows.current.get(activeOffset)?.scrollIntoView?.({ block: 'nearest' })
  }, [activeOffset])

  // A heading with no text (`##` alone is a legal, empty heading) is kept by the parser so
  // document order stays true, but it cannot be a row: there is nothing to read and a button
  // with no children has no accessible name at all.
  const shown = entries.filter((e) => e.text)
  // No headings, no panel — an outline with empty chrome is a promise the body cannot keep.
  if (!shown.length) return null

  return (
    <nav aria-label="Document outline" className="flex min-h-0 flex-col">
      <div className="mb-1.5 flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
        <List size={12} />Outline
      </div>
      {/* `gap-xs`, NOT `gap-2xs`. The tree's dense-list idiom (7 files under pages/workflows)
          writes `gap-2xs`, and `--spacing-2xs` DOES NOT EXIST — tokens.css defines
          xs/s/m/l/xl/2xl/3xl — so Tailwind emits no rule for it and the gap is silently 0.
          Confirmed against the built bundle: `.gap-2xs{` is absent, `.gap-xs{` is present.
          `design/inertUtilities.test.ts` cannot see this family: it scans text-/bg-/border-
          prefixes only. */}
      <ol className="flex min-h-0 flex-1 flex-col gap-xs overflow-y-auto">
        {shown.map((e) => (
          <li key={e.offset} ref={(el) => { if (el) rows.current.set(e.offset, el); else rows.current.delete(e.offset) }}>
            <Button
              variant={e.offset === activeOffset ? 'tonal' : 'ghost'}
              size="xs"
              shape="squircle"
              onClick={() => onSelect(e)}
              ariaPressed={e.offset === activeOffset}
              // The row truncates, so the full heading has to stay recoverable somewhere —
              // otherwise assistive tech is the only reader getting the whole name.
              title={e.text}
              // `!justify-start` and not `justify-start`: the primitive's own class list sets
              // `justify-center`, so which one lands is decided by Tailwind's stylesheet ORDER
              // rather than by the order written here — the hazard `ui/Button`'s docstring
              // names for its colour utilities. Today's bundle happens to emit `.justify-start`
              // after `.justify-center` (measured), so the plain form works by luck. The
              // important variant is `settings/MemoryPanel`'s form for the same override.
              className="w-full !justify-start px-2"
            >
              <span className="flex-1 truncate text-left" style={{ paddingInlineStart: e.depth * 12 }}>{e.text}</span>
            </Button>
          </li>
        ))}
      </ol>
    </nav>
  )
}
