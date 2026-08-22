import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The PER-ELEMENT half of the `outline-none` problem ──────────────────────────
//
// `focusRingSurvival.test.ts` covers files that provide NO focus treatment anywhere, and credits a
// file that composes its ring in a constant, a wrapper, or a `has-` variant on the parent. That
// file-wide credit is correct for what it guards — and it necessarily cannot see a control that
// kills the outline in a file where OTHER controls are ringed. Seventeen such controls existed:
// bare `<input>`/`<textarea>` elements carrying `outline-none` whose own class string, and whose
// enclosing container, provided no replacement. Keyboard focus landed on them invisibly.
//
// ── Why this rail is an EXPLICIT INVENTORY and not a derived population ──
//
// The obvious design is a scan: find every `outline-none` element, credit its ancestors, count the
// rest, ratchet the count. Two honest attempts at that ancestor walk were WRONG on cases verified
// by hand:
//
//   * A ±14-line window credited only 1 of the 3 controls whose container is ringed — it missed
//     `settings/bento.tsx` and `ui/forms.tsx`, whose ringed ancestors sit 15-16 lines up past an
//     intervening comment block. A line window is not a scope.
//   * An indentation-based walk (Prettier makes indentation the JSX nesting) still misclassified
//     `settings/bento.tsx`, whose ancestor `<div className="group relative … focus-within:ring-2">`
//     is real and was read by eye.
//
// Resolving "does an ancestor ring this control" correctly needs a JSX parse, not a regex. A
// ratchet on a population known to be wrong is worse than none — this repo's own doctrine is that a
// baseline whose population is noise lets a real regression hide inside churn. So this rail asserts
// only what was actually verified: each control below was read, classified, and fixed or excused
// individually. It cannot discover a NEW unringed control; `e2e:a11y`'s Tab-walk is what does that.
// It can and does stop these seventeen from silently losing their treatment again.
//
// ── The two treatments, and why each site gets the one it gets ──
//
// `focus:ring-*` on the control when the CONTROL is the visible box (it has its own `rounded-*` +
// background). `focus-within:ring-*` on the container when the CONTAINER draws the box and the
// input is `bg-transparent` inside it — ringing the transparent child there would paint a rectangle
// floating inside the visible field. That split follows the shape already established in this
// codebase: 45 files use `focus:ring-2`, and `app/Onboarding.tsx`, `settings/bento.tsx` and
// `ui/forms.tsx` all use exactly `focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary/50`
// on the wrapper for the transparent-input idiom.

const SRC = join(process.cwd(), 'src')

/** A control that is itself the visible box: the ring goes on the control. */
const ELEMENT_RING = /focus:ring-2\s+focus:ring-inset\s+focus:ring-primary\/50/
/** A transparent control inside a box-drawing container: the ring goes on the container. */
const CONTAINER_RING =
  /focus-within:ring-2\s+focus-within:ring-inset\s+focus-within:ring-primary\/50/

/** Each entry: the file, a substring identifying the CONTROL, and which treatment its site must
 *  carry. `anchor` is always a distinctive run of the CONTROL's own utilities — never the
 *  container's — for two reasons. It survives reformatting and line drift in a way a line number
 *  would not; and the containers are not unique. `KnowledgeCreatePage`/`KnowledgeDetail` each render
 *  TWO `min-h-0 flex-1 overflow-hidden rounded-lg border …` boxes with byte-identical class strings,
 *  one holding the textarea below and one holding `<GistEditor>` (Monaco, which draws its own cursor
 *  and focus border and has no invisible-focus defect to fix). Keying on the container could not
 *  tell them apart; keying on the control can. `ring: CONTAINER_RING` therefore checks the class
 *  string immediately PRECEDING the control — see `containersOf`. */
const INVENTORY: { file: string; anchor: string; ring: RegExp; what: string }[] = [
  // ── the control IS the box ──
  {
    file: 'pages/ChatPage.tsx',
    anchor: 'h-8 min-w-[200px] max-w-[420px] rounded-md bg-surface-high',
    ring: ELEMENT_RING,
    what: 'rename-this-chat input',
  },
  {
    file: 'pages/ChatPage.tsx',
    anchor: 'w-full rounded-md bg-surface-high px-2 py-1.5',
    ring: ELEMENT_RING,
    what: 'auto-continue message textarea',
  },
  {
    file: 'pages/ChatPage.tsx',
    anchor: 'w-16 rounded bg-surface-high',
    ring: ELEMENT_RING,
    what: 'auto-continue idle-seconds number input',
  },
  {
    file: 'pages/ChatPage.tsx',
    anchor: 'w-14 rounded bg-surface-high',
    ring: ELEMENT_RING,
    what: 'auto-continue max-cycles number input',
  },
  {
    file: 'pages/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface-var',
    ring: ELEMENT_RING,
    what: 'task title input',
  },
  {
    file: 'pages/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface-low text-[0.75rem]',
    ring: ELEMENT_RING,
    what: 'task description input',
  },
  {
    file: 'pages/code/CodePlanReview.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-[0.8125rem] text-on-surface',
    ring: ELEMENT_RING,
    what: 'add-a-task input',
  },
  {
    file: 'ui/Combobox.tsx',
    anchor: 'w-full h-8 rounded-md bg-surface pl-8 pr-2',
    ring: ELEMENT_RING,
    what: 'combobox search input',
  },
  // ── the CONTAINER draws the box (the control is bg-transparent inside it) ──
  // CodePlanReview's title+description share ONE box, so a container ring could not say which of
  // the two has focus — those two are element-ringed above for exactly that reason.
  {
    file: 'pages/chat/ChatActivityPanel.tsx',
    anchor: 'max-h-24 min-h-0 flex-1 resize-none bg-transparent',
    ring: CONTAINER_RING,
    what: 'ask-the-side composer',
  },
  {
    file: 'pages/code/CodeCockpitPage.tsx',
    anchor: 'max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent',
    ring: CONTAINER_RING,
    what: 'steer-the-worker composers (two call sites, identical chrome — both checked)',
  },
  {
    file: 'pages/knowledge/KnowledgeCreatePage.tsx',
    anchor: 'flex-1 bg-transparent text-on-surface text-[0.9375rem]',
    ring: CONTAINER_RING,
    what: 'bookmark URL field',
  },
  {
    file: 'pages/knowledge/KnowledgeCreatePage.tsx',
    anchor: 'h-full w-full resize-none bg-transparent px-m py-2',
    ring: CONTAINER_RING,
    what: 'markdown body textarea',
  },
  {
    file: 'pages/knowledge/KnowledgeDetail.tsx',
    anchor: 'flex-1 bg-transparent text-on-surface text-[0.9375rem]',
    ring: CONTAINER_RING,
    what: 'bookmark URL field (edit)',
  },
  {
    file: 'pages/knowledge/KnowledgeDetail.tsx',
    anchor: 'h-full w-full resize-none bg-transparent px-m py-2',
    ring: CONTAINER_RING,
    what: 'markdown body textarea (edit)',
  },
  {
    file: 'pages/loop/LoopComposer.tsx',
    anchor: 'min-w-0 flex-1 bg-transparent text-on-surface text-[0.8125rem]',
    ring: CONTAINER_RING,
    what: 'codebase-path + reference-URL fields (identical chrome — both checked)',
  },
]

/** Controls that carry `outline-none` and correctly have NO treatment of their own, because an
 *  ancestor draws the ring. Each was verified by reading that ancestor — this is the set the two
 *  rejected automated walks disagreed about, recorded here so the next reader does not re-derive it.
 *
 *  `ui/RowHitTarget.tsx` + `ui/ListScaffold.tsx` — the button sits at `-z-10` and the ring is drawn
 *    by the parent via `has-[>button:focus-visible]:ring-2`; already covered by focusRingSurvival.
 *  `pages/settings/bento.tsx` — the `inset-0 z-0` overlay button inside
 *    `<div className="group relative … focus-within:ring-2 focus-within:ring-inset …">`.
 *  `app/Onboarding.tsx` + `ui/forms.tsx` — `focus-within:` on the immediate wrapper. */
const RINGED_BY_AN_ANCESTOR = [
  'ui/RowHitTarget.tsx',
  'ui/ListScaffold.tsx',
  'pages/settings/bento.tsx',
  'app/Onboarding.tsx',
  'ui/forms.tsx',
]

function read(file: string): string {
  return readFileSync(join(SRC, file), 'utf8')
}

const CLASS_STRING = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g

/** Every `[classString, endIndex]` in the file, in source order. */
function classStrings(text: string): [string, number][] {
  const out: [string, number][] = []
  for (const m of text.matchAll(CLASS_STRING)) out.push([m[1] ?? m[2] ?? '', m.index])
  return out
}

/** The control's OWN class string, once per occurrence — not just the first. `CodeCockpitPage` and
 *  `LoopComposer` each render two controls with byte-identical chrome, so a first-match helper would
 *  leave the second unasserted and free to regress. */
function controlsMatching(text: string, anchor: string): string[] {
  return classStrings(text)
    .filter(([cls]) => cls.includes(anchor))
    .map(([cls]) => cls)
}

/** For each occurrence of the control, the nearest PRECEDING class string that draws a box (carries
 *  a `rounded-*`) — the container in the `bg-transparent control inside a bordered wrapper` idiom
 *  this codebase uses.
 *
 *  Not simply the immediately-preceding string: `KnowledgeCreatePage`, `KnowledgeDetail` and
 *  `LoopComposer` all put a lucide icon between the container and the input, so the immediate
 *  predecessor is the ICON's `text-on-surface-low shrink-0`. `rounded-*` is what actually
 *  distinguishes the box from its contents — the transparent controls in these entries carry none.
 *
 *  Positional rather than an ancestor walk on purpose. This rail guards KNOWN-fixed sites, so the
 *  only risk is over-strictness: if a refactor moves the ring further up the tree, this reds and a
 *  human re-verifies and updates the entry. That is a safe failure. The reverse — a walk that
 *  wrongly credits a distant ancestor — would let a real regression through, and is exactly how the
 *  two rejected automated scans failed. */
function containersOf(text: string, anchor: string): string[] {
  const all = classStrings(text)
  const out: string[] = []
  all.forEach(([cls], i) => {
    if (!cls.includes(anchor)) return
    for (let j = i - 1; j >= 0; j--) {
      if (/\brounded-/.test(all[j][0])) {
        out.push(all[j][0])
        return
      }
    }
    out.push('') // no box-drawing ancestor found — reads as unringed, which is the safe direction
  })
  return out
}

describe('every control that kills its outline carries a replacement ring', () => {
  it.each(INVENTORY)('$file — $what', ({ file, anchor, ring }) => {
    const text = read(file)
    const matches =
      ring === CONTAINER_RING ? containersOf(text, anchor) : controlsMatching(text, anchor)
    // Anchor first: if the class string moved, `every` over an empty list would pass vacuously —
    // the failure mode every ratchet in this repo carries a floor against.
    expect(
      matches.length,
      `anchor no longer matches any class string in ${file}: "${anchor}"`,
    ).toBeGreaterThan(0)
    const unringed = matches.filter((cls) => !ring.test(cls))
    expect(
      unringed,
      `${unringed.length} of ${matches.length} matching control(s) in ${file} set outline-none, ` +
        `which defeats the global :focus-visible ring from design/tokens.css, and carry no ` +
        `replacement — keyboard focus lands on them invisibly. Restore the treatment, or move the ` +
        `ring to the container and record that here.`,
    ).toEqual([])
  })

  it('the ancestor-ringed controls still have the ancestor that rings them', () => {
    // These deliberately carry no treatment of their own, so the ONLY thing keeping them correct is
    // the ancestor. If that ancestor loses its ring, nothing else in the suite would notice.
    for (const file of RINGED_BY_AN_ANCESTOR) {
      const text = read(file)
      expect(text, `${file} relies on an ancestor ring that is now gone`).toMatch(
        /focus-within:ring-2|has-\[>button:focus-visible\]:ring-2/,
      )
    }
  })

  it('no two inventory entries duplicate a file+anchor pair', () => {
    // Not a correctness issue since every match is now asserted — a hygiene check, so a copy-paste
    // entry cannot read as extra coverage it does not add.
    const seen = new Set<string>()
    for (const { file, anchor } of INVENTORY) {
      const key = `${file}::${anchor}`
      expect(seen.has(key), `duplicate anchor in ${file}: "${anchor}"`).toBe(false)
      seen.add(key)
    }
  })
})
