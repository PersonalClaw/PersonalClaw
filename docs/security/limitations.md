# Security Limitations — What We Do Not (Yet) Enforce

PersonalClaw's strongest claim is that its controls are *enforced at the point of
execution*, not merely requested in a prompt. Honesty requires naming the places
where that is not (yet) literally true. These are deliberate, documented
tradeoffs — not oversights — and each is stated here in the same terms the
internal architecture uses, without softening.

This page is referenced from the public
[threat model](threat-model.md) and from `SECURITY.md`. Verified against the
codebase at the commit that introduced this file.

## 1. ACP agents under auto-approve (YOLO) rely on system-prompt framing, not rails

Task modes (`agent` / `ask` / `plan` / `build`) decide *which tools may run*. For
the **native runtime**, this gate is hard-enforced: `task_modes.py` is enforced
in `_guard_and_invoke` **before approval is consulted, so a Trust/YOLO
auto-approve can never bypass a task-mode restriction**
(`src/personalclaw/task_modes.py`).

For **ACP agents** (external CLI agents driven over the Agent Client Protocol),
the same module is applied in the dashboard's permission handler as
"belt-and-suspenders for ACP runtimes that gate via their own protocol path"
(`task_modes.py`). But an ACP agent running under YOLO ultimately gates through
its own protocol path, and the architecture states the tradeoff plainly:

> Task-mode tool-gating postures are hard-enforced at the permission prompt for
> the native runtime; ACP agents under YOLO rely on system-prompt framing (a
> documented tradeoff — `task_modes.py`).
> — [`docs/architecture/security.md`](../architecture/security.md#trust--yolo-state-trust_modepy)

**What this means for you:** if you enable auto-approve (YOLO) *and* run an
external ACP agent, that agent's tool use is bounded by prompt framing rather than
by the same hard rail the native runtime enforces. Running a trusted native agent,
or leaving approval prompts on, keeps the hard rail in force.

## 2. The app `network` permission is declaration-only

An app manifest declares a permission scope (`api` / `events` / `mcpTools` /
`storage` / `network` / `memory` / `cron`). Most of these are enforced
server-side by the gateway. **`network` is not**, by design:

> `can_use_network` — **DECLARATION-ONLY (unenforced by design)**, and the consent
> surface says so rather than implying otherwise. There is no per-app egress
> chokepoint to enforce at: an app's provider code is imported **in-process** by the
> gateway, so its own `httpx`/`requests` calls are the gateway's egress, and an app
> with a backend owns a separate OS process with its own network stack … So the flag
> is DISCLOSURE, and the Store discloses it as such … Treat `network: true` as an
> honest declaration, not a boundary.
> — `src/personalclaw/apps/permissions.py`

The consent surface states the non-enforcement outright. The Store shows the
network claim **outside** the list of permissions the gateway enforces, labelled
advisory, with the text *"PersonalClaw does not confine an app's outbound traffic:
this app's code can reach the network either way. The declaration is disclosure, not
containment."* It is shown whether or not the app declares `network`, so an app that
declares `network: false` is not presented as one the platform has blocked. An app's
*gateway-mediated* reach is separately bounded by its `api` permission; what is
**not** bounded is the app's own outbound traffic.

That `api` bound has two halves, and the second is worth knowing because a path
prefix says nothing about power. The allowlist half is what the app declares and
what the Store shows you. The other half is a closed **owner-only** registry
(`apps/permissions.OWNER_ONLY_API_PATHS`) of capabilities no declaration reaches at
all, not even `"*"`: the terminal and its sessions, computer-use, the credential
store and secrets vault, the security audit log and SEL rotation, your login
password and second factor, gateway restart, and local token minting. Holding any
of those would make every other line in a manifest moot, so there is nothing to
scope — and before it existed, an app declaring `/api/ws` (the event socket)
prefix-matched `/api/ws/terminal/{id}` and got an interactive shell running as you.
A manifest that names one of these paths now fails to install. None of this touches
your own access to those surfaces; the refusal applies only to requests carrying an
app identity.

**What this means for you:** treat an installed app's `network: true` as a stated
intent you are consenting to, the same way you would trust any program you choose
to run — not as a sandbox that prevents the app from talking to the network. The
supply-chain scanner (quarantine → scan → consent → install, with `dangerous`
terminal) is the control that vets what you install; the `network` flag is
disclosure, not containment.

## 3. App Python dependencies install into the venv the gateway runs from

An app may declare `dependencies.pythonDependencies` in its manifest, and the
installer pip-installs them into the **shared** virtualenv the gateway itself runs
out of — there is no per-app site-packages. Core ships lean deliberately (heavy
provider and ML libraries are not core dependencies), so this is how an app brings
what it needs.

**What is enforced:** an app may not re-pin a dependency core owns. Before anything
is installed, `app_manager._reject_core_dependency_conflicts` refuses any declared
requirement that names a core-declared dependency unless the version already
installed satisfies it — so pip is never in a position to move a core dependency
under the running gateway. The check is fail-closed: an unparseable requirement, or
a core-owned name whose installed version cannot be read, denies rather than
installs. Requirements for libraries core does not own are unaffected — that is 20
of the 22 first-party apps that declare dependencies, so the check has something to
say about **two** of them: `design-critique` pins `Pillow` and `diarization-onnx`
pins `numpy`, both core-declared, and both are admitted only while the installed
version already satisfies the pin. The provider SDKs like `openai` and `anthropic`
are *extras*, not core dependencies, so every app declaring one of those is
unaffected. (Ratio measured 2026-09-07 against `PersonalClawApps` `f623b66`, over
the 22 manifests declaring `dependencies.pythonDependencies`, compared against
core's `pyproject.toml` `[project].dependencies`. It is a claim about another
repository at a moment in time: re-derive it, do not trust it.)

**What is not enforced:** the packages an app adds are still importable by
everything in the process, and pip may still move a *transitive* dependency that
core does not declare directly. Isolating app dependencies properly requires
out-of-process providers — today an app's provider code is imported in-process, so
there is no import boundary to scope a path to. That is a platform-seam change,
recorded as such rather than approximated here.

**What the consent surface tells you:** the declared specifiers, verbatim, on the
install-consent screen itself — `anthropic>=0.20`, not "this app installs some
packages". `app_manager.describe_python_dependencies` classifies each against the
same core pin set the guard above gates on, so a package core does not own reads as
new code entering the interpreter, while a core-owned pin (`Pillow>=10,<13`) reads
as "the version you already have must satisfy this, or the install is refused". An
app declaring none shows nothing at all. This section documenting the behaviour is
not a substitute for that: a user consenting in a modal does not read a threat
model, so the duty belongs to the surface where consent is given.

When core's own pin set cannot be read — which is also when the guard refuses the
install outright — every specifier degrades to the *new code* reading rather than
disappearing. Over-disclosing a package is safe; under-disclosing one is not.

**What this means for you:** an installed app can add libraries to the gateway's
environment, so install apps you trust — the supply-chain scanner (quarantine →
scan → consent → install, with `dangerous` terminal) is the control that vets them.
What an app cannot do is silently change the version of a library the gateway
depends on.

## 4. An app's frontend bundle runs in the dashboard's own page

An app with a UI ships an ESM bundle. The host **fetches it, rewrites its bare
import specifiers to host-provided blob shims, and `import()`s the result into the
dashboard page** (`web/src/app/appSdk.tsx::loadContributedModule`), then mounts its
exported `mount` function in the host React tree
(`web/src/pages/apps/ContributedPage.tsx`, via `createRoot`). There is no iframe on
that path — `grep -c iframe` over `web/src/pages/apps/*.tsx` is `0` in every file.
The architecture doc states the posture plainly:

> **The gate is a declaration, not a sandbox.** … a contributed page already runs in
> the host React tree (`ContributedPage` mounts it with `createRoot`, no iframe) with
> the host `window`, so an undeclared app is not *prevented* from reaching the same
> components. What the block buys is legibility.
> — [`docs/architecture/app-platform.md`](../architecture/app-platform.md)

So an installed app's UI code has the dashboard's `document`, its `localStorage`, the
owner's session cookie, and authenticated same-origin reach to every `/api/*` route —
the same authority the dashboard itself has. Sharing the host's single React instance
is what the rewrite exists to do, and it is what makes this same-origin by
construction.

**What is enforced:** every call an app makes through the SDK client
(`createAppApi` / `useAppApi`) carries a short-lived app-scoped token, and
`app_permission_middleware` refuses a path the manifest did not declare — 403 + a
Security Event Log row, before the handler runs. An app's **backend** holds no other
credential, so for it that allowlist (and the closed `OWNER_ONLY_API_PATHS` registry
in §2) is a real boundary.

**What is not enforced:** the bundle is under no obligation to use that client. A bare
`fetch('/api/…')` from app UI code carries the owner's cookie and *no* app identity, so
`app_permission_middleware` — which acts only on requests that carry one — passes it as
an owner request. That reaches the owner-only surfaces too: the terminal, the credential
store, the audit log. The manifest's `api` allowlist therefore bounds the app's backend
and its SDK calls, **not** its page code. `ui.components` (the `generative-component`
capability) is loaded by the shell for every *enabled* declaring app, so that module runs
without the user ever opening the app's page.

This is not closable from the server side: a same-origin request from an app's bundle is
indistinguishable from the dashboard's own. It needs a distinct **origin** for app UI. A
`sandbox` attribute alone cannot deliver it while the bundle shares the host's React
instance, and for the same reason `ChatEmbed`'s frame
(`allow-scripts allow-same-origin`) is not an isolation boundary either — it is a
separate document, not a separate origin.

**What this means for you:** an installed app's UI is host code, so treat installing a
UI-bearing app the way you would treat running any program as yourself — the
supply-chain gate (quarantine → scan → consent → install, with `dangerous` terminal) is
the control that vets it. Install consent says so at the moment you decide: an app that
ships a UI carries an advisory row naming the host-page reach, beside the permissions
the gateway does enforce. An app that ships no UI runs no code in your browser at all,
and the same row says that too.

## 5. The bundled default model is a floor, not an assistant

A fresh install can chat before you set anything up because PersonalClaw offers one small
model you can download: `SmolLM2-135M-Instruct`, in the Q8_0 GGUF build
`unsloth/SmolLM2-135M-Instruct-GGUF`, under Apache-2.0. The native `bundled-chat` app runs
it inside the gateway, on your CPU, with numpy
(`src/personalclaw/apps/native/bundled-chat/provider.py`). The model, its licence, its
pinned source and its sha256 are recorded in
[`bundled-model-signoff.txt`](../architecture/bundled-model-signoff.txt).

It is there so a new install is not a dead end. At 135 million parameters it can greet you
and answer a simple factual question, and it gets unreliable quickly after that. When it
answers because nothing else is bound, the chat screen says so.

**What it does not get:**

- Tools. The provider declares `supports_tools = False`, so the agent loop runs it with an
  empty toolset. It cannot read or write a file, run a command, search the web or call an
  app, and no approval prompt appears because there is nothing to approve. Asked to create
  a file, it replied with a Python snippet for you to run, and nothing was written.
- The context PersonalClaw builds. For other models, each turn is assembled into one
  prompt: the agent's instructions, your memory and preferences, the skills chosen for the
  turn, today's date, then your message. On a fresh home that came to about 20,000
  characters. A model this small, handed that much, continues the instructions instead of
  following them, so the app declares that it takes your message alone (`request_only` in
  `provider.py`) and PersonalClaw sends it nothing else. On the measured first turn the
  model saw 18 tokens. It does not know your name, your notes or the date, and it does not
  know it is PersonalClaw either. Asked "What can you do for me?", it offered to help with
  health questions.
- Room. It reads 4,096 tokens at a time (the **Prompt budget** setting; the weight itself
  accepts 8,192, and the setting cannot go higher). That includes its reply, which stops at
  320 tokens (**Maximum reply length**), so about 3,770 tokens are left for the
  conversation, and older turns are dropped first. A message that does not fit on its own is
  refused before the model reads it, with a sentence giving the limit in tokens and roughly
  in characters. For plain English prose at the defaults that was about 3,765 tokens, or
  roughly 15,000 characters. A reply that stops at the maximum length is marked **Cut off**.

**What it did with real requests**, measured on the shipped weight through the
dashboard's chat route:

1. "What is the capital of France?" got Paris.
2. Asked for today's date, it said 2019.
3. Asked to search the web for news about the Mars rover, it wrote a confident summary from
   memory with invented facts, among them a Curiosity launch on a SpaceX Falcon Heavy.
4. Asked for three things in one message (list three fruits, say which has the most vitamin
   C, write a two-line poem about it), it listed three fruits and did neither of the other
   two.
5. Told a name and a city and then asked for them back, it repeated its previous reply word
   for word.

In none of these did it say it could not do something or did not know, so treat whatever it
tells you as unverified.

**It also answers for the rest of PersonalClaw.** With nothing else bound, anything that
asks for a chat model falls back to it
(`providers/provider_bridge.py::_resolve_from_config_registry`). That includes the jobs
that run after each reply to name the chat, tag it and suggest follow-ups, so every reply is
followed by more work for it on your CPU. In the same measurement, none of its four
follow-up suggestion replies came back in a form the chat could use. Goal loops and
workflows resolve their model the same way, so with nothing else bound they get this model
too.

**What it costs:** it loads on your first message and stays loaded. The weights take 538 MB
as float32, and loading them added 0.7 to 0.8 GiB to the resident size of the process that
holds them (measured on an Apple silicon Mac). Reading a long prompt takes more on top of
that: in a process holding only the model, a full 4,096-token prompt peaked at 1.3 GB
resident and took 21 seconds, and an 8,192-token one peaked at 1.8 GB and took 62 seconds.

**What is enforced:** the download never starts by itself. It is offered with its size
(138 MiB) in onboarding, on the chat screen and in **Settings → Providers**, and runs only
when you ask for it. It comes over https from a pinned revision on Hugging Face and is
checked against the sha256 in the sign-off record before it is installed. A file over the
150 MiB ceiling is refused, and a transfer that fails, is cancelled or does not match leaves
nothing behind that PersonalClaw would load. The record's licence must be on an allowlist
(Apache-2.0 or MIT); anything else is refused by name.

**What is not enforced:** the sha256 check guards the download, not the file on disk. If you
copy the weight into `$PERSONALCLAW_HOME/models/bundled-chat/` yourself, which is how you set
up a machine that has no network, PersonalClaw checks only that the file is at least the
signed-off size before it loads it. Compare its sha256 with the record's first, or run
`PERSONALCLAW_HOME=<home> python scripts/fetch_bundled_model.py --check` from a checkout.

**What this means for you:** use it to see chat working and to look around. For real work,
bind a model under **Settings → Models**. A local [Ollama](https://ollama.com) needs no key
and keeps working offline; every other provider is an app in the Store
([Getting started §3](../guides/getting-started.md#3-configure-a-model-provider)). Whatever
you bind wins, because this model answers only when nothing else does, and there is no
config to clean up afterwards. To stop it answering at all, delete it under **Settings →
Providers**, or turn off **Answer when nothing else is bound** in its settings there. That
switch takes effect the next time the gateway starts.

## Why these are listed, not fixed

Per the project's lifecycle discipline, a control *gap* discovered while writing
documentation is recorded as a candidate for the security-hardening track — never
patched inline in a docs change. Every item above has a named future direction
(extending the hard rail to ACP protocol paths for #1; OS-level app isolation for
#2; out-of-process providers for the residual half of #3; a distinct origin for app
UI, with the SDK crossing it as a message channel, for #4; checking a hand-copied
weight's sha256 when it loads, for the gap in #5). This page will shrink as those land.
The rest of #5 will not: a small model is the point of a floor, and the remedy for its
limits is to bind a real one.
