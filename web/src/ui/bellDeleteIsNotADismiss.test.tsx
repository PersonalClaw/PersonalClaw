/**
 * The bell's row "Dismiss" was a permanent DELETE — the twin #628 missed.
 *
 * `pages/notifications/NotificationsPage.remove()` carries that issue's finding in its own words:
 * *"one row's delete is as irreversible as Clear all four lines down — the entry leaves disk and
 * `messaging.py` has no restore. Every per-row delete in the app gates on confirmDelete; this was the
 * one that did not."* The sweep fixed the full feed and stopped. `ui/NotificationBell` calls the SAME
 * `api.deleteNotification` from its shade rows, and had all three of the same defects:
 *
 *   1. **It was named "Dismiss"** — and in this codebase that word is load-bearing.
 *      `ui/dialog/destructiveConfirmSaysWhatGoes.test.ts` asserts the inbox's dismiss must NOT say
 *      "cannot be undone", *"because a dismissed inbox item can be restored"*. So the bell borrowed
 *      the one verb the app reserves for a REVERSIBLE hide to name an irreversible delete, while the
 *      full feed called the identical call "Delete".
 *   2. **No confirmation** — one hover-revealed click onto a disk delete.
 *   3. **An ungated tail** — `load()` ran after a swallowed failure, so a refused delete re-rendered
 *      the identical row: "nothing happened, twice".
 *
 * 🪤 NOTHING CAUGHT IT, and the reason is worth keeping. `notificationDeleteConfirms.test.tsx` claims
 * to cover this class and mounts exactly ONE component — the page. A rail scoped to one surface cannot
 * see its twin, which is how the second instance of a fixed defect survives a fix.
 *
 * These rails drive the real bell + the real `DialogHost`, like the page's own.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'

const ITEM = {
  ts: 1785890207, kind: 'error', title: 'Inventory agent hit an error',
  body: 'boom', acked: true, mode: '', targets: [],
}

// Typed with a rest parameter so the forwarding spread below typechecks — a zero-arg mock compiles
// under vitest (which does not typecheck) and fails `tsc` with TS2556.
const deleteMock = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({}))
const notified: string[] = []

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      notifications: vi.fn(async () => ({ notifications: [ITEM] })),
      deleteNotification: (...a: unknown[]) => deleteMock(...a),
      ackNotification: vi.fn(async () => ({})),
      ackAllNotifications: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../app/appSdk', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  notify: (m: string) => { notified.push(m) },
}))

import { NotificationBell } from './NotificationBell'
import { DialogHost } from './dialog/DialogHost'

function mount() {
  return render(<><NotificationBell navigate={() => {}} /><DialogHost /></>)
}

// 🪤 MATCHED ON THE VERB PREFIX, not an exact name. The row's subject is
// `rowSubject([title, firstLine(body)])` — composed from two fields and capped at 55 chars by that
// helper's own measured rule. Pinning the assembled string here would couple this rail to
// `lib/rowSubject`'s cap, which is a different rail's business; the verb is what this file is about.
const DELETE_NAME = /^Delete: /

/** Open the shade and return the row's destructive control. */
async function openShadeAndFindDelete() {
  mount()
  fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Notifications/ })))
  return waitFor(() => screen.getByRole('button', { name: DELETE_NAME }))
}

beforeEach(() => { deleteMock.mockClear(); notified.length = 0 })
afterEach(() => cleanup())

describe('the bell row calls its delete a delete', () => {
  it('is named "Delete", not "Dismiss"', async () => {
    mount()
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Notifications/ })))
    const btn = await waitFor(() => screen.getByRole('button', { name: DELETE_NAME }))
    expect(btn.getAttribute('aria-label'), 'and it names which row').toContain(ITEM.title)
    // 🔑 The wrong word must be GONE, not merely joined by the right one — "dismiss" promises the
    // opposite of what this button does, and the app uses it for restorable hides elsewhere.
    expect(screen.queryByRole('button', { name: new RegExp(`Dismiss`, 'i') })).toBeNull()
  })

  it('names WHICH notification, since a five-row shade looks alike', async () => {
    const btn = await openShadeAndFindDelete()
    expect(btn.getAttribute('aria-label')).toMatch(DELETE_NAME)
    expect(btn.getAttribute('aria-label'), 'carrying the row it belongs to').toContain(ITEM.title)
    // The tooltip stays short — the same split the page uses.
    expect(btn.getAttribute('title')).toBe('Delete')
  })
})

describe('it confirms before the entry leaves disk', () => {
  it('a declined confirm deletes NOTHING', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    // The real dialog, from the real store.
    const cancel = await waitFor(() => screen.getByRole('button', { name: /cancel/i }))
    fireEvent.click(cancel)
    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull())
    expect(deleteMock, 'declining must not reach the API').not.toHaveBeenCalled()
  })

  it('the dialog names the notification and says it cannot be undone', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    // 🪤 SCOPED TO THE DIALOG. The row and the dialog title both carry the notification's text, so a
    // global `getByText` matches two nodes and throws — the assertion has to say which surface it
    // means. Same reason the confirm button below is found inside the dialog: the ROW's control is
    // also named "Delete…".
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    expect(within(dlg).getByText(new RegExp(`Delete notification "${ITEM.title}`))).toBeInTheDocument()
    // `confirmDelete`'s contract, asserted rather than assumed: this is the half that makes "Delete"
    // honest, where the inbox's reversible dismiss deliberately omits it.
    expect(within(dlg).getByText(/cannot be undone/i)).toBeInTheDocument()
    // 🪤 RESOLVE IT BEFORE LEAVING. `ui/dialog/dialogStore` is MODULE-level state and survives
    // `cleanup()`, so a dialog opened and never answered leaks into the next test — which then mounts
    // a second `DialogHost`, renders the leftover, and fails with "found multiple elements with the
    // role alertdialog" pointing at a dialog the *previous* test opened. Cancelling here keeps the
    // shared store's invariant (nothing open between tests) instead of draining it behind its back.
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  })

  it('a confirmed delete calls the API exactly once', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(deleteMock).toHaveBeenCalledTimes(1))
    expect(deleteMock).toHaveBeenCalledWith(ITEM.ts)
  })

  it('🪤 a FAILED delete reports and does not silently re-render the same row', async () => {
    // The ungated tail: `load()` used to run regardless, so a refused delete redrew the identical
    // row with nothing said — "nothing happened, twice", the shape `reportingWrite` returns a
    // boolean to prevent.
    deleteMock.mockRejectedValueOnce(new Error('disk is read-only'))
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(notified.length).toBeGreaterThan(0))
    // Through the shared sentence owner, so it reads like every other failed write in the app.
    expect(notified[0]).toBe("Couldn't delete this notification: disk is read-only")
    // The row is still there — the delete did not happen, and the UI says so rather than redrawing.
    expect(screen.getByRole('button', { name: DELETE_NAME })).toBeInTheDocument()
  })
})

describe('structurally: the bell now matches the sibling #628 fixed', () => {
  it('gates on confirmDelete and reports through reportingWrite, with a gated tail', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const code = readFileSync(join(process.cwd(), 'src/ui/NotificationBell.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = code.match(/async function remove\(n: NotificationItem\)[\s\S]*?\n  \}/)?.[0] ?? ''
    expect(body, 'found remove()').not.toBe('')
    expect(body, 'confirms first').toMatch(/if \(!\(await confirmDelete\('notification'/)
    expect(body, 'reports through the shared owner').toMatch(/await reportingWrite\('delete this notification'/)
    // Ordering is the whole point: a confirm after the write, or an ungated reload, changes nothing.
    expect(body.indexOf('confirmDelete'), 'confirm precedes the write').toBeLessThan(body.indexOf('reportingWrite'))
    expect(body.indexOf('reportingWrite'), 'and the reload comes last').toBeLessThan(body.indexOf('load()'))
    expect(body, 'the hand-rolled sentence is gone').not.toMatch(/notify\(/)
  })

  it('🔑 no notification surface calls a disk delete a "dismiss"', async () => {
    // The durable half. "Dismiss" stays legitimate for reversible hides (the inbox, a Discover tip);
    // what it may not do is label a control whose handler deletes. Scoped to the two surfaces that
    // share this API so the rail stays about a real property rather than policing a word globally.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    for (const rel of ['src/ui/NotificationBell.tsx', 'src/pages/notifications/NotificationsPage.tsx']) {
      const code = readFileSync(join(process.cwd(), rel), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${rel} deletes notifications`).toMatch(/deleteNotification/)
      expect(code, `${rel} must not name a delete "dismiss"`).not.toMatch(/(aria-label|title)=\{?["'`]?[^"'`}]*[Dd]ismiss/)
    }
  })
})
