import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

// ── Settings › Secrets names the keychain namespace this home's secrets are filed under ──
//
// The OS keychain is the machine's, and every PersonalClaw home on the machine reaches the same one.
// Each home files its items under a name of its own: the default home under `personalclaw`, the
// name every home used before, and any other home under `personalclaw-<id>`, made from an id it
// keeps. Where a secret is stored, the page says which name that is and whose:
//
//   • the default home's namespace is named as the default home's;
//   • another home's is named, and said to be this home's own;
//   • a home that has never stored a secret there says it gets one on its first;
//   • a home whose id file holds no id says the keychain is not used, and what to do;
//   • with no OS keychain answering there is no namespace, and the page names none.

type Store = {
  backend: 'keychain' | 'dotenv'
  keychain_namespace: string
  keychain_scope: '' | 'default' | 'own' | 'unnamed' | 'unreadable'
}

const OWN = 'personalclaw-0123456789abcdef0123456789abcdef'

const payload = (store: Store) => ({
  secrets: [],
  counts: { total: 0, global: 0, project: 0, host: 0 },
  empty_hint: 'No secrets stored yet.',
  store,
})

async function mount(store: Store) {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/api', () => ({
    ApiError: class ApiError extends Error { status = 0 },
    api: {
      secrets: async () => payload(store),
      projects: async () => [],
      putSecret: vi.fn(),
      deleteSecret: vi.fn(),
    },
  }))
  const { SecretsPanel } = await import('./SecretsPanel')
  await act(async () => {
    render(<SecretsPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

/** The row's whole text: its label, the name and sentence, and the pill. */
const namespaceRow = () => screen.getByText('Keychain namespace').parentElement?.textContent ?? ''

afterEach(() => { cleanup(); vi.resetModules(); vi.restoreAllMocks() })

describe('the keychain namespace is named where a secret is stored', () => {
  it("names another home's namespace as this home's own", async () => {
    await mount({ backend: 'keychain', keychain_namespace: OWN, keychain_scope: 'own' })
    expect(screen.getByText(OWN)).toBeTruthy()
    expect(namespaceRow()).toContain("is this home's own namespace in the OS keychain")
    expect(namespaceRow()).toContain('Other homes keep their secrets under names of their own.')
  })

  it("names the default home's namespace as the default home's", async () => {
    await mount({ backend: 'keychain', keychain_namespace: 'personalclaw', keychain_scope: 'default' })
    expect(screen.getByText('personalclaw')).toBeTruthy()
    expect(namespaceRow()).toContain("is the default home's namespace in the OS keychain")
    expect(namespaceRow()).toContain('default home')
  })

  it('says a home that has stored nothing there yet gets its own on its first secret', async () => {
    await mount({ backend: 'keychain', keychain_namespace: '', keychain_scope: 'unnamed' })
    expect(namespaceRow()).toContain('gets a namespace of its own in the OS keychain when it first stores a secret there')
    expect(namespaceRow()).toContain('none yet')
  })

  it('says the keychain is not used when the id file holds no id, and what to do', async () => {
    await mount({ backend: 'dotenv', keychain_namespace: '', keychain_scope: 'unreadable' })
    expect(namespaceRow()).toContain('the OS keychain is not used here')
    expect(namespaceRow()).toContain("Write the id back into it (this home's keychain items are filed under personalclaw-<id>)")
    expect(namespaceRow()).toContain('or delete it to start a new, empty namespace.')
    expect(namespaceRow()).toContain('unreadable')
  })

  it('names no namespace when no OS keychain answers', async () => {
    await mount({ backend: 'dotenv', keychain_namespace: '', keychain_scope: '' })
    expect(screen.getByLabelText('Secret value')).toBeTruthy()
    expect(screen.queryByText('Keychain namespace')).toBeNull()
  })
})
