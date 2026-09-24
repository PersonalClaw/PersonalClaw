/** Pure edits over a `DocumentModelJson` (DOCUMENT-FIDELITY-EDITOR §C4).
 *
 *  The editor edits the MODEL, never the bytes — so every edit is a pure function from
 *  one model to the next, and the component that hosts it holds no formatting logic at
 *  all. That split is what makes "did bolding a word actually change the document?"
 *  answerable without a DOM.
 *
 *  **Runs are the unit of character formatting**, and a user selects CHARACTERS. So
 *  applying bold to a selection means SPLITTING the run it lands in: `["a plain word"]`
 *  with `plain` selected becomes `["a ", "plain"(bold), " word"]`. Doing it any other way
 *  — bolding the whole run, or storing an offset-keyed overlay beside the runs — would
 *  either format text the user did not select or invent a second representation of
 *  formatting that the writer does not read.
 */
import type {
  DocumentBlock,
  DocumentModelJson,
  DocumentPageSetup,
  DocumentParagraphStyle,
  DocumentRun,
} from '../../lib/api'

/** The character marks a run can carry. `link` is deliberately absent: it needs a URL,
 *  i.e. a dialog, and this atom's contract is the formatting round trip. */
export type RunMark = 'bold' | 'italic' | 'code'

const EMPTY_RUN: DocumentRun = { text: '', bold: false, italic: false, code: false, link: '' }

/** A run with `text` replaced — every other mark preserved. */
function withText(run: DocumentRun, text: string): DocumentRun {
  return { ...run, text }
}

/** The plain-text view of a block, which is what a user reads and selects against.
 *  Prefers the runs (the rich truth) and falls back to `text` for a block the parser
 *  produced without runs. */
export function blockText(block: DocumentBlock): string {
  if (block.runs.length > 0) return block.runs.map((r) => r.text).join('')
  return block.text
}

/** The runs of a block, materializing a single run from `text` when the parser gave
 *  none — so an edit never has to special-case "this block has no runs yet". */
export function blockRuns(block: DocumentBlock): DocumentRun[] {
  if (block.runs.length > 0) return block.runs
  return block.text === '' ? [] : [withText(EMPTY_RUN, block.text)]
}

/** Whether `[start, end)` of the block is entirely marked with `mark` — what a toggle
 *  button reads to decide whether pressing it turns the mark ON or OFF. An empty
 *  selection is not "all marked", it is nothing. */
export function selectionHasMark(block: DocumentBlock, start: number, end: number, mark: RunMark): boolean {
  if (end <= start) return false
  let at = 0
  let covered = false
  for (const run of blockRuns(block)) {
    const from = at
    const to = at + run.text.length
    at = to
    if (to <= start || from >= end) continue
    if (!run[mark]) return false
    covered = true
  }
  return covered
}

/** Apply (or clear) `mark` over `[start, end)` of one block's runs, splitting runs at the
 *  selection edges. Returns a NEW block; the input is untouched.
 *
 *  `text` is cleared on the result because the model's `__post_init__` re-derives the
 *  plain view from the runs — leaving a stale `text` beside changed runs is how a rich
 *  edit gets silently overridden by its own plain-text shadow (`Block`'s non-clobbering
 *  precedence treats a non-empty `text` as a deliberate override). */
export function applyMark(
  block: DocumentBlock,
  start: number,
  end: number,
  mark: RunMark,
  on: boolean,
): DocumentBlock {
  if (end <= start) return block
  const out: DocumentRun[] = []
  let at = 0
  for (const run of blockRuns(block)) {
    const from = at
    const to = at + run.text.length
    at = to
    if (to <= start || from >= end) { out.push(run); continue }
    const head = run.text.slice(0, Math.max(0, start - from))
    const mid = run.text.slice(Math.max(0, start - from), Math.min(run.text.length, end - from))
    const tail = run.text.slice(Math.min(run.text.length, end - from))
    if (head) out.push(withText(run, head))
    if (mid) out.push({ ...run, text: mid, [mark]: on })
    if (tail) out.push(withText(run, tail))
  }
  return { ...block, runs: mergeRuns(out), text: '' }
}

/** Replace a block's whole text, keeping the formatting of the run each character came
 *  from where the edit was an insertion or deletion inside one run. Deliberately simple:
 *  when the new text cannot be attributed (a multi-run rewrite), the block collapses to
 *  ONE run carrying the first run's marks. Simple and predictable beats clever here — a
 *  diff-based re-attribution that guesses wrong moves formatting the user never touched.
 */
export function setBlockText(block: DocumentBlock, text: string): DocumentBlock {
  const runs = blockRuns(block)
  const before = runs.map((r) => r.text).join('')
  if (text === before) return block
  if (runs.length <= 1) {
    const base = runs[0] ?? EMPTY_RUN
    return { ...block, runs: text === '' ? [] : [withText(base, text)], text: '' }
  }
  // Attribute a single-run edit: find the common prefix/suffix and, when the change is
  // contained in one run, rewrite only that run.
  let prefix = 0
  while (prefix < text.length && prefix < before.length && text[prefix] === before[prefix]) prefix++
  let suffix = 0
  while (
    suffix < text.length - prefix &&
    suffix < before.length - prefix &&
    text[text.length - 1 - suffix] === before[before.length - 1 - suffix]
  ) suffix++
  const changeEnd = before.length - suffix   // exclusive, in `before` coordinates
  let at = 0
  let target = -1
  let targetFrom = 0
  runs.forEach((run, i) => {
    const from = at
    at += run.text.length
    if (target < 0 && from <= prefix && changeEnd <= at) { target = i; targetFrom = from }
  })
  if (target < 0) {
    const base = runs[0] ?? EMPTY_RUN
    return { ...block, runs: text === '' ? [] : [withText(base, text)], text: '' }
  }
  const run = runs[target]
  const replaced =
    run.text.slice(0, prefix - targetFrom) +
    text.slice(prefix, text.length - suffix) +
    run.text.slice(changeEnd - targetFrom)
  const next = runs.map((r, i) => (i === target ? withText(r, replaced) : r))
  return { ...block, runs: mergeRuns(next), text: '' }
}

/** Fold adjacent runs that carry identical formatting back together, so repeated
 *  bold→unbold cycles cannot grow the run list without bound. */
export function mergeRuns(runs: DocumentRun[]): DocumentRun[] {
  const out: DocumentRun[] = []
  for (const run of runs) {
    if (run.text === '') continue
    const last = out[out.length - 1]
    if (last && last.bold === run.bold && last.italic === run.italic && last.code === run.code && last.link === run.link) {
      out[out.length - 1] = withText(last, last.text + run.text)
    } else out.push(run)
  }
  return out
}

/** Replace one block of a model, returning a new model. */
export function withBlock(model: DocumentModelJson, index: number, block: DocumentBlock): DocumentModelJson {
  return { ...model, blocks: model.blocks.map((b, i) => (i === index ? block : b)) }
}

// ── layout (DFE-6) ───────────────────────────────────────────────────────────

/** An all-unset page setup. Every zero means "the writer's template decides", which is
 *  why the controls must show a document's REAL values rather than these — a control
 *  showing Letter for a document that named no size would write Letter on the first save. */
export const EMPTY_PAGE: DocumentPageSetup = {
  size: '', orientation: '',
  margin_top_pt: 0, margin_bottom_pt: 0, margin_left_pt: 0, margin_right_pt: 0,
  header_text: '', footer_text: '', page_numbers: false,
}

/** An all-unset paragraph style, for a block the parser gave none. */
export const EMPTY_STYLE: DocumentParagraphStyle = {
  align: '', space_before_pt: 0, space_after_pt: 0, line_spacing: 0,
  indent_left_pt: 0, indent_right_pt: 0, first_line_indent_pt: 0, keep_with_next: false,
}

/** The page setup as something a control can bind to — the document's own values when it
 *  has them, all-unset when it does not. Never a guessed default. */
export function pageOf(model: DocumentModelJson): DocumentPageSetup {
  return model.page ?? EMPTY_PAGE
}

export function styleOf(block: DocumentBlock): DocumentParagraphStyle {
  return block.style ?? EMPTY_STYLE
}

/** Patch the model's page setup, returning a new model. */
export function withPage(
  model: DocumentModelJson,
  patch: Partial<DocumentPageSetup>,
): DocumentModelJson {
  return { ...model, page: { ...pageOf(model), ...patch } }
}

/** Patch one block's paragraph style, returning a new model. */
export function withStyle(
  model: DocumentModelJson,
  index: number,
  patch: Partial<DocumentParagraphStyle>,
): DocumentModelJson {
  const block = model.blocks[index]
  if (!block) return model
  return withBlock(model, index, { ...block, style: { ...styleOf(block), ...patch } })
}

/** Blocks this editor can edit as text. A table / image / pagebreak has no single text
 *  body, and inventing one would let a save flatten it — those blocks are shown, named,
 *  and left exactly as parsed (DFE-7/8 own their real controls). */
export const TEXT_BLOCK_KINDS: ReadonlyArray<DocumentBlock['kind']> = ['heading', 'paragraph', 'code']

export function isTextBlock(block: DocumentBlock): boolean {
  return TEXT_BLOCK_KINDS.includes(block.kind)
}
