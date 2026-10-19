import { useEffect, useId, useRef, useState } from 'react'
import { Loader2, FileCode } from 'lucide-react'
import { api } from '../../../../lib/api'
import { SearchField } from '../../../../ui/SearchField'

// ── The cockpit's file finder ──────────────────────────────────────────────────────────────────
//
// Find a file in the workspace by name, over the tree. ⌘P focuses it.

/** Fuzzy find-file-by-name within the workspace, surfaced above the cockpit file tree.
 *  Debounced; a result opens in the center editor via the same event the tree uses. */
export function FileFinder({ ws }: { ws: string }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<{ path: string; name: string }[]>([])
  const [open, setOpen] = useState(false)
  // Keyboard-highlighted result for ↑/↓ navigation (quick-open expectation). Reset to
  // the top hit on each new result set.
  const [hi, setHi] = useState(0)
  // 🔴 THIS WAS A KEYBOARD WIDGET THAT ANNOUNCED NOTHING. Arrow keys moved a purely VISUAL highlight
  // over the results: no listbox, no options, no `aria-activedescendant`, and nothing said the popover
  // had opened — the same defect `CommandPalette` used to carry, in the same shape. The
  // pattern is the app's own (APG combobox): focus stays in the field and a virtual cursor moves, so
  // the ids below are what assistive tech follows instead of focus.
  const qoId = useId()
  // `searching` gates the dropdown's empty state so a slow query doesn't flash a
  // premature "No files match" over the PRIOR results before the new ones land.
  const [searching, setSearching] = useState(false)
  // Debounce + sequence-guard the search so fast typing doesn't apply out-of-order
  // results (a slow earlier query resolving after a faster later one).
  const seq = useRef(0)
  // Every synchronous consequence of a query change happens HERE, in the handlers that change it,
  // batched into the keystroke's own render; the effect below only runs the debounced search.
  // `setSearching(true)` used to sit in that effect, where it scheduled a render from inside every
  // keystroke's commit — the shape that throws React's #185 when keys outrun the renders (the
  // mechanism: `ui/composer/MarkdownInput`).
  const changeQuery = (v: string) => {
    setQ(v)
    const live = v.trim().length >= 2
    setSearching(live)
    if (!live) setResults([])
    if (!v) setOpen(false)
  }
  useEffect(() => {
    const needle = q.trim()
    if (needle.length < 2) return
    const mine = ++seq.current
    const t = setTimeout(() => {
      api.fileSearch(needle, ws).then((r) => {
        if (mine === seq.current) { setResults((r.results || []).map((x) => ({ path: x.path, name: x.name }))); setHi(0); setOpen(true); setSearching(false) }
      }).catch(() => { if (mine === seq.current) { setResults([]); setSearching(false) } })
    }, 200)
    return () => clearTimeout(t)
  }, [q, ws])
  const openFile = (r: { path: string; name: string }) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: r.name, path: r.path, is_dir: false } }))
    changeQuery('')
  }
  // Dismiss the results dropdown on an outside click — else it floats over the tree
  // after the user clicks away (e.g. to pick a tree file instead) with no way to close
  // it but Escape/clear. Pointerdown so it closes before the click lands on the tree.
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])
  // ⌘P focus signal from FilesRail — focus + select so the user can immediately type
  // (or retype over a prior query).
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onFocusFind = () => { inputRef.current?.focus(); inputRef.current?.select() }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
  return (
    <div ref={rootRef} className="relative shrink-0 border-b border-outline-variant/40 px-2 py-1.5">
      <div className="flex items-center gap-1.5 rounded-md bg-surface-high px-2 py-1">
        <SearchField variant="inline" inlineIconSize={13} size="md" inputRef={inputRef} value={q}
          onChange={changeQuery}
          onFocus={() => results.length && setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && results.length) { e.preventDefault(); setOpen(true); setHi((i) => Math.min(results.length - 1, i + 1)) }
            else if (e.key === 'ArrowUp' && results.length) { e.preventDefault(); setHi((i) => Math.max(0, i - 1)) }
            else if (e.key === 'Enter' && results.length) { e.preventDefault(); openFile(results[Math.min(hi, results.length - 1)]) }
            else if (e.key === 'Escape') { e.preventDefault(); changeQuery('') }
          }}
          placeholder="Find file by name…  ⌘P" spellCheck={false} autoCapitalize="off" autoCorrect="off"
          ariaLabel="Find file by name"
          ariaHasPopup="listbox" ariaControls={`${qoId}-list`}
          ariaExpanded={open && q.trim().length >= 2}
          ariaActiveDescendant={open && results.length ? `${qoId}-opt-${Math.min(hi, results.length - 1)}` : undefined} />
      </div>
      {open && q.trim().length >= 2 && (
        // A listbox is an ARIA input field, so unlike a menu it MUST be named — the exact axe
        // "serious" finding `ProjectPicker` produced when it declared one namelessly.
        <div role="listbox" aria-label="Matching files" id={`${qoId}-list`}
          className="absolute inset-x-2 z-20 mt-1 max-h-[40vh] overflow-y-auto rounded-md border border-outline-variant/50 bg-surface-container shadow-lg">
          {results.length === 0 ? (
            // While a query is in flight, show "Searching…" rather than a premature
            // "No files match" (which would flash over the prior results until the new
            // ones land + read as a false empty).
            searching
              ? <p data-type="caption" className="inline-flex items-center gap-1.5 px-2.5 py-2 text-on-surface-low"><Loader2 size={11} className="animate-spin" /> Searching…</p>
              : <p data-type="caption" className="px-2.5 py-2 text-on-surface-low">No files match “{q.trim()}”.</p>
          ) : results.map((r, i) => {
            // fileSearch returns REALPATH-resolved paths (file_index realpaths the root),
            // so a naive startsWith(ws) fails on a symlinked workspace root (/tmp vs
            // /private/tmp on macOS) → it fell back to the bare basename, losing the
            // subdir context that's the whole point of showing a rel path in quick-open
            // (two `config.py`s become indistinguishable). Relativize by the workspace's
            // LAST segment, like the Changes panel + canonPath do.
            const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
            const marker = `/${wsBase}/`
            const mi = wsBase ? r.path.lastIndexOf(marker) : -1
            const rel = mi >= 0 ? r.path.slice(mi + marker.length) : (r.path.startsWith(ws) ? r.path.slice(ws.replace(/\/$/, '').length + 1) : r.name)
            return (
              <button key={r.path} type="button" role="option" aria-selected={i === hi}
                id={`${qoId}-opt-${i}`}
                onClick={() => openFile(r)} onMouseEnter={() => setHi(i)}
                ref={(el) => { if (i === hi && open) el?.scrollIntoView({ block: 'nearest' }) }}
                data-type="caption" className={`flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left ${i === hi ? 'bg-surface-high' : 'hover:bg-surface-high'}`}>
                <FileCode size={11} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 truncate text-on-surface-var" title={rel}>{rel}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
