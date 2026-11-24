import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Artifact } from '../../lib/api'
import { notWrittenLine, sayIfNotWritten } from './linkWords'
import { ArtifactViewer } from './ArtifactViewer'

// ── An artifact that could not follow its file says so ──────────────────────────────────────────
//
// A file-backed artifact follows a file. When a read could not follow it (a home restored
// elsewhere, the workspace earlier releases made under the user's home, a file deleted), the
// viewer showed the saved copy as if it were the file. Now a bar names the file and why. A save or
// a revert the gateway kept here without writing the file says the file did not change.

const SLUG = 'garden-plan'
const PATH = '/Users/someone/workplace/personalclaw-workspace/beds/plan.md'
let served: Partial<Artifact> = {}

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Garden plan', kind: 'markdown', source: 'chat', description: '', tags: [],
    version: 1, content: '# Plan', created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    events: [], source_path: PATH, ...served,
  } as Artifact
}

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => fixture(),
      artifactVersions: async () => ({ slug: SLUG, versions: [1] }),
      artifactEvents: async () => ({ slug: SLUG, events: [] }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

afterEach(() => { cleanup(); served = {} })

const view = () => render(<ArtifactViewer slug={SLUG} onChanged={() => {}} onDeleted={() => {}} onOpenSourceFile={() => {}} />)

describe('an artifact whose read could not follow its file', () => {
  it('says which file it no longer follows, and why', async () => {
    served = { source_unfollowed: 'outside' }
    view()
    const note = await screen.findByRole('note')
    expect(note.textContent).toBe(`This no longer follows ${PATH}: it is outside the folders an artifact can follow. It shows its last saved copy, and saving changes only that copy.`)
  })

  it('says a file that is gone is gone', async () => {
    served = { source_unfollowed: 'missing' }
    view()
    expect((await screen.findByRole('note')).textContent).toContain(`${PATH}: there is no file there.`)
  })

  it('says nothing while it follows its file', async () => {
    served = { source_unfollowed: '' }
    view()
    await screen.findByText('Garden plan')
    expect(screen.queryByRole('note')).toBeNull()
  })
})

describe('a save the gateway kept here without writing the file', () => {
  it('says the file did not change', () => {
    const say = vi.fn()
    const answer = { ...fixture(), source_unwritten: 'outside' } as Artifact
    expect(sayIfNotWritten(answer, say)).toBe(answer)
    expect(say).toHaveBeenCalledWith(`Saved here, but ${PATH} did not change: it is outside the folders an artifact can follow.`)
    expect(notWrittenLine(PATH, 'unwritable')).toBe(`Saved here, but ${PATH} did not change: it could not be written.`)
  })

  it('says nothing when the save wrote the file', () => {
    const say = vi.fn()
    sayIfNotWritten(fixture(), say)
    sayIfNotWritten(undefined, say)
    expect(say).not.toHaveBeenCalled()
  })

  it('is said after every body write the viewer makes: save, snapshot and revert', () => {
    const src = readFileSync(join(__dirname, 'ArtifactViewer.tsx'), 'utf8')
    const writes = [...src.matchAll(/api\.(saveArtifactBody|snapshotArtifactBody|updateArtifact\(slug, \{ event_type: 'reverted')/g)]
    expect(writes.length, 'the viewer\'s body writes moved: find them again').toBe(4)
    for (const w of writes) {
      const line = src.slice(src.lastIndexOf('\n', w.index) + 1, src.indexOf('\n', w.index))
      expect(line, `a body write that never says a missed file: ${line.trim()}`).toContain('sayIfNotWritten(')
    }
  })
})
