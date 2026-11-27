import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  androidAccepts,
  androidNavigation,
  originRuleOf,
  rewriteAndroidConfig,
} from '../scripts/androidOriginRules.mjs'
import { ANDROID_IN_APP_PATTERNS, PRIVATE_HOST_PATTERNS } from '../www/shell/network.mjs'

const CONFIG = JSON.parse(readFileSync(new URL('../capacitor.config.json', import.meta.url), 'utf8'))

test('Android takes a wildcard only as a leading *., as Chromium parses an origin rule', () => {
  for (const rule of ['https://localhost', 'https://127.0.0.1', 'https://*.local', 'https://*.ts.net',
    'http://192.168.1.5:10000', 'https://[::1]', '*']) {
    assert.ok(androidAccepts(rule), `${rule} is a rule Android accepts`)
  }
  for (const rule of ['https://10.*', 'https://192.168.*', 'https://172.16.*', 'https://*.*.local',
    'https://a.*.b', 'https://*', 'localhost', 'http://', 'ht*p://x.local', 'https://x.local/p', 'file://x']) {
    assert.ok(!androidAccepts(rule), `${rule} is a rule Android refuses`)
  }
  assert.equal(originRuleOf('10.*'), 'https://10.*')
  assert.equal(originRuleOf('http://x.local'), 'http://x.local')
})

test("Android's list is exactly the shell's in-app hosts, and every rule of it is accepted", () => {
  const { kept, refused } = androidNavigation(CONFIG.server.allowNavigation)
  assert.deepEqual(kept, [...ANDROID_IN_APP_PATTERNS])
  assert.deepEqual(refused, PRIVATE_HOST_PATTERNS.filter((p) => !ANDROID_IN_APP_PATTERNS.includes(p)))
  for (const entry of kept) assert.ok(androidAccepts(originRuleOf(entry)), entry)
})

function copiedProject(config) {
  const root = mkdtempSync(join(tmpdir(), 'pc-cap-'))
  const assets = join(root, 'android', 'app', 'src', 'main', 'assets')
  mkdirSync(assets, { recursive: true })
  writeFileSync(join(assets, 'capacitor.config.json'), JSON.stringify(config, null, '\t'))
  return { root, file: join(assets, 'capacitor.config.json') }
}

const env = (root, platform) => ({
  CAPACITOR_PLATFORM_NAME: platform,
  CAPACITOR_ROOT_DIR: root,
  CAPACITOR_CONFIG: JSON.stringify(CONFIG),
})

test("after a copy, Android's config holds exactly the in-app hosts and nothing else changed", () => {
  const { root, file } = copiedProject(CONFIG)
  const said = []
  const { refused } = rewriteAndroidConfig(env(root, 'android'), (line) => said.push(line))
  const written = JSON.parse(readFileSync(file, 'utf8'))

  assert.deepEqual(written.server.allowNavigation, [...ANDROID_IN_APP_PATTERNS])
  assert.ok(written.server.allowNavigation.every((e) => androidAccepts(originRuleOf(e))))
  assert.deepEqual({ ...written, server: { ...written.server, allowNavigation: null } },
    { ...CONFIG, server: { ...CONFIG.server, allowNavigation: null } })
  assert.ok(refused.includes('10.*') && said.length === 1 && said[0].includes('opens outside the app'))
})

test('on iOS the copy is left as written, IP ranges and all', () => {
  const { root, file } = copiedProject(CONFIG)
  const before = readFileSync(file, 'utf8')
  rewriteAndroidConfig(env(root, 'ios'), () => {})
  assert.equal(readFileSync(file, 'utf8'), before)
})

test('a copy that wrote no Android config fails the hook, loudly', () => {
  const root = mkdtempSync(join(tmpdir(), 'pc-cap-'))
  assert.ok(!existsSync(join(root, 'android')))
  assert.throws(() => rewriteAndroidConfig(env(root, 'android'), () => {}), /ENOENT/)
})
