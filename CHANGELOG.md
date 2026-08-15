# Changelog

All notable changes to PersonalClaw are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The in-app Updates panel reads this file (`GET /api/changelog`) to show "what's new."

## [Unreleased]

### Added

- **You can now replay a finished workflow run and see exactly where an edit would change it.**
  `personalclaw workflow replay <run_id>` re-drives the run's decision path against its OWN recorded
  responses — it calls no model and spends nothing — and compares the result to the path the run
  actually took, reporting the first step that moved. Replaying an unchanged run reproduces its
  trajectory exactly; edit a step's prompt and replay names that step as the first to diverge, which
  is the question a mid-run edit really asks: *what did my change actually affect, and from where?*
  Divergence is a normal answer, not an error — a template edit is supposed to diverge, and the verb
  tells you where rather than failing.

- **Runs now record what LANDED, not just what they did.** A workflow that made a measurable
  decision could already journal the bet it was making and have it graded once the horizon passed.
  That was the only thing in the system able to do it. Now any producer can open the same kind of
  question, and two more already do: **publishing an artifact** asks whether anyone ever consumed it
  (a week's horizon), and **stopping to ask you a question** asks whether interrupting you was worth
  it — graded from your own answer, so an approval reads as the interruption landing, a rejection as
  a bet that lost, and a gate nobody ever answered as an interruption that went nowhere. Two
  restraints kept from the original: a question whose ground truth cannot be read closes as
  *inconclusive* rather than being invented, and that weaker evidence ages out about four times
  faster than a real measurement; and only a decision's outcome files anything for you to review —
  the rest are recorded in the run's own ledger, because "this artifact's outcome is inconclusive"
  is not something you can act on.

- **You can now ask which runs of a template went a different way — and get warned when they start
  going a worse way.** Every run now carries a *trajectory signature*: a fingerprint of the exact path
  it took — which steps ran, in what order, how each resolved, which branches it skipped. Two runs of
  a template that took the same path share the same signature, so "show me the runs that did something
  different" is finally a question with an answer, per run on its introspection view and per template
  at `GET /api/workflows/{name}/trajectory`. And when a template's recent runs shift onto a path that
  fails more often than the one they used to take, that shift is surfaced as a risk — not on the third
  run of a brand-new template (that is just a template that has barely run), but once there is enough
  history for the shift to be real. It costs nothing to compute: it is read straight from the ledger
  each run already writes, with no new tracking of any kind.
- **Work that nobody reads now says so.** Every watchdog until now measured whether a scheduled run
  was still *working* — findings, wall-time, errors, stagnation. None asked whether anyone ever
  opened what it produced, so a template quietly writing a deliverable on a cadence into a document
  nobody reads looked perfectly healthy. Now, when the last three deliverables of one work unit each
  sat a full week without being opened, pinned or edited, it appears in your review queue as a
  proposal to **pause or retire** it, naming the runs and the documents involved.
  It **never stops anything by itself.** "Nobody has looked yet" and "nobody will ever look" are
  different facts and only you can tell them apart, so this reports and waits — accepting the
  proposal does not change a schedule either. Three further restraints so it stays worth reading:
  one open, one pin or one edit anywhere in those three cycles and it stays silent; a document that
  was deleted counts as *unknown*, never as unread; and if you reject the finding once, it is not
  raised again.

### Changed
- **A step that reads another step's output no longer needs a hand-written ordering — and steps in
  different branches of a workflow can now feed each other.** The engine now derives "run after"
  directly from "reads the output of": if a step binds `{{nodes.other.output}}`, the scheduler holds
  it until `other` has finished, wherever the two sit in the workflow. Two things follow. A step that
  reads a sibling running alongside it is simply held rather than refused, so you no longer hand-write
  an ordering the engine could see for itself. And a shape that used to be impossible now works: a
  workflow can fan out into parallel branches and have a later step pull results from *across* those
  branches — a diamond spanning two containers — instead of being told the branches cannot reference
  each other. A hand-written `needs` still has one job, expressing an ordering that is not about data
  (a lock, "publish only after the announcement went out"); it is now checked against what the data
  already implies — the engine warns when a `needs` merely restates a binding, and refuses one the
  workflow's structure could never honour. No bundled template changed how it schedules.
- **A workflow that reads another step's output now refuses to save unless that step is guaranteed to
  run first.** The engine kept two separate pictures of how steps relate: what must run before what,
  and what reads whose output. Nothing checked they agreed, so a template could pull
  `{{nodes.some_step.output}}` from a step running *alongside* it — and instead of a clear complaint,
  the run failed partway through with "binding failed: check the referenced node id and field exist",
  pointing at a step id that was perfectly correct. That is now a typed error at save time
  (`WF_UNORDERED_DEP`) naming the reader, the step it reads, and why the ordering is missing. **No
  bundled template was affected** — all 19 were checked first, and every one already ordered its
  steps correctly. Nothing about how workflows run has changed.

- **A workflow step that declares what its output will contain is now checked against the steps that
  read it.** A step can declare an output contract — "this must be JSON, and it must contain these
  keys" — and the engine has always enforced it on the *producing* step. Nothing ever compared it to
  the steps reading that output, so a template could bind `{{nodes.classify.output.summary}}` from a
  step whose own contract promises `findings`, save perfectly clean, and then die partway through the
  run on an unresolvable reference. That is now a typed error at save time
  (`WF_UNSATISFIABLE_OUTPUT_REF`) naming the reader, the step it reads, the key it wanted and the
  keys the producer actually guarantees. In a workflow that already uses contracts, a step read at a
  sub-path but declaring none raises an advisory warning instead, listing the readers that would
  benefit. **No bundled template was affected** — all 19 were censused first: none declares an output
  contract today, so nothing shipped changes and nothing new appears in validation output. No new
  contract vocabulary was added, and nothing about how workflows run has changed.


- **A loop that keeps working but stops getting anywhere now stalls, even when it insists it is
  making progress.** The supervisor's stall detector used to read one number the worker itself
  writes — `new_findings_count` — and treated a missing number as "progressing". So a worker that
  reported *any* nonzero count, or simply stopped reporting one, could spin for its entire cycle
  budget on your money without anything noticing. The supervisor now also watches two things the
  worker cannot write for itself: whether the cycle report is **byte-identical** to the previous
  cycles', and whether every cycle **checked the same sources or touched the same files**. Either
  one stalls the loop to *"needs direction"* with the reason on the card, so you can steer it
  instead of paying for another ten identical cycles. The self-reported count is still used — it is
  the cheapest and clearest signal when it is honest — it just can no longer overrule what the
  supervisor can see, and its silence no longer counts as progress.
  A monitor loop still never stalls (a quiet cycle is the point), and a loop kind that records
  nothing to compare is judged only on content, so nothing stalls for lack of data. The number of
  no-progress cycles it takes is now a setting, `loops.stagnation_window` (default 5, minimum 2),
  read live — no restart needed.

- **A loop's judge no longer runs on the same model as the worker it grades.** Autonomous goal
  loops never let the worker certify its own work — a separate judge, in its own session with its
  own prompt and no write tools, decides whether a cycle is done. But that judge was resolving the
  same model binding as the worker, so the two shared a blind spot on exactly the question the judge
  exists to answer. The judge now resolves its own axis, `loops.judge_use_case`, which defaults to
  **Reasoning** — so if you have pinned a stronger model to Reasoning in Settings → Models, that is
  now the model that decides done-ness, and the loop's work still runs on the Loops binding. Set
  `loops.judge_use_case` to `loops` in `config.json` to put both back on one binding.
  A judge whose model is unavailable behaves exactly as before: the cycle is **deferred**, never
  reported complete, and the warning in the log now names the binding to go check.

- **The approval prompt now tells you what a tool call can touch, and how far your answer
  reaches.** When the agent asks permission to run a tool, the card is a four-part brief instead
  of a tool name and four buttons: **what** (the tool and its arguments), **why** (the one-line
  purpose, when the agent gave one), **what it can touch** — chips like "Runs a command", "Writes
  files", "Uses the network", "Reads only" — and **how far the answer reaches**, a
  "Remember this choice" picker (*Just this once* · *This chat* · *This agent*) that spells out, in
  plain text, exactly what gets remembered before you answer. Then one **Allow** and one **Deny**.
  The out-of-context approval toast (an approval raised in a chat you are not looking at) carries
  the same one-line summary, so "another chat session needs approval to run bash (runs a command)"
  is legible without opening it.
  Two deliberate restraints: the chips are **claims, not an audit** — only facets the system can
  actually establish are shown, and when it can establish nothing it says nothing rather than
  painting four reassuring negatives; and the brief **never advocates**. Nothing recommends
  approving, no answer is preselected or focused, and neither verb is styled as "the" action —
  the only thing chosen for you is the narrowest scope, which remembers nothing.

### Security

- **The built-in command denylist now repairs itself.** The 112 always-on patterns that refuse
  credential exfiltration, destructive commands and self-tampering used to live only as a list
  inside a Python module. Anything running in the same process — a stray monkeypatch, an agent that
  talked a tool into editing module state, a future refactor — could shorten that list, and every
  command screened afterwards would quietly obey the shorter version. The patterns now ship as a
  packaged data file with a sha256 over their exact contents and order, and every read re-checks the
  live list against it: a shortened, edited or reordered denylist is restored before the next command
  is screened, and the repair is written to your security event log as
  `baseline_denylist_reasserted` (with which patterns came back). A refused attempt to shrink the set
  is logged as `baseline_denylist_tamper_attempt`. Your own additions in
  `security.denied_commands` work exactly as before — they are appended, deduped against the
  built-ins, and can only ever make the list longer. `personalclaw doctor` gained a
  **Baseline command denylist integrity** row that re-verifies the packaged file and reports a
  divergence without ever adopting it, so a tampered file cannot shrink what is enforced.
  **Honest limitation:** this is protection against drift and against an agent tampering at runtime,
  not against you. Anyone who can edit the installed package before the process starts can change
  the baseline — it is your machine.
- **Settings → Security now shows which denylist is actually protecting you.** The panel listed 112
  patterns without saying where they came from, so there was no way to tell a healthy instance from a
  drifted one. It now shows the baseline's version, the sha256 recorded at release, how many patterns
  are enforced, and whether the packaged file still matches — and if it no longer matches, the quiet
  status line becomes an alert that says so while confirming the verified patterns are still being
  enforced. The baseline is read-only on this surface by design: there is no control that can edit,
  reorder or remove one of its patterns. Your own list is now summarised as **"N user additions"**
  counted from what actually takes effect, so an entry that merely repeats a built-in is reported as
  adding nothing instead of inflating the number. The panel states the same honest limitation the
  release notes do — the check proves the patterns match what shipped, and anyone who can edit the
  installed package before PersonalClaw starts owns the baseline; the full statement lives in
  `docs/security/threat-model.md`. A failed read of either the posture counts or the denylist now
  renders an error with the server's message and a Retry, replacing a silent empty list that was
  indistinguishable from "nothing is blocked".
- **Installed apps' backends no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for an app backend that read a variable it never declared.** An app with a backend runs
  it as a subprocess, and that subprocess used to start from a full copy of the gateway's own
  environment — which, because PersonalClaw deliberately puts your `.env` credentials there so
  "trusted children" can see them, meant every installed app's backend could read every credential
  you had configured, plus (measured on a real gateway) about **130** other variables it had no
  declared need for, including your SSH agent socket, AWS settings and your git identity. An app
  backend is the least-trusted long-running process in the system — third-party code, scanned but
  not trusted at install — so it now gets the same *minimal* environment hooks and cron scripts got
  in the previous release: `PATH`, `SHELL`, `PWD`, `TERM`, `PYTHONPATH`, your locale and `TZ`,
  `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle settings, and
  `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT`. The four variables the app contract promises are
  unchanged: `PORT`, `PERSONALCLAW_APP_NAME`, `PERSONALCLAW_APP_SECRET`, and
  `PERSONALCLAW_APP_DATA_DIR` for an app that declares the `storage` permission. Everything else is
  withheld. **All 44 first-party apps were booted against this change and are unaffected** — the two
  that ship a backend (Growth, Minutes) read only `PORT` and `PERSONALCLAW_APP_DATA_DIR`, and the
  model/search apps read their API-key fallbacks in the gateway process itself, not in a backend, so
  a key exported in your shell still works for them exactly as before. **If a third-party app's
  backend stops working for want of an environment variable, you have two ways to fix it:** move the
  value into PersonalClaw's credential store and configure it on the app's instance (the supported
  route — every first-party model app treats the environment variable as a *fallback* to a
  configured credential), or, if the app genuinely needs the ambient variable, name it in
  `sandbox.env_passthrough` (`personalclaw config set sandbox.env_passthrough '["MY_VAR"]'`). Note
  that `sandbox.env_passthrough` is global — a name declared there is visible to your hooks and cron
  scripts too, not just to app backends — and that credential-shaped names (`AWS_SECRET*`,
  `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`, `GIT_ASKPASS`) stay refused even if you declare
  them. To see what a backend is missing, run the gateway with `--verbose`: each backend launch logs
  the names it withheld.
- **A scheduled Python script can no longer exhaust PersonalClaw's file descriptors.** A
  `run-script` cron ran inside the OS sandbox with a minimal environment, but with no cap on what
  it could *consume* — so a script that leaked open files could starve the gateway of descriptors,
  something an agent `bash` command has been unable to do for a while. Scheduled scripts now run
  under the same resource ceiling as every other agent-driven child: the `sandbox.nofile` limit
  (default 4096) applies to the script and everything it starts. A script that hits it gets an
  ordinary `OSError`/`EMFILE`, which surfaces in the job's run history. If you have a legitimately
  descriptor-hungry script, raise the limit with `personalclaw config set sandbox.nofile 16384`.
- **The Store now tells you which other apps an app may message.** An app can declare that it may
  send messages to other installed apps, and PersonalClaw really does enforce that list: a brokered
  message is the only way one app can reach another, and a target the app did not declare is refused
  and written to the security log. But the Store never showed you the list. Installing an app that
  declared it could message your mail and notes apps looked identical to installing one that could
  message nothing — the permission was enforced behind your back rather than consented to. Both the
  install-consent panel and the installed-app panel now name each target among the permissions the
  gateway enforces. A wildcard target is spelled out rather than shown as-is, because it grants more
  than it looks like: an app declaring `mail-*` reads as "any app whose name starts with mail-",
  which covers apps you have not installed yet, and `*` reads as "any installed app". An app that
  declared no target is stated too — "App messaging: none — it declared no target, and the gateway
  broker is the only way one app can reach another, so it can message no other app" — so silence is
  never left to your imagination. Nothing about what an app can do has changed; the enforcement was
  already there and is unchanged. No first-party app declares this permission today, so nothing in
  your Library will start showing a messaging row.
- **The Store no longer implies PersonalClaw confines an app's network access.** An app's manifest can
  declare a `network` permission, and the Store used to list it as a bullet under "Permissions"
  alongside storage, scheduled jobs and background agents — all of which the gateway really does
  enforce. This one it does not, and cannot: an app's code runs inside PersonalClaw's own process (or,
  for the handful of apps that ship a backend, as an ordinary OS process of its own), so there is no
  point at which the platform can intercept the app's outbound traffic. The misleading half was the
  quiet one: an app that declared `network: false` showed **no** network row at all, which read as a
  guarantee that it had been blocked — including for the only two first-party apps that ship a
  backend, Growth and Minutes, both of which declare exactly that. The app detail and install-consent
  panels now show the network claim *outside* the list of permissions the gateway enforces, marked
  advisory, and show it whether or not the app declares one: "Network access: declared / not declared
  — advisory only. PersonalClaw does not confine an app's outbound traffic: this app's code can reach
  the network either way. The declaration is disclosure, not containment." Nothing about what an app
  can do has changed — only what the interface promises. The controls that really do bound an installed
  app are unchanged: its `api` permission bounds what it may ask the gateway for, and the supply-chain
  scanner still gates what you can install in the first place.
  Platform note: on Linux the same mechanism also carries the optional `sandbox.max_pids` and
  `sandbox.max_rss_mb` bounds (both off by default) plus the OOM-killer preference that protects
  the gateway; on macOS only the descriptor limit is enforced.
- **Your hooks and cron scripts no longer inherit PersonalClaw's environment.** ⚠️ **This changes
  behaviour for any hook, cron script or bash action that read an inherited environment variable.**
  A hook command, a `run-script` cron script and a bash action used to start from a copy of the
  gateway's own environment with a few names filtered out — measured on a real gateway, that was
  **121** variables, and PersonalClaw deliberately puts your `.env` credentials in there so
  "trusted children" can see them. A one-line hook (`printenv`) could read them. Those children now
  get a *minimal* environment built from a fixed list instead: `PATH`, `SHELL`, `PWD`, `TERM`,
  `PYTHONPATH`, your locale and `TZ`, `HOME`/`TMPDIR`/`USER`/`XDG_*`, your proxy and CA-bundle
  settings, and `PERSONALCLAW_HOME`/`_WORKSPACE`/`_PORT` — plus, for a hook, the
  `PERSONALCLAW_HOOK_EVENT`/`_CONTEXT` variables and the trigger's `$variables` exactly as before.
  Everything else is withheld. **If a script of yours needs one more variable, name it in
  `sandbox.env_passthrough`** (`personalclaw config set sandbox.env_passthrough '["SLACK_BOT_TOKEN"]'`,
  or the config API) — for example a Slack token for a notifier script, or a language runtime's
  variable. Credential-shaped names (`AWS_SECRET*`, `AWS_SESSION*`, `SSH_AUTH_SOCK`, `GNUPGHOME`,
  `GIT_ASKPASS`) stay refused even if you declare them. To see what a script is missing, run the
  gateway with `--verbose`: every spawn logs the names it withheld. Unchanged: cron scripts still
  receive PersonalClaw's internal secret and port through the temp file they always did, so scripts
  that call back into the API keep working.
- **Scheduled, file-watch, webhook and chained automations now honour the action denylist — they
  never did.** ⚠️ **This changes behaviour for automations that already exist.** The denylist that
  refuses an automated action touching a credential path or running a destructive/exfiltrating
  command was enforced when a *script hook* or a *memory-event trigger* fired an action, but not on
  the busiest path of all: the one every clock, file-watch, webhook and chained trigger dispatches
  through. That seam had the incident kill switch and the autonomy ladder, so the omission was easy
  to miss — the denylist was lost when the old scheduled-job dispatcher was retired and its
  replacement was never re-wired. From this release all three dispatch paths enforce it, which also
  means an action contributed by an installed **app** inherits the denylist wherever it is fired.
  A refused fire does not run, is recorded in the automation's run history as a **skipped gate**
  (not a failure, so it will not count toward auto-pausing your automation) naming the rule that
  matched, is written to the security event log, and — for a `needs_human` rule — raises a
  notification. What this can affect, measured before shipping: only `bash` (its `command`),
  `run-script` (its script name) and `run-prompt` (its `cwd`) carry a field the denylist inspects;
  the other shipped action types are unaffected. If one of your scheduled commands stops running,
  the likely built-in patterns are `rm -rf ~…` / `rm -rf /…` and `git … push` — the same patterns
  the assistant's own shell tool has always refused, which is the point: this is not a new policy,
  it is an existing one that one seam was skipping. Nothing new is configured by default
  (`security.autonomy_denylist` stays empty); to allow a command that is being refused, adjust the
  command rather than the guardrail.
- **A governance ceiling an operator writes once now bounds every unattended run — and the safety
  profile it bounds is finally read at all.** PersonalClaw already described each run's posture in a
  `SafetyProfile` (approval, tool grants, egress tier, path denies, budget, secret-scan mode), but
  nothing consulted the tier/grants/denies, and every automated dispatch seam — clock, file, webhook
  and chained triggers, memory-event triggers, and script hooks fired without a parent session —
  asked for its posture with an *empty* session identity, which classified as "a human is watching"
  and resolved the **interactive** posture. Automated work has been running under the interactive
  profile. Now: those seams identify themselves as unattended, so they resolve the `headless`
  posture, and an optional operator file at `$PERSONALCLAW_HOME/governance/ceiling.json` (or an
  absolute path in `PERSONALCLAW_CEILING_FILE`) sets a hard bound that a run can only make
  *stricter* — never looser. Six governed scopes (`approval`, `scan`, `egress`, `paths`, `tools`,
  `budget`); for example `{"version": 1, "scopes": {"approval": {"value": "ask"}, "paths":
  {"mode": "closed", "allow": ["~/workspace/**"]}}}` means nothing on this machine auto-approves and
  automated actions may only touch your workspace. **No file means no change**: absent a ceiling,
  behaviour is exactly what it was. A malformed one is a hard stop with a WHAT/WHY/FIX message
  rather than a silent start, because "governance could not be established" is not a degraded mode.
  The file is deliberately not editable through the app (it is absent from the config PATCH
  allowlist and its directory is on the built-in sensitive-path denylist, so the agent's own write
  paths refuse it), it is read once at start-up so an edit cannot widen a running gateway, and every
  clamp is logged and written to the security event log. What it cannot do is stop a process running
  as you from editing the file and restarting — for a real trust root, point
  `PERSONALCLAW_CEILING_FILE` at a root-owned `0444` file outside your home. See
  `docs/architecture/security.md`.
  Two effects of the seam correction you may notice even with no ceiling file, both by design: an
  automation whose action type is allowed to run fully on its own now runs with the **undo handle and
  the passive "this happened" notification** kept (the "runs on its own, silently" rung is reserved
  for work a human is watching, since nobody is there to notice otherwise) — it still runs, it is
  just no longer invisible; and outbound secret scanning for automated runs follows your configured
  mode (redact by default) instead of warn-only.
- **An egress "allow-list" now actually restricts.** `allow_hosts` only ever *waived* the
  private-address block, so the `registry` and `listed` egress tiers reached every public host
  exactly like the default — a limit in name only. Policies can now be exclusive (only listed hosts
  are reachable, checked before DNS resolution), which is what lets a run's egress tier — or a
  ceiling of `{"egress": {"value": "listed"}}` — genuinely confine outbound traffic. The agent's web
  fetch and watched-source polls both honour it, and a tier of `off` refuses the request with a
  visible reason instead of making it.
- **A watched-source poll now honours your denied hosts on the headless-browser tier too.** The
  JavaScript-rendering tier passed a hardcoded policy, so `Security → Network` deny-hosts applied to
  the plain fetch and were ignored on the render path. Both tiers now use the same resolved policy.
- **An auto-approval grant for a spawned subagent can be refused by the ceiling.** The trust toggle,
  `--approval yolo`, an `approval_mode: auto` caller and the config default could each widen a
  subagent to auto-approve tool calls; none of them consulted an operator bound. With a ceiling of
  `{"approval": {"value": "ask"}}` the grant is refused and the refusal is audited. Without a
  ceiling file, the toggles behave exactly as before.
- **Path rules are matched correctly.** The action denylist compared paths as strings without
  anchoring them, so a relative path like `../../etc/passwd` could slip past a deny of `/etc/**`,
  and `**` was treated as a single-level `*`, so `~/.ssh/**` missed `~/.ssh/sub/key`. There is now
  one matcher: the queried path is expanded and absolutized, patterns are never rewritten, and `**`
  crosses directories.

- **App backends now authenticate inbound requests, closing a direct-to-port bypass.** An app's
  backend subprocess binds on loopback (`127.0.0.1:<port>`), which is a network boundary, not an
  authorization one — before this change any local process that found the port could talk to the
  backend directly, bypassing the gateway proxy and therefore session auth and the app-permission
  middleware. Every request the proxy forwards now carries an HMAC signature
  (`X-PersonalClaw-Proxy: <ts>:<hmac>` over `<ts>:<METHOD>:<path?query>:<sha256(body)>`, ±60s
  replay window, constant-time compare) keyed by a per-app 256-bit secret minted 0600 at
  `apps_dir()/<app>/.app_secret`. The backend verifies it **fail-closed** via the new
  `personalclaw.sdk.security.require_proxy_signature()` middleware — no/stale/bad signature ⇒ 401
  before any route runs; a backend that cannot obtain a verifiable secret does not start. `/health`
  is exempt so the watchdog can probe it. Both first-party backends adopt the middleware. This
  proves a request came from the gateway proxy; it does not encrypt loopback traffic or defend
  against a process that can read the root-only secret file (see
  `docs/architecture/app-platform.md`).

### Fixed

- **A security-audit write that fails is no longer swallowed.** When the subagent reaper
  force-kills a subagent that blew its deadline, it writes one security-event row — the only
  record that the kill happened. That write sat inside a catch-all `except`, so on a home that
  could not be written (read-only, full, permissions) the kill went ahead and the audit row was
  lost with nothing raised: an unauditable kill that looked identical to an audited one. The
  failure now surfaces (logged per-agent by the reaper sweep, which continues with the other
  agents) rather than being absorbed. Every other audit write on this path already behaved this
  way; this one was the exception.
- **Developer-facing: the test suite no longer leaks SQLite handles, and the self-dev harness works
  in a git worktree.** A full run printed ~1,600 `unclosed database` resource warnings from 95 test
  files — every sqlite-backed store was built by a fixture and never closed — and closing them
  exposed a real isolation bug: the process-wide knowledge store was memoized across tests, so
  tests were searching an earlier test's database. Separately, the harness resolved its interpreter
  as the cwd-relative `.venv/bin/python`, which does not exist in a worktree, so
  `python -m harness validate` could not collect the suite there and three of its tests failed in
  every worktree.

- **A lesson saved for one project no longer becomes a rule for every project.** The
  `memory_remember` tool offers `scope: "workspace"` and the workspace-identity prompt block
  promises such a lesson is "only visible in this working directory" — but `POST /api/lessons`
  read neither the scope nor the workspace name, and neither `write_lesson` layer had a scope
  parameter, so the lesson was stored **global** and the caller was told `ok`. A workspace lesson
  is now stored against the working directory it was taught in (`realpath`, matched exactly), and
  is injected only into sessions running in that directory. Global lessons are unchanged: every
  lesson saved before this release is global and still applies everywhere, and no migration runs.
  The endpoint now **refuses instead of downgrading** — `scope="workspace"` without a workspace, a
  workspace that is not an absolute path, or an unrecognized scope each return 400 rather than
  quietly writing a global lesson. Your lesson list still shows every lesson (and now says which
  workspace each belongs to), so a workspace lesson stays visible to manage and delete;
  `GET /api/lessons?workspace=<absolute path>` asks for one directory's view.

- **`until_dry` workflow loops now end when the work reports no progress, instead of always running
  to their iteration cap.** A loop can declare which field of its iteration output counts as
  progress (`progress_field`), and two shipped templates do — `goal-pursuit-open-ended`
  (`new_findings_count`) and `general-project` (`meaningful_progress`) — but the engine never read
  it. Dryness was measured over the *whole* output of the last node in the iteration, which in both
  templates is a judge stage returning a populated JSON object; so a cycle that honestly reported
  `new_findings_count: 0` still counted as progress, the "two clean cycles in a row" streak never
  completed, and the run paid for a model call per iteration up to `max_iterations` (12 and 6) to
  learn nothing. A declared field now decides, wherever in the iteration it was emitted: zero,
  false, blank, empty or null means the cycle surfaced nothing new; anything else is progress. Loops
  that declare no field — the majority, including `audit-sweep` and `deep-research` — keep the
  previous whole-output rule unchanged. A declared field an iteration did not emit also falls back
  to that rule rather than counting as dryness: ending a run because the body forgot a key would
  silently truncate real work, which is worse than paying for one more iteration. `streak` is
  unchanged and still means N *consecutive* dry iterations.
- **Run history no longer says "ran" for automations that did not run.** The run feed translates
  each store's own status word into a typed outcome, and four of the statuses actually being written
  had no entry in that translation — so they landed on a fallback nobody had chosen for them. A
  lifecycle hook that only *launched* background work, and a hook the incident kill switch stopped
  **before** it reached its action, both showed as "ran"; a payload the injection screen blocked, and
  every fire suppressed by quiet hours, a budget cap, an overlap or a triage decision, all showed as
  a red "failed". Each now shows what it was: `deferred` ("outcome not yet known"), a neutral grey
  suppression that folds into the archived half of the feed with its reason, and the shield-marked
  `blocked`. Suppressed and screened rows are also marked as ledger entries rather than openable
  runs, since neither ever reached a runner. This was a reporting fix only — the trigger
  autopause counter reads the stored rows directly and was never affected, so no automation's
  pause/resume behaviour changes. A status the build cannot classify is now logged by name and never
  reported as a success.

### Changed

- **Knowledge search now finds the passage, not just the document — and tells you which passage.**
  Semantic search used to compare your query against one vector per item, built from the item's title
  and summary, so an answer buried on page 12 of a long document was effectively invisible to it: the
  document either matched as a whole or not at all. Search now compares your query against the
  individual passages the library indexes for each document, and scores each document by its single
  best-matching passage. Two things change for you. Long documents become findable by what is *inside*
  them, which is where most of a real library's value sits. And a result that matched semantically now
  carries a citation — the section heading and line range of the passage that actually matched — where
  before, if none of your literal query words appeared in the text, the result could only name the
  document and point at its top. A document with no indexed passages yet (anything added before this
  release, until it is re-indexed) still matches the way it always did, so nothing becomes unfindable
  in the meantime. Ranking, the relevance-cliff cutoff and the similarity threshold are unchanged;
  only the evidence feeding them got better. Indexing many passages per document also means many more
  vectors to compare, so this release pairs the change with the index described next.

- **Semantic search on a large library got about twenty times faster.** Comparing your query against
  every indexed passage was done one vector at a time in Python, which is fine for a few hundred notes
  and slow for a real library — on a 300-document library with five passages each, measured at roughly
  40 ms of vector work per query, growing in a straight line from there (a 5,000-document library would
  have spent over half a second on every search). PersonalClaw now keeps a vector index inside the same
  knowledge database file and asks it for the nearest passages instead of reading them all: the same
  measurement drops to about 2 ms, and the results are **identical** — same documents, same order, same
  citations — because the index only narrows down which passages to score, and the scoring, the
  similarity threshold, the ranking and the relevance-cliff cutoff are the ones you already had. If
  your Python's SQLite cannot load extensions, search keeps working exactly as before at the old speed;
  it says so once in the log, and `personalclaw doctor` shows a line explaining why search is slower
  rather than leaving you to guess it is broken. Libraries indexed before this release are brought into
  the index automatically on the first search after upgrading.

- **The library you already have becomes searchable by content, without you doing anything.** The two
  changes above only help documents PersonalClaw has broken into passages, and it only started doing
  that on the way in — so everything you added *before* it kept matching the old way, on its title and
  summary alone. That was the gap: on a library of six long documents, asking "how far behind can a
  replica get before it stops serving reads" returned one result, the document whose *title* was the
  closest match and which did not contain the answer anywhere, with no citation, while the document
  that actually answered it was not returned at all. PersonalClaw now indexes the passages of your
  existing documents in the background, starting the next time it launches, and after that the same
  question returns the right document first and cites the section that answers it — the heading and the
  exact lines. Three things about how it does that. It works in small batches, so a library of any size
  costs the same in memory; it can be interrupted at any point — quit, crash, power cut — and picks up
  exactly where it stopped, without redoing or skipping a document; and searching *while* it is partway
  through is safe: documents it has not reached yet keep matching the way they always did, so nothing
  becomes unfindable in the meantime. Running it a second time does nothing, by design. It reads your
  documents and adds passages; it never rewrites anything you can see, and it leaves the whole-document
  index alone. Expect the database to grow — measured on a 300-document library, indexing 2,100
  passages took it from 1.5 MB to 11 MB and took about nine seconds. If no embedding model is available
  yet, it says so in the log and waits for the next launch.
- **Anthropic models now reuse the stable head of a conversation instead of re-reading it every
  turn.** Anthropic bills prompt content it has already seen at a fraction of the normal input
  price, but only when the request marks where the reusable part ends — and PersonalClaw never sent
  that mark, so every turn paid full price for the same assembled context, memory and skills. It is
  sent now, on the last piece of stable content in each request, which means from the second turn of
  a conversation onward that whole prefix is billed at the reduced rate and comes back faster.
  Nothing about *what* the model is told changes: the same words in the same order, just flagged as
  reusable. Only Anthropic-family models are affected — providers that cache on their own
  (OpenAI-compatible endpoints) already benefited from the prompt reordering in the previous
  release, and a provider with no cache support sends byte-for-byte the request it sent before.
  Anthropic reports how much of each request it served from cache; showing that back to you as a
  per-turn saving is still to come.
- **The Retro Terminal and Claw Arcade personalities now skin the error surfaces too.** Picking one
  of the two shipped personalities in Settings → Design changed the palette, the wordmark and the tab
  title, but a failed page and the incident banner still looked exactly like the default identity —
  the two moments where an identity is most noticeable stayed generic. Retro Terminal now draws them
  as a hard-edged mono-type terminal frame and Claw Arcade as a dashed cabinet panel. This is a
  **skin and nothing else**: the wording, the Retry and Resume buttons, and the fact that the incident
  banner announces itself to a screen reader are byte-for-byte the same under every personality, and
  each treatment's colours are checked against WCAG AA in both light and dark before it can ship
  (worst measured pairing 4.98:1). If you use a standard colour scheme — which is everyone who has
  not deliberately picked a personality — **nothing changes**: both surfaces render exactly the markup
  they did before, asserted against the previous release's output rather than promised.

- **A goal loop's judge verdict now shows you what the supervisor checked for itself.** When a loop
  declares something checkable — a verify command, or a named deliverable file — PersonalClaw does
  not take the worker's word that it worked: it runs the command and reads the file itself, and
  weighs that over the worker's account. It has done so for a while, but only the *judge* saw the
  result; you got a one-line reason and no way to tell whether it rested on an independent
  observation or on the worker's own narration. A cycle's verdict panel now lists what was
  independently observed — the command it ran and what that command returned, and each deliverable
  it read — so "done" comes with its receipts. A cycle with nothing checkable to observe says
  nothing new, which is the honest answer for a goal that named no anchor. Under the hood this is
  one verdict record instead of three: the loop's private vocabulary was folded into the workflow
  judge contract, so a loop cycle and a workflow gate now describe "was this good enough?" the same
  way, including the marginal-value and regression signals that drive when a loop decides it has
  stopped making progress. Verdicts stored by an earlier version still display correctly.
- **"Reduce motion" now actually stops the springs — and the Bounciness slider reaches everything it
  claimed to.** If your operating system is set to reduce motion, PersonalClaw relied on a framework
  setting that neutralises movement *across the screen* but leaves the underlying spring running, so
  anything animating opacity or a blur still bounced its way in. Every spring in the app now collapses
  to an instant swap under that setting, in one place, so a new animation cannot escape it. Separately,
  menus and popovers were reading your **Bounciness** value once when the app loaded and then ignoring
  it: moving the slider changed nothing for them until a full reload. They now read it every time they
  open. The app's spring presets are also down to one named set of four — *snappy*, *smooth*, *fluid*
  and *playful* — so motion is consistent between surfaces that used to pick from two overlapping
  lists; a few entrances (dialogs, the update overlay, the composer) are slightly quicker and bouncier
  as a result, and Settings → Design → Motion tunes all of them. **Three new sliders in that same
  group** control drag and swipe feel: how far a dragged card stretches past its edge, and the flick
  speed *or* distance at which a swipe dismisses it (a toast can now be flicked away quickly or hauled
  away slowly — previously only one worked, at values you could not change).

- **Housekeeping now runs when your machine actually needs it, instead of on a fixed clock — and
  one system does it, not two.** PersonalClaw's minute-by-minute heartbeat used to carry its own
  maintenance schedule: rebuild the memory search index every 15 minutes whether or not anything
  had changed, and once a day trim old daily-history files, trim the security event log, and age
  the skill library. The self-healing Maintenance engine on the Doctor page (Settings → Doctor) was
  doing the same kind of work from measured evidence, so the two overlapped. Those four passes are
  now jobs the engine owns and schedules from what it can measure — how many memory files disagree
  with the search index, how many history files are past your retention setting, how many
  security-log entries are past theirs, how many skills are due for aging — so each one runs when
  there is genuinely something to do and is recorded in the Doctor's run history with what it did.
  Two consequences worth knowing. **If you turn the engine off** (`resilience.remediation.enabled`),
  the heartbeat picks all four back up on its original schedule, so you are never left with no
  housekeeping at all. **And the memory search index is now reconciled properly:** deleting old
  history used to leave its text in the search index until the next rebuild, so a search could quote
  a file that no longer existed; the prune now removes both together. Skill *tamper detection* is
  also finally scheduled — a skill whose files changed after installation used to be noticed only if
  you happened to open the Skills page, and is now checked on every maintenance pass and shown on
  the Doctor page (it is reported, not silently "fixed": re-recording a changed skill's fingerprint
  would hide the very change you need to see).
- **A workflow judge now has to show its work, and a PASS it cannot justify is refused.** A `judge`
  gate used to be asked for exactly one word — `PASS`, `RETRY`, `ESCALATE` or `REJECT` — which
  cannot carry a score, a citation or a reason. Every rule the judge contract states ("a PASS
  without cited proof is invalid", "any rubric criterion below its target fails the stage") was
  therefore written down and applied to nothing: on `goal-pursuit-open-ended` the template asked
  for a full verdict object and the engine appended the one-word demand right after it, so the gate
  shipped two contradictory instructions. Judges are now asked for the verdict object and the
  engine validates it: a PASS carrying neither `proof` nor `evidence_refs` is refused, the rubric
  criteria your template declares in `runtime_hints.judge` are compared against their
  `target_score` for the first time, the overall score is recomputed from the dimension scores
  (the model's own number is kept beside it so any drift is visible), the judge's evidence has
  retry/iteration markers stripped before it reads it ("attempt 4 of 5" tells a judge how much
  patience is left), and a gate that opted into `self_judge` now parks for a human instead of
  approving its own work. The six templates whose judge STAGE already produced this object have it
  validated and carried forward — into the review handoff, or into the next cycle's worker prompt
  as the critique to start from — instead of discarded.

  **What you may notice.** Judge answers cost more than they did (an object instead of a word), and
  a judge that replies with prose instead of JSON now fails its gate with a named protocol error
  rather than a guess. Enforcement was deliberately scoped so it cannot break a working template: a
  run that declares no rubric has nothing to fall short of, the prompt spells out the exact score
  keys the check will look for, and a restated key still counts. This is a behaviour change to
  every judge gate and judge stage — `personalclaw snapshot` before upgrading if you have judged
  runs in flight. Templates you wrote yourself that hand-write a "reply with one word" judge prompt
  should drop that line; the engine supplies the shape.

- **A workflow plan now tells you which of its stops survive an unattended run.** The autonomy
  offer routinely recommends `per_stage` while still offering `unattended`, and the plan preview
  listed the confirmations for the *recommended* mode only — so choosing "run it unattended" meant
  giving up an unnamed set of stops. The preview gained an `unattended_interrupts` block naming, per
  confirmation, whether unattended still stops for it and which interrupt fires (`irreversible`, or
  `uninferable` for a credential or payment detail nobody can guess). A credential ask now reports
  as *uninferable* rather than *irreversible* — the same stop, but it tells you to supply a value
  instead of sending you looking for a blast radius. Advisory only: what an unattended run may
  actually do is unchanged, and still decided by the engine's gate policy.
- **A `foreach` with `on_item_error: collect` now has defined behaviour, and it collects.**
  `collect` was accepted by the validator and advertised in the workflow capabilities catalog, but
  no code branched on it: it fell through to the generic container outcome, so it neither halted
  early like `halt` nor tolerated failures like `skip` — a mixture nothing had specified. It now
  means one thing: **run every item to completion, then fail the run if any item failed**, with the
  per-item failures written to the run ledger as one `items_collected` record (item index, label,
  node, failure class and cause). `skip` (the default) and `halt` are unchanged. **If you have a
  spec using `collect`,** its verdict is what it was before — the run still fails when an item
  fails — but the behaviour is now guaranteed rather than incidental, and you can read which items
  broke out of the ledger instead of reconstructing it from per-node events. Use `skip` if you want
  a fan-out's failures tolerated and the run to complete.

- **Security docs now describe what the sandbox actually does — credential-hiding, not
  confinement.** `docs/architecture/security.md` gains an explicit "what the sandbox does and does
  not do" section (no network/process/write confinement beyond `~/.ssh`); the "bounded by
  guardrails" claim is scoped to *unattended* work (interactive chat is never gated); and the
  desktop shell is described as an experimental macOS-only build, not a shipped platform — matching
  what CI actually releases. Documentation only; every change narrows a claim rather than widening
  one.

### Added

- **The desktop app can now tell the dashboard what it is actually allowed to do.** The macOS shell
  gained a typed capability bridge — `window.pclawDesktop.capabilities` — covering the microphone,
  screen recording, native notifications, the menu-bar item, a global hotkey and open-at-login. Each
  one can be probed for its real OS permission state and, where macOS allows an app to ask, requested;
  the request raises exactly one system dialog, and a capability already denied routes you to System
  Settings instead of silently doing nothing. Two capabilities are honest about their limits rather
  than guessing: macOS gives an app no way to prompt for Screen Recording and no way to read whether
  notifications are authorized, so those are labelled as such and offer no button that would do
  nothing. On boot the shell registers this manifest with your gateway over loopback, so
  **Settings → Security → Desktop capabilities** shows the truth — and in an ordinary browser tab it
  says "Desktop app not connected" instead of listing native permissions a tab could never grant.
  Apps can reach a capability only by declaring it (`"permissions": {"desktop": ["audio_capture"]}`);
  the gateway mediates every such call, refuses an undeclared one, records the refusal in the security
  event log, and the Store names the capabilities an app asked for before you install it.

- **First run now sets you up with a working model instead of pointing at Settings.** The second step
  of the welcome flow used to be a readiness check: if no model provider was configured it told you
  chat could not run, offered a link to Settings, and left you to find your own way. It is now an
  **Essential apps** step that does the work in place. Four groups are listed from the app catalog —
  a **model provider** (required; nothing else works without one), plus optional **web search**,
  **speech** (transcription/voice) and a **messaging channel** — and for the model you can go from
  nothing to a bound, tested model without leaving the flow: install the provider app, fill in its
  own settings fields, run its real connection **Test**, then pick which model chat should use. If
  the Test fails you see the provider's actual error and can correct it right there; skipping every
  optional group still gets you to a working dashboard. **Nothing installs by itself.** Each app has
  a **Review** step that shows exactly what installing grants it — the same permission disclosure,
  scheduled-job list and scanner warning the Store shows — and installs only when you click that
  card's own Install button. Where you are in the flow, and which of the four you set up, is now
  remembered on the server.
- **Prompt caching is now a switch you can find, in Settings → Models.** Providers that support it
  can be asked to cache the stable front of your prompt — the assembled context that does not change
  from turn to turn — so a long conversation stops re-paying for the same tokens on every turn.
  That was already happening; there was no way to see it or stop it. There is now a **Prompt
  caching** switch beside your model bindings, on by default. It is on by default because caching is
  transparent: the model is shown exactly the same tokens either way, and a provider without cache
  support is unaffected. Turn it off when you are debugging a provider and want caching ruled out —
  nothing else changes when you do. In particular, **what the model is shown and in what order is
  identical either way**: the ordering of the served prompt is a correctness property, not part of
  the caching feature, so the switch does not quietly serve you a different prompt.

- **You can approve what PersonalClaw is waiting on from your phone.** A run that stops to ask
  permission used to stay stopped until you were back at a desk, because the only place the question
  appeared was the desktop dashboard. There is now a phone route at **`#/companion`** — open it on
  your phone (over your tailnet or however you reach your gateway) and it shows every pending tool
  approval with the whole decision on screen: the tool, its **full arguments** (not a truncated
  preview — you should never approve something you cannot read), why it wants to run, which session
  or automation asked, and how long it has been blocked. Allow and Deny are thumb-sized, and the
  answer lands on the same gateway the dashboard talks to, so a run held up by a permission prompt
  proceeds the moment you tap. Two things it deliberately will *not* do: if it cannot reach your
  gateway it says so and offers a retry, rather than showing an empty list that would read as
  "nothing needs you"; and if an answer fails to send, the card comes back instead of quietly
  disappearing. The rest of the companion — running loops, inbox, notifications — is named on the
  page as not built yet rather than shown as empty, and push notifications for approvals are still
  to come. This first release has no app-store app and no offline install; it is a page you open in
  your phone's browser.
- **PersonalClaw can now earn autonomy one action at a time, and lose it instantly.** The safety
  floor used to be binary — an unattended action was read-only, or it was a permission you granted
  when you created the automation — so a reply draft you had approved unchanged forty times still
  asked every time. There is now a per-action-type ladder: **draft only → one tap → run with undo →
  autonomous**, where each action type declares the rung it starts at and a ceiling it can never
  pass (anything that leaves your machine stops below "autonomous" unless its declaration says
  otherwise). The track record behind a promotion is **recomputed every time from what already
  happened** — the approvals in your security event log and the 👎 you have given that action's
  output — and never stored as a score, so it cannot outlive the evidence. Two rules make it safe:
  **PersonalClaw never promotes itself** (clearing the bar files a suggestion; only your click
  grants a rung), and **one rejection demotes immediately** and starts a cooldown before the
  suggestion can come back. `personalclaw incident on` holds every action at "one tap" or below
  until you resume, whatever it had earned. Thresholds live under Settings (`guardrails.autonomy`:
  approvals required, days they must span, rejections tolerated, cooldown, evidence window), the
  grants and demotions are saved to `autonomy_rungs.json` and travel with `personalclaw snapshot`,
  and an unreadable or unrecognized entry grants nothing. This release ships the mechanism; the
  action types that use it, and the ladder panel that shows it, follow.
- **The autonomy ladder now actually decides whether an automated action runs.** Every built-in
  action declares its rung, and an app can declare one for its own action with an `autonomy:
  {floor, ceiling}` block on its provider — which the lifecycle-hook, data-event and
  clock/file/webhook trigger paths all honour. An action held at **draft only** files a proposal
  saying what it would have done; held at **one tap** it files a request for you to decide; at
  **run with undo** it runs and tells you quietly, keeping a handle on what it created; at
  **autonomous** it just runs. Held actions leave a real row in your inbox and a typed entry in the
  automation's history, never a silent stop. Two things an app cannot do: it cannot claim its action
  stays on your machine (PersonalClaw decides that from the network permission the app already
  declares), and it cannot claim the top rung for an action that reaches the network — that request
  is lowered to "run with undo", and both your log and the security event log say so rather than
  quietly overruling the app. Actions that carry no declaration behave exactly as before: the
  denylist, the kill switch and the permission you granted when you created the automation are
  unchanged, and nothing you already run stops running.
- **You can now see, grant and take back what each automation may do on its own.** The autonomy
  ladder had no face: it decided quietly, and there was no way to find out why an action was allowed
  to run unattended, no way to accept a rung it had earned, and — worst — no way to actually undo an
  action that ran at "run with undo", however loudly the notification offered one. Settings →
  Guardrails now lists every governed action with the rung it runs at, a plain sentence saying WHERE
  that permission came from (declared that way · you promoted it on this date, with the record you
  were shown · granted, but held down right now by incident mode), the track record behind its next
  rung, and its demotion history. The same chip rides every row on the Triggers page, so you can
  scan a list of automations and see which ones act on their own. **Promotion is still only ever
  your click** — when an action clears the bar, PersonalClaw files a proposal in your inbox and
  waits; nothing in the system can promote anything, and a request asking for a rung above an
  action's declared ceiling, or during a cooldown, is refused with the reason. Two buttons take
  autonomy back: **Hand back** returns an action to the rung it was declared with, and **Undo** on
  an automatic action's notification (or in the panel) really reverses it — the task an automation
  filed is deleted — **and** stops that action from doing it by itself again. The undo asks the
  provider that created the thing to take it back, since only it knows what "undo" means for its own
  effect, and it works from PersonalClaw's own record of what ran: if that record is gone, the thing
  was already deleted, or nothing installed can reverse it, the undo refuses and says which, and
  crucially leaves the action's earned rung alone — a broken undo request can never be a way to
  quietly degrade what your automations are allowed to do.
- **You can share a chat as a read-only artifact — inside your own instance, never on the
  internet.** Right-click a chat in Chat History → **Share as read-only artifact** and the
  conversation is saved into your artifacts library as a Markdown record, then opened for you. The
  body is the *same* credential-redacted transcript the Export action produces (redaction covers
  your own messages too, which the chat log itself does not), and the artifact is frozen: it can be
  read, downloaded, or deleted, but never edited — a record you can point at, not a document that
  can drift from what happened. It is created only when you ask, on an authenticated request: there
  is no public link, no share token, and nothing shares a chat automatically. Incognito and
  temporary chats refuse to be shared, since an artifact is durable and those chats promise not to
  be. Export is unchanged.
- **A Routing & Efficiency panel in Settings shows which model is efficient for which kind of
  work.** Settings → Routing & Efficiency lets you pick a use case (chat / code & tools / reasoning)
  and a request kind (short chat, code, summarize, extract structured, long reasoning — both
  round-trip the URL) and shows, per model, the real success rate, p50/p95 latency, and cost per
  call for that kind of request, with the ones on the efficiency **frontier** (not beaten on all of
  quality, speed, and cost) flagged and floated to the top. Observation only — it visualizes
  already-recorded telemetry and does not change how requests are routed. A bucket with no data yet
  shows a friendly "fills in as models handle this kind of request" note; a local/zero-cost model
  reads "free," never a misleading "$0.00."
- **A Usage panel in Settings shows what you're spending.** Settings → Usage renders Today / 7-day /
  30-day cost + token totals (the period control round-trips the URL), a by-model and a by-source
  table with each row's share, a cache-savings line, and — when you've set a daily budget in
  Guardrails — a read-only "spent $X of your $Y cap" (automations only; interactive chat is
  uncapped). A period that includes a model with no price row shows a "partial — N unpriced models"
  marker instead of a misleadingly complete figure. Observation only — nothing here caps or throttles
  a turn. This completes cost observability: per-turn, per-conversation, and per-account.
- **The chat header shows what the whole conversation has cost.** A cost chip — e.g.
  `$0.19 · 46k tokens` — appears in the session header once a chat has recorded usage, reading the
  per-turn ledger scoped to that session. A conversation whose models are all priced shows a real
  dollar figure; one that used a model with no price row shows `unpriced` rather than a misleadingly
  precise total.
- **The "Turn complete" line now shows what the turn cost.** When a turn finishes, its telemetry
  line (in the collapsible per-turn details) reports real USD plus in/out token counts — e.g.
  `$0.0123 · 1,200 in / 340 out tokens`. A model with no price row shows `unpriced` rather than a
  misleading `$0.00`, and a cache fragment appears only when the provider actually reported cached
  tokens. Cost is provider-reported when available, otherwise derived from the pricing table.
- **`personalclaw doctor` now reports your SQLite driver and its capabilities.** The Dependencies
  section shows the resolved driver (`pysqlite3` or the stdlib `sqlite3`), its version, and whether
  FTS5 and JSON1 are compiled in — with a fix hint (`pip install pysqlite3-binary`) when FTS5 is
  missing, since the knowledge and memory search paths need it. Under the hood the driver is now
  selected in one place (`sqlite_compat`) instead of seven, so every subsystem shares one honest
  answer.
- **Memory-backed answers cite their sources, and say so when memory is empty.** When a reply
  draws on episodic memory recalled for the turn, it can cite a fact inline as `[Memory N]`, and the
  chat renders each such token as a chip that deep-links to that episode in Settings → Memory. The
  system prompt also instructs the model to answer only from the recalled memory — to say it doesn't
  have something in memory rather than present an un-recalled fact as remembered. A citation resolves
  through a per-message manifest keyed by the memory's record id (never the model's echoed text), so
  a mis-cited or hallucinated `[Memory N]` degrades to plain text instead of a wrong link.
- **A muted agent can be un-muted from its detail page.** When the auto-router stops suggesting an
  agent because you dismissed its chip enough times, the agent's Advanced → Routing status now shows
  that it's muted and offers an Unmute control to make it eligible for suggestions again — previously
  the mute was invisible and irreversible from the UI.
- **Local models now carry a capability matrix and a runtime/license contract from a declarative
  catalog.** A local-model provider can describe its models in a `catalog.json` — per-model feature
  flags (word/segment timestamps, speaker labels, hotword budget, languages), runtime and
  runtime-contract tags, SPDX license, and context/output budgets — which flow to
  `GET /api/models/available` and render as chips in Settings → Models. A non-commercial license
  shows a warning chip at bind time; a deprecated model shows a chip but stays bindable; and a
  download whose weights are incomplete (under 60% of the declared size) is flagged `truncated` with
  a Repair action that re-downloads it. Config-only pipeline repos (no local weights) are never
  mis-flagged.
- **Mid-run steering now takes effect, and the judge leaves a paper trail.** A workflow's decision
  layers are wired into the run loop: an instruction you queue while a loop is running is consumed
  at the next iteration boundary and re-ranks the plan (rather than sitting unread until the run
  ends); every judge-gate verdict is recorded to the Run Ledger with its evidence chain, and a
  human overriding a judge records the divergence — so the flywheel can tell a human-steered
  outcome from an autonomous one. A judge gate that has never rejected across enough runs is now
  flagged as a "nodding loop" and blocked from becoming its kind's default. The loop breaker keeps
  a single authority (no duplicate trip path).

### Fixed

- **An automation fired from a background write could be dropped without a trace when the retry
  it was owed was skipped.** Some fires are recorded on disk instead of running immediately — a
  memory write from the CLI, for example, has no event loop to run an action on, so the fire is
  parked and picked up on the next scheduler tick. If picking it up failed, the fire was marked
  handled anyway and deleted: a warning in the log, and an automation that never ran. It is now
  retried. A failure that happened *before* the automation could have started anything is held and
  tried again on later ticks, up to five times, and the attempt count survives a restart, so a
  temporary problem no longer costs you the fire. A failure that happened *after* the automation
  was handed off is never retried, because running it twice is worse than not knowing whether it
  finished. A fire that can never work — a malformed record, or five failed attempts — is dropped
  once, loudly, with the reason, instead of stalling every automation behind it. Two identical
  fires parked within five minutes of each other now run once, which is the same rule already
  applied to a webhook a sender retried or a file saved twice.
- **A workflow set to `on_overlap: queue` started a second run alongside the first instead of
  queueing it.** The policy did the opposite of its name: with a run already in flight, `queue`
  matched no branch in the code that applies the setting and fell through to "start now", so a
  trigger that fired every minute against a slow workflow stacked runs without bound — the exact
  thing the default (`skip`) exists to prevent. `queue` now means what it says: the start is saved
  as an unstarted run and begins when the run in flight ends, whether that happens while the
  gateway is up or after a restart. The queue holds one pending start (the run after next would do
  the same work with staler inputs), and a start refused by that limit says so — in the trigger's
  recorded outcome and in the log — rather than reporting itself as queued. An unstarted run you
  created yourself is never picked up by this: only starts the overlap policy queued are, and the
  distinction is recorded on the run. Trigger history shows a queued fire as deferred with its own
  reason, so it is no longer indistinguishable from one that ran.
- **The Inbox's Mentions and Email filters could never match anything.** The dashboard has
  filtered and counted items by kind for a while, and the inbox stored a kind per row — but the
  message-source seam every provider builds on had no field for it, so every message a source
  polled arrived as a plain "message" no matter what it really was. Mail landed
  indistinguishable from a chat message, and two of the kind chips were unreachable by
  construction. A source can now state what a message is (`IncomingMessage.kind` — `message`,
  `mention` or `email`) and that value is what gets stored, filtered and counted. Nothing infers
  a kind from the text of a message: a mention is something the source knows from its payload,
  not a guess about whose name appears in a sentence. A source that declares a kind the dashboard
  cannot render keeps its message — the row still arrives as a plain message — and the mistake is
  logged with the source that made it rather than silently accepted.
- **`personalclaw update` was a dead end unless you had installed from git.** If you installed the
  documented way — `pipx install personalclaw`, `pip install personalclaw`, `uv tool install
  personalclaw` — `personalclaw update` printed "❌ PERSONALCLAW_PROJECT_DIR not set — cannot locate
  source tree" and exited 1, because the CLI still ran the old git-only pipeline while the
  install-kind-aware updater the dashboard uses lived elsewhere. The command now behaves per install
  kind: a **wheel install** upgrades itself in place (`-U personalclaw==<latest>`, no source tree, no
  Node) and tells you to `personalclaw restart`; a **git checkout** keeps the fetch + reset + rebuild
  pipeline and honours Developer update mode (ride release tags by default, every commit when it's
  on); a **container** prints the two `docker compose pull` / `up -d` commands rather than pretending
  it can patch an image; a **desktop** install defers to the app's own updater. An install kind it
  does not recognize says what it detected and refuses to guess, instead of falling back to
  `git reset --hard` on a tree it may not own.
- **`personalclaw update` could run `git reset --hard` without anyone agreeing to it.** With
  uncommitted tracked changes, the confirmation prompt used to read whatever was on stdin — so from
  cron, a pipe, or `< /dev/null` it could take a piped "y" and discard your work, or crash with an
  `EOFError` traceback. Without a terminal it now refuses the destructive reset, names the files at
  risk and the remedy (`git stash` or commit), and exits non-zero. Declining at a real prompt still
  exits 0 — that's a choice, not a failure. Untracked files are no longer listed as at risk, because
  a reset does not touch them.
- **A detached-HEAD update fetched a branch that does not exist.** When git reported no branch (a
  checkout parked on a release tag), the updater fell back to a hardcoded branch name this project
  has never used, so the fetch failed with a confusing git error. It now resolves the branch
  honestly: the branch you are on, else the remote's own `HEAD` (read locally, so it still works
  offline), else what the remote reports, else `main`.
- **Deleting a knowledge item mid-enrichment crashed its background pipeline with a noisy
  error.** If you deleted an item within the ~30 seconds its tags/insights were still being
  extracted, the enrichment pipeline hit a `FOREIGN KEY constraint failed` error, logged a full
  traceback, and then tried to record a "failed" status on the row you had just deleted. Deletion
  itself always worked, but the orphaned pipeline made noise it shouldn't. A delete during
  enrichment now aborts the pipeline quietly — the item is gone, so there is nothing left to
  enrich. Genuine mid-pipeline failures (where the item still exists) still record as failed.
- **Renaming the built-in Personal or Repeatable project quietly broke your projects.** The API
  let you rename a default project even though the UI hides the button — and because the system
  re-creates any missing default by name, the rename left you with two Personal (or two
  Repeatable) projects: the original, now holding your task lists but no longer the one new work
  routes to, and a fresh empty duplicate. Worse, neither could be cleaned up through the app.
  Renaming a default is now refused with a clear message (other edits like its brief or workspace
  still work), and the delete guard now protects a project by its current name rather than a
  sticky internal flag, so any stray duplicate left behind by the old bug can finally be deleted.
- **"Run now" did nothing for almost every automation, while reporting success.** Clicking Run now
  (or dry-run's live counterpart) on a schedule or automation reported that it had run, but no
  action executed and nothing appeared in the run history — the Run button just sat on "Running…"
  waiting for a run that had never started. Automations still fired on their own schedule, so
  nothing looked broken until you tried to test one by hand. Manual runs now execute the
  automation's action, and a run that cannot start says so instead of claiming success.
- **A manual "Run now" left no trace and the "Running…" pill never cleared.** Even after Run now
  began actually executing the action, the run was recorded nowhere — the run history gained no
  row, the "last run" time never advanced, and so the animated "Running…" indicator waited forever
  for a completion it could not see. A manual run is now written to the automation's run history
  (tagged as a manual run) and advances its last-run time, so the history updates and the pill
  clears. A manual run that fails is recorded as a failed run rather than swallowed. Testing an
  automation by hand still never counts against its own fire limit.
- **Knowledge ingest reported steps as finished that never ran.** The per-item ingest view marked
  entity extraction, intent matching, and embedding as done on every item regardless of what
  actually happened — so with no embedding model bound, an item that stored zero vectors still
  showed "Embed ✓". Each step now reports its real outcome: done when it did the work, skipped
  when there was nothing to do (no model bound, no intents defined), failed when it errored.
- **Accepting a "refine an existing skill" proposal always failed with an error.** When the
  system proposed refining a skill you already have (rather than creating a brand-new one),
  clicking Accept returned "could not write skill … (invalid, oversized, or exists)" and the
  proposal stayed stuck in the queue forever — the only way out was to reject it and lose the
  improvement. Accept now updates the named skill in place, appending the refinement under a
  dated heading so the original skill and any earlier refinements are preserved. New-skill
  proposals are unaffected, and a refine whose target skill was since deleted quietly falls
  back to creating a new one instead of erroring.
- **Importing a memory file that wasn't a JSON object failed with an unhelpful server error.**
  Handing `POST /api/memory/import` (or `personalclaw memory import`) a JSON list, string, number,
  `null`, or `true` crashed the import instead of telling you the file was the wrong shape. Both
  now say so plainly, and a valid export imports as before.
- **A request that named no task mode relaxed every chat to full execution.** `POST
  /api/chat/task-mode` read a missing `mode` as `agent`, and a missing `session` as "all
  sessions", so one under-specified request could take every chat you had set to Ask or
  Plan and hand it full tool access. An absent or non-string `mode` is now refused the
  same way an invalid one always was, and the response names the sessions it changed so a
  caller can tell a one-chat change from a fleet-wide one.
- **A project could be pointed at your credential directories.** Binding a project's workspace
  accepted `~/.ssh`, `~/.aws` or an OS system tree with no complaint — the same paths the
  terminal refuses to open in — and a chat started under that project inherited the path as the
  working directory of an unsandboxed agent. Binding one is now refused, on exactly the check
  the terminal already used. Editing a project also stops accepting field names it never wrote:
  an unrecognised field now comes back as an error naming it, instead of a silent no-op or a
  blank "server error".
- **The dashboard could be tricked into handing over your secrets by changing the case of a
  filename.** The file panel refuses to open credential files — the session signing key, the
  local secret, the telemetry salt, `.env`, and anything ending in `.key`/`.pem`/`.secret`. On
  macOS and Windows, where the filesystem ignores upper/lowercase, that block was case-sensitive
  and could be walked straight past: asking for `.LOCAL_SECRET` instead of `.local_secret`
  returned the real bytes. The same hole let the write endpoint clobber a protected file under a
  different-case name. The block is now case-insensitive and also compares file identity, so no
  spelling — including hard links or Windows short names — reaches a protected file, in either
  the read or write direction.

- **One app could borrow another app's permission to run an agent, and read agent runs that
  weren't its own.** The permission check on the app agent-run endpoints read the app name out of
  the URL instead of asking who was actually calling, so an app allowed to reach `/api/apps/*`
  could name any agent-permitted app in the path and get an auto-approving background agent run.
  Separately, polling a run's status never checked who owned the run: any app holding the `agent`
  permission could read the task text and result of runs started by the dashboard, a schedule, or
  a different app. Both endpoints now gate on the calling app's verified identity, and a run is
  only readable by the app that started it. No app in the Store declares the `agent` permission
  today, so nothing shipped was exposed — this closes the hole before the first app needs it.
- **Discover's "see goal loops" tip opened a blank new-loop form instead of your loops.** The
  button pointed at an address the app no longer serves, so it redirected to the composer and
  asked "What do you want to accomplish?" — the opposite of showing you what already exists. It
  now opens the loop list.
- **Changing your embedding model silently stopped the assistant remembering anything.** If the
  embedding model changed after the memory index was built, every attempt to save a new memory
  failed — and nothing said so. Recall and the nightly consolidation pass broke the same way for
  the same reason. Memories are now kept whenever this happens, with a warning naming both sizes
  and telling you to re-embed, so the worst case is that older memories are temporarily missing
  from semantic search instead of new ones being lost entirely. If you have hit this, your
  memories are still there: re-embed to bring them back into search.
- **A task comment could be signed as anyone, and never taken back.** The comment endpoint took
  the author straight from the request, so any client reaching your gateway could post a comment
  under your name — and with no delete, a forged one was permanent short of hand-editing files.
  The author is now derived on the server from your configured username, sending an `author` is
  rejected outright rather than quietly ignored, and comments can be deleted from the task panel.
  A malformed comment body now answers 400 instead of failing with a server error.

## [0.1.3] — 2026-07-30

The **attention-and-access** release. Two themes:

**One place for everything waiting on you.** The inbox stops being a message list and becomes
the single attention surface — a goal loop that needs a decision, a proposed skill, and a tool
approval you walked away from all land there as items you can answer in place, instead of a
toast that scrolls past while the work stays stalled. Delivery becomes a choice per kind of
notification (notify / badge / digest / never) rather than one global severity floor, with a
daily digest for the noisy kinds.

**Reach your own assistant from anywhere.** Sessions now survive a restart (they didn't — every
restart logged you out, and away from home that meant locked out), and an optional password
sign-in with 2FA and device pairing lets a browser anywhere get in. It is off by default and
purely additive: the local token link keeps working and remains the way back in, so a login you
misconfigure cannot lock you out of your own box.

Plus: artifacts get a real library, knowledge gets shelves and a proper tag taxonomy, the agent
navigates code by symbol instead of grepping blind, backups run and verify themselves, and
👍/👎 on AI judgments starts actually teaching.


> **Note (0.x clean break):** model bindings in `active_models.json` now carry
> ordered fallback-chain semantics. Old stores read cleanly (a single binding is a
> one-entry chain); consider `personalclaw snapshot` before upgrading, per the
> pre-1.0 banner.
>
> **Note (0.x clean break):** true rewind adds a `rewound` field to persisted chat
> messages (the retained discarded tail). Old sessions read cleanly (missing field =
> today's behavior — no migration); consider `personalclaw snapshot` before upgrading.
>
> **Note (0.x clean break):** knowledge-item tags move from a JSON column into their own
> tables, and the old column is dropped. Opening your library migrates it in place — the
> upgrade is verified against duplicates, blanks, non-ASCII and malformed values, and
> refuses to drop the column if any tag would be lost. Consider `personalclaw snapshot`
> before upgrading, per the pre-1.0 banner.
>
> **Note (0.x clean break):** the unread badge now counts unresolved **inbox** items instead
> of unacknowledged notifications, so **it resets once on upgrade** — any old unacked toasts
> stop contributing to it. Nothing is lost: the notification list keeps its full history and
> becomes a delivery audit. The badge is more honest afterwards (dismissing a toast no longer
> hides work that is still outstanding, and handling something in the inbox actually clears
> it). Your inbox alert keywords move to notification rules automatically. Consider
> `personalclaw snapshot` before upgrading, per the pre-1.0 banner.

### Added

- **Sign in from outside your home network.** Reaching your own dashboard while away used to
  mean being at the machine — the only way in was a token link you had to mint locally. You can
  now set a password (`personalclaw auth set-password`) and turn on a sign-in page, so a browser
  anywhere can log in for a session. **It is off by default and it is additive:** the token link
  and the loopback paths keep working exactly as before, and they stay the way back in if you
  ever forget the password — a login you misconfigure cannot lock you out of your own box.
  Optional 2FA (`personalclaw auth totp setup`) adds a time-based code. Failed attempts are rate
  limited with a lockout, and every attempt is recorded in the audit log.
- **Pair a phone without typing your password into it.** `personalclaw auth enroll` prints a
  short code you enter once on the other device. It works exactly once, expires in five minutes,
  and is stored only as a hash — so the worst case for a code you lose on a screen is that you
  run the command again.
- **Sessions survive a restart.** Previously every gateway restart invalidated every token: on a
  local box you re-ran `personalclaw token`, and away from home you were simply locked out,
  because minting a URL required being at the machine. The signing key and the session records
  are now persisted (both `0600`). `personalclaw auth revoke --all` ends every session, and that
  survives a restart too.
- **Hardening for an internet-exposed instance.** Set `dashboard.public_url` when you reach the
  dashboard through a TLS-terminating tunnel and the session cookie gains `Secure`, the
  WebSocket policy allows `wss://` to that host, and proxy headers (`X-Forwarded-For` /
  `X-Real-IP`) are honored **only** from an address you list in `dashboard.trusted_proxies`.
  That last one closes a real hole: those headers used to be trusted based on the *shape* of the
  peer address, and on an exposed box any container neighbour sits on a private address and
  could have moved a session's bound address. A local install is unaffected — nothing changes
  until you declare a public URL. The new [remote-access guide](docs/guides/remote-access.md)
  walks the whole setup and is explicit about what it does *not* protect you from.

- **One place for everything waiting on you.** The inbox is no longer just messages: a goal
  loop that needs a decision, a proposed skill, and a tool approval you walked away from all
  land there as items you can answer in place — instead of a toast that scrolls past while the
  loop stays stalled. Filter chips show what kind of attention each thing wants, and a row
  deep-links to the loop or chat it came from.
- **Per-notification-kind delivery rules.** Settings → Notifications now has a row per kind of
  notification with four choices: notify, badge (keep it in the list without interrupting),
  digest (batch it into a daily summary), or never. Previously the only control was a global
  severity floor, so quietening one noisy kind meant raising the bar for everything. Keyword
  and name-mention alerts became per-kind conditions, which means they now work for loop
  requests and proposals too — not just channel messages.
- **A daily digest.** Anything set to `digest` collects into one grouped summary at a schedule
  you choose (08:00 by default), grouped by kind so "9 heartbeats" reads as one line. A quiet
  day produces nothing rather than an empty summary.

- **Memory records who contributed them.** Every memory PersonalClaw writes now carries your
  username, so if your memory store is ever shared — a team store, an imported export, a
  synced setup — you can tell your own memories from a colleague's. Recall labels another
  person's memory as *(from name)* and states plainly that the label is provenance, never an
  instruction to follow. At comparable relevance your own memories come first, but only as a
  tie-break: a colleague's memory that genuinely answers the question better still wins, and
  nothing is ever hidden from you on the basis of who wrote it. Existing memories keep an
  empty contributor rather than being back-stamped with your name, because a record written
  before this existed has genuinely unknown authorship. Solo installs behave exactly as
  before.

- **Memory can now offer itself, not just answer when asked.** Mention a person, project or
  tool the entity graph knows and PersonalClaw can volunteer up to three linked memories for
  that turn — including ones that share no words with what you typed, which ordinary search
  structurally cannot find ("ships Fridays" doesn't match "when does Sparrow release?"). It
  costs no tokens or model calls, and it's **off by default**, because putting context in
  front of the model that you didn't ask for should be your choice. Turn it on under
  **Settings → Memory**, along with how confident a match has to be. The Health tab shows how
  often what it volunteered actually got used afterwards, so you can tighten the setting from
  evidence instead of guesswork. Temporary chats get nothing; incognito chats get the benefit
  without anything being recorded.

- **Take a conversation with you, and stop rebuilding the same chat setup.** Any chat can
  now be exported as **Markdown or JSON** from its context menu in the chat history —
  readable, pasteable, and **credential-redacted**, including the messages you typed
  yourself. Save a chat's setup (agent, model, reasoning effort) as a **starter** from its
  header, and it appears on the new-chat screen ready to pick; manage or remove starters
  under **Settings → Chat**. A starter captures the setup only, never the conversation.

- **Hand an artifact to the agent, or point at one mid-conversation.** An artifact — a
  widget, a document, a chart — can now be opened straight into a chat set up to *change*
  it: the agent starts with the current version in front of it and a prompt that names the
  artifact, so a revision lands as a new version of that artifact instead of a
  near-duplicate beside it. And in any chat you can now reference artifacts from the
  composer's **+** menu, which grounds the reply in whatever those artifacts say *right
  now*. Each reference is recorded on the artifact's own timeline, so you can see where it
  got used and jump back to that conversation.
- **Shelves for your knowledge library — including ones that fill themselves.** Saved
  documents, notes and links were one flat list. You can now group them onto
  **collections**: a *manual* shelf holds whatever you put on it, and a **smart** shelf
  holds whatever matches a search you name once — so "everything about the borrow
  checker" stays current on its own as you save more, with nothing to re-run and no
  backfill step.

  Items can sit on several shelves at once, and a shelf is a *view*, not a container:
  deleting one leaves every document in your library untouched (the confirmation says
  so). Each shelf has its own URL, so you can link straight to one. Alongside it, items
  gain a **reading state** — unread → reading → read, because "reading" is the state a
  reading list exists to represent — and a **favorite** star. Marking something read
  deliberately does *not* count as editing it, so working through a backlog won't
  reshuffle a library sorted by recency.
- **Clean up a long chat list in one action, and let old chats retire themselves.**
  Conversations pile up, and until now the only tools were one-at-a-time. You can now
  select many chats at once and **archive**, restore, tag, re-file, or exempt them in a
  single action — and chats you haven't touched in a while (30 days by default) move to
  **Archived** on their own.

  Archiving is not deleting, and that's the point: an archived chat keeps its full
  transcript, **stays searchable**, and is one click from coming back. That's what makes
  it safe to do automatically. Anything you want kept in the list forever can be pinned
  "never archive", and opening or replying to an archived chat brings it back by itself.
  Set the window in Settings, or set it to 0 to switch auto-archive off entirely.

  Two deliberate limits: bulk **delete** is not offered beside archive — irreversible
  actions shouldn't sit one mis-click from reversible ones — and chats with no recorded
  activity yet (everything from before this shipped) are never auto-archived, so
  upgrading can't sweep away your history.
- **On a shared task board, your assistant only works on *your* tasks.** If tasks come
  from somewhere other people also write to, task rows now show who a task belongs to,
  and you can switch between "Mine" and "Everyone" — the filter appears only when
  someone else's work is actually there, so nothing changes for a solo setup.

  The part that matters most is invisible: ready-task counts, the "what should I do
  next" picker, and the agent's own work selection all count and choose **only your
  tasks**. Someone else's task can never quietly become something your assistant picks
  up. Dependencies are still honored across everyone, so a task of yours blocked by a
  colleague's unfinished work is correctly *not* ready rather than falsely startable.
- **Decks and PDFs too — and anything already saved can become a document.**
  **deck_create** turns a markdown outline into a real PowerPoint deck: each `##` is a
  slide, the lines under it become bullets, and `<!-- notes: ... -->` becomes that slide's
  speaker notes. PDF generation works on every install rather than depending on whatever
  converter happens to be on the machine.

  And the library now works in both directions: point `document_create` at a saved
  knowledge item or a note you already have, and it comes back out as a Word document you
  can send. Same generator, so an exported document is no different from a freshly
  written one.

- **It can make you a Word document or a spreadsheet you can actually send.**
  PersonalClaw could read .docx, .xlsx, .pptx and .pdf but could not produce a single
  one — everything it generated stayed inside the app. Ask for a document now and you get
  a real file: **document_create** turns markdown into a Word document (headings, bullets,
  numbered lists, tables, code, page breaks) and **sheet_create** builds a spreadsheet
  with a bold header row and frozen panes.

  Numbers stay numbers, so the result can be summed and charted — a spreadsheet full of
  text-formatted numbers is the main way generated ones turn out useless. Both land in
  your Artifacts library with version history, so re-generating one updates it in place
  instead of leaving a near-duplicate beside it, and each has a download button plus a
  text preview.

  Verified by opening the output in real applications, not just our own reader: macOS
  identifies the files as genuine Office documents and renders them correctly.

  Two things this fixed along the way. **Tables in Word documents you upload were being
  silently dropped** — the reader only looked at paragraphs, so often the densest
  information in a document was invisible to search and to the agent. And **generated
  videos were being stored as images**, which made them unplayable; video is now a real
  artifact type with a working player.

- **Tags are a real taxonomy now — nest them, rename them, merge them.** Tags were a
  flat list of strings stapled to each item, so a typo meant editing every item that
  carried it and there was no way to express that "tokio" is a kind of "rust". The new
  **Tags** view on the Knowledge page shows every tag with how many items actually use
  it, and lets you rename, nest one under another, merge two together, or delete one —
  from a right-click.

  Renaming is instant and applies everywhere at once. Merging moves every item onto the
  surviving tag and takes nested tags with it rather than orphaning them. Deleting a tag
  removes it from your items but never deletes the items, and its nested tags become
  top-level rather than disappearing with it. Unused tags are kept on purpose: a tag you
  built is part of your taxonomy even when nothing carries it this week.

  Two things this quietly fixed. **Tags with non-Latin characters were unsearchable** —
  a tag like 日本語 was stored in an escaped form the search index couldn't match, so it
  simply never came up; it does now. And the tags you write by hand are now marked as
  yours, so automatic enrichment can refresh the tags it generated without ever
  overwriting one you chose.

- **Your reading state and favorites are now visible, and filterable.** Marking a saved
  item as reading, read, or a favorite already worked — but nothing showed it anywhere,
  so favoriting was effectively write-only: you could star something and then had no way
  to find it again. Items now carry a **star** for favorites and a **reading** badge, a
  read item's title dims, and chips let you filter to Reading, Unread, Read or Favorites
  (each appearing only when there's something to show, with a count).

  Favorites finally have their own mark instead of borrowing the pin icon — pinning
  floats an item to the top of the list, favoriting is a personal bookmark, and they read
  as different things now. The reader gained the same two controls beside Pin and
  Archive. Unread items stay unbadged on purpose: it's the default state, and marking
  every new item would just be noise.

- **Curate a whole shelf of saved items in one action.** Working through a knowledge
  library one item at a time is what makes nobody do it. Select many items and **mark
  read or unread, favorite, add to a shelf, or archive** them together. Each action
  reports what actually happened — "38 shelved · 2 already there" — because a selection
  can go stale between the click and the request, and a partial success is not a failure.

  Marking a backlog read deliberately does *not* count as editing those items, so
  catching up won't reshuffle a library sorted by recency. Bulk **delete** is not
  offered beside these: everything here is reversible, and an irreversible action
  shouldn't sit one mis-click away from a safe one.
- **See what changed between two versions of an artifact.** An artifact keeps every
  version, but the only way to tell what actually moved between two of them was to open
  each in turn and compare by eye. **Compare versions** now shows a real side-by-side
  diff — for images, the two versions themselves, before and after. It opens on the two
  most recent versions, since "what changed in the last pass?" is usually the question,
  and you can pick any pair or swap which side is which.

  Whitespace-only changes are shown rather than hidden: an agent re-rendering a widget
  often re-indents it, and reporting "nothing changed" would be a lie.
- **Backups now happen on their own, and they get checked.** PersonalClaw takes a
  full snapshot nightly and exports whatever changed every hour, so how much you can
  lose is bounded by an hour rather than by when you last remembered to run
  `personalclaw snapshot`.

  Retention keeps a *spread* instead of a window — two weeks of daily snapshots, then
  weekly, then monthly — so a year of history costs about 30 files and you can still
  reach back to January. Settings → **Backups** is where all of this lives: when each
  job last ran, a button to run one now, how long each tier is kept, and the snapshot
  list marking exactly which files the current settings would remove — before anything
  is removed. Restoring stays a command-line action, and the screen says so, because a
  restore has to replace live state while the gateway is stopped.

  Once a month it also runs a **restore drill**: the newest snapshot is unpacked into
  a temporary directory and every database inside it is integrity-checked, then you
  get a pass/fail notification. A backup nobody has restored is a hope, not a backup —
  and a failure is reported as a warning, so it isn't hidden by quiet hours.
  `durability.auto_backup` turns the schedule off if you'd rather do it by hand.

  Fixed while building it: the incremental export had been unable to notice memory
  changes at all. Databases run in WAL mode, so a saved change lands in a companion
  file and the main database's timestamp never moves — the export saw "nothing
  changed" through an entire session of work.
- **Find any chat by what was said in it.** Chat search now runs against a real
  full-text index of your transcripts instead of scanning the 500 most recent files,
  so a conversation from months ago is as findable as yesterday's — and each result
  shows the matching passage with your terms highlighted, so you can tell at a glance
  which chat is the one you meant.

  On a 120-chat history a content search returns in about 30 milliseconds. The index
  keeps itself current as you chat and repairs itself on a schedule, so there is
  nothing to maintain. **Incognito and temporary chats are never indexed** — and a
  chat you switch to incognito after the fact disappears from search immediately.
  If the index is ever unavailable, search quietly falls back to the previous
  behavior rather than failing.

  Fixed along the way: a chat marked incognito *after* some of it was written could
  still appear in content search, because only its saved mode was checked and that
  still read "persistent". Both search paths now honor the live setting.
- **The agent navigates your code by symbol instead of grepping blind.** Asking
  "where is this function defined and what calls it?" used to cost a grep, a couple
  of file reads, and often another grep — every round-trip spending tokens on
  navigation instead of the actual work. A new `code_map` tool answers it in one
  call from a tree-sitter index of your workspace, and `code_map_overview` gives the
  shape of an unfamiliar codebase (the most-referenced modules and their public
  surface) in one read.

  Indexing Python, TypeScript, JavaScript, Rust and Go: on a 1,500-file repository
  the first pass takes about four seconds and later passes are effectively
  instant, since only changed files are re-read. The same index makes SDLC planning
  passes start from a map of the codebase rather than exploring for it, and ranks
  the chat composer's `@` file picker so widely-used modules surface above
  same-named leaves.

  It is strictly an accelerator: with no index, no parsers, or an unparseable file,
  everything falls back to the grep-and-read behavior it had before, and the tool
  says so plainly rather than guessing.
- **Memory now knows what it's *about*.** Every memory is linked to the people,
  projects and tools it names, so asking "what do I know about Ana?" follows those
  links instead of hoping a similarity search surfaces everything. A memory about a
  standup, a stored fact about a repo, and a lesson from last month finally connect
  when they concern the same thing.

  Linking happens the moment a memory is written and **costs nothing** — no tokens,
  no model call, just exact-name matching against the people and projects you've
  named. Links are typed, so the graph distinguishes a memory that's *about* you
  from one that merely mentions a project.

  Unknown names are **proposed, never invented**: a name that shows up across three
  separate memories appears in Memory → Health for one-click accept, because a junk
  entity quietly degrades every future search. Everything is reversible through the
  existing memory undo. Find it under **Settings → Memory → Health**, where
  *Rebuild links* seeds entities from what you've already stored and links your
  whole history in one pass. `memory.graph_enabled` turns it off — existing links
  are kept, so turning it back on needs no rebuild.

  > **Note (0.x clean break):** this adds tables to `memory.db` (schema v7). Old
  > stores upgrade in place on first run with no data loss and nothing to migrate;
  > consider `personalclaw snapshot` beforehand, per the pre-1.0 banner.
- **Your IDE can now actually ask your assistant things.** The MCP endpoint ships with
  six read-only tools: recall what the assistant remembers, search your saved documents,
  list or read a task, search past conversations by what was said, and check what the
  instance can currently do. Every answer is wrapped as data rather than instructions,
  conversation results are credential-redacted, and temporary or incognito chats are
  never searchable. There is no path from any of these to a write — the tool list is
  short and hand-written precisely so that stays true.

  **Security fix found while building it:** credential redaction did not recognize LLM
  provider API keys. Anthropic, OpenAI, GitHub, and Google keys pasted into a chat were
  invisible to the redactor, so they could survive into anything that strips secrets on
  the way out — session-search results and conversation titles included. All of those
  key shapes are now redacted everywhere redaction applies.
- **Point your IDE at your assistant: a read-only MCP endpoint.** PersonalClaw can
  now answer questions from a local MCP client (your IDE, an MCP inspector) over
  `POST /mcp` — JSON-RPC 2.0, with `initialize`, `tools/list` and `tools/call`.
  It is **off until you deliberately turn it on**, and turning it on takes two
  separate steps that are both re-checked on every request:

  ```bash
  personalclaw inbound token create mcp        # printed once — copy it now
  personalclaw config set inbound.mcp.enabled true
  ```

  Because both are checked per request, `inbound.mcp.enabled false` is an immediate
  kill switch — no restart. The surface answers **loopback callers only** unless you
  explicitly declare a public URL and opt into remote access in the config file;
  neither of those knobs is editable from the dashboard, so widening your network
  exposure can't be one mis-click in a browser. Requests are capped on every
  dimension an outside caller controls (body size, rate, concurrency, result size),
  refused requests come back with a real JSON-RPC error, and every request — allowed
  or refused — is recorded in `<home>/inbound_audit.jsonl`, with refusals also
  landing in the security event log. This release ships the surface with **no tools
  yet**: a client can connect and see an empty table. The five curated read-only
  tools (memory, knowledge, tasks, sessions, status) follow next, and by
  construction they can only ever read — there is no path from an inbound request to
  a write.
- **Tool groups: the agent loads the tools it needs, not all of them.** Every tool
  provider is now an activatable **group** (`schedule`, `artifacts`, `memory`, one
  per MCP server or app, …), and a session can run with only the groups it needs —
  so unused capabilities cost one line of catalog instead of every schema. Measured
  on the 69-tool built-in surface: a background session's tool block drops **56%
  (~6,900 tokens per turn)**. The agent manages this itself via a new `reset_tools`
  tool that takes the *final* set of groups it wants.
  Capability is never reduced, only context: **every tool stays callable by name
  even while its group is inactive**, each inactive group advertises itself in one
  line, and `tool_search` still searches everything — naming the activation step
  when it finds a tool in an inactive group. Off by default (`tools.groups_enabled`),
  and interactive chat keeps every group active even when on, so nothing changes for
  the chat you're watching; background, loop, and subagent runs start focused
  (tune per surface with `tools.group_defaults`). `GET /api/tools` now reports each
  tool's group.
- **The artifacts library: live previews, search, and collections.** The new
  Artifacts page is now a real library: a responsive grid where every card renders
  a **live preview** — widgets/HTML/React/documents/SVG in the same sandboxed,
  theme-injected frame chat widgets use (scaled down, inert, never re-implemented),
  images from their raw bytes, prose as an excerpt. Previews mount lazily as cards
  approach the viewport and at most a dozen live frames exist at once (older ones
  quietly demote to placeholders), so a 200-artifact library scrolls smoothly. The
  toolbar filters by text, kind, source, and **collection** — all URL-backed, so a
  filtered view is shareable — plus Recent/Name/Kind sort. Opening a card gives the
  full-page detail (version rail, events timeline, edit/snapshot/revert) with
  `?v=N` deep-linking a historical snapshot read-only, and a header control to
  assign the artifact to a collection. File-backed cards show a "source changed"
  drift badge.
- **Artifacts get their own page.** Artifacts — the named, versioned outputs agents
  produce — moved out of the Files page into their own top-level **Artifacts** nav
  entry (`#/artifacts`, deep-linkable per slug). Files now shows only your raw file
  roots; old `#/files/<slug>` artifact links (in past chats and event timelines)
  redirect automatically to the artifact's new home, and "Save as artifact" from a
  file jumps you there. The viewer itself is unchanged — render, edit/snapshot,
  version history + revert, and the events timeline all work exactly as before.
- **Artifacts: collections + save-time dedup.** Saved artifacts can now carry a
  **collection** label (a free-form grouping for the coming library), settable at
  save time and reassignable later, and filterable via `GET /api/artifacts?collection=`
  and the `artifact_list` tool. And saving no longer silently mints duplicates: a
  fresh `artifact_save` (or `POST /api/artifacts`) whose name matches an existing
  artifact now **refuses with a hint** — the tool tells the agent to update the
  existing slug or pass `force`, and the REST route returns `409 similar_artifact_exists`
  with the existing slug (bypass with `?force=1`). File-backed saves keep their
  existing source-path dedup. Pre-existing artifacts load unchanged (tolerant read).
- **Agent routing: suggest the right specialist, never route silently.** Give an
  installed agent a **Specialty** and comma-separated **Routing hints** (in the agent
  editor), and when a message in a default-agent chat clearly fits it, a quiet
  "route to `<agent>`?" chip appears above the composer. One click re-targets the
  session (via the existing agent-switch path); the ✕ dismisses it and suppresses
  that agent for a cooldown (three dismissals mute it until you re-enable). It is a
  **proposal** — nothing changes until you click — and classification is
  deterministic-first (keyword-phrase overlap, then embedding cosine when an
  embedding model is bound), with the LLM never in the hot path. Silent auto-routing
  is deliberately out of scope. Route/dismiss also feed the routing pair's accuracy
  into Settings → AI feedback. Tune it in Settings → Chat → Agent routing
  (`agents_routing.*`); zero behavior change until you author routing metadata.
- **Chat craft: seven chat-surface mechanics.** The chat surface gains the pieces
  the sibling platforms proved out. **True rewind** — edit ANY past user message and
  replay from there; the discarded answers are kept in this chat's history (viewable
  under a "rewound from here" divider, restorable as a fork) and the provider context
  rebuilds from the truncated transcript, so the agent never references the undone
  turns. **Queue with manners** — each queued message now has an "Interrupt now" that
  gracefully stops the running turn and runs that message next. **Find in
  conversation** — Cmd/Ctrl+F opens an in-chat find bar (count, next/prev, jump-to-
  match) that highlights every occurrence without ever re-rendering the markdown.
  **Quote toolbar** — selecting transcript text floats a Quote + Copy toolbar; Quote
  inserts an attributed blockquote (who said it) into the composer, now from keyboard
  and touch selections too. **Follow-up chips** — after each reply, 2-3 suggested next
  messages appear via one cheap background call (never blocks the turn; skipped for
  temporary/incognito chats and silent when no model is bound; toggle in Settings →
  Chat). **Smoother streaming** — the reveal snaps to word boundaries so text lands in
  whole words, with a new Settings → Chat "Streaming text reveal" (smooth | immediate)
  control.
- **Background compression keeps long chats fast.** Old, idle conversation history
  is now topic-segmented and compressed in the background on the maintenance
  cadence — the always-on complement to on-demand tool-output projection. A
  transcript untouched for a week (default) is split into topics (by embedding drift
  when an embedding model is bound; a deterministic turn-count fallback otherwise),
  then compressed by attention: the most-recent topic stays verbatim, middle topics
  reduce to their request/response pairs, and the oldest tier is summarized by a
  cheap background model. It only ever touches sessions **at rest** (never a live
  turn), incognito/temporary chats are skipped entirely, every dropped span is
  archived first (fully recoverable) and any tool-result recovery handle is
  preserved, and savings land in the TokenJuice ledger under `bg_topic`. Toggle and
  idle window live in Settings → Chat config (`tools.bg_compress_enabled` /
  `tools.bg_compress_idle_days`); disabling it stops the pass within one tick.
- **Feedback that actually teaches: 👍/👎 on AI judgments.** Inbox classifications,
  drafted replies, digests, and loop findings now carry a quiet thumbs pair. 👍 is
  silent-positive ("Mark accurate" — it only feeds the accuracy denominator); 👎
  optionally takes a one-line "why". Every verdict is attributed to the source that
  produced the judgment — the bound prompt, the loop judge, a workflow's surfacing —
  and per-source rolling accuracy lives in Settings → AI feedback (honest counts,
  shown only after enough verdicts). A source that keeps being wrong **stops
  surfacing** and raises a one-time "retire this rule?" notification with a deep
  link; snooze or clear it after an edit. Everything is deterministic counting —
  no model calls, and feedback never leaves the instance. Apps record feedback on
  their own judgments via `personalclaw.sdk.feedback` / `POST /api/feedback`
  (namespaced server-side, so an app can never impersonate a core source).
- **Investigate anywhere: chat about any entity with its context pre-loaded.** Inbox
  items and loop findings (more surfaces to follow) gain an "Investigate in chat"
  button that opens a fresh chat carrying the entity's full context — composed
  server-side from the owning store, injected as fenced untrusted data on your
  first message (never pasted into your visible text), with the composer pre-filled
  with an editable opening question. The session opens in read-only **Ask** mode —
  investigating never mutates the entity; you escalate the mode yourself. A header
  chip deep-links back to the source. Apps get the same primitive via
  `useInvestigate` in the app SDK.
- **Model use-cases v2: routing sub-categories + fallback chains.** Chat work is
  now routable by kind — `background` (titles, tags, suggestions, digests,
  consolidation), `orchestration` (supervising turns and model-less subagents),
  `loops` (goal-loop workers and judges), alongside the existing `code_tools` and
  `reasoning` — each bindable in Settings → Models under a new **Chat routing**
  group, falling back to your Chat chain when unbound. Bind a cheap or local model
  to `background` and housekeeping chores stop burning your flagship chat model.
- **Every model binding is an ordered fallback chain.** The first model is the
  default; later entries take over when an earlier provider's circuit breaker is
  open or a call fails (background calls advance mid-batch; a failed chain surfaces
  one clear error). The Models panel gains a chain editor with reordering and
  per-entry provider-health dots; the composer's model pick sits above the chain —
  if the picked model fails, the chain takes over.
- **Type-routed tool-output compressors.** Large tool results now project smarter: a
  JSON array of thousands of items becomes a per-field schema (names, types, ranges,
  null counts) plus the first/last item verbatim; a large code file becomes a
  signatures-and-docstrings outline with a line map (`code` is a new content type,
  sniffed conservatively). The full raw always stays one `tool_result_get` away.
- **Projection rules: three layers + line operations.** A builtin rule pack now
  recognises common command output (git, pytest, npm, docker, cargo…) so e.g. a
  `git diff` run through the shell projects as a diff; a repo can ship its own
  `.personalclaw/projection_rules.json` (project layer, beats user rules); and every
  rule may carry declarative line operations — head/tail window, keep/skip filters,
  and a fold-repeats counter — editable in Settings → Tool output.
- **Background prose summarizer.** Long natural-language output on background paths
  can be model-summarized with a guaranteed deterministic fallback (never wired into
  the synchronous tool path).

- **"Investigate in chat" is now on everything worth asking about.** The affordance
  that shipped for inbox items and loop findings now covers eleven more kinds:
  notifications, tasks, schedule runs, triggers, loop cycles, knowledge items, memory
  records and lessons, Doctor findings, crash reports, and audit events. One click opens
  a read-only chat already carrying that entity's context, with the question already
  written for you.
  Failures get the richest context, which is the point: investigating a failed cron or
  loop notification pulls in **the run it's about** — its task, status, latest finding,
  the job's cadence and consecutive-failure count — and asks "why did this fail?".
  A Doctor finding re-runs its probes and brings the offered fix's *dry-run preview*
  (never applying it). A learned lesson brings its provenance and the chain of beliefs
  it replaced, and asks "why do you believe this?". An audit entry brings the other
  entries from the same approval flow, so one decision reads as one story.
- **Tool groups are now visible, and they hide what can't work.** The Tools page shows
  the group partition — every group with its tool count, which ones are always loaded,
  and what each kind of session starts with — plus the switch to turn grouping on or
  off. Each tool provider is labeled with the group it belongs to. And groups whose
  capability isn't configured (subagent tools with no model bound, say) are now hidden
  entirely rather than offered in a state where they'd fail; asking to activate one
  says so plainly instead of quietly doing nothing. New `GET /api/tools/groups` reports
  the partition for anything else that needs it.

- **Personalities: themes that carry an identity, not just a palette.** Settings → Design
  now offers a **personality** — one switch that sets the color scheme, the wordmark, the
  browser tab title, and the interface density together, and can offer the assistant a
  matching name. Two are included as starting points (a mono-green **Retro Terminal** with
  a terse operator voice, and **Claw Arcade**), alongside the default PersonalClaw identity.
  Renaming the assistant is **offered, never assumed**: activating a personality shows a
  toggle naming the exact setting it would change, and declining it switches the look while
  leaving your configuration alone. Switching back restores everything, including the name.
  Every personality's palette goes through the same accessibility contrast checks as the
  built-in schemes, so a personality can't ship an unreadable theme.

- **A username, so your contributions stay attributable.** Settings → Account now takes
  a short handle (Settings suggests one from your name), and PersonalClaw stamps it onto
  things you create — tasks and task comments carry an author. It's a label, not a login:
  nothing signs in with it, and leaving it empty keeps records unattributed exactly as
  before. Renaming it affects future writes only; existing records keep the name they
  were written with, because rewriting them would falsify the very history attribution
  exists to preserve.

- **Backups you can actually read and verify: `personalclaw backup`.** Alongside the
  opaque snapshot tarball, state can now be exported as **deterministic shards** —
  one canonical JSONL file per store plus a SHA-256 manifest. Identical state always
  produces identical bytes, so the export diffs cleanly (adding one task shows up as
  exactly one added line — `git log` over your shards becomes a readable history of
  what the assistant learned) and a future sync never re-uploads data that didn't
  change. `personalclaw backup validate` re-derives every shard's size, row count and
  checksum, re-parses every row, and **exits non-zero** on any problem, so you can run
  it from cron: a backup nobody has verified is a hope, not a backup. `--incremental`
  re-exports only what changed. Secrets are never included — shards are the
  representation that leaves your machine.

### Fixed

- **`personalclaw logout` never actually revoked anything.** It printed
  "All dashboard sessions revoked" and returned success while the gateway refused the request
  (403) and every session kept working — the endpoint was gated behind the very dashboard
  session it was meant to end. Found by running the command against a real gateway rather than
  reading the code.

- **Auto-archive skipped the very chats it existed to tidy — and you couldn't see or
  change the rule.** Chats are archived after a period of inactivity (30 days by default),
  but the sweep only considered conversations currently loaded in memory. Since old chats
  aren't loaded until you open them, a conversation idle for months was exactly the one the
  rule could never reach: it ran hourly and reported that nothing was stale. The sweep now
  covers chats that exist only on disk, archiving them in place without loading them.
  Separately, the threshold had no control anywhere in the app — it now lives under
  **Settings → Chat → Context & lifecycle**, showing how many chats are currently stale so
  you can see what the rule would do before it does it. Archiving remains fully
  reversible; nothing is deleted.

- **The "Steer" button never steered.** Typing while an answer was streaming showed a
  button labelled *"Steer — send into the running turn"*, and clicking it queued the
  message for *after* that turn instead — then displayed a card reading "1 queued · sent
  one at a time as each turn finishes", contradicting the button you had just pressed. The
  message was never lost, but it never did what the label promised. Steering now actually
  reaches the answer being written, and the message appears above the composer marked as
  steered rather than queued.

  Four independent faults each guaranteed the failure, which is why it survived: the
  frontend asked for `followup` regardless of the button; the lookup used a session key the
  session manager never registers, so the steer path could not match a session *at all*;
  the drain lived only after a tool batch, so a plain-prose turn — the most common kind —
  ran past it and discarded the message; and the confirmation event was filtered out as
  status noise, so even a successful steer was invisible. A steer sent to a runtime with
  no delivery path used to be buffered and silently dropped while the API answered
  `{"steered": true}`, growing an unread backlog for the life of the process; it now
  queues visibly instead. Mid-turn handling also gained a **Steer** option in
  Settings → Chat, alongside Queue and Replace — the policy field shipped earlier with no
  control at all.
- **Knowledge and memory could never embed with a config-defined provider.** With an
  embedding model bound to a provider you configured yourself (an Ollama endpoint, say),
  ingested knowledge items sat at "processing" with no embedding **forever** — no error,
  no notification, just nothing. Semantic search and the entity graph had nothing to work
  with, and memory's semantic layer could not embed at all. Chat through the very same
  provider worked, which made it look like embedding was broken rather than unavailable.
  The cause: configured providers are replayed into the model registry during gateway
  startup, but the background embed pass could run before or outside that path and then
  saw an empty registry. It now replays the configured providers itself when a lookup
  misses, so the embed succeeds instead of silently returning nothing — and a provider
  that genuinely is not configured still reports that plainly rather than retrying. (#47)
- **Binding a model can no longer fail silently.** `PUT /api/models/active/{use_case}`
  answered "ok" in two cases where the binding had not actually taken. A request whose
  body never mentioned `models` — an automation or a person reasonably guessing the key
  name — was read as "clear this binding", so it **unset the use-case's model and still
  reported success**. And on a fresh install (a config with no providers configured yet)
  the unknown-provider check was skipped entirely, so a model reference naming a provider
  that does not exist was stored unchallenged as a dead binding. Now a body without
  `models` is a `400` that names the keys it did receive, clearing requires an explicit
  `{"models": []}`, and an unknown provider is rejected whether or not other providers are
  configured. The model *id* is still deliberately not checked against the discovered
  catalog — a real provider that is slow to enumerate its models must not have valid
  references rejected. (#48)
- **Settings and the Store no longer blink to a loading skeleton when you touch
  anything.** Clicking "Check" for updates, flipping a toggle, rotating a key, adding a
  lexicon entry, or finishing an app install tore the whole panel (or the entire apps
  grid) down to a skeleton and rebuilt it — reading as a jarring full-page refresh even
  though the data had barely changed. One shared cause: the stale-while-revalidate cache
  dropped its value the instant a panel asked to reload, so every panel's "no data yet →
  show a skeleton" branch fired on the way to fresh data that was already in flight.
  Reloading now *holds* what is on screen and swaps in the new data when it lands, which
  is what stale-while-revalidate was supposed to mean. Switching to a genuinely different
  resource still clears, so one page's rows can never paint under another's filter. The
  fix is in the shared data hook, so all **88 reload sites across 32 panels** are covered
  at once. (#52)
- **Installing an app and updating PersonalClaw both failed on a `uv` virtualenv.**
  A `uv venv` ships no `pip` module — uv is the installer — but four separate code
  paths hardcoded `python -m pip install`, so each died with `No module named pip`
  on the project's own documented dev setup and on the uv-based end-user install
  path. Installing **any** app that declares `pythonDependencies` failed, and
  Settings → Updates showed "Update failed — pip upgrade failed" forever with no way
  to see why (the real cause was logged but never sent to the UI). Now one resolver
  picks the installer that actually exists — **uv** (targeted at the running
  interpreter, so the packages land where the gateway imports from) or **pip** — and
  says so plainly, naming both remedies, when neither is available. The update panel
  now shows the **real** failure reason instead of a static label, with the
  installer's colour codes stripped and the meaningful line first. The same resolver
  also fixes startup dependency repair and the git-checkout updater's editable
  install, which had the identical assumption. (#46, #51)
- **`personalclaw snapshot` was not backing up everything — and could copy a live
  database unsafely.** Two real problems, both closed. Your **tasks, projects,
  autonomous runs, artifacts, prompts, workflows, agents, installed apps, and
  per-entity settings were in no backup at all** — the snapshot carried a
  hand-written file list that had drifted from what the app actually stores, so a
  "full backup" could silently omit your entire task board. And only two of the
  five databases were copied with SQLite's safe backup API; the rest
  (knowledge, lexicon, autonomous-run records) were copied as plain files while
  the gateway held them open, which can capture a half-written database. In a
  reproduction of that case, a raw copy lost **2000 of 4000 rows**; the fixed path
  captures all of them.
  There is now one declared inventory of every store, which the snapshot and the
  portable export both read — plus a test that fails the build the moment a new
  store or database is added without being declared, so this can't drift again.
- **Snapshots of a non-default home no longer land in your real home.** With
  `PERSONALCLAW_HOME` set, the archive went to `~/.personalclaw/snapshots` anyway,
  mixing two installs' backups in the directory that retention pruning walks.

### Changed

- **Breaking-change policy is now written down, and it distinguishes maintainer from
  contributor.** During 0.x the maintainer keeps making backward-incompatible
  clean-break architectural changes with no migrations — that stays true, and the
  README now says plainly that this is expected to last a while, because
  migration-backed discipline arrives deliberately late, once the architecture stops
  moving. What's new is the other half: **contributors are
  not expected to make breaking changes.** Contributor guidance stays
  lifecycle-shaped — additive by default, no hand-rolled gate or migration
  machinery, and surface a needed break in an issue or PR description instead of
  shipping it, so the maintainer decides whether to take it, reshape it, or schedule
  it. See [CONTRIBUTING.md](CONTRIBUTING.md#breaking-changes); the PR template's change
  class now spells out both paths.

Forward-looking work is tracked in [docs/roadmap/](docs/roadmap/roadmap.md).

## [0.1.2] — 2026-07-26

The **safety-and-resilience** release: the autonomy guardrails program (kill switch,
spend budgets, denylist, outbound scanning, named safety profiles), the full Platform
Resilience program (Doctor health probes, no-model degraded mode, mid-turn message
policy, confirm-gated fixes + trust simulators + crash capture, and a health-scored
self-maintenance engine), first-party apps in the Store on a plain install, the
legibility surfaces (self-documenting UI kit, Discover, routed project context, offline
agent reference), and a render-smoke gate that closes the v0.1.0 blank-dashboard hole.

### Added

- **One health-scored maintenance engine replaces scattered upkeep.** PersonalClaw now
  computes a **health score** (100 − measured deficits: knowledge items missing an
  embedding, orphaned stale locks, skills due for aging — each capped, and an *unfixable*
  deficit like "no embedder bound" is excluded rather than held against a score you can't
  improve) and runs a **dependency-ordered remediation plan** to raise it: re-index,
  orphan-prune, skill-age, stopping when the target score is reached, the per-run dollar
  cap is spent, or the plan is exhausted. It runs itself on an **adaptive heartbeat cadence**
  (further apart when healthy, sooner when degraded) and is visible + runnable on demand
  from Settings → Doctor → Maintenance, with a run ledger. Deterministic jobs are free;
  model-touching jobs (future) charge the guardrails spend meter. Every run is idempotent
  (per-job cooldowns) and the whole engine is one toggle — disabling it falls back to the
  legacy per-tick heartbeat maintenance. This is the final slice of the **Platform
  Resilience** program (Doctor · degraded mode · mid-turn · fixes/simulators/crash-capture ·
  this engine).
- **The Doctor can now fix what it finds, explain what it surfaces, and remember what
  crashed.** Three additions to the health surface (all Settings → Doctor):
  **confirm-gated fixes** — a finding that has a repair (a `static/dist` copy shadowing
  the runtime symlink, stale locks/rollback leftovers, model bindings pointing at removed
  providers) shows a **Fix** button with a read-only preview; nothing auto-applies, a
  two-step confirm runs it, and every application is security-audited and touches harness
  mechanics only (never your content). A **per-provider selftest** fires a tiny real
  inference per capability (a one-token chat / short embed) for true ground-truth instead
  of a reachability guess. A **surfacing simulator** dry-runs the skill scorer in explain
  mode — type a query and see, per candidate, the keyword/semantic scores, the thresholds,
  and exactly why each skill was included or excluded (zero model calls). And **structured
  crash capture**: an unhandled failure at a turn/loop/gateway boundary now writes one
  redacted, recoverable artifact under `~/.personalclaw/crashes/` (capped, never uploaded)
  that the Doctor surfaces as a card — a mid-stream death leaves a record instead of a lost
  stack trace.
- **Mid-turn message policy: queue (default) or cancel-and-replace.** A follow-up sent
  while a turn is still generating now follows a *declared* policy. The default,
  **`queue`**, is today's behavior formalized — the message is delivered next turn. Opt
  into **`cancel_and_replace`** (a platform default in Settings, overridable per channel)
  and a rapid follow-up instead cancels the in-flight answer and starts fresh with the new
  message — no stale ghost response, no wasted compute. A per-session debounce coalesces a
  burst of messages into ONE cancel + the last message. The guard is strict: only
  **interactive** turns (the web chat, a channel DM) are ever cancel-and-replaced —
  unattended work (goal loops, cron, subagents, the heartbeat) always queues, so a user
  message can never pull the rug out from under a background job. Built on the existing
  soft-cancel verb and turn-end queue drain (no new dispatch path); a new
  `resilience/active_jobs.py` tracks each turn's origin as the bookkeeping behind the
  decision.
- **No-model degraded mode: the assistant stays useful, and honest, with no model bound.**
  Every model-dependent surface now declares its **LLM-free floor** explicitly, so an
  offline laptop (dead ollama, wiped cache, no API key) degrades by design instead of
  error-walling: search drops from hybrid to keyword (FTS) + graph + recency ranking;
  the inbox keeps raising keyword/name-mention alerts (only auto-classify/draft/digest
  pause); knowledge still captures documents (only entity/insight extraction is skipped,
  marking the item partial); memory keeps its deterministic preference-facet capture;
  speech features turn visibly off rather than erroring; and chat says so plainly rather
  than faking a reply. A compact **degraded chip** appears in the shell (with a popover
  listing each degraded surface, its floor, and any pending-enrichment backlog) whenever a
  surface is running on its floor, and a notification fires on the transition down
  (`warning`) and on recovery (`info`). A lint test asserts every non-interactive
  model-call site maps to a registered contract, so a future surface can't ship without
  declaring its floor. New `GET /api/resilience/degraded`; two guard-class config switches
  (`resilience.doctor_enabled`, `resilience.degraded_indicator` — a missing/unknown value
  keeps the surface visible).
- **A Doctor tab now diagnoses every subsystem from one read-only view.** Settings →
  Doctor runs **tiered health probes** — process → socket → cheap-RPC → per-capability
  — across memory (db + faiss consistency), channels, local models (availability +
  phantom bindings), app backends (+ interrupted-update leftovers), the SPA
  `static/dist` symlink (the stale-SPA bug-class), and model-provider breakers
  (composed from the guardrails audit). The core doctrine is enforced: **a degraded
  capability never marks the gateway down and never suggests a restart** — only a
  core-tier failure does. Every probe is read-only and fail-safe (an exception
  becomes a failed row, never a 500), and secrets are redacted from probe output. New
  endpoints `GET /api/doctor` (all capabilities, cached 30s) and
  `GET /api/doctor/{capability}` (re-run one card); the dashboard System Health strip
  gains a one-line rollup that appears only when something needs attention and links
  to the tab. Confirm-gated auto-fixes and the trust/debug simulators land in later
  Platform-Resilience sessions.
- **First-party apps now appear in the Store on a plain install.** The published
  first-party apps repository (`github.com/PersonalClaw/PersonalClawApps`) ships as
  a default Store source, so a bare `pip install personalclaw` surfaces every
  first-party app — model providers (OpenAI, Anthropic, Bedrock, Ollama, …),
  search, speech, channels — without the dev workspace tree. They appear
  **uninstalled**: nothing runs until you click Install, so the per-app
  install-consent + provider-agnostic-core contracts are unchanged. The source is a
  built-in default (not user-removable); the dev filesystem source and the
  `PERSONALCLAW_FIRST_PARTY_APPS_DIR` override still work for offline/local-clone
  development. The Store's catalog scan is cached (5-minute TTL) and runs off the
  event loop, so the first open clones once in the background.
- **Every non-interactive model call now passes through one guarded seam.**
  Background LLM calls (the `reasoning` axis behind `one_shot_completion`, the
  goal-loop judges, the loop gates, web-extract) are now wrapped in a
  **model-call guard** — the LLM twin of the network egress chokepoint. It adds a
  **per-provider circuit breaker** (opens after N consecutive failures, half-opens
  after a recovery window): during a provider outage an overnight run fails in
  microseconds instead of stacking timeouts. It adds a **hard wall-clock timeout**
  on every call, and an **attempt-level JSONL audit trail**
  (`~/.personalclaw/model_calls.jsonl`, one line per attempt, trimmed to the most
  recent entries) recording provider, model, latency, tokens, and outcome. The
  **interactive chat stream is deliberately untouched** — a human is watching it.
  `one_shot_completion` also gains a typed **`output_type`** option: pass `dict`
  or `list` to require a parseable JSON shape, and a parse miss is retried once
  with a targeted correction note before raising a loud `OutputContractError` —
  replacing the silent `None` degrade that `parse_llm_json` returned at every
  call site (migrated: web-extract, inbox classify). Goal-loop and eval judge
  verdicts gain a bounded **`reasoning`** field written before the verdict, so a
  structured-output constraint no longer suppresses the judge's chain of thought.
  A new graded provider capability descriptor (`structured_output`:
  `none`/`json_mode`/`json_schema`) lets provider apps opt into native
  schema enforcement in a later change; until then every provider gets the
  universal parse-with-retry path. This is a **clean break** (pre-1.0): the new
  audit trail is additive on-disk state under `~/.personalclaw/` — **run
  `personalclaw snapshot` before upgrading** if you want a rollback point.
  (AUTONOMY-GUARDRAILS §2, Session 1.)
- **Unattended spend now has budgets, and outbound prompts are scanned for
  secrets.** A new **Guardrails** settings section (`config.json` → `guardrails`)
  adds daily spend ceilings for unattended work: set a **max tokens/day** or
  **max dollars/day** and when the day's automated spend hits the ceiling, further
  unattended LLM calls are refused (a cron agent fire is skipped with a one-time
  "daily automation budget reached" notification, a subagent spawn is refused) —
  interactive chat is never budget-gated. Spend is metered at the model-call seam
  into `~/.personalclaw/spend.json` (per-day, pruned after 30 days), with dollars
  estimated from the existing per-model price table (provider-reported cost
  preferred). Every outbound prompt bound for a **remote** provider is scanned for
  secrets/PII (AWS keys, private keys, Slack tokens, emails, phone numbers) and
  handled per a configurable **scan mode**: `warn` (log + send), `redact`
  (substitute + send, the default), or `block` (refuse the call); a local provider
  is always `warn` since its content never leaves the machine. The circuit-breaker
  thresholds from Session 1 are now configurable here too (failure threshold,
  recovery seconds). Defaults are **unlimited budget + redact**, so an existing
  install's behavior is unchanged until you set a ceiling. Clean break (pre-1.0):
  additive on-disk state (`spend.json`) — **run `personalclaw snapshot` before
  upgrading** for a rollback point. (AUTONOMY-GUARDRAILS §1.1, §2.2, Session 2.)
- **A kill switch, a path/action denylist, and a live-write guard for unattended
  work.** Three safety-floor controls land. (1) **`personalclaw incident on`** (and
  `POST /api/incident`) suspends every unattended fire — cron, hooks, event
  triggers, subagent spawns — within one poll interval; **interactive chat keeps
  working**, and resuming requires an explicit `personalclaw incident off` (or
  `POST /api/incident/resume {confirm:true}`). Activation/resume are tamper-evidently
  logged. (2) A **path/action denylist** (`security.autonomy_denylist`, rules of
  `{paths, actions, verdict: block|needs_human}`) is enforced at all three
  action-dispatch seams (script hooks, scheduled jobs, memory-event triggers), so
  an app-contributed action provider inherits it without cooperating; it composes
  with the always-on built-in sensitive-path + destructive-command denylists. A
  `needs_human` rule holds the action and raises a needs-input notification instead
  of dropping it. (3) **`PERSONALCLAW_DISABLE_LIVE_WRITES=1`** makes live,
  hard-to-reverse writes (deleting a downloaded model, a non-GET request to a
  non-loopback host) refuse with a loud typed error instead of executing — and it
  is auto-set for the whole test suite, structurally closing the bug class where a
  destructive test once deleted a real bound model. Guard flags parse fail-safe
  (a missing/typo'd value keeps the guard ON), and the outbound scan now defaults
  to `redact` (never the leaky `warn`), enforced by a schema test. Clean break
  (pre-1.0): additive config + an `incident.json` flag file — **run `personalclaw
  snapshot` before upgrading**. (AUTONOMY-GUARDRAILS §1.2–§1.4, §5, Session 3.)
- **A Guardrails settings surface, a provider-health view, and named safety
  profiles.** The safety floor gets its cockpit and its posture layer. A new
  **Settings → Guardrails** panel gathers the incident kill switch (with a
  one-click toggle), the daily spend budgets, the outbound scan mode, and the
  circuit-breaker tuning — and a **provider-health view** derived from the
  model-call audit (per-provider breaker state, pass rate, p50/p90/p99 latency,
  recent failure modes; `GET /api/models/health`, computed from files already on
  disk — no telemetry). A **persistent incident banner** now shows on every page
  while incident mode is active, with inline Resume. Under the hood, **named safety
  profiles** (`interactive` / `coding` / `review-only` / `cleanup` / `incident` /
  `headless`) become the single object that decides approval + tool grants + egress
  tier + budget + scan for a run; unattended runs (cron, subagents, channel, inbox,
  loop workers) resolve to the read-only **`headless`** profile *by construction*
  from their session key, and a curated **package-registry egress tier** lets a
  sandboxed run reach pypi/npm/crates/GitHub/… without opening the whole internet.
  Defaults preserve today's behavior. Clean break (pre-1.0), additive. (AUTONOMY-
  GUARDRAILS §2.5, §3, §4.2, §4.4, Session 4.)
- **The animated dot-wave backdrop is now a choosable background style.** A new
  **Background** control in Settings → Design → Backdrop & motion switches the
  surface behind chat, the new-chat composer, and onboarding between four modes:
  `waves` (the animated breathing dot-wave surface, default), `still` (the same
  dot field frozen — the lattice without the motion), `glow` (only the soft light
  hugging the composer, no dots), and `none` (a plain, empty canvas). The choice
  persists in your appearance settings and applies live with no reload. Motion
  modes still honor `prefers-reduced-motion` (they render one static frame).
- **PersonalClaw describes its own UI kit, guides you to the parts of itself you
  haven't tried, and hands external agents a routed project context.** Three
  legibility surfaces land together. (1) The `ui/` component kit is now
  self-documenting: each primitive ships a `.doc.ts` object (purpose, props,
  best-practice tenet) compiled into `ui-docs.json` at build time, and two agent
  tools — `ui_search(query)` for a budgeted brief and `ui_get(name)` for
  machine-readable props — let an app-building agent find the right primitive
  instead of hand-rolling chrome; a drift test fails the build if a primitive ships
  without its doc. (2) A **Discover** surface guides you through the parts of
  PersonalClaw you haven't tried yet — a hand-authored catalog of user-facing areas
  (Chat, goal loops, automation, Tasks, Projects, Inbox, Knowledge, Memory, Skills,
  Apps), each a one- or two-sentence lesson with a deep link into the page that owns
  it. It is deliberately NOT tool-derived: the tool surface is an implementation
  detail you're never meant to drive by hand. The dashboard shows a rotating
  spotlight of the first few; a dedicated **Discover hub** (`#/discover`, also in the
  command palette) lists every tip grouped by area. A tip leaves the feed two ways,
  both hide-only: an explicit dismiss that persists forever, and an auto-hide once
  you've actually used that area (detected from state that already exists — a chat on
  disk, a knowledge item, a scheduled job…). It only points and hides, never enables
  anything, and the whole surface is behind the `legibility.discover_tips` config
  flag. (3) PersonalClaw
  can act as a **routed-context provider** for external coding agents: per project it
  assembles a tiered manifest — hard rules/brief at the top, scored memories + skills
  + knowledge *pointers* in the middle, and an L0 catalog of what was NOT loaded (with
  the tool to pull each) at the bottom — exposed as the in-process `get_context` MCP
  tool and, opt-in per project (`legibility.context_adapters`, default off), rendered
  into the project's `CLAUDE.md` / `AGENTS.md` / `.cursorrules` inside a
  `<!-- PCLAW:START -->` fence that regenerates in place and never touches your own
  content outside the markers. Memory-derived and knowledge-derived content stay under
  distinct headings, and knowledge items render as titled pointers — never inlined
  bodies. A "Refresh context files" action on the project page (re)writes the block.
- **Apps surface their skills and backend routes to the agent (declared, not
  discovered).** An app now declares two legible surfaces in `app.json`, both
  readable without executing app code. `skills[]` names SKILL.md directories the
  app ships and OWNS: on enable they seed into the user skills tree **through the
  supply-chain chokepoint** (quarantine → scan at the app's trust tier →
  `.pclaw-lock.json` provenance) — an app skill never bypasses the gate just
  because it arrived inside an app — and are removed provenance-keyed on disable,
  never touching a user's own or another app's skill. `backend.routes[]` names the
  app's agent-callable HTTP surface (`op`, method, path, summary, param/body
  hints); one generic tool provider turns every enabled app's `agentCallable`
  routes into `app_<name>_<op>` tools (risk keyed off the verb) and drives them
  through the existing loopback reverse proxy, and a `call-app-route` action lets
  hooks/crons fire the same routes — both share one resolver so the callable gate
  can't diverge. The routes also render into `GET /api/manifest`'s `app_surfaces[]`
  (a non-callable route documents the surface with `tool: null`), and a declared
  route whose backend answers 404 raises a one-shot drift notification so a
  dead-declared route is caught the moment it's called. First-party Growth (17
  routes) and Minutes (24 routes) ship their route tables.
- **Offline agent reference + `pclaw-api` skill** — an agent driving PersonalClaw
  from outside a running gateway now reads exact tool/route signatures instead of
  guessing them. The distribution ships a generated markdown reference
  (`personalclaw/reference/`: every registered tool with its input schema +
  examples, the agent-callable HTTP routes, and the provider taxonomy) rendered
  from the same source as the live `GET /api/manifest`, plus a bundled `pclaw-api`
  operator skill (the never-guess-copy-it + verify-after-mutate discipline). Locate
  the files from the installed binary with the new `personalclaw doctor --paths`,
  which prints the resolved reference / config / skills / install directories. A
  drift test byte-compares the checked-in reference against a fresh render, so a
  tool or route added without its metadata reddens the build.
- **Render-smoke gate** (`npm run smoke:render`): the built SPA is now loaded
  in headless Chromium — key routes must mount real content with no uncaught
  errors — before any frontend-affecting push (repository-owned pre-push hook,
  `npm run hooks:install`) and on every PR (CI `web` job). Closes the
  verification hole behind the v0.1.0 blank dashboard, where typecheck, unit
  tests, and the production build all passed without ever rendering the
  artifact in a browser.

### Changed

- **The dashboard's system indicators are now a docked bottom rail.** The System
  strip (uptime, version, CPU/memory/network/disk/load, triggers, subagents, and
  the update action) was the last item in the scrolling column; it's now a
  shell-like rail pinned to the bottom edge, so the live indicators stay visible
  while the rest of the dashboard scrolls. The dashboard header's at-a-glance
  pulse strip sheds two now-redundant indicators: the gateway connectivity pill
  ("Live/Offline") and the gateway-version pill. The app shell's top-right corner
  already carries a live connectivity dot on every page, and its expanded system
  card now shows the gateway version (sourced from `/api/system`) — so the header
  strip is just the live count pills. The rail itself is width-responsive (a CSS
  container query, keyed to the content-width preset + sidebar, not the viewport):
  it sheds the decorative CPU sparkline and the metric word-labels — icon + value
  keep carrying the reading, with the full text on hover — to stay on one line as
  the available width tightens, and the "Details →" action stays anchored to the
  right edge.

## [0.1.1] — 2026-07-22

### Fixed

- **Blank dashboard in v0.1.0 (critical).** The released SPA crashed at first
  render with `TypeError: Cannot read properties of null (reading 'useContext')`
  — a dependency-group bump had split the installed tree across React 18 and
  React-DOM 19 (the classic dual-React invalid-hook failure), so every install
  kind (pip/uv, container, git) served an empty page. The web toolchain is
  reverted to its known-good React-18 set, a root npm `overrides` pins
  `@types/react`/`@types/react-dom` so transitive packages cannot drag React-19
  types back in, and the lockfile is regenerated from a clean install so the
  declared and resolved trees agree.
- **`monaco-editor` was never declared as a dependency** — it is a peer of
  `@monaco-editor/react` and imported directly, but resolved only by lockfile
  accident; a clean reinstall broke the build. Now a direct dependency
  (`^0.55.1`, the version v0.1.0 shipped transitively).

## [0.1.0] — 2026-07-19

### Added

- **App-contributed CLI seams** — an app can now hook into `personalclaw setup` and
  `personalclaw doctor` via manifest `cli.setup` / `cli.doctor` (`module:function`),
  and declare its log namespaces via `loggerRoots`. `personalclaw setup --app <name>`
  runs just one app's setup step. Core names no channel vendor in its CLI.
- **CI & release engineering** — GitHub Actions for both repos: `ci.yml`
  (lint/test/web/rails, ≤10-min budget) and `full.yml` (3.12/3.13 × ubuntu/macos
  matrix, audit, coverage) on core; manifest-validate/tests/boundary on the apps repo.
  A tag-triggered `release.yml` builds the wheel (with the prebuilt SPA) + multi-arch
  GHCR images, publishes to PyPI via Trusted Publishing behind an owner-approval gate,
  and attaches an SBOM + build-provenance attestations. `uv.lock` pins the dependency
  graph (CI installs `--locked`); Dependabot watches pip/npm/actions weekly. See the
  [supply-chain posture](README.md#supply-chain).

### Changed

- **Provider-boundary completion (Slack residue retired from core):** the Slack
  channel app now ships its own token/slash-command setup and doctor probe (via the
  new `cli.setup`/`cli.doctor` seams) instead of living hardcoded in core's CLI; app
  logger roots are derived from installed manifests (`constants.APP_LOGGER_ROOTS`
  removed); `slack-sdk` is no longer a core runtime dependency (kept as the `[slack]`
  extra, and the slack-channel app declares it via manifest `pythonDependencies`, which
  the app-install pipeline installs). A residue-sweep test + a machine-checked keeps
  table (`docs/architecture/provider-boundary-keeps.txt`) prevent vendor residue from
  regrowing in core.
- **LLM SDKs demoted out of core dependencies (`openai`, `anthropic`):** a bare
  `pip install personalclaw` no longer pulls the OpenAI or Anthropic SDKs. They now
  ship via (a) the `[openai]` / `[anthropic]` packaging extras for pip/uv users, and
  (b) the branded provider apps' manifest `dependencies.pythonDependencies`, which the
  app-install pipeline installs into the shared venv (plan 32 T2.1). The provider
  adapters import their SDK lazily and now raise a clear `MissingSDKError` naming the
  exact `pip install 'personalclaw[openai]'` remedy (and `personalclaw doctor`) when a
  hosted provider is used without its SDK. This trims the default install; users who
  install a provider app or the matching extra are unaffected (plan 34 T1.4).
- **Self-update is now install-kind aware (git · pip · container · desktop):** the
  in-app updater (Settings → Updates) and the update check no longer assume a git
  checkout. The availability signal is the **latest GitHub release tag** (ETag-cached,
  offline-tolerant) compared against the running version — tags are the release truth
  for every install path. Apply adapts to the install kind: a **git** checkout runs the
  existing pull → reinstall → rebuild → restart pipeline (with a new *Developer update
  mode* toggle, `dashboard.update_dev_mode`, to track every commit instead of only
  tagged releases); a **pip/uv/pipx** install runs `pip install -U personalclaw==<tag>`
  into its own interpreter and gracefully re-execs (no web build — the wheel ships the
  dashboard); a **container** install shows the exact `docker compose … pull && up -d`
  commands (no in-place apply); a **desktop** install delegates to the app shell. The
  Updates panel renders the right affordance per kind, and git installs also surface
  commits-behind as secondary info.

  This is a **clean break** (pre-1.0): the old git-only updater is replaced directly,
  not gated — the migration-backed gate machinery is deferred, so there is no
  `update_kind_aware` gate to flip (owner decision 2026-07-20). Behavior change: a git
  checkout now updates on new *release tags* by default instead of every commit — flip
  *Developer update mode* on to restore per-commit updates. **Run `personalclaw
  snapshot` before updating.** (plan 34 S4.)

### Removed

- **`personalclaw gateway --slack-only`** — the legacy alias for `--headless` is
  removed. Use `--headless`.

### Fixed

- **Release wheel now bundles the SPA when built via `python -m build`.** The release
  pipeline (and `make build`) build the sdist first, then build the wheel from that
  sdist; the built `web/dist` was not included in the sdist, so the wheel-from-sdist
  shipped without the dashboard and failed `scripts/verify_wheel.py`. A new
  `MANIFEST.in` grafts `web/dist` into the sdist, which also makes the sdist itself
  self-contained (a wheel built from the PyPI sdist serves the dashboard too). Guarded
  by `tests/test_sdist_bundles_spa.py`. (plan 34; caught in the release dry-run.)


Initial public release — the first end-to-end PersonalClaw: a self-hosted, local-first,
provider-agnostic personal AI agent behind one gateway and one web dashboard.

### Added

- **Agentic chat** — multi-session chat with tool use and approval controls, session
  forking/undo, answer variants/regenerate, folders/tags/kanban, side conversations,
  per-session model and reasoning-effort overrides, and temporary/incognito memory modes.
- **Goal loops** — give the agent a target; it classifies, plans, and loops autonomously
  under a deterministic supervisor you can pause, nudge, or stop.
- **Memory** — layered semantic/episodic/procedural memory with active recall, after-turn
  learning from corrections, promotion of repeated facts, and an Obsidian-compatible vault.
- **Knowledge base** — document/media/web ingestion, AI enrichment, entity extraction, a
  knowledge graph, and semantic search wired into chat context.
- **Skills** — SKILL.md procedures with a marketplace, supply-chain scanning on install,
  session-scoped ephemeral skills, and an approval inbox for agent-proposed skills.
- **Automation** — cron/interval/webhook triggers, background subagents, a channel-watching
  inbox with drafted replies, and workflow SOPs surfaced on match.
- **App platform** — a permission-gated, scanner-gated Store: model providers, search,
  speech (STT/TTS), local models, channel connectors, agents, and full backend+UI apps,
  each installed through a quarantine → scan → consent lifecycle with subprocess isolation.
- **Agent runtimes** — the built-in native loop plus external CLI agents over ACP
  (Agent Client Protocol) as pluggable runtimes.
- **Model layer** — per-use-case model bindings (chat, background, embedding, ingestion,
  speech) over 16 provider apps; nothing is hardwired to a vendor.
- **Security** — four auth modes (loopback-forced `none`), command screening (denylist +
  suspicious-pattern watchers), an OS child sandbox, one egress chokepoint with host
  policy, untrusted-content fencing, a non-overridable "dangerous" install verdict, an
  HMAC-chained tamper-evident security event log, and credential-excluding exports.
- **Delivery surfaces** — local gateway, Docker Compose, systemd/launchd service install,
  a desktop shell, and portable snapshot/restore.

### Notes

- Single-user, self-hosted, MIT-licensed. **Zero telemetry** — no usage data leaves your
  machine.
- Requires Python 3.12+; a model-provider API key (or a local Ollama) to start chatting.

[Unreleased]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/PersonalClaw/PersonalClaw/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/PersonalClaw/PersonalClaw/releases/tag/v0.1.0
