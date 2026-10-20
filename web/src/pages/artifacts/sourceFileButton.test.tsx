import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { Artifact } from '../../lib/api'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'
import { ArtifactsSection } from './ArtifactsSection'

const SOURCE = '/workspace/reports/q3 review.md'
const artifact = {
  slug: 'q3-review',
  name: 'Q3 review',
  description: '',
  tags: [],
  kind: 'markdown',
  source: 'manual',
  source_path: SOURCE,
  version: 1,
  content: '# Q3 review',
  events: [],
  readonly: false,
  created_at: '2026-09-19T00:00:00Z',
  updated_at: '2026-09-19T00:00:00Z',
} as Artifact

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifacts: async () => [artifact],
      artifact: async () => artifact,
      artifactVersions: async () => ({ slug: artifact.slug, versions: [1] }),
      artifactEvents: async () => ({ slug: artifact.slug, events: [] }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

registerBuiltinContentTypes()

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('artifact source-file action (#351)', () => {
  it('routes to the containing directory and the exact file', async () => {
    const navigate = vi.fn()
    render(
      <ArtifactsSection
        sub={artifact.slug}
        navigate={navigate}
        navEpoch={0}
        query={{}}
        setQuery={() => {}}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Source file' }))

    expect(navigate).toHaveBeenCalledWith(
      'files?dir=%2Fworkspace%2Freports&file=%2Fworkspace%2Freports%2Fq3+review.md',
    )
  })
})
