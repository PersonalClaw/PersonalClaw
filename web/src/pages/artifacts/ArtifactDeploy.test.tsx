import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ArtifactDeployment } from '../../lib/api'
import { ArtifactDeploy, DeployedAppsMenu, isDeployableKind } from './ArtifactDeploy'

// ── PEP-8: the in-app half of "an html widget artifact can be OPENED and INTERACTED
// with in-app". The backend rails (containment, the CSP fence, teardown) live in
// tests/test_artifact_deploy_serve.py; what this file holds is that the user can reach
// the served page from the library at all, and that teardown takes the affordance away.

let deployments: ArtifactDeployment[]
const tornDown: string[] = []

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      deployedArtifacts: async () => deployments,
      deployArtifact: async (slug: string) => {
        const dep = { slug, entry: 'index.html', created_at: '', url: `/artifacts/serve/${slug}/` }
        deployments = [dep]
        return { ok: true, deployment: dep }
      },
      teardownArtifact: async (slug: string) => {
        tornDown.push(slug)
        deployments = deployments.filter((d) => d.slug !== slug)
      },
    },
  }
})

const dep = (slug: string): ArtifactDeployment => ({
  slug, entry: 'index.html', created_at: '2026-08-15T00:00:00Z', url: `/artifacts/serve/${slug}/`,
})

beforeEach(() => { deployments = []; tornDown.length = 0 })

describe('isDeployableKind', () => {
  it('offers deploy only for the kinds the server will serve', () => {
    // Mirrors DEPLOYABLE_KINDS in artifacts/deploy.py — a control the server 400s is
    // worse than no control.
    expect(['widget', 'html', 'react'].every(isDeployableKind)).toBe(true)
    expect(['markdown', 'image', 'json', 'docx'].some(isDeployableKind)).toBe(false)
  })
})

describe('ArtifactDeploy', () => {
  it('renders nothing for a kind that cannot be served', async () => {
    const { container } = render(<ArtifactDeploy slug="notes" kind="markdown" />)
    await waitFor(() => expect(container.textContent).toBe(''))
  })

  it('deploys, then embeds the served page in a pane pointed at the in-gateway url', async () => {
    render(<ArtifactDeploy slug="my-app" kind="widget" />)
    fireEvent.click(await screen.findByTitle(/Publish this artifact/))
    // The pane IS the in-app open: an iframe whose src is the serve URL, so the page is
    // driven inside the dashboard rather than only described by it.
    const frame = await waitFor(() => {
      const f = document.querySelector('iframe')
      expect(f).not.toBeNull()
      return f as HTMLIFrameElement
    })
    expect(frame.getAttribute('src')).toBe('/artifacts/serve/my-app/')
    expect(frame.getAttribute('title')).toBe('Deployed artifact: my-app')
    // …and a real link for a new tab, on the same URL.
    const link = screen.getByTitle('Open the deployed page in a new tab') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/artifacts/serve/my-app/')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('teardown takes the url, the pane and the open affordance away', async () => {
    deployments = [dep('my-app')]
    render(<ArtifactDeploy slug="my-app" kind="widget" />)
    fireEvent.click(await screen.findByTitle(/Open the deployed page in a pane here/))
    expect(document.querySelector('iframe')).not.toBeNull()

    fireEvent.click(screen.getByTitle(/Remove the serve route/))
    await waitFor(() => expect(tornDown).toEqual(['my-app']))
    await waitFor(() => expect(document.querySelector('iframe')).toBeNull())
    // Back to offering a deploy — no stale URL left on screen implying it still serves.
    expect(await screen.findByTitle(/Publish this artifact/)).toBeTruthy()
    expect(screen.queryByText('/artifacts/serve/my-app/')).toBeNull()
  })
})

describe('DeployedAppsMenu', () => {
  it('is absent when nothing is deployed', async () => {
    const { container } = render(<DeployedAppsMenu onOpen={() => {}} />)
    await waitFor(() => expect(container.textContent).toBe(''))
  })

  it('lists every deployed artifact with its url', async () => {
    deployments = [dep('one'), dep('two')]
    render(<DeployedAppsMenu onOpen={() => {}} />)
    fireEvent.click(await screen.findByTitle('Artifacts currently served as pages'))
    for (const slug of ['one', 'two']) {
      const link = await screen.findByTitle(`Open /artifacts/serve/${slug}/ in a new tab`)
      expect(link.getAttribute('href')).toBe(`/artifacts/serve/${slug}/`)
    }
  })

  it('tears a listed deployment down and refreshes the list', async () => {
    deployments = [dep('one')]
    const onChanged = vi.fn()
    render(<DeployedAppsMenu onOpen={() => {}} onChanged={onChanged} />)
    fireEvent.click(await screen.findByTitle('Artifacts currently served as pages'))
    fireEvent.click(screen.getByRole('button', { name: 'Tear down one' }))
    await waitFor(() => expect(tornDown).toEqual(['one']))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})
