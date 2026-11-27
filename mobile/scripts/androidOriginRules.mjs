/**
 * Android's copy of the navigation list holds only entries Android's WebView accepts.
 *
 * On Android, Capacitor hands each `server.allowNavigation` entry to the WebView as an origin
 * rule too (`https://` + the entry, for one with no scheme), and the WebView accepts a rule only
 * as Chromium's origin matcher parses one (`components/origin_matcher`): `*`, or a scheme, `://`,
 * and a host with at most one wildcard, as a leading `*.`. So the private IP ranges iOS navigates
 * by (`network.mjs`, `PRIVATE_HOST_PATTERNS`) cannot be in Android's list: there, a gateway at
 * such an address opens outside the app, and the shell says so first (`boot.mjs`).
 *
 * `android/` is generated and never committed, so every Android build is of a copy this hook
 * wrote: `npm run build:android` is `cap sync android` first. A build from an `android/` folder
 * synced before this hook existed keeps the earlier list until it is synced again.
 *
 * Run by the Capacitor CLI as the `capacitor:copy:after` hook (`package.json`), once it has
 * written each platform's `capacitor.config.json`, with `CAPACITOR_PLATFORM_NAME`,
 * `CAPACITOR_ROOT_DIR` and `CAPACITOR_CONFIG` set. On Android it writes the list again with only
 * the entries Android accepts, says which it took out, and fails if the file is not where a copy
 * writes it. On any other platform it does nothing.
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** The origin rule Capacitor makes of a navigation entry (`Bridge.setAllowedOriginRules`). */
export function originRuleOf(entry) {
  const text = String(entry)
  return text.startsWith('http') ? text : `https://${text}`
}

/**
 * Whether Android's WebView accepts *rule* as an origin rule, as Chromium's
 * `OriginMatcher::AddRuleFromString` parses one: `*` alone; else a scheme with no wildcard,
 * `://`, and, for http and https, a host (with an optional port) that holds no `/` and at most
 * one wildcard, as its leading `*.`. Other schemes take no host.
 */
export function androidAccepts(rule) {
  const raw = String(rule ?? '').trim()
  if (raw === '*') return true
  const at = raw.indexOf('://')
  if (at <= 0) return false
  const scheme = raw.slice(0, at)
  if (scheme.includes('*')) return false
  const hostAndPort = raw.slice(at + 3)
  const takesHost = scheme === 'http' || scheme === 'https'
  if (!hostAndPort) return !takesHost
  if (!takesHost) return false
  const host = hostOf(hostAndPort)
  if (host === null || host.includes('/')) return false
  const wildcards = [...host].filter((c) => c === '*').length
  return wildcards === 0 || (wildcards === 1 && host.startsWith('*.'))
}

/** The host of `host[:port]` (`[v6]` keeps its brackets), or `null` for one that is malformed. */
function hostOf(hostAndPort) {
  if (hostAndPort.startsWith('[')) {
    const close = hostAndPort.indexOf(']')
    if (close < 0) return null
    const rest = hostAndPort.slice(close + 1)
    return rest === '' || /^:\d{1,5}$/.test(rest) ? hostAndPort.slice(0, close + 1) : null
  }
  const parts = hostAndPort.split(':')
  if (parts.length > 2) return null
  if (parts.length === 2 && !/^\d{1,5}$/.test(parts[1])) return null
  return parts[0] || null
}

/** The navigation entries Android can keep, in their order, and those it cannot. */
export function androidNavigation(entries) {
  const kept = []
  const refused = []
  for (const entry of entries ?? []) (androidAccepts(originRuleOf(entry)) ? kept : refused).push(entry)
  return { kept, refused }
}

/** The Android project's folder, as the config names it (`android.path`), else `android`. */
function androidFolder(config) {
  const path = config?.android?.path
  return typeof path === 'string' && path ? path : 'android'
}

/**
 * The hook: on Android, write the copied config's `server.allowNavigation` again with only what
 * Android accepts. Returns what it took out; throws when the copied config cannot be read.
 */
export function rewriteAndroidConfig(env = process.env, log = console.log) {
  if (env.CAPACITOR_PLATFORM_NAME !== 'android') return { refused: [] }
  const root = env.CAPACITOR_ROOT_DIR || process.cwd()
  const config = env.CAPACITOR_CONFIG ? JSON.parse(env.CAPACITOR_CONFIG) : {}
  const file = join(root, androidFolder(config), 'app', 'src', 'main', 'assets', 'capacitor.config.json')
  const copied = JSON.parse(readFileSync(file, 'utf8'))
  const { kept, refused } = androidNavigation(copied?.server?.allowNavigation)
  if (refused.length) {
    copied.server.allowNavigation = kept
    writeFileSync(file, `${JSON.stringify(copied, null, '\t')}\n`)
    log(
      `android: left out of server.allowNavigation, since Android refuses them as origin rules: ` +
        `${refused.join(', ')}. A gateway at one of these opens outside the app.`,
    )
  }
  return { refused }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    rewriteAndroidConfig()
  } catch (err) {
    console.error(`android: could not hold the navigation list to what Android accepts: ${err.message}`)
    process.exit(1)
  }
}
