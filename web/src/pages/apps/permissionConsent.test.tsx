// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList } from './installConsent'
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

// ── INU-7: a declared proposal kind reaches install consent ───────────────────────────
//
// The same APE-12 leg, for the newest enforced grant: `POST /api/inbox/proposals` refuses
// an undeclared kind 403 + SEL, and refuses a callback into another app, so "what may this
// app ask you to approve" is an enforced capability — and a capability the Store never
// renders is a grant the user never consented to. Annotated `AppPermissionsWire`, NOT cast:
// a wire type missing the field must fail `tsc --noEmit` here, which is how the appMessaging
// defect would have been caught. Named by the LABEL, because that is the wording the inbox
// row will carry.
const PROPOSALS_PAYLOAD: AppPermissionsWire = {
  proposals: [
    { kind_suffix: 'draft', label: 'Draft replies' },
    { kind_suffix: 'retire' },
  ],
}

describe('PermissionList — declared proposal kinds are disclosed as enforced', () => {
  it('names every declared kind inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={PROPOSALS_PAYLOAD} />)
    const row = enforcedRows(container).find((r) => /Can ask you to approve/.test(r))
    expect(row).toBeDefined()
    expect(row).toContain('Draft replies')
    // No label declared → the slug is shown rather than an empty entry.
    expect(row).toContain('retire')
    expect(row).not.toMatch(/advisory|does not confine/i)
  })

  it('says nothing about proposals for an app that declared none', () => {
    const { container } = render(<PermissionList perms={{ cron: true }} />)
    expect(container.textContent ?? '').not.toMatch(/Can ask you to approve/)
  })
})

// ── APE-1/APE-2: the two grants have SPLIT, and the panel has to say which is which ──
//
// This file exists for the third case: a grant that is declared at install and enforced by
// nothing. `appMessaging`/`desktop`/`proposals` are enforced, so they belong in the
// bullets. `network` is unenforceable in principle, so it gets an always-on advisory row.
// Under APE-1 both `backgroundTasks` and `eventSubscriptions` were the third case.
//
// APE-3 shipped the worker host, so `backgroundTasks` has now made the SAME move
// `eventSubscriptions` made under APE-2: `apps/worker_runtime` consults
// `can_run_background_tasks()` before every spawn AND every revival, so the grant denies as
// well as declares. With both grants enforced, the "Declared, not yet in effect" block has
// no feeder left and is gone — a box that renders for nothing is a shape a future grant can
// silently fall into. The reading this file now kills is the INVERSE of the original D2
// defect: understating a live capability, so a user weighs a real grant as disclosure-only.
//
// APE-2 shipped the platform event registry, so `eventSubscriptions` is now ENFORCED —
// `apps/app_events.emit` is the only path a platform event reaches an app by and it
// consults `can_receive_platform_event` per app per event (deny by default, exact name).
// It therefore MOVES into the enforced bullets. Leaving it in "Declared, not yet in
// effect" would be the D2 defect inverted: understating a live capability, so the user
// weighs a real grant as disclosure-only. `backgroundTasks` stays behind until APE-3 ships
// a worker host. The two readings still to kill, now for `backgroundTasks` alone:
//
//  1. Listing it among "Permissions the gateway enforces" — the D2 defect verbatim, and
//     worse here, because there is not even a partial mechanism behind it.
//  2. Rendering nothing at all — the declaration is a STANDING grant that goes live with
//     no second prompt once the host ships, so install is the user's only say.
//
// Annotated `AppPermissionsWire`, NOT cast: a wire type missing the field must fail
// `tsc --noEmit` here. The key set is pinned server-side by
// `tests/test_app_permissions.py::test_consent_wire_declares_exactly_the_permissions_the_
// server_emits`; this is the rendering half.
const PENDING_PAYLOAD: AppPermissionsWire = {
  cron: true,
  backgroundTasks: true,
  eventSubscriptions: ['session.created', 'task.completed'],
}

describe('PermissionList — eventSubscriptions is enforced, backgroundTasks is not', () => {
  it('puts platform events IN the enforced bullets, naming every event', () => {
    // A subscription the user cannot see is a grant they did not weigh, so each declared
    // name is rendered verbatim rather than counted.
    const rows = enforcedRows(render(<PermissionList perms={PENDING_PAYLOAD} />).container)
    expect(rows.some((r) => /Receive platform events: session\.created, task\.completed/.test(r))).toBe(true)
  })

  it('puts backgroundTasks IN the enforced bullets now that APE-3 hosts it', () => {
    // This asserted the OPPOSITE until APE-3: with no worker host, listing it among
    // "Permissions the gateway enforces" was the D2 defect verbatim. The host exists now, so
    // keeping it out would understate a live capability instead.
    const { container } = render(<PermissionList perms={PENDING_PAYLOAD} />)
    const rows = enforcedRows(container)
    expect(rows.some((r) => /background worker/i.test(r))).toBe(true)
    // The vacuity floor: the enforced list still renders what was already enforced.
    expect(rows.some((r) => /Scheduled jobs/.test(r))).toBe(true)
  })

  it('no longer claims the worker grant does nothing yet, and drops the empty box', () => {
    // The three phrases below were correct under APE-1 and are now false: the host exists,
    // so the grant is not "not yet in effect", it does not "grant the app nothing today",
    // and there is no later moment when it "becomes live without asking you again" — it is
    // live at install. A disclosure that keeps saying so would train users to discount it.
    const { container } = render(<PermissionList perms={PENDING_PAYLOAD} />)
    const text = container.textContent ?? ''
    expect(text).toMatch(/Run a long-lived background worker/)
    expect(text).not.toMatch(/Declared, not yet in effect/)
    expect(text).not.toMatch(/grants the app nothing today/)
    expect(text).not.toMatch(/without asking you again/)
    expect(text).not.toMatch(/deliver platform events yet/)
  })

  it('discloses each grant on its own, not only as a pair', () => {
    const worker = render(<PermissionList perms={{ backgroundTasks: true }} />)
    expect(worker.container.textContent ?? '').toMatch(/Run a long-lived background worker/)
    expect(worker.container.textContent ?? '').not.toMatch(/Receive platform events/)
    const events = render(<PermissionList perms={{ eventSubscriptions: ['knowledge.ingested'] }} />)
    expect(enforcedRows(events.container).some((r) => /Receive platform events: knowledge\.ingested/.test(r))).toBe(true)
    // …and the pending block is gone for everyone: with both grants enforced it has no
    // feeder, and a box that renders for nothing is where a future grant falls silently.
    expect(events.container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
    expect(worker.container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
  })

  it('makes no claim at all for an app that declares neither', () => {
    // Unlike `network`, silence here is TRUE: the app gets no worker and no event either
    // way, so an always-on row would imply a worker host the platform does not have.
    for (const perms of [{}, { cron: true }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      expect(container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
      expect(container.textContent ?? '').not.toMatch(/Receive platform events/)
    }
    // ...and an empty declaration is not mistaken for a declaration.
    const { container } = render(
      <PermissionList perms={{ backgroundTasks: false, eventSubscriptions: [] }} />,
    )
    expect(container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
    expect(container.textContent ?? '').not.toMatch(/Receive platform events/)
  })
})
