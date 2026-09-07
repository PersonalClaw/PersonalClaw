# The desktop app

PersonalClaw's desktop shell is a thin Electron wrapper around the same gateway and
dashboard you get in a browser. It exists for the things a browser tab cannot do: a
menu-bar presence, native permissions, and a **global push-to-talk shortcut** that works
while another app has focus.

Everything the shell adds is a *capability*, granted individually and visible in
**Settings → Security → Desktop capabilities**. In a browser tab that panel says "desktop
app not connected" and none of this applies.

## The menu-bar item

The shell puts an icon in the macOS menu bar and keeps it there while PersonalClaw is
running. Its menu is live, refreshed every few seconds from the local gateway:

- **Approvals waiting** — how many tool approvals are pending. Clicking the row opens
  the chat surface where they are answered. It stays clickable at zero, so it is a way
  in rather than a row that greys out the moment you catch up.
- **Loops running** — a submenu of the loops currently `running`; each entry deep-links
  to that loop. Paused, blocked and awaiting-input loops are *not* counted here — they
  are active, but nothing is working on them.
- **Quick Capture Note** — opens the Inbox ready to capture. *(The note-writing half is
  not built yet; today this is a shortcut to the Inbox.)*
- **Open Dashboard**, **Open at Login**, and **Quit PersonalClaw**.

When the count beside the icon and the "● Listening" capture indicator want the same
space, **listening always wins** — a live microphone is never hidden behind a badge.

If the gateway cannot be reached the menu says **"not connected"** rather than showing
zeroes, because a zero looks like good news.

### Closing the window is not quitting

Closing the window hides it; the menu-bar item brings it back, and PersonalClaw keeps
running. If the menu-bar item could not be created — a missing icon, or a platform
without one — the shell notices and closes the window for real instead, so you can
never end up with a running app you have no way to reach.

## Open PersonalClaw at login

**Off by default.** There are two places to turn it on, and they are the same switch:
the menu bar's **Open at Login**, and **Settings → Security → Desktop capabilities →
Open at login**. Flip either and the other follows — there is one registration, not one
per surface.

It registers this app with macOS Login Items — the same list in **System Settings →
General → Login Items**, where you can also remove it without launching PersonalClaw. It
needs no administrator password, writes no launch agent of its own, and turning it on
twice cannot leave two entries behind.

macOS owns this setting, so PersonalClaw keeps no copy of it. Both surfaces read the
registration from the OS every time they draw, which is why removing it in System
Settings shows up here rather than leaving a switch stuck on. If macOS declines the
change, the switch says so and stays where the OS actually left it.

In a browser tab there is no switch: registering a login item needs the desktop app, and
a toggle that could not do anything would be worse than an honest absence.

## Connecting to a gateway you did not start

By default the desktop app starts its own gateway on this computer and loads that. That has not
changed, and nothing below happens unless you ask for it.

**Gateway → Gateways…** (⌘⇧G) opens the switcher. It lists every gateway you have paired with, marks
the one you are looking at, and lets you add another — a PersonalClaw running on a different machine
on your network, or your own gateway reached through the [remote access](remote-access.md) tunnel.
Picking a different one reloads the dashboard from that gateway. Nothing is shared between them: see
[No hub, ever](companion-apps.md#no-hub-ever).

### Adding one: paste the pairing link

On the gateway you want to reach, open **Settings → Devices → Pair a device**. It gives you a link
that looks like `http://claw.local:10000/pair?code=ABCD-2345`. Paste that whole link into the
switcher.

Paste the *link*, not just the code, because the address in it was composed by that gateway rather
than typed by you — so there is no address to get wrong. The app then shows you exactly which host it
is about to open, and which network that host is on, and waits for you to say yes.

You can type a bare address instead (`192.168.1.5:10000`) if you are reading it off a screen. Same
checks, same confirmation, but now the address is only as right as your typing.

### What the app refuses, and what it asks you about

The three cases are genuinely different and the app treats them differently:

| Where the gateway is | `http://` | `https://` |
|---|---|---|
| This computer (`localhost`, `127.0.0.1`) | fine, no prompt — this is the default mode | fine |
| Your network or tailnet (`10.x`, `192.168.x`, `172.16–31.x`, `100.64–127.x`, `*.local`, `*.ts.net`) | allowed **once you confirm**, with a plain warning that the traffic is readable by anyone else on that network | allowed once you confirm |
| Anywhere else (the public internet) | **refused** | allowed once you confirm |

Plaintext to a public address is refused rather than warned about. A gateway reached over the
internet goes through your own tunnel, which terminates TLS, and the session cookie it sets is
`Secure` — so it would not be sent over `http://` anyway. "It does not work" is not a trade-off worth
offering you.

A few addresses are refused outright, and they are all the same kind of thing: an IP written as a
number or in hex (`2130706433`, `0x7f000001`), an IPv4 address written as IPv6
(`::ffff:127.0.0.1`), a link-local address (`169.254.x` — one of which is a cloud metadata service),
and any address with a username and password in it. None of these is something you would type on
purpose, and each is a known way to make one program disagree with another about which machine an
address names.

A name is also checked against what it actually resolves to. If `brain.example.com` points into your
LAN, the app says "your local network" rather than "the public internet". If it points at more than
one kind of network at once, the app will not connect, because it cannot tell you one true thing
about where you are going. And a name that resolves to `127.0.0.1` is treated as a network address,
never as this computer — the next section is why that distinction is load-bearing.

### Desktop capabilities are for the gateway on this computer only

The microphone, the global shortcut, native notifications and the login item are exposed to the
dashboard through a bridge the app attaches to the page. **That bridge is only ever attached to the
gateway this app started.** A gateway on the network gets a plain window: the panel in **Settings →
Security → Desktop capabilities** reads "desktop app not connected", exactly as it does in a browser
tab, and push-to-talk does nothing there.

That is not a limitation waiting to be lifted. The bridge is authorised by a secret file in
`~/.personalclaw`, which proves "I am running as you, on this machine" — a claim no other gateway can
check and none should be asked to trust. So the shortcut and the microphone follow the local gateway,
and switching to a paired one leaves them behind.

### When a paired gateway stops answering

The switcher shows a status per row, and the statuses mean different things on purpose:

- **Not checked yet** — nobody has asked. Not the same as a failure.
- **Not answering** / **Timed out** — the machine is off, asleep, or off the network. The app
  re-checks on a widening delay a bounded number of times, then stops and waits for you. When it
  answers again, the dashboard reloads by itself.
- **Needs pairing again** — the gateway answered and refused. That is what a revoked device session
  looks like. The app stops immediately and does not try again: re-presenting a credential that was
  just refused achieves nothing and would trip that gateway's own pairing lockout. Pair it again from
  its Devices panel.
- **Answered, but not a PersonalClaw gateway** — something is at that address, and it is not this.
- **Redirected somewhere else** — the address answered by pointing at a third host. The app does not
  follow it.

Revoking one gateway's device session breaks that row and nothing else. The others keep working, and
switching back to them does not ask you to pair anything.

### Where the list is kept

In `~/.personalclaw/desktop/shell-store.json`, readable and writable by your account only. If those
permissions are ever loosened — so that another account on the machine could edit it — the app will
not silently connect to whatever it finds there: it starts its own gateway and asks you to confirm the
address again. If the file is damaged it is **left exactly as it is** rather than overwritten, so
nothing is lost while you look at it.

The file holds addresses and names. It holds no tokens: your session for each gateway is an http-only
cookie in the app's own cookie jar, which is also why pairing happens on the gateway's own page
rather than inside the app.

## Quitting shuts the gateway down, not off

The desktop app starts its own gateway, so quitting has to stop it in the right order.
Quit asks the gateway to shut down and then **waits for it to actually exit** before the
app goes away, rather than sending a signal and disappearing — the difference between a
clean stop and a half-finished write. A gateway that will not stop is escalated after a
few seconds, and if it still will not, the app says so in its log rather than pretending
the quit was clean.

## Push-to-talk

Press the shortcut, speak, press it again. The recording is transcribed by whichever
model you bound to the **STT** use case (Settings → Models) and the text is inserted into
the composer **at your cursor** — it does not replace or append to what you had already
typed.

Dictation needs somewhere to land, so the shortcut is armed wherever a composer is — chat and
the goal composer. On a screen with no composer (Settings, say) it does nothing rather than
recording into nowhere, which also means the capture indicator is never lit somewhere you cannot
see what it is feeding.

The shortcut is configurable in **Settings → Speech & Transcription → Hands-free voice**.
The default is `⌘⇧Space`. A shortcut needs at least one modifier: a bare key would be
taken away from every other app on your machine, so PersonalClaw refuses to bind one. If
another app already owns the chord you pick, the setting says so and keeps your previous
shortcut rather than silently failing at the next launch.

### It toggles; it does not read key-release

The global shortcut **toggles** capture: one press starts, the next stops. This is a real
platform limit rather than a shortcut we took — Electron's global-shortcut API delivers a
press and exposes no key-release event, and reading raw key-up system-wide would mean
requesting an accessibility-level input tap, which PersonalClaw does not do.

The practical difference is that you cannot hold the chord down. So that a forgotten
toggle cannot leave the microphone open indefinitely, a capture that runs past two
minutes is stopped for you.

### While it is capturing you can always see it

Two indicators are lit while the microphone is live, and both matter:

1. **macOS's own** orange microphone dot in the menu bar. That one is drawn by the
   system, and PersonalClaw cannot suppress or fake it — which is exactly why it is the
   one to trust.
2. **PersonalClaw's menu-bar item** changes to `● Listening`. This says *which* app is
   listening, which the system dot cannot. It is in the menu bar rather than in the page
   because the shortcut is global: if the window were hidden behind a full-screen app, an
   in-window indicator would be a capture indicator you could not see. Inside the app
   there is also a "Listening" chip beside the composer.

Releasing the toggle ends the recording and **stops the microphone track**. The app does
not hold an idle-but-open microphone between captures.

If you have not granted microphone access yet, the first capture asks. If you have
already denied it, PersonalClaw cannot re-ask — macOS will not prompt twice — so it tells
you that and points at Privacy & Security in System Settings.

## System audio is not captured — microphone only

PersonalClaw captures **the microphone only**. It does not capture system audio: not what
your speakers are playing, not the other side of a call, not another app's output.

This is a deliberate refusal, not an unfinished feature. macOS exposes no audio-only tap
for system output; the only route is the **Screen Recording** entitlement's audio
side-channel, which would mean asking you for the right to record your screen in order to
record sound. That is not a trade worth making quietly for a transcription feature, so
there is no code path in PersonalClaw that captures system audio at all.

Probing the capability reflects this: `system_audio` reports `unavailable` with that
reason on every platform, rather than reporting "not implemented" as though a later
release will simply turn it on.

## Native notifications

The desktop app can deliver notifications as real OS notifications instead of only as a
badge on the dashboard's bell. It is **per notification kind**, not a global switch:
Settings → Notifications → the rules matrix → open a row's *detail* → tick **Desktop
notification**. Nothing is ticked for you.

Two things follow from where that switch lives:

- **A kind you have set to Badge or Digest never raises one**, even with Desktop ticked.
  Those modes mean "do not interrupt me", and an OS banner is an interruption. Only the
  **Notify** mode delivers natively.
- **The dashboard is always the record.** A native notification is an *addition* — the
  note still lands in the bell and the notifications feed. So nothing is lost when the
  desktop app is closed: the rule simply falls back to the dashboard delivery it would
  have had anyway, and the note's stored detail says why (`the desktop shell is not
  connected`).

Clicking a notification brings PersonalClaw forward and opens the surface the note came
from — an inbox alert opens Inbox, a loop's progress opens Loops, a skill proposal opens
Skills. A kind with no surface of its own opens the notifications feed.

macOS never tells an app whether notifications are authorized, so PersonalClaw cannot show
you a truthful "granted" state for this one and does not pretend to (Settings → Security →
Desktop capabilities says as much). The first notification asks; after that, **System
Settings → Notifications → PersonalClaw** is the only control. If you have turned them off
there, ticking Desktop in a rule will silently do nothing — that is macOS's answer, not a
bug in the rule.

## Platforms

The shell ships for **macOS** and **Linux x86-64**. There is no Windows build — see
below for exactly why and what would change that.

### Linux — AppImage and .deb, unsigned

Every release attaches two Linux artifacts, built by CI from the same tree as the
release tag:

- `PersonalClaw-<version>.AppImage` — self-contained: `chmod +x` it and run it.
- `personalclaw-desktop_<version>_amd64.deb` — install with
  `sudo apt install ./personalclaw-desktop_<version>_amd64.deb`.

**Neither artifact is code-signed, and that is deliberate, not a gap.** Linux has no
OS-level signing gate — nothing analogous to macOS Gatekeeper consumes a signature on
an AppImage or a .deb — so a signature would change nothing your system checks. What
you will actually see:

- The AppImage runs after `chmod +x` with **no provenance prompt at all**, which is
  exactly why *where you downloaded it* is the whole integrity story. Get it from the
  GitHub Release page only.
- `apt` will not warn about the .deb either: repository GPG signing applies to
  packages served *from an apt repository*, and this file installs directly. Same
  rule — download from the release page.

(App *bundles* installed inside PersonalClaw are a separate, signed story — see
[artifact signing](../security/signing.md).)

One Linux-specific behavior worth knowing: the menu-bar presence above depends on
your desktop environment offering a tray. On one that does not, the shell notices —
closing the window then quits for real instead of hiding, exactly as described under
"Closing the window is not quitting", so you can never strand a running app you
cannot reach.

### macOS — built from a checkout, for now

The dmg is not attached to releases yet: that step waits on Apple Developer
signing + notarization credentials that do not exist in CI today. Until they do,
build from a checkout with `make desktop-dist`. A locally-built, unsigned app gets
macOS's normal Gatekeeper treatment on first launch.

### Windows — deferred (2026-09-05)

There is no Windows shell, and none is planned until **both** of these change:

1. The [native-Windows audit](../roadmap/research/windows-native-audit.md) ruled the
   backend port **no-go** (its "Go / no-go" section): a native port would silently
   weaken file-permission and sandbox guarantees the rest of the system depends on.
   That doc's demand-evidence criteria are the flip condition.
2. Windows code-signing secrets exist. None do — and an unsigned Windows executable
   is SmartScreen-hostile in a way an unsigned AppImage is not: users would see a
   scary "unrecognized app" interstitial on every install.

The desktop shell follows platform support, never leads it. Windows users have real,
tested backend paths today: [WSL2 or Docker Desktop](platforms.md#windows-via-wsl2).

## Related

- [Platforms](platforms.md) — which OSes the desktop shell targets.
- [Configuration reference](../reference/configuration.md) — `voice.push_to_talk_chord`
  and the rest of the voice settings.
