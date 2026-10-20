import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Artifact } from '../../lib/api'
import { ArtifactViewer } from './ArtifactViewer'

// ── A CSV artifact downloads as a CSV file ───────────────────────────────────────────────────
//
// The artifact store keeps a CSV artifact's text so that a spreadsheet program opens it as the
// data it holds: a cell whose text begins like a formula is kept behind a single quote. But the
// Download control named the file `.txt` (the kind had no extension of its own), so it opened in a
// text editor, and a spreadsheet took it only after the user renamed it. It is named `.csv` and
// typed `text/csv` now, and it holds the text exactly as the store keeps it. Every other kind
// downloads as it did.

const LEDGER = "Date,Item,Amount\n2026-10-04,Refund,-20\n2026-10-05,'=1+1,-$45.20\n"
const NOTES = '# Errands\n\n- eggs\n- flour\n'

const { saved } = vi.hoisted(() => ({
  saved: [] as Array<{ filename: string; content: string; mime?: string }>,
}))

function fixture(slug: string): Artifact {
  const [name, kind, content] = slug === 'ledger'
    ? ['October ledger', 'csv', LEDGER]
    : ['Errands', 'markdown', NOTES]
  return {
    slug, name, kind, source: 'chat', description: '', tags: [], version: 1, content,
    created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

vi.mock('../../lib/download', async (orig) => {
  const real = await orig<typeof import('../../lib/download')>()
  return {
    ...real,
    downloadText: (filename: string, content: string, mime?: string) => { saved.push({ filename, content, mime }) },
  }
})

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async (slug: string) => fixture(slug),
      artifactVersions: async (slug: string) => ({ slug, versions: [1] }),
      artifactEvents: async (slug: string) => ({ slug, events: [] }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

beforeEach(() => { saved.length = 0 })

async function download(slug: string) {
  render(<ArtifactViewer slug={slug} onChanged={() => {}} onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  await userEvent.click(await screen.findByRole('button', { name: /Download/ }))
}

describe('a CSV artifact downloads as a CSV file', () => {
  it('names the file .csv, types it text/csv and saves the text the store keeps', async () => {
    await download('ledger')

    expect(saved).toEqual([{ filename: 'October-ledger.csv', content: LEDGER, mime: 'text/csv;charset=utf-8' }])
  })

  it('downloads every other kind as it did', async () => {
    await download('errands')

    expect(saved).toEqual([{ filename: 'Errands.md', content: NOTES, mime: undefined }])
  })
})
