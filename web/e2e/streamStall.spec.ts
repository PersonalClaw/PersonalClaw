import { test, expect } from '@playwright/test'
import { gotoRoute } from './helpers'

const PROMPT = 'Index this turn on the session map, please'

// ── A turn whose terminal frame never arrives must not strand the chat ─────────────────────────
//
// `chat_done` is the ONLY frame that clears the streaming state, `/api/chat?ws=1` is the ONLY
// transport (`api.sendChat` has no SSE fallback), and that frame can be lost. The measured loss is
// the session-create remount: `useChatSocket`'s effect is per-instance with its `everOpened` flag
// closure-local, so the first send on a brand-new chat re-keys `ChatSession`, closes the socket,
// and the replacement's first `onopen` is NOT a reconnect — so `resyncOnReconnect` never runs and
// every frame emitted in the gap is delivered to nothing. `chat_handlers.py` records the same gap
// for `routing_suggestion` (issue 569) and closed it by giving that payload a second transport; a
// terminal frame has none, and an ordinary socket drop loses it anyway.
//
// CI run 36091495547 (`e2e-a11y`, sessionMap.spec.ts SSM-13, and again on retry1) is that state:
// the reply fully rendered, the ledger row landed ("unpriced · 51 tokens"), and the page still read
// "Assistant is responding…" with the composer on Stop for 84 seconds — polling session detail 43
// times with `running: false` in hand and discarding every one. `!(isLast && streaming)` then
// suppressed the assistant action row, so Speak / Copy / Regenerate / Fork never existed for that
// answer and nothing short of a reload recovered it.
test.describe('a chat whose terminal stream frame is lost', () => {
  // 🪤 THE LOSS IS FORCED, AND IT IS FORCED ON THE **SECOND** TURN. Two decisions, both measured:
  //
  //  · `routeWebSocket` accepts the page's socket and forwards nothing. That is the same condition
  //    the remount creates — socket open (so no reconnect fires, so `resyncOnReconnect` stays
  //    unreachable) and no frame ever delivered — without racing it. A test that waits for a race
  //    is a test that passes for the wrong reason.
  //  · The FIRST send cannot carry the clause, because it is the one send that remounts: the
  //    replacement's `streaming` comes from its mount load's `running`, and whether that read
  //    catches the run live is exactly the race. Measured here: with the scripted provider on a
  //    local gateway the turn had already finished, the load hydrated it complete, and the chat was
  //    never stranded at all. The second send has no remount, so `send()` itself claims the stream
  //    and the swallowed `chat_done` strands it every time. Same end state, no coin flip.
  test('recovers the finished turn from the server instead of claiming the stream forever', async ({ page }) => {
    await page.routeWebSocket('**/api/ws', () => { /* accept, forward nothing, both ways */ })
    await gotoRoute(page, 'chat')

    const composer = page.getByRole('textbox', { name: 'Message input' })
    const send = page.getByRole('button', { name: 'Send message', exact: true })
    const speak = page.getByRole('button', { name: 'Speak', exact: true })
    const sendTurn = async (n: number) => {
      await composer.click()
      await composer.pressSequentially(`${PROMPT} (${n})`, { delay: 3 })
      await expect(send, `send ${n} was refused — the composer stayed aria-disabled`).not.toHaveAttribute('aria-disabled', 'true')
      await send.click()
    }
    await expect(composer).toBeVisible({ timeout: 15_000 })

    // Turn 1 — the session-creating send. Asserted only as a PRECONDITION: it leaves a settled
    // one-turn session, which is what makes turn 2 free of the remount.
    await sendTurn(1)
    await expect(
      speak,
      'turn 1 never landed, so the session this test needs does not exist — the socket route has\n' +
        'broken the send path rather than only the frames.',
    ).toHaveCount(1, { timeout: 60_000 })
    await expect(send, 'turn 1 left the composer streaming').toBeVisible({ timeout: 30_000 })

    // Turn 2 — no remount, so `send()` owns the streaming claim and the swallowed `chat_done` is
    // the only thing that would ever have released it.
    await sendTurn(2)

    // ── CONTROL: the stranded state is really reached ────────────────────────────────────────
    // Without it the clause below would pass on a turn that simply completed, which is the one way
    // this test could be vacuous.
    await expect(
      page.getByRole('button', { name: 'Stop', exact: true }),
      'the composer never entered its streaming state, so there was no terminal frame to lose and\n' +
        'the recovery asserted below would be vacuous.',
    ).toBeVisible({ timeout: 20_000 })
    await expect(speak, 'turn 2 already has its action row, so its terminal frame was NOT lost').toHaveCount(1)

    // ── THE CLAUSE: the server settles it ───────────────────────────────────────────────────
    // Once the turn lands, the server holds no task for this session — so the client must stop
    // claiming the stream AND adopt the transcript the server persisted. Both are asserted:
    // clearing the flag while leaving the answer missing is a different kind of wrong.
    await expect(
      speak,
      'turn 2 finished server-side and never got its assistant action row: the client is still\n' +
        'claiming a stream that no longer exists, so Speak / Copy / Regenerate / Fork do not exist\n' +
        'for that answer and only a reload can recover it. This is a stranded chat, not a slow one.',
    ).toHaveCount(2, { timeout: 30_000 })
    await expect(
      send,
      'the composer still offers Stop over a session the server is not running',
    ).toBeVisible({ timeout: 10_000 })
    await expect(
      page.getByRole('button', { name: 'Edit & resend', exact: true }),
      'the recovered transcript lost a user turn — the hydrate replaced more than it restored',
    ).toHaveCount(2, { timeout: 10_000 })
  })
})
