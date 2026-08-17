import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DuplicateList } from './DuplicateList'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { api, type KnowledgeDuplicate } from '../../lib/api'

// ── The dedup/merge frontend (KNOWLEDGE-LIBRARY S3, T3.2 — atom KL-6) ─────────────────────────
//
// A merge DELETES a knowledge item and cannot be undone, so the three ways this surface can look
// finished while being wrong are each pinned here:
//
//  • THE DIRECTION. `POST /items/{id}/merge` deletes the id in the BODY and keeps the id in the
//    PATH. A call site that swaps them destroys the document the user is reading, and every
//    render assertion still passes — the list looks identical either way. So the test asserts the
//    ARGUMENT ORDER of the request, not that a request happened.
//  • THE CONFIRMATION. "It asks first" is not the claim; the claim is that the question names
//    which copy survives, which is deleted, that curation moves, and that it cannot be undone.
//    A bare "Are you sure?" satisfies a `confirm`-was-called spy and tells the user nothing, so
//    the REAL dialog is mounted and its rendered copy is read.
//  • AN EMPTY LIST vs A FAILED LOOKUP. "No duplicates" is the correct answer for almost every
//    item, so a swallowed rejection is indistinguishable from the truth — permanently, on the one
//    surface whose whole job is to say a second copy exists. Both halves are pinned: the fetcher
//    must not substitute `[]`, and the component must render the failure rather than nothing.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const KEEPER = { id: 'keep-1', title: 'Rust async book' }

function dup(over: Partial<KnowledgeDuplicate> = {}): KnowledgeDuplicate {
  return {
    id: 'loser-1',
    title: 'Rust async book (1)',
    item_type: 'note',
    created_at: '2026-08-10T09:00:00',
    word_count: 4200,
    reason: 'title 0.97 + cosine 0.99, same series date',
    ...over,
  }
}

function mount(props: Partial<Parameters<typeof DuplicateList>[0]> = {}) {
  const onMerged = vi.fn()
  const onRetry = vi.fn()
  const onOpenItem = vi.fn()
  const view = render(
    <>
      <DuplicateList item={KEEPER} duplicates={[dup()]} onRetry={onRetry}
        onOpenItem={onOpenItem} onMerged={onMerged} {...props} />
      <DialogHost />
    </>,
  )
  return { ...view, onMerged, onRetry, onOpenItem }
}

/** Click through the real dialog. Returns the dialog node so its copy can be read.
 *
 *  `alertdialog`, not `dialog`, and that is load-bearing rather than incidental: `DialogShell`
 *  only promotes the role when the request is `danger`, so finding it by this role IS the proof
 *  that the confirmation was raised as destructive. Dropping `danger: true` fails every test
 *  that calls this helper. */
async function openConfirmation() {
  const trigger = screen.getByRole('button', { name: /merge into this item/i })
  trigger.click()
  return await screen.findByRole('alertdialog')
}

let merge: MockInstance<typeof api.mergeKnowledgeItems>

beforeEach(() => {
  merge = vi.spyOn(api, 'mergeKnowledgeItems').mockResolvedValue({
    ok: true, kept: KEEPER.id, merged: 'loser-1',
    moved: { collections: 2, tags: 1, mentions: 3, annotations: 0 },
  })
})

afterEach(() => {
  // 🪤 THE DIALOG STORE IS MODULE-LEVEL, so an unresolved dialog outlives the test that raised
  // it — RTL's cleanup unmounts the host but never touches `_dialogs`. The next test's host then
  // subscribes and renders the LEFTOVER beside its own, and `findByRole('alertdialog')` fails
  // with "found multiple elements" on tests that are individually correct. Drain it explicitly.
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()  // fires once with the snapshot, then unsubs
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('near-duplicate candidates are surfaced for an item', () => {
  it('lists the candidate with the scorer’s own reason for the match', () => {
    mount()
    expect(screen.getByText('Rust async book (1)')).toBeTruthy()
    // The reason is the whole basis for a destructive decision — a merge button with no stated
    // grounds asks the user to delete a document on the app's unexplained word.
    expect(screen.getByText(/title 0\.97 \+ cosine 0\.99/)).toBeTruthy()
  })

  it('the merge control names the DIRECTION, not just "Merge"', () => {
    mount()
    // "Merge" alone leaves which of two similarly-titled documents dies to be inferred from
    // layout — the exact ambiguity that makes this action dangerous.
    expect(screen.getByRole('button', { name: /merge into this item/i })).toBeTruthy()
  })

  it('the candidate can be OPENED before being destroyed, under a name that says which', () => {
    const { onOpenItem } = mount()
    // Not `{ name: /open/i }`: four rows would give four identical "Open" names, so the control
    // has to name its own item — the point of inspecting is knowing which one you looked at.
    screen.getByRole('button', { name: 'Open “Rust async book (1)”' }).click()
    expect(onOpenItem).toHaveBeenCalledWith('loser-1')
    expect(merge).not.toHaveBeenCalled()
  })

  it('the title is not itself a control (no bespoke button in a primitive-adoption surface)', () => {
    // `Button` is `whitespace-nowrap` by contract, so a truncating title cannot be one — and the
    // adoption ratchet forbids hand-rolling a `<button>` for it. Pinned as behaviour, not style:
    // a future "make the title clickable" would silently reintroduce the bespoke element.
    mount()
    expect(screen.queryByRole('button', { name: 'Rust async book (1)' })).toBeNull()
    expect(screen.getByText('Rust async book (1)').tagName).toBe('P')
  })
})

describe('the confirmation names what the merge will do', () => {
  it('says which copy is KEPT and which is DELETED', async () => {
    mount()
    const dialog = await openConfirmation()
    const text = dialog.textContent ?? ''
    expect(text, 'the survivor must be identified as kept').toMatch(/item you have open is kept/i)
    expect(text, 'the deletion must be stated').toMatch(/duplicate is then deleted/i)
    expect(text, 'and the loser named').toContain('Rust async book (1)')
  })

  it('🔑 stays unambiguous when BOTH copies carry the SAME title', async () => {
    // The defining case, not an edge case: `find_duplicates` requires title similarity ≥ 0.85, so
    // a candidate nearly always shares the survivor's title and very often matches it exactly.
    // Found by driving a real pair — the copy read "Merge “X” into “X”?", naming neither item. So
    // the survivor is identified by POSITION ("the item you have open") and the loser by the
    // metadata that differs. A title-based rewrite of this dialog fails here.
    mount({
      item: { id: 'keep-1', title: 'Rust async book notes' },
      duplicates: [dup({ title: 'Rust async book notes', word_count: 31 })],
    })
    const text = (await openConfirmation()).textContent ?? ''
    expect(text, 'the survivor is named by position, which two identical titles cannot be')
      .toMatch(/item you have open is kept/i)
    expect(text, 'and the loser carries distinguishing metadata').toMatch(/31 words/)
    expect(text).toMatch(/added /)
  })

  it('says the curation MOVES and that the act is irreversible', async () => {
    mount()
    const text = (await openConfirmation()).textContent ?? ''
    // Naming what is inherited is the difference between "merge" meaning fold-in and meaning
    // discard-the-other-one; a user cannot consent to the first if the copy reads like the second.
    expect(text).toMatch(/collections, tags, entity mentions and highlights/i)
    expect(text).toMatch(/cannot be undone/i)
  })

  it('says how to get the OPPOSITE direction instead of leaving it to be guessed', async () => {
    mount()
    const text = (await openConfirmation()).textContent ?? ''
    expect(text).toMatch(/to keep that copy instead, open it and merge from there/i)
  })

  it('is raised as an ALERT dialog, and its button names the destruction', async () => {
    mount()
    const dialog = await openConfirmation()
    // `alertdialog` is the shell's danger role (see openConfirmation) — the confirmation is
    // structurally destructive, not merely worded that way.
    expect(dialog.getAttribute('role')).toBe('alertdialog')
    // Not "Confirm" (the shell's default): the button a user is about to press should say what
    // it does, because that label is the last thing read before an irreversible act.
    expect(dialog.textContent).toMatch(/Merge and delete/)
  })
})

describe('the merge only happens on confirmation, and in the right direction', () => {
  it('cancelling fires NO request', async () => {
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    ;(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /cancel/i.test(b.textContent ?? '')) as HTMLButtonElement).click()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(merge).not.toHaveBeenCalled()
    expect(onMerged).not.toHaveBeenCalled()
  })

  it('confirming merges the CANDIDATE into the item being viewed — survivor first', async () => {
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    ;(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /merge and delete/i.test(b.textContent ?? '')) as HTMLButtonElement).click()
    // 🔑 THE ORDER IS THE ASSERTION. `(loser, keeper)` would delete the item on screen and every
    // other assertion in this file would still pass.
    await waitFor(() => expect(merge).toHaveBeenCalledWith(KEEPER.id, 'loser-1'))
    await waitFor(() => expect(onMerged).toHaveBeenCalled())
  })

  it('a failed merge tells the host nothing landed', async () => {
    merge.mockRejectedValue(new Error('database is locked'))
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    ;(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /merge and delete/i.test(b.textContent ?? '')) as HTMLButtonElement).click()
    await waitFor(() => expect(merge).toHaveBeenCalled())
    // The refresh must NOT run: re-reading on a failure would repaint an unchanged list and read
    // as though something happened.
    expect(onMerged).not.toHaveBeenCalled()
  })
})

describe('a failed lookup is not an empty one', () => {
  it('renders the failure and a retry, and never claims there are no duplicates', () => {
    const { onRetry } = mount({ duplicates: [], error: new Error('database is locked') })
    // The server's own message survives — a generic apology hides which failure this was.
    expect(screen.getByRole('alert').textContent).toMatch(/database is locked/)
    // 🪤 The claim it must NOT make. This is the entire distinction the atom draws.
    expect(screen.queryByText(/no duplicates/i)).toBeNull()
    expect(screen.getByText(/may still have duplicates/i)).toBeTruthy()
    screen.getByRole('button', { name: /try again/i }).click()
    expect(onRetry).toHaveBeenCalled()
    // And no merge control at all: there is nothing to merge, and offering one would imply
    // the lookup answered.
    expect(screen.queryByRole('button', { name: /merge into this item/i })).toBeNull()
  })

  it('the failure ANNOUNCES — a silent error is one a screen-reader user never learns of', () => {
    mount({ duplicates: [], error: new Error('database is locked') })
    expect(screen.getByRole('alert')).toBeTruthy()
  })

  it('the fetcher does not substitute an empty list for a rejection', () => {
    // Half the fix lives in the API layer: `.catch(() => [])` there makes the error branch above
    // unreachable by construction, and the component would be asserting a state it can never
    // enter. Scoped to this one helper so an unrelated swallow elsewhere does not fail this.
    const src = read('lib/api.ts')
    const helper = src.slice(src.indexOf('knowledgeDuplicates:'))
      .slice(0, src.slice(src.indexOf('knowledgeDuplicates:')).indexOf('mergeKnowledgeItems:'))
    expect(helper, 'the helper must exist to be checked').toContain('/duplicates')
    expect(helper, 'the rejection must reach the caller').not.toMatch(/\.catch\(/)
  })

  it('the page STORES the rejection instead of dropping it, and mounts the section for it', () => {
    // The other half: the page's sibling reads use `.catch(() => {})`, which for THIS read would
    // leave `duplicates` at `[]` and no error — an honest-looking clean library.
    const page = read('pages/knowledge/KnowledgeDetailPage.tsx')
    expect(page).toMatch(/api\.knowledgeDuplicates\(id\)/)
    expect(page, 'the rejection is stored, not swallowed')
      .toMatch(/\.catch\(\(e\) => \{ if \(alive\) \{ setDuplicates\(\[\]\); setDuplicatesErr\(e\) \} \}\)/)
    expect(page, 'and a failed lookup must mount the section')
      .toMatch(/const showDuplicates = duplicates\.length > 0 \|\| !!duplicatesError/)
  })

  it('a merge invalidates the item AND the candidate list, not just one of them', () => {
    // The survivor inherited rows and the loser no longer exists. Refreshing only the item leaves
    // a Merge button pointing at a deleted id; refreshing only the list hides the inheritance.
    const page = read('pages/knowledge/KnowledgeDetailPage.tsx')
    const after = page.slice(page.indexOf('const afterMerge'))
    expect(after.slice(0, 400)).toMatch(/setReloadKey/)
    expect(after.slice(0, 400)).toMatch(/reloadDuplicates\(\)/)
  })
})
