// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList } from './AppsSection'

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
