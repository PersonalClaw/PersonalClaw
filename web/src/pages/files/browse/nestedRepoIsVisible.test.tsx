/** Issue 428: the Files explorer's git integration was invisible in practice.
 *
 * The surface itself was complete and careful — branch chip, per-file porcelain badges with
 * real labels, unborn-branch handling, rename/copy parsing. What a user actually saw was
 * nothing, because of how the two halves were wired:
 *
 *   • `useGitStatus(activeRoot)` (`filesData.ts:137`) fetches status for the ACTIVE root only,
 *     and the backend resolves a repo by walking UP — so it answers correctly for a repo root
 *     and returns `{repoRoot:"", statuses:{}}` for anything above one.
 *   • the three root tabs (Workspace / Home / Outbox) are never repos, and `#/files` lands on
 *     Workspace. A project checked out ONE LEVEL DOWN — the normal layout for anyone whose
 *     work is a checkout — therefore produced an empty status map at the root the FE asked
 *     about, so no chip and no badges anywhere.
 *   • `api_file_list` HIDES `.git`, so the tree could not even hint that a repo was there.
 *     A repo-holding folder rendered identically to an unversioned one, and the feature was
 *     reachable only by typing the repo's exact path into "Go to path".
 *
 * The fix is at the derivation, not the call site: the LISTING now reports which children are
 * repo roots (validated server-side, gitdir included), and the tree marks them. This file pins
 * the visible consequence — a nested repo is distinguishable from a plain folder — because
 * that, not the fetch, is what the user was missing.
 *
 * Deliberately NOT asserted here: that badges appear for a nested repo at the root view. That
 * is the "resolve status per visible subtree" direction the issue records as the larger second
 * option and does not ask for; this marker is what turns an invisible feature into a findable
 * one, and it changes nothing about what status is fetched.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { FsEntry } from '../../../lib/api'
import { FileTree } from './FileTree'

const entry = (over: Partial<FsEntry> & { name: string }): FsEntry => ({
  path: `/ws/${over.name}`, is_dir: true, size: 0, mtime: 0, ...over,
})

const ROOT = '/ws'
const LISTING: FsEntry[] = [
  entry({ name: 'translation-memory', repo: true }),   // a real checkout, one level down
  entry({ name: 'notes' }),                            // an ordinary folder — the control
  entry({ name: 'loose.txt', is_dir: false }),         // a file — never a repo root
]

/** The `useDirCache` shape FileTree consumes, pre-seeded so nothing fetches. */
const dirs = () => ({
  cache: { [ROOT]: LISTING } as Record<string, FsEntry[]>,
  errors: {} as Record<string, string>,
  load: vi.fn(async (p: string) => (p === ROOT ? LISTING : [])),
  invalidate: vi.fn(),
  invalidateSubtree: vi.fn(),
})

function tree(over: Partial<Parameters<typeof FileTree>[0]> = {}) {
  return render(
    <FileTree dirs={dirs() as unknown as Parameters<typeof FileTree>[0]['dirs']}
      rootPath={ROOT} activePath={null} gitStatuses={{}} onOpenFile={vi.fn()}
      artifactPaths={new Set()} onRename={vi.fn()} onDelete={vi.fn()} onUpload={vi.fn()}
      {...over} />,
  )
}

const MARKER = /git repository/i

describe('a git repo nested inside the browsed root is visible in the tree', () => {
  it('marks the repo-root child', async () => {
    tree()
    expect(await screen.findByText('translation-memory')).toBeTruthy()
    const marks = screen.getAllByTitle(MARKER)
    expect(marks).toHaveLength(1)
  })

  it('does NOT mark an ordinary folder or a file — the marker means something', async () => {
    tree()
    await screen.findByText('notes')
    // Exactly one marker for three rows: were the flag ignored (or defaulted true) this count
    // would be 0 or 3, and either failure is what makes this assertion worth having.
    expect(screen.getAllByTitle(MARKER)).toHaveLength(1)
    const marked = screen.getAllByTitle(MARKER)[0].closest('button')
    expect(marked?.textContent).toContain('translation-memory')
    expect(marked?.textContent).not.toContain('notes')
  })

  it('renders nothing extra when no child is a repo (the pre-#428 tree is unchanged)', async () => {
    const plain = LISTING.map((e) => ({ ...e, repo: false }))
    render(
      <FileTree dirs={{ cache: { [ROOT]: plain }, errors: {}, load: vi.fn(async () => plain), invalidate: vi.fn(), invalidateSubtree: vi.fn() } as unknown as Parameters<typeof FileTree>[0]['dirs']}
        rootPath={ROOT} activePath={null} gitStatuses={{}} onOpenFile={vi.fn()}
        artifactPaths={new Set()} onRename={vi.fn()} onDelete={vi.fn()} onUpload={vi.fn()} />,
    )
    await screen.findByText('translation-memory')
    expect(screen.queryAllByTitle(MARKER)).toHaveLength(0)
  })
})
