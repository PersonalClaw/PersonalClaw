import type { ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState, useId } from 'react'
import { useFocusTrap } from '../ui/useFocusTrap'
import { AnimatePresence, motion } from 'framer-motion'
import { CornerDownLeft, ArrowRight, MessageSquare, Brain, BookOpen, ListChecks } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../design/motion'
import { Eyebrow } from '../ui/Eyebrow'
import { SearchField } from '../ui/SearchField'
import { CONTENT_SOURCES, MIN_CONTENT_QUERY, SOURCE_LABEL, useContentSearch, type ContentHit, type ContentSource } from './paletteSearch'

export interface Command {
  id: string
  label: string
  hint?: string         // section / context (e.g. "Go to", "Action")
  icon: LucideIcon
  keywords?: string     // extra search terms
  run: () => void
}

const SOURCE_ICON: Record<ContentSource, LucideIcon> = {
  chats: MessageSquare, memory: Brain, knowledge: BookOpen, tasks: ListChecks,
}

/** The commands *q* finds, best first; every command when it is blank.
 *
 *  Each word of the query has to appear in the command's label, hint or keywords, so a row is
 *  found by the words it shows in any order they are typed: "Go to Discover" finds the row that
 *  reads "Discover · Go to". Matching the whole query as ONE substring of those words found
 *  nothing for that, and Enter then ran whatever the content search listed first. A label that
 *  starts with the query ranks first, then one that contains it, then one holding any of its
 *  words, then a match on the other words alone. */
export function rankCommands(commands: Command[], q: string): Command[] {
  const n = q.trim().toLowerCase().replace(/\s+/g, ' ')
  if (!n) return commands
  const words = n.split(' ')
  return commands
    .map((c) => {
      const label = c.label.toLowerCase()
      const hay = `${label} ${c.hint ?? ''} ${c.keywords ?? ''}`.toLowerCase()
      const at = label.indexOf(n)
      const score = at === 0 ? 4 : at > 0 ? 3
        : !words.every((w) => hay.includes(w)) ? 0
        : words.some((w) => label.includes(w)) ? 2 : 1
      return { c, score }
    })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((x) => x.c)
}

/** One row of the listbox: a command, or something found inside the user's content. */
type Row = { id: string; label: string; hint?: string; detail?: string; icon: LucideIcon; run: () => void }

/** ⌘K / Ctrl+K command palette — search + run navigation and actions, and search what is IN the
 *  app: chats, memory, knowledge and tasks (`paletteSearch.ts`). The single keyboard entry point
 *  (designed for THIS featureset: 16 destinations + global actions, no per-page chord soup). Opens
 *  on ⌘K, closes on Esc, arrows to move, Enter to run. Mounted once at the app shell; `navigate`
 *  is the shell's, and a content hit opens through it. */
export function CommandPalette({ commands, navigate }: { commands: Command[]; navigate: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const cpId = useId()

  // global ⌘K / Ctrl+K toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((v) => !v); setQ(''); setActive(0) }
      else if (e.key === 'Escape' && open) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 0) }, [open])

  const content = useContentSearch(q, open)

  const results = useMemo(() => rankCommands(commands, q), [q, commands])

  // Commands first, then the content hits grouped by source: ONE list for the cursor, so the arrows
  // and Enter work the same across both halves and `aria-activedescendant` can name any row.
  const contentHits = content.query === q.trim() ? content.hits : []
  const groups = CONTENT_SOURCES
    .map((source) => ({ source, hits: contentHits.filter((h) => h.source === source) }))
    .filter((g) => g.hits.length)
  const rows: Row[] = [
    ...results.map((c) => ({ id: c.id, label: c.label, hint: c.hint, icon: c.icon, run: c.run })),
    ...groups.flatMap((g) => g.hits.map((h: ContentHit) => ({
      id: h.id, label: h.label, detail: h.detail, icon: SOURCE_ICON[h.source], run: () => navigate(h.path),
    }))),
  ]
  const searchingContent = q.trim().length >= MIN_CONTENT_QUERY && (content.searching || content.query !== q.trim())
  const failures = CONTENT_SOURCES.flatMap((s) => (content.query === q.trim() && content.failures[s] ? [content.failures[s]!] : []))
  const notes = CONTENT_SOURCES.flatMap((s) => (content.query === q.trim() && content.notes[s] ? [content.notes[s]!] : []))

  // A new query puts the cursor back on the first match, IN the handler that changes the query —
  // the same place ⌘K already resets it. This was `useEffect(() => { setActive(0) }, [q])`, which
  // scheduled a render from inside every keystroke's own commit; typed fast, ~50 keys threw React's
  // "Maximum update depth exceeded" (#185). The mechanism is written up in `ui/composer/MarkdownInput`.
  const search = (v: string) => { setQ(v); setActive(0) }

  // keep the highlighted row visible as arrows move the selection
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-idx="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const run = (c?: Row) => { if (!c) return; setOpen(false); c.run() }

  const option = (row: Row, i: number) => (
    <PaletteOption key={row.id} row={row} id={`${cpId}-opt-${i}`} index={i} on={i === active}
      onHover={() => setActive(i)} onRun={() => run(row)} />
  )

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, rows.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(rows[Math.min(active, rows.length - 1)]) }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          className="fixed inset-0 z-[var(--z-toast)] flex items-start justify-center px-l pt-[14vh]"
          onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
          {/* 🔴 THE APP'S KEYBOARD FEATURE ANNOUNCED NOTHING. Measured with the palette open on
              `#/dashboard`: **0** dialogs, **0** listboxes, **0** options for 22
              commands, and `aria-activedescendant` **null** while arrowing moved a purely VISUAL
              highlight — so a screen-reader user typing here heard no overlay open, no result count and
              nothing as the selection moved. The page behind was not hidden either. WCAG 4.1.2 / 1.3.1.
              Every part of this is invisible to the audit, which only ever sees the closed shell.
              The shapes are the app's own: `ui/Modal` and `ui/dialog/DialogShell` pair
              a dialog role + `aria-modal` with `ui/useFocusTrap` (the contract
              `ui/dialogFocusContract.test.tsx` enforces), and `ui/composer/SlashMenu` +
              `ui/composer/MarkdownInput` already ship the listbox/option/activedescendant trio. */}
          <TrappedCard
            initial={{ opacity: 0, y: -10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -10, scale: 0.98 }}
            transition={spring.spatialFast}
            className="relative w-full overflow-hidden rounded-2xl border border-outline/40 bg-surface-container shadow-2xl"
            style={{ maxWidth: 560, borderRadius: 'var(--radius-xl)' }}
            onClick={(e) => e.stopPropagation()}>
            {/* search row */}
            <div className="flex items-center gap-s border-b border-outline-variant/40 px-l h-14">
              <SearchField variant="inline" inlineIconSize={17} clearable={false}
                inputRef={inputRef} value={q} onChange={search} onKeyDown={onKeyDown}
                placeholder="Search pages, actions and content…" ariaLabel="Search pages, actions and content"
                ariaHasPopup="listbox" ariaControls={`${cpId}-list`}
                ariaActiveDescendant={rows.length ? `${cpId}-opt-${Math.min(active, rows.length - 1)}` : undefined}
                trailingSlot={<kbd className="rounded-md bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-low text-[0.75rem]">esc</kbd>} />
            </div>
            {/* results */}
            <div ref={listRef} role="listbox" aria-label="Commands and results" id={`${cpId}-list`}
              className="max-h-[50vh] overflow-y-auto py-1.5">
              {rows.slice(0, results.length).map((row, i) => option(row, i))}
              {groups.map((g) => {
                const headingId = `${cpId}-group-${g.source}`
                const first = results.length + groups.slice(0, groups.indexOf(g)).reduce((n, x) => n + x.hits.length, 0)
                return (
                  <div key={g.source} role="group" aria-labelledby={headingId} className="pt-1.5">
                    <Eyebrow id={headingId} className="px-l pb-1 pt-1.5">
                      {SOURCE_LABEL[g.source]}
                    </Eyebrow>
                    {g.hits.map((_, j) => option(rows[first + j], first + j))}
                  </div>
                )
              })}
              {rows.length === 0 && !searchingContent && failures.length === 0 && (
                <div data-type="body-s" className="px-l py-2xl text-center text-on-surface-low">No matches for “{q}”.</div>
              )}
            </div>
            {/* The content half's progress, failures and partial answers. Outside the listbox,
                because none is an option; a failed source is said in words so it never reads as a
                source with nothing in it, and one that looked in only part of what it holds says
                how much. */}
            {(searchingContent || failures.length > 0 || notes.length > 0) && (
              <div role="status" data-type="caption" className="flex flex-col gap-0.5 border-t border-outline-variant/40 px-l py-2">
                {searchingContent && <span className="text-on-surface-low">Searching chats, memory, knowledge and tasks…</span>}
                {failures.map((f) => <span key={f} className="text-danger">{f}</span>)}
                {notes.map((note) => <span key={note} data-partial="true" className="text-on-surface-low">{note}</span>)}
              </div>
            )}
            {/* footer hint */}
            <div data-type="caption" className="flex items-center gap-m border-t border-outline-variant/40 px-l py-s text-on-surface-low">
              <span className="inline-flex items-center gap-xs"><ArrowRight size={11} className="rotate-90" /> navigate</span>
              <span className="inline-flex items-center gap-xs"><CornerDownLeft size={11} /> select</span>
              <span className="ml-auto inline-flex items-center gap-xs"><kbd className="rounded bg-surface-high px-1 font-mono">⌘K</kbd> toggle</span>
            </div>
          </TrappedCard>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** One row of the palette's listbox. */
function PaletteOption({ row, id, index, on, onHover, onRun }: {
  row: Row; id: string; index: number; on: boolean; onHover: () => void; onRun: () => void
}) {
  const Icon = row.icon
  return (
    <button type="button" role="option" aria-selected={on} id={id}
      data-cmd-idx={index} onMouseEnter={onHover} onClick={onRun}
      // 🔴 INSET RING. These rows are full-width inside the palette card, which is
      // `overflow-hidden rounded-2xl`, so an outward-drawn outline is clipped
      // LEFT AND RIGHT and the ring renders as two bars instead of a rectangle.
      // Measured on the opened palette: ring 2px + offset 2px = 4px of reach, 3px of it
      // clipped on each side.
      // 🪤 IT IS NOT THE SCROLL CONTAINER, which is the thing that looks guilty. The
      // results list is `overflow-y-auto`, and CSS computes the other axis to `auto`
      // too — but `scrollWidth === clientWidth` there, so nothing can be scrolled into
      // view horizontally. The clipper is the CARD, and its clip is permanent.
      // This is the app's primary keyboard surface, which is where a focus indicator
      // matters most.
      className="flex w-full items-center gap-3 px-l py-2.5 text-left focus-visible:-outline-offset-2"
      style={{ background: on ? 'var(--color-surface-high)' : undefined }}>
      <Icon size={16} className="shrink-0" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-on-surface text-[0.8125rem]">{row.label}</span>
        {row.detail && <span className="truncate text-on-surface-low text-[0.75rem]">{row.detail}</span>}
      </span>
      {row.hint && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{row.hint}</span>}
      {on && <CornerDownLeft size={13} className="shrink-0 text-on-surface-low" />}
    </button>
  )
}

/** The palette's card, split out for ONE reason: `ui/useFocusTrap` attaches its Tab handler in a
 *  mount-time effect (`[]` deps) to `ref.current`. `CommandPalette` is mounted once at the app shell
 *  and toggles `open` internally, so the hook's effect ran while the card did not exist — the ref was
 *  null, the effect returned early, and it never re-ran. `aria-modal="true"` was therefore a promise
 *  with nothing behind it: measured, focus escaped the dialog within 30 Tabs.
 *
 *  🪤 IT DOES NOT CLAIM A DIALOG ROLE OR `aria-modal`, deliberately. Declaring them here would add an
 *  AD-HOC DIALOG, and `primitiveAdoption`'s baseline for those is **0** — `ui/Modal` is canonical. The
 *  trap is kept because focus containment is a behaviour fix independent of the role; ANNOUNCING the
 *  overlay properly means routing this palette through `ui/Modal`, which is a real refactor (top-anchored
 *  chrome, no header or close button) and belongs in its own cycle. Measured before that decision:
 *  focus escaped within 30 Tabs; after, it does not.
 *
 *  🔑 A SOURCE-LEVEL CHECK WOULD HAVE PASSED. The hook was imported, called and its ref attached; only
 *  driving Tab 30 times showed the trap was inert. Every other dialog in this app (`ui/Modal`,
 *  `ui/dialog/DialogShell`) mounts WITH its overlay, which is why they never hit this — matching that
 *  shape is the fix, not patching the hook. */
function TrappedCard({ children, ...rest }: { children: ReactNode } & React.ComponentProps<typeof motion.div>) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  return (
    <motion.div ref={trapRef} {...rest}>
      {children}
    </motion.div>
  )
}
