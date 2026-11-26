import { test, expect, type Page, type Route, type WebSocketRoute } from '@playwright/test'
import { gotoRoute } from './helpers'
import { idleRunState } from '../src/lib/workPhase'

// ── A DRAFT AND A WAITING SEND SURVIVE A RELOAD (#4012) ─────────────────────────────────────────
//
// What the owner typed half-way is back after a reload, read from this browser's own IndexedDB,
// and Discard draft takes it out for good. A send made while PersonalClaw is away waits in the
// same database through a reload, and goes exactly once, with the id it was first sent with, when
// PersonalClaw is back. The unit tests run these rules on the in-memory store; this runs them on
// the browser's real one, across a real reload.
//
// The chat, its sends and its socket are answered here, so nothing reaches the shared gateway's
// home. "Away" is a send that never reaches the gateway (the fetch fails) and a dropped socket;
// "back" is a socket that opens again, as it does when the gateway has restarted.

const KEY = 'draft-and-waiting-send-1790000003'
const SEND = /\/api\/chat(\?.*)?$/
const DETAIL = new RegExp(`/api/chat/sessions/${KEY}(\\?.*)?$`)
const TYPED = 'Half-way through a thought about the backups'
const WAITING = 'Are the backups done?'

function detail() {
  return {
    key: KEY, title: 'The backups', queue: [], run_state: idleRunState(KEY),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    messages: [
      { role: 'user', content: 'When do the backups run?', ts: '2026-11-01T09:00:00+00:00' },
      { role: 'assistant', content: 'Every night at two.', ts: '2026-11-01T09:00:20+00:00' },
    ],
  }
}

async function pinTheChat(page: Page): Promise<void> {
  const body = JSON.stringify(detail())
  await page.route(DETAIL, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body })
      : route.fallback())
}

/** What this browser holds in one of its two stores, read from IndexedDB itself. */
async function kept(page: Page, store: 'drafts' | 'outbox'): Promise<unknown[]> {
  return page.evaluate((name) => new Promise<unknown[]>((resolve, reject) => {
    const open = indexedDB.open('personalclaw')
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const db = open.result
      if (!db.objectStoreNames.contains(name)) { db.close(); resolve([]); return }
      const all = db.transaction(name, 'readonly').objectStore(name).getAll()
      all.onsuccess = () => { db.close(); resolve(all.result) }
      all.onerror = () => { db.close(); reject(all.error) }
    }
  }), store)
}

const composer = (page: Page) => page.getByRole('textbox', { name: 'Message input' })

/** The composer's own words: its editor's lines, without the placeholder an empty one shows. */
const typed = (page: Page) => composer(page).evaluate((el) => [...el.querySelectorAll('.cm-line')].map((line) => {
  const copy = line.cloneNode(true) as HTMLElement
  copy.querySelectorAll('.cm-placeholder').forEach((p) => p.remove())
  return copy.textContent ?? ''
}).join('\n'))

async function openTheChat(page: Page): Promise<void> {
  await gotoRoute(page, `chat/${KEY}`)
  await expect(page.getByText('Every night at two.'), 'the chat never showed its turns').toBeVisible({ timeout: 15_000 })
  await expect(composer(page)).toBeVisible()
}

test.describe('a draft survives a reload', () => {
  test('what was typed half-way is back after a reload, and Discard draft takes it out for good', async ({ page }) => {
    await pinTheChat(page)
    await openTheChat(page)
    await composer(page).click()
    await composer(page).pressSequentially(TYPED, { delay: 5 })
    await expect.poll(async () => (await kept(page, 'drafts') as { text?: string }[]).map((d) => d.text),
      { message: 'the draft was never written to this browser' }).toEqual([TYPED])

    await page.reload()
    await expect.poll(() => typed(page), { message: 'the draft is not back in the composer', timeout: 15_000 }).toBe(TYPED)
    await expect(page.getByText('Your draft from before is back.')).toBeVisible()

    await page.getByRole('button', { name: 'Discard draft' }).click()
    await expect.poll(() => typed(page)).toBe('')
    await expect.poll(() => kept(page, 'drafts'), { message: 'Discard draft left the draft in this browser' }).toEqual([])

    await page.reload()
    await openTheChat(page)
    await expect.poll(() => typed(page)).toBe('')
    await expect(page.getByText('Your draft from before is back.')).toHaveCount(0)
  })
})

test.describe('a send made while PersonalClaw is away', () => {
  test('waits through a reload, and goes exactly once, with its first id, when PersonalClaw is back', async ({ page }) => {
    let away = false
    const attempts: { id: string; message: string; reached: boolean }[] = []
    const sockets: WebSocketRoute[] = []
    await page.routeWebSocket('**/api/ws', (ws) => { sockets.push(ws) })
    await page.route(SEND, (route: Route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      const body = route.request().postDataJSON() as { client_message_id: string; message: string }
      attempts.push({ id: body.client_message_id, message: body.message, reached: !away })
      if (away) return route.abort('internetdisconnected')
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ session: KEY, message_id: body.client_message_id, accepted_at: new Date().toISOString(), disposition: 'started', turn_message_id: body.client_message_id, duplicate: false }),
      })
    })
    await pinTheChat(page)
    await openTheChat(page)

    away = true
    for (const ws of sockets.splice(0)) await ws.close()
    await composer(page).click()
    await composer(page).pressSequentially(WAITING, { delay: 5 })
    await composer(page).press('Enter')
    const strip = page.getByRole('group', { name: 'Waiting to send' })
    await expect(strip.getByText(WAITING), 'the send did not wait').toBeVisible({ timeout: 15_000 })
    await expect.poll(() => typed(page)).toBe('')
    await expect.poll(() => kept(page, 'outbox'), { message: 'the waiting send is not in this browser' }).toHaveLength(1)

    await page.reload()
    await openTheChat(page)
    await expect(page.getByRole('group', { name: 'Waiting to send' }).getByText(WAITING), 'the waiting send did not survive the reload').toBeVisible({ timeout: 15_000 })
    expect(attempts.filter((a) => a.reached), 'a send reached the gateway while it was away').toEqual([])

    away = false
    for (const ws of sockets.splice(0)) await ws.close()
    await expect(page.getByRole('group', { name: 'Waiting to send' }), 'the waiting send never went').toHaveCount(0, { timeout: 20_000 })
    await expect(page.locator('[data-transcript-turn]').getByText(WAITING), 'the sent message has no bubble').toBeVisible()
    await expect.poll(() => kept(page, 'outbox'), { message: 'the sent message is still waiting in this browser' }).toEqual([])

    await page.reload()
    await openTheChat(page)
    await page.waitForTimeout(1_000)
    const reached = attempts.filter((a) => a.reached)
    expect(reached.map((a) => a.message), 'the waiting send did not go exactly once').toEqual([WAITING])
    expect(new Set(attempts.map((a) => a.id)).size, 'a retry carried a new id, so the gateway could not tell it was the same send').toBe(1)
    await expect(page.getByRole('group', { name: 'Waiting to send' })).toHaveCount(0)
  })
})
