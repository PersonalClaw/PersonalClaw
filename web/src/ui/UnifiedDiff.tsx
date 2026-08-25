/** Unified-diff patch text, colored by leading marker.
 *
 *  Extracted from the code cockpit's commit view (`CodeCockpitPage.tsx`), which had this
 *  exact `lineColor` map inline and was the only place in the app that could render a patch.
 *  The skill-refinement approval surface needs the same rendering, and a second copy of the
 *  marker→token map would be a second answer to "what color is an added line" — so both call
 *  this. One owner, one vocabulary, same tokens in both themes.
 *
 *  🔒 The patch is UNTRUSTED TEXT. A refine proposal's diff is built from a turn's own
 *  transcript, so it can contain anything the user or a tool said. It is rendered as text
 *  content only — never injected as raw HTML, never through a markdown pass, and with no
 *  link autodetection —
 *  which is what makes an approval surface safe to look at before you approve it.
 */

/** The design token for one patch line, or `undefined` for ordinary context. */
export function diffLineColor(l: string): string | undefined {
  if (l.startsWith('+') && !l.startsWith('+++')) return 'var(--color-ok)'
  if (l.startsWith('-') && !l.startsWith('---')) return 'var(--color-danger)'
  if (l.startsWith('@@')) return 'var(--color-primary)'
  if (l.startsWith('diff ') || l.startsWith('index ') || l.startsWith('+++') || l.startsWith('---')) return 'var(--color-on-surface-low)'
  return undefined
}

/** A whole patch. An empty line falls back to a NON-BREAKING space (not a plain one) so the
 *  row keeps its line box — a blank context line otherwise collapses and the patch loses a
 *  row of vertical alignment against the line beside it.
 *
 *  The box scrolls sideways, so it is focusable AND named: a scroll region a keyboard user
 *  cannot reach is a region they cannot read, and an unnamed one is announced as its own
 *  content. `label` defaults to 'Diff' but should be specific when a page can show two
 *  patches — two regions both called "Diff" have a name that does not distinguish them. */
export function UnifiedDiff({ patch, label = 'Diff', className }: { patch: string; label?: string; className?: string }) {
  return (
    <pre tabIndex={0} aria-label={label} className={className ?? 'overflow-x-auto font-mono text-[0.75rem] leading-snug'}>
      {patch.split('\n').map((l, i) => <div key={i} style={{ color: diffLineColor(l) }}>{l || ' '}</div>)}
    </pre>
  )
}
