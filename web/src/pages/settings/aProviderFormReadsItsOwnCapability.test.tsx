import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { ProviderConfigForm } from './ProviderConfigForm'
import { MultiInstanceCard } from './MultiInstanceCard'
import { api, type SettingsProvider } from '../../lib/api'

// ── A provider's settings form is its own capability's (#3841) ───────────────────────────────────
//
// `GET /api/providers/{name}/schema` answered with the app's FIRST provider's form, whichever card
// asked, so an app with two providers showed the first one's fields on both cards. Each card now
// reads its own capability's form, `GET /api/providers/{name}/capabilities/{id}`.

const json = (o: Record<string, unknown>) =>
  new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } })

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe("a provider's form is its capability's", () => {
  it('asks the capability route for the one capability, each part encoded', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      return json({ schema: { properties: { folder: { type: 'string' } } } })
    }))
    const schema = await api.providerSchema('twin sync', 'folder')
    expect(urls).toEqual(['/api/providers/twin%20sync/capabilities/folder'])
    expect(schema).toEqual({ properties: { folder: { type: 'string' } } })
  })

  it('the settings form names its capability', async () => {
    const asked = vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: {} })
    vi.spyOn(api, 'providerConfig').mockResolvedValue({ config: {}, _secret_set: [], revision: 'r' })
    render(<ProviderConfigForm name="twin" capability="folder" />)
    await waitFor(() => expect(asked).toHaveBeenCalledWith('twin', 'folder'))
  })

  it('a multi-instance card names its capability', async () => {
    const asked = vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: {} })
    vi.spyOn(api, 'providerInstances').mockResolvedValue([])
    const ext: SettingsProvider = {
      name: 'twin', id: 'embeddings', key: 'twin/embeddings', label: 'Text embeddings', enabled: true,
      managed: true, provider: { type: 'model', capabilities: ['embedding'], multiInstance: true },
    }
    render(<MultiInstanceCard ext={ext} onChanged={() => {}} />)
    await waitFor(() => expect(asked).toHaveBeenCalledWith('twin', 'embeddings'))
  })
})
