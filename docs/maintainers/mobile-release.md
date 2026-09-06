# Mobile release runbook (TestFlight + Play internal track)

How to take a clean checkout to an installable **iOS TestFlight build** and an
**Android Play internal-track build** of the companion shell (`mobile/`).
Written like the [release runbook](release-runbook.md): where a number, path, or
flag appears here it came from running the tool or reading its source, not from
memory. Steps that need Xcode, an Android SDK, or a store console could not be
run where this page was written and are marked **(not exercised)** — walk them
with the page open and fix the page where reality disagrees.

Steps marked **OWNER** need the owner's store accounts and are owner tasks 3-4
in [MOBILE-COMPANION](../roadmap/plans/MOBILE-COMPANION.md): the enrollments,
the console clicking, and the review answers. Everything else any maintainer
can do.

## What a mobile release actually is

There is no CI job and no tag trigger: both stores require locally signed
uploads, so this is a maintainer-machine procedure. The committed inputs are
small and the native projects are **regenerated, never committed**
(`mobile/.gitignore`):

| Committed | Generated per release |
|---|---|
| `mobile/capacitor.config.json`, `mobile/www/` — the shell itself | `mobile/ios/`, `mobile/android/` — via `cap add` |
| `mobile/assets/` — the five images `@capacitor/assets` consumes, built by `mobile/scripts/generate_store_assets.py` from the brand assets | every AppIcon slot, adaptive-icon layer, and splash density — via `@capacitor/assets generate` |
| `mobile/store/PrivacyInfo.xcprivacy`, `mobile/store/play-data-safety.md` — the truthful no-data-collection declarations | — |

The consequence of "regenerated, never committed": every manual step you
perform inside `ios/` or `android/` (privacy manifest, push capability, version
stamp, `google-services.json`) **repeats on every regeneration**. They are kept
deliberately few and are the checklists below.

## Prerequisites

| | Needs |
|---|---|
| both | Node (repo root `npm ci`), the repo venv (asset regeneration only) |
| iOS | macOS + **full Xcode** (not just Command Line Tools), signed into Xcode with the owner's Apple ID. CocoaPods is **not** required: Capacitor 8's `cap add ios` generates a Swift Package Manager project (verified — the generated `ios/App/CapApp-SPM/Package.swift` pins `capacitor-swift-pm` 8.5.0) |
| Android | JDK 17+ and an Android SDK (`ANDROID_HOME` set; Android Studio is the easy way to get both) |
| **OWNER** | Apple Developer Program ($99/yr) and Google Play Console ($25 once) enrollments; a Firebase project for FCM if push is wanted on Android |

## Versioning — where the version lives

The shell's version of record is `mobile/package.json` → `version`. The native
projects regenerate with template defaults — verified: iOS
`MARKETING_VERSION = 1.0` / `CURRENT_PROJECT_VERSION = 1` in
`ios/App/App.xcodeproj`, Android `versionName "1.0"` / `versionCode 1` in
`android/app/build.gradle` — so stamping is a per-release step:

| Platform | User-facing version (set to `mobile/package.json`) | Build number (monotonic per upload) |
|---|---|---|
| iOS | `MARKETING_VERSION` (Xcode → App target → General → Version) | `CURRENT_PROJECT_VERSION` (→ Build) |
| Android | `versionName` in `android/app/build.gradle` | `versionCode` in the same block |

Both stores refuse a reused build number (App Store Connect lists uploaded
builds per version; Play rejects a duplicate `versionCode` outright), so "last
uploaded + 1" is recoverable from the consoles if you lose count. Bump
`mobile/package.json` in the PR that changes the shell, not during the build.

## From a clean checkout (both platforms)

```bash
npm ci                                 # repo root — single-root lockfile
npm run test:mobile                    # the shell's own gate, node only
```

The five source images in `mobile/assets/` are committed; regenerate them
**only when the brand assets changed**, and commit the result:

```bash
.venv/bin/python mobile/scripts/generate_store_assets.py
```

## iOS → TestFlight

```bash
npm run add:ios --workspace mobile     # generates mobile/ios/ (gitignored)
npm run assets:ios --workspace mobile  # icons + splash into the Xcode asset catalog
```

Both verified: `cap add ios` emits the SPM project with the
`@capacitor/push-notifications` plugin picked up, and the assets step writes 7
resources (the 1024px AppIcon plus universal light/dark splash at 1x/2x/3x)
into `ios/App/App/Assets.xcassets/`.

Then the regeneration checklist — the manual steps a fresh `ios/` loses:

1. **Privacy manifest.** Copy `mobile/store/PrivacyInfo.xcprivacy` to
   `ios/App/App/PrivacyInfo.xcprivacy`, then in Xcode (`npm run open:ios
   --workspace mobile`) File → *Add Files to "App"* → select it with **App
   target membership** checked. The generated project ships without one
   (verified against the 8.5 template), and Apple has required it in App Store
   submissions since spring 2024. **(Xcode half not exercised)**
2. **Signing.** App target → Signing & Capabilities → Team = the owner's team,
   automatic signing. Bundle id is `dev.personalclaw.companion` from
   `capacitor.config.json`. **(not exercised)**
3. **Push capability.** Same tab → *+ Capability* → **Push Notifications**, and
   **Background Modes** with *Remote notifications* checked — the shell wires
   `@capacitor/push-notifications`, and without the `aps-environment`
   entitlement registration fails at runtime. **(not exercised)**
4. **Version stamp** per the table above.

Archive and upload — Xcode is the primary path **(not exercised)**:

- Product → *Archive* → Organizer → *Distribute App* → **App Store Connect** →
  Upload. TestFlight picks the build up after processing.
- CLI alternative: `npm run build:ios --workspace mobile --
  --xcode-team-id=<TEAMID>` — the CLI's export method defaults to
  `app-store-connect` with automatic signing (flags verified from
  `npx cap build --help` at 8.5.0); upload the resulting `.ipa` with
  Transporter.app.

**OWNER — App Store Connect.** Register the bundle id, create the app record,
answer App Privacy with **Data Not Collected** (the walk-through in
[`mobile/store/play-data-safety.md`](../../mobile/store/play-data-safety.md)
is store-agnostic — same facts, same answer), add an internal TestFlight group.
Internal testing needs no App Review. Setting
`ITSAppUsesNonExemptEncryption = NO` in `ios/App/App/Info.plist` (the app uses
only standard TLS) skips the per-build export-compliance question — a
regeneration-checklist item if you adopt it.

## Android → Play internal track

```bash
npm run add:android --workspace mobile     # generates mobile/android/ (gitignored)
npm run assets:android --workspace mobile  # icons + splash into android/app/src/main/res/
```

Both verified: the assets step writes 74 resources — adaptive-icon foreground/
background layers per density, round + legacy launcher icons, and light/dark
splash per density/orientation.

Regeneration checklist:

1. **Push (optional).** Drop the owner-held `google-services.json` (Firebase
   console → the Android app with package `dev.personalclaw.companion`) into
   `mobile/android/app/`. The generated `app/build.gradle` applies the
   `google-services` plugin only when that file is present, and logs *"Push
   Notifications won't work"* when it is absent (verified in the generated
   gradle). Keep the file out of the repo.
2. **Version stamp** per the table above.

Signing — once, **OWNER**: create an upload keystore and keep it (plus its
passwords) out of the repo; opt the app into **Play App Signing** at first
upload so a lost upload key is recoverable:

```bash
keytool -genkeypair -v -keystore upload.jks -alias upload \
  -keyalg RSA -keysize 2048 -validity 9125
```

Build the signed bundle **(not exercised — no Android SDK where this page was
written)**:

```bash
npm run build:android --workspace mobile -- \
  --keystorepath /path/to/upload.jks --keystorealias upload \
  --androidreleasetype AAB
# prompts aside, expect android/app/build/outputs/bundle/release/*.aab
```

The flag set is verified against `npx cap build --help` at 8.5.0 (passwords can
go on the command line as `--keystorepass`/`--keystorealiaspass`, but letting
gradle prompt keeps them out of shell history).

**OWNER — Play Console.** Create the app, fill **App content → Data safety**
by transcribing [`mobile/store/play-data-safety.md`](../../mobile/store/play-data-safety.md)
(answer: no collection, no sharing), create an **Internal testing** release,
upload the `.aab`, add testers by email, and share the opt-in link. Internal
testing propagates in minutes and needs no review.

## The push leg, end to end

The store build is what finally exercises MC-9's on-device vendor leg (no rail
in this repo can): shell registers via `@capacitor/push-notifications` → the
served companion POSTs the token to the user's gateway
(`/api/push/relay-register`, `web/src/app/nativePush.ts`) → the gateway's
`relay` backend forwards **ids-only** pings through the user-configured
`mobile.relay_url` (`tests/test_mc9_relay_push.py` proves the envelope is
content-free). Record the first real handset round-trip in
MOBILE-COMPANION's validation log when it happens.

## Provenance of this page

Walked on 2026-09-05 against Capacitor **8.5.0** from a clean worktree, macOS,
no CocoaPods installed: root `npm ci`, `npm run test:mobile`,
`cap add ios` (SPM project, push plugin detected), `cap add android`,
`@capacitor/assets generate` for both platforms (7 iOS + 74 Android resources
from `mobile/assets/`), the asset-source regeneration script, and
`cap build --help` for the exact flag names. Confirmed by inspection: the iOS
template carries no app-level privacy manifest; the Android template's
`google-services.json` guard; both templates' version-field defaults.

Not exercised here, and therefore the steps to watch on first walk-through:
everything inside Xcode (add-files, signing, capability, archive, upload), the
Android release build itself, both store consoles, and any real handset.
