// Unit tests for the dev tools' browser sign-in (named_home.mjs's signIn). Run: node --test scripts/lib/
//
// A browser's sign-in needs its pair beside the cookie. signIn signs a Playwright context in through
// its request client and keeps the pair the sign-in answers in the page's origin storage, for the
// gateway's own origin only, as the app does (web/src/lib/pair.ts). These run against a fake
// context: request.get answers { ok, status, headers }, and addInitScript records each script.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { Refused, signIn } from './named_home.mjs'

const GATEWAY = { url: 'http://127.0.0.1:61234', token: 'tok-1', home: '/tmp/pc-fake-home' }
const PAIR = 'p'.repeat(43)

/** A context whose request client answers *answer*, and which records its init scripts. */
function fakeContext(answer) {
  const scripts = []
  const asked = []
  return {
    scripts,
    asked,
    request: {
      get: async (url) => {
        asked.push(url)
        return { ok: () => answer.ok, status: () => answer.status, headers: () => answer.headers }
      },
    },
    addInitScript: async (fn, arg) => { scripts.push({ fn, arg }) },
  }
}

/** Run an init script as a page at *origin* would, against a storage of its own. */
function runOn(script, origin) {
  const stored = new Map()
  const saved = globalThis.window
  globalThis.window = {
    location: { origin },
    localStorage: { setItem: (k, v) => { stored.set(k, String(v)) } },
  }
  try {
    script.fn(script.arg)
  } finally {
    globalThis.window = saved
  }
  return stored
}

test('a sign-in that answers a pair returns it and seeds it for the gateway\'s own origin', async () => {
  const ctx = fakeContext({ ok: true, status: 200, headers: { 'x-personalclaw-pair': PAIR } })
  const pair = await signIn(ctx, GATEWAY)
  assert.equal(pair, PAIR)
  assert.deepEqual(ctx.asked, [`${GATEWAY.url}/?token=tok-1`])
  assert.equal(ctx.scripts.length, 1)
  assert.deepEqual(ctx.scripts[0].arg, ['http://127.0.0.1:61234', PAIR])
  assert.equal(runOn(ctx.scripts[0], 'http://127.0.0.1:61234').get('pc_pair'), PAIR)
  assert.equal(runOn(ctx.scripts[0], 'https://elsewhere.example').size, 0, 'another origin\'s storage is left alone')
})

test('a sign-in that answers no pair returns \'\' and seeds nothing', async () => {
  const ctx = fakeContext({ ok: true, status: 200, headers: {} })
  assert.equal(await signIn(ctx, GATEWAY), '')
  assert.equal(ctx.scripts.length, 0)
})

test('a refused sign-in throws Refused, naming the home and the status', async () => {
  const ctx = fakeContext({ ok: false, status: 401, headers: { 'x-personalclaw-pair': PAIR } })
  await assert.rejects(signIn(ctx, GATEWAY), (err) => err instanceof Refused && /pc-fake-home/.test(err.message) && /401/.test(err.message))
  assert.equal(ctx.scripts.length, 0)
})
