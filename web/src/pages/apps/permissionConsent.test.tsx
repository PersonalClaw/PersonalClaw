// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList } from './AppsSection'
import type { AppPermissionsWire } from '../../lib/api'

// EI-12 D2. The consent surface must not present `permissions.network` as something
// the platform polices. It cannot: an app's provider code is imported IN-PROCESS by
// the gateway, so there is no per-app egress chokepoint (docs/security/limitations.md
// §2). Two readings had to be killed:
//
//  1. `network` listed as a bullet inside "Permissions" alongside storage/cron/agent —
//     which ARE enforced server-side — reads as a grant the gateway hands out.
//  2. NO network row when the app declares `network: false` — silence reads as "this
//     app is blocked from the network", which is false for every app.
//
// So the network claim lives outside the enforced list, is labelled advisory, and is
// rendered whether or not the app declares it.

/** The bullets of the enforced-permission list (each `<li>`). */
function enforcedRows(el: HTMLElement): string[] {
  return Array.from(el.querySelectorAll('li')).map((li) => li.textContent ?? '')
}

describe('PermissionList — the network claim is advisory, not a grant', () => {
  it('keeps network OUT of the enforced list while still disclosing it', () => {
    const { container } = render(
      <PermissionList perms={{ api: ['/api/knowledge'], network: true, cron: true }} />,
    )
    const rows = enforcedRows(container)
    // The enforced bullets are exactly the enforced permissions — network is not one.
    expect(rows.some((r) => /network/i.test(r))).toBe(false)
    expect(rows.some((r) => /Scheduled jobs/.test(r))).toBe(true)
    // ...but the declaration is still surfaced, marked as not enforced.
    const text = container.textContent ?? ''
    expect(text).toMatch(/Network access: declared/)
    expect(text).toMatch(/does not confine/)
  })

  it('discloses non-enforcement even when the app does NOT declare network', () => {
    const { container } = render(<PermissionList perms={{ api: ['/api/tasks'] }} />)
    const text = container.textContent ?? ''
    // Absence of the declaration must not read as "blocked".
    expect(text).toMatch(/Network access: not declared/)
    expect(text).toMatch(/does not confine/)
  })

  it('still discloses network for an app that declares no permissions at all', () => {
    const { container } = render(<PermissionList perms={{}} />)
    expect(enforcedRows(container)).toHaveLength(0)
    expect(container.textContent ?? '').toMatch(/does not confine/)
  })
})

// APE-12. `appMessaging` is the OPPOSITE case to D2's `network`: it IS enforced — the
// broker (`POST /api/apps/message`) is the only app-to-app path and refuses an
// undeclared target 403 + SEL. It belongs in the enforced bullets, and its copy must
// not hedge. Before this, `AppPermissionsWire` did not declare the field at all, so a
// declaring app's targets never rendered: measured on the unchanged component with
// `{cron: true, appMessaging: ['receiver', 'mail-*']}` — the whole output was
// "Permissions the gateway enforces • Scheduled jobs" plus the network advisory. The
// user was never told this app may message `receiver` or anything named `mail-*`.
//
// No first-party app declares `appMessaging` today, so every case below is a synthetic
// manifest payload; the fixtures ARE the coverage.

/** The payload the server sends for a declaring app, shaped as the wire type.
 *
 *  This is the browser end of the leg `tests/test_app_messaging.py` pins on the server
 *  end (`test_declared_targets_reach_the_installed_app_consent_wire` asserts
 *  `GET /api/apps` returns exactly this `appMessaging` value, and
 *  `test_declared_targets_reach_the_pre_install_consent_payload` the Store's pre-install
 *  entry). It is annotated `AppPermissionsWire` deliberately and NOT cast: the defect
 *  was the type not declaring the field, so `npx tsc --noEmit` failing here is the
 *  point. `tests/test_app_permissions.py::test_consent_wire_declares_exactly_the_
 *  permissions_the_server_emits` keeps the two key sets equal in both directions. */
const SERVER_PAYLOAD: AppPermissionsWire = {
  cron: true,
  appMessaging: ['receiver', 'mail-*'],
}

describe('PermissionList — appMessaging is disclosed as the enforced grant it is', () => {
  it('names every target the server sent, inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r))
    // Enforced, so it is a bullet under that heading — not the advisory row.
    expect(messaging).toBeDefined()
    expect(messaging).toContain('receiver')
    // ...and nothing hedges it as advisory the way the network row must be.
    expect(messaging).not.toMatch(/advisory|does not confine/i)
  })

  it('renders a trailing-* target as a PATTERN, never as a literal app name', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r)) ?? ''
    // `mail-*` grants every app under that prefix (apps/permissions.py `_matches_any`),
    // so it must not read as one app that happens to be called "mail-*".
    expect(messaging).toContain('any app whose name starts with “mail-”')
    expect(messaging).not.toContain('mail-*')
  })

  it('renders a bare * as every installed app', () => {
    const { container } = render(<PermissionList perms={{ appMessaging: ['*'] }} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r)) ?? ''
    expect(messaging).toContain('any installed app')
    expect(messaging).not.toContain('*')
  })

  it('states the deny-by-default case instead of staying silent', () => {
    // The D2 lesson applied to an enforced permission: an app that declares no target
    // may message NO app, and saying nothing would leave the user guessing whether
    // app-to-app messaging is unrestricted.
    for (const perms of [{}, { api: ['/api/tasks'] }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      const text = container.textContent ?? ''
      expect(text).toMatch(/App messaging: none/)
      expect(text).toMatch(/can message no other app/)
    }
  })

  it('does not claim "messages no app" twice for a declaring app', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    expect(container.textContent ?? '').not.toMatch(/App messaging: none/)
  })
})

// DC-2. `desktop` is the same shape of enforced grant as `appMessaging`: the gateway
// mediates every app→shell call and refuses an undeclared capability 403 + SEL
// `desktop.capability_denied` (handlers/desktop.py), so it belongs in the enforced
// bullets and its copy must not hedge. The two-sided key rail lives in
// `tests/test_app_permissions.py::test_consent_wire_declares_exactly_the_permissions_
// the_server_emits`; this is the rendering half.
const DESKTOP_PAYLOAD: AppPermissionsWire = {
  desktop: ['audio_capture', 'native_notifications'],
}

describe('PermissionList — desktop capabilities are disclosed as enforced', () => {
  it('names every capability the server sent, inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={DESKTOP_PAYLOAD} />)
    const row = enforcedRows(container).find((r) => /Desktop capabilities/.test(r))
    expect(row).toBeDefined()
    expect(row).toContain('audio capture')
    expect(row).toContain('native notifications')
    expect(row).not.toMatch(/advisory|does not confine/i)
  })

  it('states the deny-by-default case instead of staying silent', () => {
    for (const perms of [{}, { api: ['/api/tasks'] }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      const text = container.textContent ?? ''
      expect(text).toMatch(/Desktop capabilities: none/)
      expect(text).toMatch(/reach nothing native/)
    }
  })

  it('does not claim "reaches nothing native" for a declaring app', () => {
    const { container } = render(<PermissionList perms={DESKTOP_PAYLOAD} />)
    expect(container.textContent ?? '').not.toMatch(/Desktop capabilities: none/)
  })
})
