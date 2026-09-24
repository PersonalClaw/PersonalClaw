import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { gotoRoute } from './helpers'
import { SCRIPTED } from '../playwright.config'

// ── A REAL CHAT TURN, offline, credential-free (PHF-7) ──────────────────────
// The clause this spec exists for: "a gateway boots on the fake provider and
// COMPLETES A SCRIPTED CHAT TURN with no credentials present".
//
// Every other spec in this directory is careful never to need a model. That was
// not modesty — until the scripted provider existed, the harness gateway had no
// model at all, so the one thing the product is FOR could not be exercised at
// all: the SPA rendered, axe scanned it, and nothing proved a turn could run.
// This spec closes exactly that hole and nothing more. It is not a chat-feature
// test; it is the floor that says the send→stream→complete path works end to end
// against a bound provider, with the network irrelevant and no secret anywhere.
//
// WHAT MAKES THE RESULT TRUSTWORTHY (each assertion is here for a reason):
//   1. The reply text is a string the user never typed. Asserting on it cannot be
//      satisfied by the SPA echoing the prompt back into the transcript — the
//      failure mode a naive "the message appears" check would sail past.
//   2. The turn must have ENDED, not merely started: the composer's Stop control
//      is gone and its Send control is back. A hung turn leaves Stop mounted.
//   3. The LAST assistant turn must carry its action row. `ChatPage` renders it as
//      `actions={!(isLast && streaming) && <AssistantActions …/>}`, and Regenerate
//      inside it is `isLast &&` — so on the newest turn that row exists ONLY once
//      streaming has ended. Composer state and transcript state are two independent
//      readings of "finished", and a half-finished turn fails one of them.
//
// WHY NOT "Turn complete:" — MEASURED, not assumed. chat_runner.py composes that
// line (`_turn_complete_line`) and broadcasts it as `activity_event {kind:"stats"}`
// after the terminal EVENT_COMPLETE, and ChatPage folds it into the per-turn
// ContextLedger footer (collapsed summary: "telemetry"). It would be the ideal
// assertion — a completion the BACKEND attests to. It is not in the DOM. Driven
// against the real scripted provider, a fully completed turn rendered the reply,
// the per-message actions and an idle composer, and NO ledger at all — the
// `getByRole('button', {name: /telemetry/i})` assertion this comment replaced found
// zero nodes after 30 s (playwright a11y snapshot in the run's error-context.md).
// The line is documented in-tree as "live-only", so it does not survive whatever
// re-render lands the finished turn. That is a real gap worth closing in the
// product, and it is NOT this spec's clause — a spec may not assert an affordance
// the app does not render.
//
// MEASURED RUNTIME — see the RUNTIME note at the bottom of this file.

// The prompt is DELIBERATELY different text from the scripted reply. If the two
// shared a token, an echo of the user message would satisfy the reply assertion.
const PROMPT = 'Give me your scripted line, please.'

test.describe('scripted chat turn (PHF-7)', () => {
  // The config sets no top-level `timeout`, so every other spec runs on playwright's
  // 30 s default — which is right for them (navigate, scan, assert) and wrong here: a
  // real turn is the one thing in this directory that waits on the BACKEND. Scoped to
  // this describe so nothing else in the gate gets a slacker budget.
  test.describe.configure({ timeout: 120_000 })

  test('completes a turn on the scripted provider — no network, no credential', async ({ page }) => {
    // Contract drift guard. `SCRIPTED.reply` is what this spec asserts on; the
    // fixture is what the provider reads. If someone edits one and not the other
    // the turn would "fail" for a reason that has nothing to do with the product,
    // so name it here instead of letting it surface 60 seconds later as a timeout.
    // Substring on the RAW file text on purpose: it stays true whatever shape the
    // script schema settles on.
    const fixture = readFileSync(SCRIPTED.scriptPath, 'utf8')
    expect(
      fixture,
      `${SCRIPTED.scriptPath} no longer scripts SCRIPTED.reply (playwright.config.ts).\n` +
        `The fixture and the expected reply must agree — reconcile the SCRIPTED constant.`,
    ).toContain(SCRIPTED.reply)

    await gotoRoute(page, 'chat')

    // The composer is a CodeMirror 6 editor, not a <textarea> — a tag/textarea
    // selector finds nothing. It names itself via EditorView.contentAttributes
    // ({'aria-label': 'Message input'}), which is the only stable handle.
    const composer = page.getByRole('textbox', { name: 'Message input' })
    await expect(composer).toBeVisible({ timeout: 15_000 })

    // pressSequentially, not fill(): real key events are what CodeMirror's update
    // listener consumes, and they are also what flips the composer's send-button
    // state machine out of 'send-disabled'.
    await composer.click()
    await composer.pressSequentially(PROMPT, { delay: 5 })

    // The send control keeps the name "Send message" in every state and carries
    // its unavailability on aria-disabled (ui/IconButton maps `disabled` to
    // aria-disabled, NEVER the native attribute, so it keeps its tab stop).
    // toBeEnabled() does not read aria-disabled — assert the attribute itself.
    const send = page.getByRole('button', { name: 'Send message', exact: true })
    await expect(send).toBeVisible()
    await expect(
      send,
      'the composer refused the draft — send stayed aria-disabled, so no turn was ever started',
    ).not.toHaveAttribute('aria-disabled', 'true')
    await send.click()

    // The prompt landed in the transcript. This is a precondition, NOT the proof:
    // a transcript that shows only this is exactly the "echoed, never answered"
    // state the assertions below exist to distinguish.
    await expect(page.getByText(PROMPT, { exact: false }).first()).toBeVisible({ timeout: 15_000 })

    // ── (1) the SCRIPTED reply — text no user typed ──────────────────────────
    await expect(
      page.getByText(SCRIPTED.reply, { exact: false }).first(),
      `the scripted reply never rendered. Either the '${SCRIPTED.type}' provider is not bound\n` +
        `(check the gateway's stdout for a provider-resolution error), or one of\n` +
        `${SCRIPTED.scriptEnvVar} did not reach it, or the turn\n` +
        `errored. The transcript above shows what did arrive.`,
    ).toBeVisible({ timeout: 60_000 })

    // ── (2) the turn ENDED ───────────────────────────────────────────────────
    // Stop is the mid-stream face of the composer's single action button; once the
    // stream finishes the button morphs back through 'sent' to send. `exact` matters:
    // 'Stop recording' and 'Stop sharing screen' are different controls on this surface.
    await expect(
      page.getByRole('button', { name: 'Stop', exact: true }),
      'the composer is still showing Stop — the turn started but never finished streaming',
    ).toHaveCount(0, { timeout: 60_000 })
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible({ timeout: 30_000 })

    // ── (3) the TRANSCRIPT says the turn completed ───────────────────────────
    // The newest assistant turn's action row. `exact` matters: the chat header
    // carries a "Regenerate title" button that would otherwise match a substring.
    await expect(
      page.getByRole('button', { name: 'Regenerate', exact: true }),
      'the newest assistant turn has no action row — ChatPage renders it only when\n' +
        'that turn has stopped streaming, so the transcript still considers the turn\n' +
        'in flight even though the composer went idle.',
    ).toBeVisible({ timeout: 30_000 })
  })
})

// ── RUNTIME (recorded per PHF-7's "runtime recorded") ───────────────────────
// Machine class: Apple silicon laptop (Darwin 25.6, arm64), local dev checkout,
// warm npm cache, chromium from `npx playwright install`. Single spec, single
// worker, `--project=chromium`.
//
// MEASURED GREEN, `/usr/bin/time -p npx playwright test e2e/chat.spec.ts
// --project=chromium`, cold (vite build + this config's own gateway boot both
// inside the number):
//   · total wall                                          29.4 s
//   · of which this spec's own body                        5.7 s
//   · of which auth.setup.ts (token + shell mount)         1.9 s
//   → the rest is the two webServers: vite build+preview ‖ gateway boot
//
// Warm (servers already up, PW_NO_SERVER=1): 9.1 s wall, 5.8 s for the spec —
// i.e. the spec itself is stable at ~6 s and everything else is harness startup.
//
// So: **~30 s cold is the baseline**; treat a minute as a regression to
// investigate. Both numbers are the SAME assertions passing, not a projection.
// HOW they were measured matters, because this branch carries only the harness
// third of PHF-7: `llm/scripted.py` and its registry binding live on two sibling
// branches, so the runs above pointed the harness-booted gateway at a scratch
// worktree holding both (`PYTHONPATH=<merged tree>/src`, which wins over the
// editable install). Re-measure once all three land together — a number measured
// through a PYTHONPATH is a real number, but it is not the shipped path.
