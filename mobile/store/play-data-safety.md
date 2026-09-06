# Google Play data-safety declaration (draft)

The answers to paste into **Play Console → App content → Data safety** for
`dev.personalclaw.companion`. The owner transcribes these when submitting
(owner task 4 in [MOBILE-COMPANION](../../docs/roadmap/plans/MOBILE-COMPANION.md));
this file exists so the answers are written down once, with the evidence, instead
of being re-derived under a console form's time pressure.

Every answer below follows from one architecture fact: **the app is a WebView
shell over the user's own self-hosted gateway.** There is no developer-operated
backend. The only servers the app ever talks to are the ones the user types in —
their own machines, on their own network ([mobile/README.md](../README.md)).

## The form answers

| Form question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **No** |
| Is all of the user data collected by your app encrypted in transit? | *(not asked once "No" above)* |
| Do you provide a way for users to request that their data is deleted? | *(not asked once "No" above)* |

Play's definition of "collect" is transmitting data off the device to the
developer or to a third party the developer directs it to. Nothing in this app
does that. The result on the store listing is the **"No data collected"** badge —
which must stay truthful release over release, so the walk-through below is the
checklist to re-run whenever the shell gains a capability.

## Why "No" is truthful, edge case by edge case

**The gateway address the user types in** is saved on the device, in the
WebView's `localStorage`, in the registry format `web/src/lib/endpoints.ts`
owns. It is sent only *to that gateway itself* as ordinary HTTP requests. It
never leaves for any server the developer operates — none exists.

**The device session** is an httponly cookie (`pc_token_{port}`) set by the
user's own gateway during QR pairing and held in the WebView's cookie jar. The
shell stores no credential at all (`mobile/README.md`, "The two things it
configures"). First-party traffic between the user and the user's own server is
not collection by this app's developer under Play's definition.

**The push token** (FCM registration token, when the user enables push) is
delivered to the user's own gateway via `POST /api/push/relay-register` — a
same-origin call the served companion makes (`web/src/app/nativePush.ts`) — and
pings are forwarded through whatever relay the *user* configured
(`mobile.relay_url` — their own deployment of the open-source relay, or none). What transits the relay is a
routing envelope around two identifiers — `{kind, item_id}` — and no message
content. That is not a policy intention but an enforced rail:
`assert_content_free` sits inside the sender, and
[`tests/test_mc9_relay_push.py`](../../tests/test_mc9_relay_push.py) asserts on
the exact bytes handed to the HTTP layer, downstream of every composer. The
relay's own repository carries the ids-only log audit for the receiving side.

**No analytics, no crash reporting, no ads.** The dependency list is closed and
short — `@capacitor/{core,ios,android,push-notifications}`
([mobile/package.json](../package.json)) — and the shell's entire UI is one
bootstrap screen (`www/`). There is no SDK on board that *could* collect.

**Web content the WebView renders** comes from the user's gateway.
`server.allowNavigation` fences in-app navigation to private-network hosts;
everything else is kicked out to the system browser
([mobile/README.md](../README.md), "Navigation is fenced to your private
network"), so third-party pages never run inside the app's WebView.

## Security-section answers (asked regardless of collection)

- **Encryption in transit:** traffic goes only to the user's own gateway, over
  whatever transport the user configured (the docs lead with HTTPS or a
  Tailscale-encrypted path). Answer honestly: the developer does not control
  the user's transport.
- **Data deletion:** nothing is held by the developer, so there is nothing to
  request deletion of. On-device state is cleared by uninstalling (or the OS's
  "clear app data").

## When this declaration must be revisited

Re-run the walk-through above (and update this file in the same PR) if the shell
ever gains: an analytics/crash SDK, a developer-operated default relay the user
did not configure, any native storage of credentials, or any request to a host
that is not user-configured. Any of those flips at least one form answer.
