import { EditorView, Decoration, type DecorationSet, ViewPlugin, type ViewUpdate, WidgetType } from '@codemirror/view'
import { syntaxTree } from '@codemirror/language'
import type { Range } from '@codemirror/state'

/** Obsidian-style live markdown preview for CodeMirror 6.
 *
 *  The line(s) the cursor/selection touches render as RAW markdown (so the user
 *  can edit the source); every other line renders "live" — syntax marker
 *  characters (`#`, `*`, `_`, `` ` ``, `>`, list bullets) are hidden and the
 *  surrounding text is styled (heading sizes, bold, italic, inline code, etc.).
 *
 *  This is decoration-only (no document mutation): hidden marks are still in the
 *  text, just visually collapsed, so the value the host sees is always the real
 *  markdown source. */

// Syntax-tree node types whose marker tokens we hide on non-active lines.
const MARK_NODES = new Set([
  'HeaderMark', 'EmphasisMark', 'StrongEmphasisMark', 'CodeMark',
  'QuoteMark', 'ListMark', 'LinkMark', 'StrikethroughMark',
])
// Node types we style as rendered spans (always, active line or not).
const STYLE_CLASS: Record<string, string> = {
  ATXHeading1: 'cm-rmd-h1', ATXHeading2: 'cm-rmd-h2', ATXHeading3: 'cm-rmd-h3',
  ATXHeading4: 'cm-rmd-h4', ATXHeading5: 'cm-rmd-h5', ATXHeading6: 'cm-rmd-h6',
  StrongEmphasis: 'cm-rmd-strong', Emphasis: 'cm-rmd-em', Strikethrough: 'cm-rmd-strike',
  InlineCode: 'cm-rmd-code', Blockquote: 'cm-rmd-quote',
}

const hiddenMark = Decoration.replace({})

/** A small round bullet shown in place of a hidden list marker on live lines. */
class BulletWidget extends WidgetType {
  eq() { return true }
  toDOM() {
    const s = document.createElement('span')
    s.className = 'cm-rmd-bullet'
    s.textContent = '• '
    return s
  }
}
const bulletDeco = Decoration.replace({ widget: new BulletWidget() })

function buildDecorations(view: EditorView): DecorationSet {
  // Collect into an array, then sort + build the set — the syntax-tree walk
  // visits a parent (e.g. StrongEmphasis) before its child marks, which would
  // violate RangeSetBuilder's strictly-increasing requirement; sorting fixes it.
  // Mark decorations (styling) and replace/widget decorations (hiding) are kept
  // in the right precedence by Decoration.set's own sorting.
  const marks: Range<Decoration>[] = []
  // Lines touched by any selection range are "active" → shown raw.
  const activeLines = new Set<number>()
  for (const r of view.state.selection.ranges) {
    const from = view.state.doc.lineAt(r.from).number
    const to = view.state.doc.lineAt(r.to).number
    for (let n = from; n <= to; n++) activeLines.add(n)
  }

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(view.state).iterate({
      from, to,
      enter: (node) => {
        const name = node.name
        if (node.to <= node.from) return
        // style spans (rendered look) — applied over the WHOLE node range,
        // regardless of active line, so formatted text is visibly distinct.
        const cls = STYLE_CLASS[name]
        if (cls) marks.push(Decoration.mark({ class: cls }).range(node.from, node.to))
        if (!MARK_NODES.has(name)) return
        const lineNo = view.state.doc.lineAt(node.from).number
        if (activeLines.has(lineNo)) return  // raw on the active line
        // list bullets become a real bullet glyph; other marks just hide.
        if (name === 'ListMark') {
          const ch = view.state.doc.sliceString(node.from, node.to)
          if (/^[-*+]$/.test(ch.trim())) { marks.push(bulletDeco.range(node.from, node.to)); return }
          return  // ordered-list "1." marks stay visible
        }
        marks.push(hiddenMark.range(node.from, node.to))
      },
    })
  }
  // sort by from, then startSide (replace/widget vs mark) so Decoration.set is happy
  marks.sort((a, b) => a.from - b.from || a.value.startSide - b.value.startSide)
  return Decoration.set(marks, true)
}

export const liveMarkdown = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet
    constructor(view: EditorView) { this.decorations = buildDecorations(view) }
    update(u: ViewUpdate) {
      if (u.docChanged || u.selectionSet || u.viewportChanged) this.decorations = buildDecorations(u.view)
    }
  },
  { decorations: (v) => v.decorations },
)

/** Theme that makes CM look like the composer (transparent, no gutter chrome) +
 *  the rendered-markdown styling classes the plugin attaches. */
export const liveMarkdownTheme = EditorView.theme({
  '&': { backgroundColor: 'transparent', color: 'var(--color-on-surface)', fontSize: '17px' },
  '.cm-content': { padding: '4px 4px 0', fontFamily: 'inherit', lineHeight: '1.5', caretColor: 'var(--color-on-surface)' },
  '.cm-line': { padding: '0' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'inherit', lineHeight: '1.5' },
  '.cm-placeholder': { color: 'var(--color-on-surface-low)' },
  '.cm-cursor': { borderLeftColor: 'var(--color-on-surface)' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'color-mix(in srgb, var(--color-primary) 28%, transparent)' },
  // rendered-markdown looks. The app's UI font is variable-weight driven by
  // `font-variation-settings` — plain `font-weight` alone won't bolden it, so we
  // set BOTH. Headings also get a tighter line-height so the larger glyphs sit
  // cleanly on their line.
  '.cm-rmd-h1': { fontSize: '1.7em', fontWeight: '700', fontVariationSettings: '"wght" 680', lineHeight: '1.25', color: 'var(--color-on-surface)' },
  '.cm-rmd-h2': { fontSize: '1.42em', fontWeight: '700', fontVariationSettings: '"wght" 660', lineHeight: '1.25', color: 'var(--color-on-surface)' },
  '.cm-rmd-h3': { fontSize: '1.22em', fontWeight: '650', fontVariationSettings: '"wght" 640', lineHeight: '1.3', color: 'var(--color-on-surface)' },
  '.cm-rmd-h4': { fontSize: '1.08em', fontWeight: '650', fontVariationSettings: '"wght" 620', color: 'var(--color-on-surface)' },
  '.cm-rmd-h5': { fontWeight: '650', fontVariationSettings: '"wght" 620', color: 'var(--color-on-surface)' },
  '.cm-rmd-h6': { fontWeight: '650', fontVariationSettings: '"wght" 620', color: 'var(--color-on-surface-var)' },
  '.cm-rmd-strong': { fontWeight: '700', fontVariationSettings: '"wght" 700', color: 'var(--color-on-surface)' },
  '.cm-rmd-em': { fontStyle: 'italic' },
  '.cm-rmd-strike': { textDecoration: 'line-through', opacity: '0.7' },
  '.cm-rmd-code': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.9em', color: 'var(--color-primary-emphasis, var(--color-primary))', background: 'var(--color-surface-high)', borderRadius: '4px', padding: '0.05em 0.3em' },
  '.cm-rmd-quote': { color: 'var(--color-on-surface-var)', fontStyle: 'italic', borderLeft: '3px solid color-mix(in srgb, var(--color-primary) 50%, transparent)', paddingLeft: '0.5em' },
  '.cm-rmd-bullet': { color: 'var(--color-primary)' },
})
