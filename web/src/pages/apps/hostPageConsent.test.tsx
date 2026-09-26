// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { AppCatalogEntry, AppInstallResult, AppSummary } from '../../lib/api'

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
    updateApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn() }))

// Imported after the mocks so the dialog binds them.
import { PermissionList, consentHostUi } from './installConsent'
import { InstallDialogHarness } from '../../test/installDialogHarness'

// #492. An app's UI bundle is fetched, rewritten and `import()`-ed into THIS page
// (`appSdk.loadContributedModule` — no iframe, sharing the host React instance), so its
// page code holds the host DOM, the owner's session cookie and authenticated same-origin
// `/api/*` reach. The gateway cannot scope that: `app_permission_middleware` acts on
// requests carrying an app identity, and a bare `fetch` from app UI carries none.
//
// So install consent showed declared permissions, and the thing it showed was not the
// thing that bounds the UI — an app declaring NO permission still gets host-page
// authority the moment its page mounts. Same shape of fix as EI-12 D2 made for `network`:
// its own advisory row, outside the enforced bullets, stated either way. Three readings
// have to be killed, and a test that only asserted the new sentence would miss two:
//
//  1. host-page reach listed as an enforced bullet → reads as a grant the gateway polices;
//  2. no row for a UI-bearing app → the silence the issue was filed about;
//  3. "ships no browser code" asserted about an app nobody read (a registry pointer) →
//     a false reassurance, which is worse than the silence it replaced.

const rows = (el: HTMLElement): string[] =>
  Array.from(el.querySelectorAll('li')).map((li) => li.textContent ?? '')

const text = (el: HTMLElement): string => (el.textContent ?? '').replace(/\s+/g, ' ')

describe('PermissionList — the host-page row (#492)', () => {
  it('discloses host-page reach for a UI app, and NOT as an enforced bullet', () => {
    const { container } = render(
      <PermissionList perms={{ api: ['/api/tasks'] }} hostUi={{ page: true, components: false }} />,
    )
    // The enforced bullets stay exactly the enforced permissions.
    expect(rows(container).some((r) => /dashboard page|browser code/i.test(r))).toBe(false)
    const t = text(container)
    expect(t).toMatch(/Runs in this dashboard page: yes/)
    expect(t).toMatch(/advisory only/)
    // The consequence, not just the mechanism — this is the sentence the issue asked for.
    expect(t).toMatch(/bound its backend and its SDK calls, not its page code/)
  })

  it('states the negative for an app that ships no browser code', () => {
    const { container } = render(
      <PermissionList perms={{ storage: true }} hostUi={{ page: false, components: false }} />,
    )
    const t = text(container)
    expect(t).toMatch(/Runs in this dashboard page: no/)
    expect(t).toMatch(/ships no browser code/)
    // And it must not imply the platform confined something.
    expect(t).not.toMatch(/sandbox|confine[sd]? (it|the app)/i)
  })

  it('names the components module, which runs without the user opening the page', () => {
    // `ui.components` is the BROADER fact: the shell loads that module for every ENABLED
    // declaring app (AMBIENT-SURFACES §5.1), so "don't open that app's page" is not an
    // opt-out. Collapsing both facts into one boolean would understate exactly this case.
    const withModule = render(
      <PermissionList perms={{}} hostUi={{ page: false, components: true }} />,
    )
    expect(text(withModule.container)).toMatch(/Runs in this dashboard page: yes/)
    expect(text(withModule.container)).toMatch(/as soon as the app is enabled, without you opening its page/)

    const pageOnly = render(<PermissionList perms={{}} hostUi={{ page: true, components: false }} />)
    expect(text(pageOnly.container)).not.toMatch(/as soon as the app is enabled/)
  })

  it('renders NO row when the caller supplies no reading', () => {
    // An omitted `hostUi` must stay silent rather than claim "no browser code" about an
    // app nobody has read — the issue-614 trap, one field along. `consentHostUiRendered`
    // is the rail that keeps every production caller from taking this branch.
    const { container } = render(<PermissionList perms={{ api: ['/api/tasks'] }} />)
    const t = text(container)
    expect(t).not.toMatch(/Runs in this dashboard page/)
    // The rest of the surface is untouched — the network row still discloses.
    expect(t).toMatch(/does not confine/)
  })
})

describe('the install dialog passes the fact through to the row', () => {
  // The dialog is a SECOND reader of the same disclosure, and the surface a user actually
  // consents on. `consentHostUiRendered.test.ts` counts that every `PermissionList` is handed
  // `hostUi`; this pins that the server's review still reaches the row, which a field renamed on
  // the way through `AppDisclosureView` would break silently.
  it('renders the host-page row inside the dialog, from the review the server read', async () => {
    previewApp.mockResolvedValue({
      ok: false, name: 'ui-app', error: '', needs_consent: true,
      scan: { verdict: 'clean', tier: 'community', findings: [], signature: null },
      displayName: 'UI App', version: '1.0.0', previous: null, consent: 'u'.repeat(64),
      disclosure: {
        permissions: { api: ['/api/tasks'] }, crons: [], pythonDependencies: [],
        hasUI: true, uiComponents: '', hasBackend: false, onInstall: '', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
      },
    } satisfies AppInstallResult)
    render(<InstallDialogHarness target={{ source: '/apps/ui-app', label: 'ui-app' }} />)
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan:/))
    expect(text(screen.getByRole('dialog'))).toMatch(/Runs in this dashboard page: yes/)
  })
})

describe('consentHostUi — one reading for both wires', () => {
  it('reads a pre-install catalog entry', () => {
    const entry = { hasUI: true, uiComponents: '' } as Pick<AppCatalogEntry, 'hasUI' | 'uiComponents'>
    expect(consentHostUi(entry)).toEqual({ page: true, components: false })
  })

  it('reads an installed app summary through the same two fields', () => {
    // The point of naming the catalog fields `hasUI`/`uiComponents` (rather than a third
    // spelling) is that the Store detail panel, the install dialog and the installed panel
    // cannot answer this question differently.
    const app = { hasUI: false, uiComponents: 'components.mjs' } as Pick<AppSummary, 'hasUI' | 'uiComponents'>
    expect(consentHostUi(app)).toEqual({ page: false, components: true })
  })

  it('answers undefined for an entry it was not given', () => {
    expect(consentHostUi(undefined)).toBeUndefined()
  })
})
