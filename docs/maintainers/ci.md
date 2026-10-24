# CI

## The ruleset on `main`

`main` in core and in the apps is protected by one repository ruleset, `main-protect`, checked
in as `.github/rulesets/main-protect.json` in each repository. It has **no bypass actor**: it
holds everyone, the maintainer and every App included, and nothing toggles it around a merge.
Its rules:

- `pull_request`: a change reaches `main` only as the merge of a pull request, with 0
  approvals, stale reviews dismissed on a push, and every conversation resolved, as classic
  protection held it. Squash is the only merge method (#3910), in the ruleset and in the
  repository settings, so every commit on `main` is a tree a pull request's checks tested. A
  squash keeps the author and every commit's `Signed-off-by`, which #3908's check reads on the
  push to `main`.
  GitHub's `require_extra_approval_for_unattributed_changes` is off, as nothing in classic
  protection asked for it.
- `required_linear_history`, `non_fast_forward` (no force push) and `deletion`.

#3910 adds the one required check, `merge-verdict`, bound to the merge controller's App, with
branches up to date. Classic branch protection is gone; `tests/rails/test_ci_rulesets_tree.py`
holds the file to these rules and to its empty bypass list.

**Apply or change it** (the maintainer, with an admin login), from the repository's root:

```bash
gh api repos/PersonalClaw/PersonalClaw/rulesets --method POST --input .github/rulesets/main-protect.json
gh api repos/PersonalClaw/PersonalClaw/rulesets/<id> --method PUT --input .github/rulesets/main-protect.json
```

The first creates it; the second, with the id `gh api repos/PersonalClaw/PersonalClaw/rulesets`
lists, replaces it with the file. Use `PersonalClaw/PersonalClawApps` for the apps.

**Read it back** with the same login, which is the one way to see its bypass list, and compare it
with the file, leaving out what GitHub adds (ids, links, times, `source`):

```bash
gh api repos/PersonalClaw/PersonalClaw/rulesets/<id> \
  --jq '{name, target, enforcement, conditions, rules, bypass_actors}' | jq -S . \
  | diff - <(jq -S . .github/rulesets/main-protect.json)
```

The weekly [settings report](#the-settings-report) compares the rules every Monday, and fails on
any bypass used in the week.

**The emergency path** is an audited ruleset change, never a normal merge: edit the ruleset (the
audit log records who and when), do the one thing, put the file's version back, and open an
issue that says why. A bypass actor added for it shows in the next report as bypass use.

### The release tags

Two `v*` tag rulesets sit beside `main-protect` (#4584), apart because a ruleset's bypass list
covers every rule in it:

- `release-tags` refuses moving (`update`, `non_fast_forward`) and deleting a release tag, for
  everyone, with no bypass actor.
- `release-tag-creation` refuses creating one, with one bypass actor: the release controller's
  GitHub App, which creates a tag only from the tagging workflow on `main`, for a commit on
  `main`. So a hand-pushed `v*` tag is refused, the maintainer's included, and every tag's
  `release.yml` is one `main` holds.

Both are applied (2026-10-23): `release-tags` first, then `release-tag-creation`, naming the App
`personalclaw-ci-controller` (id 5444147), once a rehearsal through `release-tag.yml` had shown
the path end to end. `tests/rails/test_ci_rulesets_tree.py` holds both files to these rules, and
the creation ruleset to that one App with its real id. The weekly report lets exactly that App's
bypass through, on `refs/tags/v` and `refs/tags/rehearsal-v` (`allowed bypass` in
`.github/settings-expected.json`); any other bypass fails it.

**The tagging workflow.** `.github/workflows/release-tag.yml` is how a tag is made: the
maintainer dispatches it on `main` with the tag and the commit
(`gh workflow run release-tag.yml --ref main -f tag=vX.Y.Z -f commit=<sha>`).
`python -m tools.ci release-tag` (`tools/ci/release_tag.py`), from `main`'s own checkout, refuses
unless the tag is a release tag by `scripts/release_tags.py`, the commit is on `main`
(`trust.require_on_main`), its workflows, actions, `tools/ci` and the entry points they run are
`main`'s tip's (`trust.compare`), no tag of that name exists here or on the remote, and the
commit's own push run on `main` is green (#3911's gate, `release_tag.main_push_green`). That run
is read in each workflow a push to `main` runs in, the newer shape first (`workflows.MAIN_PUSH`:
`merge.yml` once #3915's flip lands, `ci.yml` until then), and the first with a run for the
commit decides, so a red there is never passed over for the other's green. Its newest run must
have completed with success: one still going, failed, cancelled, or whose gate read a shard as
superseded or unproven is refused, naming the run. No `release.yml` run may be unfinished
either (`release_tag.no_release_running`: queued, in progress, waiting or any other state before
it completes), named when one is: every release shares one concurrency group, where GitHub keeps
one waiting run and a newer one replaces it, so a tag made behind a running release could be left
with no release. The job reads both with its own token, given `actions: read` for that alone. Only then is the App's token made, asked for `contents: write`
alone and handed only to the step that creates the annotated tag and its ref. The job runs only
on `main`, in the `ci-controller` environment, which deploys only from `main` and holds the
App's key. The trusted `merge-verdict` (#3910) joins the gate once `main-protect` requires it.
`-f rehearsal=true` creates `rehearsal-<tag>-<sha7>` instead: the same checks but breadth's and
the same App, under a name no `v*` rule and no release trigger sees, which is how the path is
proved without a release (on a scratch copy of the creation ruleset over
`refs/tags/rehearsal-v*`). The short commit lets a later commit rehearse the same tag, since tags
are never moved or deleted; the gate matches the full commit, never the name.

**Breadth in the gate.** The rehearsal tag's push starts `breadth.yml` on the commit
(`push: tags: [rehearsal-v*]`), off `main`, where nothing is reused, so nothing dispatches it and
no workflow holds `actions: write` for it. A release tag's gate (`release_tag.breadth_green`)
then reads breadth's runs of the commit off `main`. The newest must be a rehearsal tag's push,
finished, every `breadth-evidence-<sha256>` record of it downloaded and verified under its
digest, naming that run and the commit's tree. Together the records must hold every attempt and,
in the newest, every shard, none carried. Every gating leg (each OS, Python and architecture,
random-order, security, the installer smoke, the audit and the union checks) must be green on
every attempt. The advisory legs (`release_tag.ADVISORY_LEGS`: the next-Ubuntu canary and the two
legs that read its suite, and coverage) measure rather than gate: each must have run, and each
red one must be owned by an open issue titled as breadth's report titles it (`breadth: <leg> is
red`), filed by that report or a maintainer, never by anyone else, since on a public repository
anyone may open an issue. The gate names each advisory red with its issue, so they stay visible
and owned without holding a release on a measurement. A red leg's job fails its run, so a run
whose only reds are advisory concludes `failure`: the records, not the conclusion, say which legs
were red, and a run that was cancelled, timed out or never started is refused. The run's commit is the tagged one, whose definitions the gate already holds
to `main`'s, so the `breadth.yml` that ran is `main`'s, and a rehearsal tag someone else pushed
cannot stand in for another tree. An earlier red run of the commit refuses unless the owner's
record, `.github/release-breadth-exceptions.json` on `main`, names that run with that commit and
why (#4712). Only the owner writes it, by hand, and that is a rule, not a check: agents act on
GitHub as the owner's account. Once the record names a red run, the owner may measure the commit
again by dispatching breadth on its rehearsal tag (`gh workflow run breadth.yml --ref
rehearsal-<tag>-<sha7>`), and that dispatch may then be the newest run; with no named red before
it, a dispatch decides nothing. The job reads the issues with `issues: read`. The gate never
waits: until the run exists and has finished, it
refuses, naming the run or saying none exists yet. The record's zip is read through the API's
redirect, and the token is never sent to the storage address it names.
`tests/rails/test_ci_release_tag_tree.py` holds the workflow's shape, both steps passing the
rehearsal on alike. A hand-pushed `v*` tag is refused, the maintainer's included (the
[release runbook](release-runbook.md#tagging) says how to tag).

The `release`, `release-client` and `website-follow` environments deploy only from `v*` tags,
so no branch's copy of `release.yml`, dispatched or pushed, can reach the PyPI publishers or the
website token. A dispatch on a tag still can: it runs the tag's own copy.

## Trust boundary

A candidate (a pull request, or a push to a branch other than `main`) must not be able to change
the code that decides whether that same candidate may merge or publish. The line between what
decides and what is decided is drawn here once, and the workflows, `tools/ci/` and the CI
contract table follow it.

### Trusted

- **The workflows GitHub runs from the default branch:** `pull_request_target`, `workflow_run`,
  `schedule`, and pushes or dispatches on `main`. GitHub takes these from `main`, whatever the
  candidate's copy says.
- **The default branch's copy of `tools/ci/`,** checked out by one of those workflows.
- **The push of a `v*` tag,** once only the release controller can create one, on a verified
  `main` commit. Until the tag rules are in place, a tag push is trusted only as far as the
  person who pushed it.

### Untrusted

Everything a candidate brings:

- its workflows, its `.github/actions/`, and its catalogue, planner and verdict code;
- every artifact and every job output of its run.

Candidate code means any job of a `pull_request` workflow, of a workflow a push to a branch
other than `main` runs, or of a workflow either of those calls, and any step that checks out a
pull request's ref. GitHub runs a fork's pull request with a read-only token and no secrets; a
same-repository branch gets whatever its own workflow asks for, which is why the rows below hold
every candidate job to a read-only token whoever opened it.

### How a trusted job reads a candidate

A trusted job reads candidate output only as data:

- It takes the pull request, the head commit and the tree from the event or the API, never from
  an artifact. `pr-feedback.yml` finds the one open pull request whose head is the run's head
  commit; the artifact's `pr.txt` is only cross-checked against it, and a mismatch posts
  nothing.
- It never checks out or runs candidate code. It checks out the default branch, and puts any
  artifact it downloads outside that checkout.
- It shows candidate text as text. `scripts/ci_pr_feedback.mjs` fences the lint output itself,
  with a fence longer than any backtick run inside it, so text that closes a block or adds a
  link stays inside the fence.
- It reads an artifact's files only so far: the relay reads at most 256 KiB of `violations.md`
  and 64 bytes of `pr.txt`, and shows at most 60000 characters, so an artifact past those caps
  is cut, never read whole.

`pr-feedback.yml` follows CI and PR (`pr.yml`). CI's `lint` job writes `lint-feedback` itself.
In PR, the catalogue's one `lint_feedback` job, `static`, has `run-job` keep each command step's
output, its first 60000 bytes (`run.OUTPUT_LIMIT`). When a step exits non-zero on a pull
request's run, `run-job` leaves the step's command and output as `violations.md`, with the pull
request's number from the run's own event as `pr.txt`, and the stage job uploads them. A
dispatched run has no pull request, so it leaves none. `run-job` stops a job at its first failed
step, so a PR run's comment shows the first failing tool's output alone (`black`'s before
`flake8`'s), where CI's `lint` job runs every tool and shows each one's. The flip deletes
`ci.yml` and takes CI off the list.

### Definitions: which files judge

The definitions are the files that decide which jobs run, what each one runs and how the
verdict is computed: `.github/workflows/**`, `.github/actions/**`, `tools/ci/**` (the catalogue
included), the CI contract table, and every file a command names as its entry point, such as
the `Makefile` for a `make` target or a script under `scripts/`. Until the workflows run the
catalogue's commands, a workflow step is a command. Files an entry point loads are listed by
hand in `REVIEWED_HELPERS` in `tools/ci/trust.py`; add a helper there when an entry point starts
loading one.

`tools/ci/trust.py` compares the full set of those paths, each with its git mode and object id,
at the candidate's head with the default branch's. So an added, deleted or renamed file counts
as a change, and so does one whose content is unchanged but whose mode is: an executable bit, or
a file that becomes a symlink or a gitlink.

- **Equal:** the candidate's run used the trusted definitions, and its per-job results count.
- **Different:** the candidate's run is advisory. The verdict comes from a run of the default
  branch's definitions on the candidate's tree, which holds a read-only token, references no
  secret and saves no cache that `main` or a release restores. A changed controller judges only
  after it merges.

### Receipts

A GitHub receipt (the merge verdict's output, the push's reuse receipt and the release
manifest) names its judge beside what it judged: `controller_revision` (the commit of the
`tools/ci` copy the trusted job ran), `head_sha`, `tree_oid`, `external_revisions`, `toolchain`
and `evidence_digests`. `trust.check_receipt` refuses a receipt that lacks one, whose controller
revision is not a commit on `main`, or whose tree is not its head's tree. A local receipt names
no controller, because a local result never stands in for GitHub's. `trust.receipt_from` is the
format half of that check, with no git: the merge verdict's verifier uses it, and checks the
head's tree from the API.

### The merge verdict's record

The merge controller posts its verdict as a `merge-verdict` check run on the pull request's head,
with its GitHub App's token, and that check run is the record (#3910). Its output has a title and
a summary for people, and the receipt as canonical JSON in one fenced block
(`tools/ci/merge_verdict.py`). Only the App that posted a check run can change it, and it stays
with the commit. Neither an artifact (it expires, and any job can upload one under the same name)
nor a commit status (no room for the receipt) holds it. The main commit a squash lands is not in
it: the push job will record that in its own record, beside this one, when #3908 checks rule 4.

`python -m tools.ci verify-check-run <sha> --repo OWNER/NAME [--external NAME=SHA ...]` says
whether the verdict on a head counts: exit 0 accepted (it prints the receipt), 1 refused, 2 an
answer it could not read. `--help` exits 0 and a usage error 2, as argparse's do, so acceptance
is exit 0 with the `accepted:` line. Every rule fails closed:

- **Which App is believed:** the one `main`'s required-status-check rule names for
  `merge-verdict`. No such rule, a rule naming no App, two Apps, or GitHub Actions' own App
  refuses every verdict, so a candidate's own job named `merge-verdict` satisfies nothing. Until
  that ruleset exists, every verdict is refused.
- **Newest wins:** every `merge-verdict` check run on the head is read, page by page against the
  API's count, which must hold from page to page. The newest of that App's runs (the highest id,
  whatever order the API lists them in) decides, so a later pending or red run takes back an
  older green. Only a `completed` run concluded `success` is accepted; another App's runs
  neither count nor hide the controller's.
- **The identity:** exactly one receipt, with no key given twice, naming this head; the head's
  tree as the API reads it (a squash-merged head is not on `main`, so local git cannot); a
  controller revision on `main`; and every external revision the caller expects.

`.github/workflows/merge-verdict.yml` posts the record (the next section).
`tests/test_ci_merge_verdict.py` holds each rule on planted check runs.

### The merge controller's decision

`tools/ci/merge_decision.py` decides what the controller will post: green, red or pending, with
a reason, and for a green the receipt, written in the record's one format. It is a library only;
no workflow calls it yet.

- **Which run counts:** `trust.compare` of the default branch's commit, `main`'s tip, and the
  head. A comparison whose base is not `main`'s tip is red: a base off `main` never picks which
  run counts, and nor does an older commit on `main`, against which a candidate that puts back a
  definition `main` has changed since shows no change. When no definition changed, the
  candidate's own run counts. When any did, that run is advisory and only a run of the default
  branch's definitions on the candidate's tree counts. So a job kept by name but changed to always
  pass, an edited `tools/ci/verdict.py` or an emptied `Makefile` target is judged by the default
  branch's copy.
- **The planned set** is an input. Until the planner lands, the caller reads it with
  `planned_jobs`: the merge tier of `tools/ci/catalogue.toml` at `main`'s tip, read from git,
  never from an older commit on `main`, the candidate's files or a worktree.
- **Red:** a planned job that is missing, cancelled, skipped or red, even when the candidate's own
  workflow dropped it (results are read with `verdict.from_needs` and judged with
  `verdict.decide`); the same of the `changes` job (`merge_decision.CATALOGUE_CHECK`), whose
  `catalogue-check` step runs main's `tools/ci` over the candidate's catalogue: no plan names
  it, so it is required beside every non-empty plan (an empty plan stays red as empty), and a
  check that was refused, skipped or never ran is red; results or a plan that cannot be read; a
  run naming the head with another tree, whichever run counted; a green whose receipt would lack
  its toolchain or evidence.
- **Pending:** no counted run yet, or one of an older head, of other definitions, or against
  other partner heads than the current ones, so a partner head that moves takes a green back.
  A red or pending output carries no receipt, so `verify-check-run` never accepts it.
- **A pull request's reading:** it judges with `verdict.decide`, never `decide_main_push`, so a
  cancelled planned job stays red as cancelled and is never read as superseded. A red on an
  earlier attempt is not in `needs`; reading it from the API is the controller workflow's job.

### The merge controller's poster

`.github/workflows/merge-verdict.yml` posts `merge-verdict` on a head each time a Merge run
(`merge.yml`) or a Dependency review run completes, or code scanning's CodeQL check does. It is a `workflow_run` listener, so GitHub
runs main's copy of it and of
the `tools/ci` it runs, whatever the candidate brings. `python -m tools.ci merge-controller
decide` (`tools/ci/merge_controller.py`) reads everything through the API by the run's id:
the run, every attempt's jobs, both trees (`trust.compare_trees` over blobs the API returns, so
nothing of the candidate is fetched or checked out), the comparison with `main` and the run's
artifact digests. The rules, in order:

- **Which runs post:** only this repository's `merge.yml`, completed, on a `pull_request` or a
  dispatch. Another workflow named Merge, which a candidate can add, a push and a schedule post
  nothing.
- **A head that changes a CI definition,** against `main`'s tip, is judged by a run of `main`'s
  definitions on its tree, never by its own run, which posts nothing (#4584 step 4). The
  `trusted` job builds one commit through the API with the controller App's token: `main`'s tip
  as its parent, `main`'s definitions, and every other path as the head has it. It points
  `trusted-run/<head>` at it and runs `main`'s merge.yml there with its own token, which holds
  `actions: write` alone. The commit changes no workflow file against its parent, and every
  push trigger here is `main`'s alone, so the push starts nothing; its caches belong to that
  branch, never to `main`. When that run completes, its verdict is posted on the head, only if
  its commit still has a parent on `main`, `main`'s tip's definitions and the head's every other
  path. One whose definitions `main` has moved past is made again; any other is red. Then the
  branch is deleted. Until that run reports, the head has no verdict, which a required check
  reads as not passed.
- **Up to date:** a head behind `main` is red, so the squash lands the tree the run tested.
- **The run's verdict:** merge.yml's `results` job, which judges the run with main's
  `verdict.py`, concluded `success` on every attempt. Missing, cancelled, skipped or red on any
  attempt is red, and a rerun never turns a red into a green.
- **The dependency review (#4585 step 6):** the newest `pull_request_target` run of
  `dependency-review.yml` on the head, `main`'s copy, concluded `success`. One that has not run,
  is still running or did not pass is red. The poster also follows Dependency review: when one
  completes, it judges that head again from the head's own completed Merge run, or from its
  trusted run for a head that changes a definition, and starts nothing. So the two finish in
  either order.
- **CodeQL, read from what it ran:** the newest run of GitHub's dynamic CodeQL workflow
  (`dynamic/github-code-scanning/codeql`) on the head finished, with an `Analyze (<language>)`
  job concluded `success` for each language `main`'s default-setup analyses name; each of those
  languages' analyses of the head processed on the pull request's ref (`refs/pull/<n>/head`, the
  head's commit), since a job's success means its results were uploaded, not yet read; and no
  alert open on that ref that is not open on `main`. A scratch pull
  request that planted one `js/code-injection` showed exactly that alert there and not on
  `main`, with all three analyses green. The `CodeQL` summary check is never read for its
  conclusion: GitHub computes it when the first language's analysis uploads, so it reads
  `neutral` on most pull requests. A red whose only reasons wait on CodeQL (not run, still
  running, still processing) is marked waiting in its output, and such heads are judged again:
  when GitHub's CodeQL workflow completes (followed by name, matched by its run's path), when the
  summary check completes (`check_run`, which starts a runner only for code scanning's CodeQL
  check), and every ten minutes, for open pull requests whose newest verdict is a waiting red,
  posting only a verdict that changed. The CodeQL workflow's wake is proved: the completion of
  main's CodeQL run 40071173659 started poster run 40072081053. The schedule stays all the same,
  for one gap: that wake fires when the run completes, right after its last language uploads,
  and a head judged before that upload is processed waits with nothing left to wake it, since
  the summary check fired at the first upload. The schedule closes that gap and nothing else.
  Its first read is one GraphQL query of every open pull request's head and the App's verdicts
  on it: when none waits, the sweep ends there; otherwise only the heads that wait are read
  again, through the REST reads that stay the gate, and a query that gives no answer leaves the
  sweep reading every head.
- **What takes a runner:** both jobs' one condition starts one only for what can judge a pull
  request's head. The schedule and a completed dispatch off `main` (a trusted run, a dispatch
  proof) always do. A pull request's wakes, code scanning's CodeQL check and a completed run of
  a pull request (its own Merge run, its dependency review, CodeQL's on `refs/pull/*`), do only
  while the repository variable `MERGE_VERDICT_LIVE` is `true`. It is `false` until #3915's flip
  runs merge.yml on pull requests, because until then a head has no Merge run to judge, and the
  flip sets it: forgotten, nothing posts on a pull request and `merge-verdict` cannot be
  required, so it fails closed. A push run, a dispatch on `main`, CodeQL on `main` and any other
  check run never take one. decide checks each again through the API, which is the gate: the
  condition only spares the queue. Measured on 2026-10-24, 08:34 to 14:34Z: 144 poster runs, of
  which 2 posted, whose runners waited a median 686 to 993 s.
- **The schedule lags under load.** GitHub drops or delays a cron when its runners are busy: the
  same six hours started the sweep once, not 36 times. It is the backstop for the one gap above,
  so a late sweep only makes a waiting red wait longer; it never posts a wrong verdict.
- **The receipt:** a green names the controller's commit on `main`, the head and its tree, the
  runner and the definitions commit, and the digests of the run's `ci-record-*` artifacts as the
  API reports them. With no record artifact there is no evidence, so no green. The partner head
  is not recorded yet; the partner rule comes with the apps half of #3910.

The verdict is `success` or `failure`, never neutral; an answer the API would not give is
`failure`, named as unread. The `post` job reads with its own token (`contents: read`,
`actions: read`, `security-events: read`), in the `ci-controller` environment, which only `main` may deploy to. Only when
there is a verdict to post does it mint the controller App's token, with `checks: write` alone,
and only the step that posts holds it. The `trusted` job, in the same environment, mints it with
`contents: write` alone, for the step that makes the trusted run's commit. `post` refuses a body that is not one completed `merge-verdict`
check run, or a green without its receipt.

It is advisory: `main-protect` does not require `merge-verdict` yet. It will once #3915's flip is
live and three pull requests' posted verdicts have matched their real results (#3910's plan,
piece 5). `tests/test_ci_merge_controller.py` drives the rules against a fake API, and
`tests/rails/test_ci_merge_verdict_poster_tree.py` holds the workflow's shape.

### Reachability

`trust.require_on_main` refuses a commit that is not an ancestor of `main`. Only a full commit
id is accepted, and a shallow clone refuses, because it cannot show ancestry.

### The rows that hold it

`tests/rails/test_ci_contract.py` holds five workflow rules, each with a planted violation per form:

- No `pull_request_target` or `workflow_run` workflow checks out candidate code: a checkout of
  the pull request's or the run's head, a `refs/pull/` ref, `github.head_ref`, the pull
  request's test-merge commit (`merge_commit_sha`), the head repository, `gh pr checkout`, or a
  `git fetch` of a pull request's ref.
- No job that runs candidate code holds a write permission or a secret: a write grant,
  `write-all`, no `permissions` at all (the repository default is unknown to the file), a
  `secrets.` reference anywhere in the job, or `secrets: inherit`.
- Only `release.yml` holds a publishing permission (`contents`, `packages`, `id-token`,
  `attestations` or `deployments` write, or `write-all`) or any deployment environment. A new
  environment has to argue its way into `release.yml`. The one other is `settings-report`, on
  `settings-report.yml`'s `report` job alone, in a workflow that runs only on its schedule or a
  dispatch: it holds the settings report's read-only credential and deploys only from `main`
  (see [The settings report](#the-settings-report)).
- No workflow that runs candidate code posts `merge-verdict`, the one check the default branch's
  controller posts: no job whose id or name is `merge-verdict` (in any case, a matrix-built name
  included), and no step, action input, key or workflow-level value that names it. A job name the
  row cannot read (a matrix that is an expression, a matrix value that is one, any other
  expression) is refused, since it could be `merge-verdict`. A tier workflow's stage job is the
  exception: its matrix is its tier's plan (see "The tier workflows"), so the row reads it as
  every entry the tree's catalogue can give that tier, and each entry's key is a leg. A trusted
  workflow, or a push to `main`, may post it; that only the controller does is #3910's to hold
  once its workflow exists.
- Every concurrency group a candidate's run joins, the workflow's and each job's (once per literal
  matrix leg, a tier workflow's planned matrix read as its tier's entries), keys on its pull
  request or its head. The row evaluates each group against the runs of several unrelated
  candidates, each event as GitHub fills it in: two pull requests, two
  forks with the same branch name, pushes to two feature branches, and the same pull requests
  under `pull_request_target`, whose `github.ref` and `github.sha` are main's for every pull
  request. It refuses a group two candidates share, in one workflow or across two, and one that
  two runs for one head do not share: a second run, or a re-run (the same run id and commit, a
  later `run_attempt`). A called workflow is held for its callers, and a `workflow_run` listener
  of a candidate workflow is held to the first rule against each candidate's run. A context the
  row does not model (`vars`, `inputs`, `needs`, an unreadable matrix) is refused, never read as
  empty. A `github` property the row leaves out (`github.workflow`, `github.actor`, ...) is the
  exception: it reads as empty, as GitHub reads a missing property, so the row does not hold a
  group keyed on one. A `workflows:` entry of a listener is read as a glob, and a listener of a
  `pull_request_target` workflow is held like one of a `pull_request` workflow. A push to a feature branch counts as the pull request whose head it is. A push to `main`
  and a scheduled run are not a candidate's, so `main`'s own grouping is not this row's.

Main's `tools/ci` now reads a pull request's catalogue (see "Main's copy reads a pull request's
catalogue" under "The catalogue"), but these rows still run in the candidate's own suite, so a
candidate could delete one. Running them from main's copy is #3915's.

## The catalogue

`tools/ci/catalogue.toml` lists CI's validation jobs, one `[[job]]` entry each, and
`tools/ci/catalogue.py` reads it into typed jobs. `python -m tools.ci run-job` and `run` run its
jobs on a maintainer's machine (see "Local runs"), and `pr.yml` and `merge.yml` run each planned
job through `run-job` when they are dispatched (see "The tier workflows"); `ci.yml`,
`breadth.yml` and `docker-single-container.yml` still run their own `run:` lines. The contract
table's row "every catalogue command is the workflow's" holds each entry to the jobs of those
workflows it maps to (`image`'s is the single-container workflow's), in
both directions:

- Each step is one of those jobs' `run:` lines, token for token, from the same working directory
  and with the same step `env`. An entry's steps come in `ci.yml`'s order, and none comes twice.
- Every `run:` line of a mapped job is one entry's step. The only lines left out are a closed
  list in the row, each named for what it does: the runner's identity, the installs the
  toolchain names, the shell around a step (its options, and the branch on which base there
  is), the fetch of that base, a value set through `GITHUB_ENV`, the lint job's wrapper and
  feedback relay, and breadth's evidence lines (a leg's inputs, the one leg's decision to
  carry, what it ran, its result read back) with the scratch interpreter they run on and the
  coverage shards' gate. A listed line that no mapped job runs any more fails the row too, so
  the list holds only lines that exist. No line is a step of two entries. An entry mapped to a
  job in each workflow (`security`) holds each workflow's lines on its own.
- An install is toolchain, never a step: `uv sync`, `npm ci`, the Playwright browser install and
  Electron's binary (`sh tools/ci/install_electron.sh`, which `npm ci` does not run) are
  refused as steps, and each install an entry's toolchain names (`uv-sync`, `npm-ci`, `chromium`,
  `electron`) is one its `ci.yml` jobs run. Each entry names every install it needs, so no entry
  leans on another job's installs.
- The cap, the runner, the Python (the one `setup-uv` sets before the entry's steps, inside the
  setup action when the job calls it there), the environment, the JUnit reports and the shard
  count are the jobs'.
- An entry whose pytest steps name a security-marked module, by its file or by a folder that
  holds one, is `security` posture. The census in `tests/security_census.py` says which modules
  are marked.
- Each entry's tiers, `security`, `live` and `reuse` are the ones the row's table gives it, so
  changing one takes an edit in both places.
- Every `ci.yml` and `breadth.yml` job is an entry's or orchestration (`tree-check`, `changes`,
  `shard-plan`, `test`, `test-macos-gate`; breadth's `freshness` and `report`). An entry with a
  `script` list (`install-smoke`) holds its job's `sh <script>` steps, one per script, in order:
  `sh <path>` is the one way the supply-chain check reads a script a step runs. Each of those
  scripts is the tree's and opens with `set -eu`, so a command that fails fails the job.
- No job outside the catalogue runs a test, in any workflow file. The row
  "no-workflow-job-outside-the-catalogue-runs-a-test" reads every job no entry is mapped to,
  with the local actions it calls, and refuses a command whose word is a test runner:
  `pytest`, `python -m pytest` (or `unittest`, `tox`, `nox`), `vitest`, `playwright`, `jest`,
  `mocha`, `tox`, `nox`, `npm test`, `npm run test*`, and `make test`, `check`, `gate`,
  `e2e*` or `test-*`. It reads the command behind `NAME=value`, `env`, `cd ... &&`, `uv run`,
  `npx` and `npm exec`. A word that only names a runner (`echo pytest`, `pip install
  pytest`) runs none. A new test goes in an entry, whose command the row above holds to its
  job.
- The harness's `replay` and `exemplars` profiles are local shortcuts, not catalogue
  entries: the merge tier already runs what they gate. `python-suite` collects
  `tests/test_harness_replay.py` (every replay scenario against its baseline),
  `tests/test_harness_replay_scenarios.py` (the two required scenarios and their fold) and
  `tests/test_harness_exemplars.py` (each exemplar's smoke script, over a pinned set of
  slices), and `web-unit`'s `npm run test:web` includes `web/src/harness/replayFold.test.ts`.
  The row "the-harness-replay-and-exemplar-gates-run-in-the-merge-tier" holds each link: a
  gate file out of the suite's universe, a gate test renamed, a slice the pinned set does not
  name, or a change to `web-unit`'s command, the two test scripts or the vitest file set
  fails it.
- `breadth.yml` names each suite leg and its shard count as literals (`--leg ubuntu --shard
  ${{ matrix.shard }}/4`), as `ci.yml` does. Its steps that must not install the project first
  run the uv-managed interpreter of the job's Python, `"$(uv python find "$PY")"`, which the
  catalogue writes `{python}`; `run-job` fills it from `uv python find <python>`. The
  randomized-order leg's seeds are spelled `${{ github.run_number }}1` and `...2`, which the
  catalogue writes `{run_number}1` and `{run_number}2`.

The map from entry to job lives in that row.

### The setup action

`ci.yml`'s jobs, and the stage jobs of `pr.yml` and `merge.yml`, set up their toolchain with one
composite action, `.github/actions/setup`. It runs setup-uv with the one uv release and the exact
Python the job names (`python-version`), and setup-node from `.nvmrc` when the job asks for it
(`node: "true"`, with `node-cache`, `npm` by default). `programs` names the programs of a
catalogue job's toolchain that `run-job` finds on PATH and does not install
(`tools/ci/plan.py`'s `SETUP_PROGRAMS`: `tmux`, `shellcheck`, `dash`), space-separated, as the
plan's matrix gives them: every name is checked before anything installs, and any other name
fails the step with nothing installed (none is read as a pattern); each one PATH lacks is then
installed from the runner's package manager (`apt-get` on Linux, `brew` on macOS, nothing
elsewhere); and empty, the default, installs none, so no `ci.yml` call changes. Each job keeps
the rest itself:

- the checkout, before the action, because a local action loads only from a checkout;
- the Runner identity step, first, so a failed checkout still logs the image. The runner
  identity row reads it from the job's own steps and fails one inside a local action;
- its installs (`uv sync`, `npm ci`), which the catalogue row holds to the toolchain each entry
  names;
- its JUnit upload, after its tests: a composite action has no post step.

The toolchain rows (Python, uv, the uv Python preference, Node, npm, the runner identity), the
catalogue row and the uv-cache rail read a job's steps with each local action it calls read in
place (`tests/rails/local_actions.py`), as the runner reads them for that call: the call's
inputs fill the action's `${{ inputs.* }}`, its `env:` sits beneath each step's own, and a step
whose `if:` compares an input to a literal, bare or as the whole of one `${{ }}`, is read only
when that call runs it, the comparison ignoring case as GitHub's does. Text outside the
`${{ }}`, such as a block scalar's newline, makes the condition a string GitHub fills in, which
is always true, so that step is held. So is a step behind any other condition the reader cannot
evaluate, and one whose input is itself an expression or a YAML boolean (the rails parse YAML
1.1, which reads `yes` and `on` as `true`, where GitHub hands on the word): a step it cannot
rule out is still held. A local action read from `action.yaml`, or called from inside another,
is read the same way, with the outer call's inputs and `env:`, and a call of one the reader
cannot read fails the row. A setup step inside the action counts at the index of the call. Each
of those rows has a plant inside the action.

A change under `.github/actions/` runs the planned jobs, the browser run's (`e2e-shard`,
`e2e-stateful`, their merge `e2e` and the UI quality check `ui-quality`), `desktop-smoke` and
`apps-contract`, as a change to `ci.yml` does: each sets up its toolchain through the action, so it
defines them too.
The planner plans the whole tier for it (`tools/ci/areas.toml`). The rails check that every
local action `apps-contract` calls, and each one that action calls, plans that job, and that
every one a `ci.yml` job calls plans the whole tier.

`python -m tools.ci list` prints every job. `python -m tools.ci explain <tier>` says what a tier
proves and what it does not, and lists each of its jobs' commands. Both read only the catalogue
and import only the standard library, as everything `python -m tools.ci` imports must.

### Fields

Every entry declares:

- `id`;
- `tiers`, from `change`, `merge`, `breadth`, `weekly` and `release`;
- `inputs`, as git pathspecs;
- `external_inputs`: what the job reads that the tree does not pin, such as the apps checkout,
  the advisory feed, the package index or the shard history;
- `toolchain` and `cache`;
- `security`: `security` when its result is security evidence, else `ordinary`. A `security`
  job is never selected out; the job's result is never rerun or reused, and a security-marked
  test is never rerun; an ordinary failed test inside it gets one diagnostic rerun, recorded
  only (see "The diagnostic rerun" under "The quarantine");
- `timeout_min`, 2 or more, since a local run stops a job a minute before its cap.

An entry may also set `platform`, `python`, `live`, `reuse`, `env`, `workers`, `workers_env`,
`mem_gb`, `junit`, `shards`, `needs_node`, `needs_browser`, `after` (the jobs whose output it
uses), `install_exemptions`, each exemption with its reason, `retiring`, the issue that
retires the job, as `"#<number>"` (see "Main's copy reads a pull request's catalogue"),
`nightly`, the breadth job that runs a live job's steps every night (see "Planning a run"), and
`lint_feedback`: a failed step's output is the pull request's lint remediation, which
`pr-feedback.yml` posts. One job sets it, of the change tier. `prepush = false` leaves a
change-tier job out of `run change --pre-push`, the pre-push hook's run, for a test suite CI
gates; every other change-tier job runs in it. A key the reader does not know is refused, so a
misspelt field fails rather than being ignored.

Every pytest step writes a JUnit report (`--junitxml`) unless it only collects (`--collect-only`),
and `junit` lists what the steps write. Every step that installs or fetches packages the lock does
not hold is named in `install_exemptions`, word for word, with the reason it may: `uv pip install`,
`uv tool install`, `uv add`, `pip install` however its interpreter is spelled (also behind
`uv run` and its options, read with their values), `uv run --with`, `--with-editable` and
`--with-requirements`, `uvx` and `uv tool run`, and `npm install`. An `npx` step runs a bin that
the nearest `package-lock.json` above its workdir installs, so it fetches nothing; `npx
--package` is refused. An exemption that names no install of the entry's steps fails too, so the
list cannot outlive what it excused. The locked installs are the toolchain's (`uv-sync`,
`npm-ci`), which the reader refuses as steps. The
contract table's row "every-pytest-step-writes-junit-and-every-install-off-the-lock-is-named"
holds both, with a plant for each install form.

`workers` is the job's worker count, a whole number or `auto`. Every pytest step's `-n` must equal
it, and a step that passes `-n` needs it. `workers_env` names the variable through which another
tool reads it: `web-unit`'s `VITEST_MAX_WORKERS`, which `ci.yml` sets to the runner's core count.
A job that sets `workers` passes it through a pytest `-n` or a `workers_env`. `workers_env` may
not name a variable the runner owns.

A job has either a `command` or a `script` with its `container`, never both. A `script` is one
repository path or a list of them, each run in turn in the container as `sh <path>`; a path is
inside the repository, in its one normal form (no `./`, no empty or `.` part) and without
whitespace, and named once. A command is a list of steps. Each step is an argv run from the
repository root, or from `workdir` under it, with its own `env` set on top of the job's: the
workflow step's own `env:`, such as a flag that turns a skip into a failure. A value only the runner
knows is written as a placeholder: `{runner_temp}`, `{shard}`, `{base}` (the commit a diff is taken
against), `{python}` (the uv-managed interpreter of the entry's `python`) or `{run_number}` (the
run's number, which every attempt of the run keeps; another value every run, so a job whose steps
name it is never reused, by TTL or by tree). An argument or an `env`
value containing `$`, or a placeholder the catalogue does not have, is refused, so no step reads the
environment around the catalogue. An `env` name is a variable name (`[A-Z_][A-Z0-9_]*`), and not one
the runner owns (`tools.ci.run.runner_owns`: `PATH`, `HOME`, `TMPDIR`, the locale, git's variables
and `PERSONALCLAW_HOME`), so no entry the loader reads is refused when it runs. A `workdir` holds no
`$` and no placeholder. A sharded job's steps name `{shard}`; an unsharded job's do not.

`needs_node` and `needs_browser` agree with the toolchain: `needs_node` exactly when it installs
`node`, `needs_browser` exactly when it installs `chromium`. A pathspec's magic is read as git
reads it, in the long form (`:(exclude)`) or the short one (`:!`, `:^`, `:/`), and a pathspec
that leaves the repository is refused in either form.

### Reuse

`reuse` says when a job's result may stand for a later run with the same inputs:

- `never`, the default when `reuse` is absent;
- `tree`: any run of the same tree and external inputs;
- `ttl:<hours>`: within that many hours, the hours a positive whole number.

A `security` job or a `live` job is never reused, by TTL or by tree: its evidence is about the
run, not the tree. Each external input has a kind (`catalogue.EXTERNAL_INPUT_KINDS`), and the
reader holds a job's reuse to it:

- `revision` (`apps-partner`): its commit is part of the result's identity, so tree reuse is
  allowed.
- `plan-only` (`shard-history`): it decides which shard runs a file, never what is tested, so
  tree reuse is allowed.
- `unpinned` (`package-index`, `browser-download`): what it fetches can change while the tree
  does not, so a job that reads one is never reused by tree. A TTL is allowed.
- `live` (`advisory-feed`): it changes with the time of the run and not with the tree, so the
  reader refuses a job that reads it without `live = true`. `catalogue.parse_reuse` is the one parser of these values; any other reader
imports it.

A local run's reuse of a recorded pass is under [Verdict reuse](#verdict-reuse).

### The tiers

- **merge:** what a pull request into `main` must pass. Its security evidence is the suite's own
  run of the security-marked modules, `python-suite` and `python-suite-macos`, and `security`,
  the one run of the security corpus (`tests/security` and the supply-chain scanner's tests),
  which no suite shard collects. The catalogue is refused if this tier lacks the `rails` job,
  lacks any of the three, holds one as anything but `security`, or runs one off its own
  platform: `python-suite` and `security` run on an `ubuntu-` runner and `python-suite-macos` on
  a `macos-` one. So no other security job stands in for one of them, and no platform's run is
  lost. The merge verdict reads a planned job that is missing, skipped or cancelled as red, so
  a merge verdict on a run without `security` is red. `[tier.merge] jobs` lists the tier's
  jobs, checked both ways: every job whose tiers claim `merge` is in the list, and the list
  names only such jobs, so dropping a job from the merge tier takes two edits. A catalogue
  without the list is refused. `[tier.merge] every_push` lists the merge-tier jobs a push to
  `main` runs whatever its tree check answers; when given, it names only merge-tier jobs, none
  twice, and every one of `catalogue.EVERY_PUSH_JOBS` (the security corpus and
  `apps-contract`) the tier runs. A catalogue that names none plans the whole tier for a tested
  push.
- **change:** the fast jobs, run before a commit.
- **breadth:** the checks run on `main` that are too slow or too wide for every pull request,
  among them the security corpus again, on Linux and on macOS.
- **weekly** and **release:** reserved for the long checks and for what a release is cut from.
  No job is in them yet.

`explain` prints, for each tier, what it proves and what it does not.

### Main's copy reads a pull request's catalogue

A pull request's catalogue is read by main's `tools/ci`, with main's catalogue as the loader's
reference (`catalogue.load` with `reference`, its rules in `catalogue.against`). Against main's,
a pull request's catalogue is refused when it:

- drops a `security` or `live` job main has, by removing its entry or renaming it, unless main's
  copy marks that entry `retiring = "#<issue>"`;
- turns one of those jobs `ordinary`, or no longer `live`;
- takes one out of a tier main runs it in (adding a tier is fine);
- runs no security job in the `breadth` tier while main runs one there, whatever main marks.

So a security or live job leaves in two merges. The first marks it `retiring`, naming the issue
that retires it; the job stays in the catalogue and still runs. Once that is on `main`, a second
pull request drops the entry, and only that: main's mark allows the drop, never a change of
posture or tiers to a job that stays. A mark the pull request adds in the change that drops the
entry is its own, not main's, so it allows nothing. An ordinary job leaves in one merge, and a
new job needs nothing from main.

**In `ci.yml`,** the `changes` job's `catalogue-check` step runs `python -m tools.ci
catalogue-check --catalogue <the checkout's> --against <main's>` from the folder the plan step
archived main's `tools/ci` into, so main's loader and rules read the pull request's catalogue,
never its own. It exits 0 with no objection, 1 on a refusal or a catalogue either side cannot
read, and 2 on a usage error; each line it prints starts `catalogue-check:`, so none reads as a
workflow command (a line break in a problem, which can quote the pull request's text, is printed
escaped). A refusal fails the job, and the merge decision requires that job of the run
it counts, beside the plan (see "The merge controller's decision"), so a refusal reds the merge
verdict too. Unlike the plan, the step fails closed: when main's copy, or main's catalogue in
it, could not be fetched, the job is red. A push to `main` checks nothing, since its catalogue
is main's own; any other run with no pull request is red. The merge that adds the check, whose
`main` has neither `catalogue_check.py` nor its verb yet, passes with a warning; a copy of
main's with one and not the other is red. The planner reads the catalogue against the same
reference, so a catalogue main refuses plans nothing (exit 2) and the whole tier runs beside
the red check. `e2e` and `apps-contract` survive the red `changes` job (`!cancelled()`), as they
survive any other failure of it.

`tests/test_ci_catalogue_check.py` plants each refusal and each allowance.
`tests/rails/test_ci_plan_step_tree.py` runs the step as written, over this checkout's
`tools/ci` as main's and over it as a `main` without the check has it, beside a checkout whose
own copy of the rules accepts everything, and runs the real planner over a catalogue main
refuses. `tests/test_ci_merge_decision.py` holds the decision red on a refused, skipped,
cancelled or missing check.

The contract table's rows still run in the candidate's own suite (see "The rows that hold it"
under "Trust boundary"). Running them from main's copy is #3915's.

### Adding a job

1. Add a `[[job]]` entry with every required field, and its commands copied from the
   workflow's `run:` lines.
2. Add its id, its `ci.yml` jobs, its tiers and its posture to the table in the row "every
   catalogue command is the workflow's". Any `run:` line of its `ci.yml` steps that validates
   nothing goes in that row's list of such lines, under what it does.
3. Run that row and `python -m tools.ci explain <tier>`.

A workflow command changed without its entry, or an entry changed without its workflow, fails
the row.

### Planning a run

`tools/ci/plan.py` (`python -m tools.ci plan <tier>`, changed paths on stdin or `--paths`, `-z`
for NUL-separated) turns a diff into the jobs to run and prints them as JSON: `tier`, `full`,
`jobs`, `reasons` (why each job is in), `full_reasons` and `rails_floor`. The area map is
generated from the catalogue's declared inputs; `tools/ci/areas.toml` holds only the explicit
overrides (`--areas`, beside the catalogue by default):

- **A declared input schedules its job.** A changed path a job's `inputs` reach schedules that
  job. So does a path one of its steps runs: a script or folder a step names, and a sibling
  script that script imports.
- **Only an input names a path.** A path is known when an input other than the whole tree
  names it: an include selects it, or an exclude leaves it out by name. A step or an override
  schedules a job but makes no path known.
- **An `[[override]]` only adds.** Each names its `paths`, the catalogue `jobs` it adds (or
  `whole_tier = true`) and a `reason`. The loader refuses an override with no reason or one
  naming a job the catalogue lacks. Since it makes no path known, an override never takes a
  job out of a plan: a path no input names still plans the whole tier, the override's jobs and
  reason in it. The overrides today are the workflows (`.github/workflows/`), the local actions
  their jobs call (`.github/actions/`), `tools/ci/`, the locks and manifests and the image
  smoke's own inputs (all whole-tier), the installer (`static`) and the SDK snapshot script
  (`apps-contract`).
- **Always planned:** the `rails` job (all three legs) and `rails-checks`, whose `rails check`
  holds every rail to exactly one leg, every `security` job, every `live` job but one marked
  `nightly`, and each job an `after` of a planned one names. A live job's `nightly` names the
  breadth job that runs its steps every night, whatever changed, so the plan selects the job by
  its inputs like any other. The loader refuses the mark on a job that is not live or is
  security posture, and one naming a job that is not live, not of the breadth tier, or runs other
  steps. Read against `main`'s catalogue, a mark `main`'s copy of the job does not carry counts
  for nothing (`catalogue.trusted_marks`), so the pull request that adds one still plans the job,
  as a `retiring` mark allows nothing until `main` carries it. `rails-checks` also runs the gate
  aggregate and the npm advisory floors; the asset licences are `static`'s, whose inputs are the
  whole tree, so the pre-push hook checks them too. The loader refuses a merge tier whose rails
  run in legs without `rails-checks`, as it refuses one without `rails`.
- **Fail-safe: the whole tier.** An empty diff, a path no input but the whole tree names, a
  path that is not a plain path inside the repository (empty, absolute, `..`, not in its
  normal form, a newline, a carriage return or a NUL in it, not a string), a list that cannot
  be read, `--full`, and any error inside the planner. A pathspec that matches every path
  (`:(glob)**`, `:/`, `*`) still schedules its job but names no path, so a path only such
  inputs reach runs the whole tier: a forgotten path runs everything, never nothing.
- **Pathspecs are read as git reads them.** The loader takes a pathspec only in git's normal
  form (no `.` or empty part, since git reads `.` as the tree but `:(top).` as nothing) and
  refuses a wildcard the planner would read otherwise than git: a backslash, a `[` it never
  closes, a `/` or a `[:name:]` in a class, `literal` with `glob`, and an exclude of every path
  (`:!`, `:(exclude)*`, `:(exclude,glob)**`: a path of wildcards alone), which git 2.54 reads
  beside an include as every path and git 2.55 as none, so an exclude names the paths it skips.
  A test holds the matcher to `git ls-files` over the forms it accepts.
- **The rails floor** is the median, over a folder of telemetry history (`--history`), of each
  `merge.yml` or `ci.yml` push run's rails minutes: the slowest of its three legs, `rails (1/3)`
  to `rails (3/3)` in `merge.yml` (named by matrix key) and `rails (1)` to `rails (3)` in
  `ci.yml`, or, in `ci.yml` alone, the one `rails` job a run before the legs had. A run with a leg that did not pass, a leg
  missing (a run of the two halves the rails ran in before is one) or a leg named twice measures
  nothing, and so does a head it cannot measure from (no job list, minutes that are not a finite
  positive number); with none it reads
  `{"minutes": null, "runs": 0}`, never a guessed number. On CI it is read with `--repo
  <owner/name>` from `main`'s history instead: the newest three completed `merge.yml` runs on
  `main`, then `ci.yml`'s, whose history the collector itself uploaded, a cancelled run skipped and a red one
  counted when its rails jobs passed, and only a history line naming that run. Each `gh` call
  gets what is left of 60 seconds, so the read ends within them; any failure, running out of
  time included, gives what was read and a warning on stderr. The floor is a report, never a
  gate: nothing it reads changes the plan's jobs.
- **`--trusted-areas <file>`** adds the overrides of a trusted areas file (main's), read
  against the catalogue beside it, to the tree's own: the overrides in force are the union of
  the two. Their reasons say they are main's. A job one of main's adds that the tree's
  catalogue does not have makes that override's paths whole-tier instead.
- **`--outputs`** prints the two GitHub output lines on stdout, `full=true|false` and
  `jobs=<JSON list>`, and the plan as JSON on stderr, with anything the floor read printed.
- **`--matrix`** prints the plan as a job matrix instead: one entry per planned job and shard,
  in plan order, each with its `id`, `key`, `shard` (`i/N`, or empty for a job that takes no
  `--shard`, so a workflow passes `--shard` only when it is set), `runs-on`, `timeout`,
  `python`, `node`, `browser`, `container` (a script job's, or empty), `after` and `stage`, all
  taken from the catalogue. Each also carries what a workflow's test stage needs: `slug`, the
  key as the record artifact's name spells it (`verdict.record_slug`, since a workflow cannot
  work it out from the key), `history` (the checkout needs the whole history: a step names
  `{base}`, which `run-job` reads as the merge-base with `origin/main`), `programs` (the toolchain's `SETUP_PROGRAMS`, which the setup action installs)
  and `external` (the job's external inputs: the shard plan, a partner checkout). `after`
  names the planned jobs whose output the entry's job uses (the catalogue's `after`), and
  `stage` is 1 for an entry that waits for none, else one more
  than the latest stage it waits for: a `strategy.matrix` starts every entry at once, so a
  workflow runs one matrix per stage, each after the one before (in the merge tier `web-build`
  is stage 1, `package` and the e2e legs 2, and `e2e`, which merges the legs, 3). An entry's
  `key` is the key `run-job` writes in that shard's record (`python-suite (2/4)`), and `keys`
  lists them all: the plan a results stage judges the uploaded records against. With
  `--outputs` it adds `matrix=` (holding `include` alone, since any other key of a
  `strategy.matrix` would become a dimension), `keys=`, and one `matrix-<job>=` line for each
  workflow job that runs part of it, each holding `include` alone (`plan.stage_jobs`): `build`,
  the stage-1 entries a later entry waits for; `test`, every other stage-1 entry, which starts
  beside `build`; and `test-<k>` for stage `k` from 2, after the stage before it (`test-2` after
  `build`). So a long stage-1 entry no other entry needs, a suite shard, never holds back the
  stages that wait on the build. A planned job the catalogue does not hold, or an `after` that
  makes a cycle, exits 2. `pr.yml` and `merge.yml` call it (see "The tier workflows").
- **Exit codes:** 0 with a plan; 2, printing nothing, when the tier has no job in the catalogue
  (`weekly` and `release` have none yet), or the catalogue or its overrides, or the
  `--trusted-areas` file or the catalogue beside it, cannot be read, so a caller plans
  everything.

`run --changed` plans the same way, from `git diff --name-only --no-renames -z` against the
base, so a move names both the path it left and the path it reached.

`desktop-smoke`, `apps-contract`, `python-suite`, `image`, `client`, `package`, `audit`,
`web-unit` and `web-build` name their inputs; every other job declares the whole tree today, the
browser run's less what cannot reach it. `client`'s are its folder and uv's settings at the root
(`pyproject.toml`, `.python-version`). `package`'s are what setuptools reads (its config, `src/`,
the README, the licence) with the web workspace and the root's npm manifests `web/dist` is built
from, and what `verify_wheel.py` names, followed into each script it names. `audit`'s are what it
audits: `uv.lock` and `pyproject.toml`, every npm manifest and lock, and its own code and waivers.
`web-unit`'s and `web-build`'s are the workspaces their `npm run` steps run in, every workspace's
manifest (npm reads each on every run), and what that code names outside them: `src/`, which the
parity tests read, the design docs, brand files and guides the tests hold the app to, and the
scripts it runs. One run of each job's steps under a file-read recorder found no read of a tracked
path outside those lists but `mobile/package.json`, now listed, and `web-build`'s licence check,
whose whole-tree half is `static`'s on every change. `tests/rails/test_ci_job_inputs_tree.py` holds
each of the five to a census of its reads, with a planted read per form the census follows and a
dropped entry per list. A docs page none of them reads plans none of them. `desktop-smoke`'s are `desktop/` and
`web/`, the shell and the dashboard it loads (#3942): it packs the shell with electron-builder's
Linux dir build on a stub backend that runs the checkout's gateway, holds the packed `app.asar`
to `build.files`, starts the app under Xvfb and watches it, over a DevTools pipe rather than a
port, load the gateway's origin and draw the chat. It then hands the running app
`personalclaw://` links by a second launch, as another app does (#3827): `personalclaw://settings`
opens `#/settings`, a link to no route moves nothing, and a new thread's `?prompt=` link shows its
draft in the composer and sends nothing, which it reads off every request body, non-GET address
and WebSocket frame the page sends. Reloading the address the link left, and the same link
again, bring back the same draft and still send nothing. A code cockpit's link,
`personalclaw://code/<id>?pane=changes&diff=<file>&task=<task>`, opens the cockpit of a paused
code loop the stub backend seeds before its gateway starts (in the smoke's own home: the seed
refuses the default one), and nothing that leaves the page carries the loop's id or the task's.
`personalclaw://threads/new?mode=plan` opens a new thread in Plan and writes nothing. Each of
those two addresses, copied, brings back the same review or task mode in the same window (a
reload) and in a new one (a new document at the address in the same signed-in session, by way of
`about:blank`, its session storage cleared and its script state gone), and sends
the gateway nothing but reads. Six planted copies must each fail for their own reason: the asar
without a module `main.js` requires, a preload that throws, a shell that drops every link, one
whose link delivery presses Enter on the draft, one whose link delivery resumes the cockpit's
loop, and a preload that sends a write from each page a reopened address loads. A backend change
is e2e's to catch: the smoke proves the shell starts, draws and follows a link, not the gateway.
It is a fast smoke from the source tree, never a packaged-release test. The browser run's inputs
are the whole tree less the paths
that cannot reach it, each exclusion carrying its reason in the catalogue, and its three jobs
name the same ones. `apps-contract`'s are the triggers of the SDK classifier ci.yml ran it by
before the plan, less the classifier's own file. `python-suite`'s are every top-level entry but
`flake.nix` and `flake.lock`. The suite reads every tracked path: its publication-hygiene tests
list the tracked tree and open each file in it, and suite modules name `docs/`, `web/`,
`deploy/` and the root files besides. `tests/rails/test_ci_python_suite_inputs_tree.py` holds
the list to that census (the hygiene check's own `tracked_files`, and every repository path a
suite module names), so a tracked path the list does not name fails the rail however a test
reads it. The flake is left out by choice, and the rail holds the choice in one row: the suite
reads it too, but it is what the Nix workflow builds and checks, a workflow of its own and no
catalogue job, so a change to it alone is named by no input and plans the whole tier.

The list does not decide whether `python-suite` runs: it is a security job, so every plan holds
it, as every plan holds `python-suite-macos` and `browse-live`. It decides which paths are known.
Because it names `src/`, a `src/` diff is known: the plan is not the whole tier, and
`apps-contract` runs only when the diff reaches one of its inputs. So two kinds of rule now
decide plans alone, and `tests/rails/test_ci_plan_tree.py` holds each across the tree:
`apps-contract`'s inputs, held to the classifier's triggers and planned on every tracked path the
classifier ran the job on, and the whole-tier overrides of folders (`.github/workflows/`,
`.github/actions/`, `tools/ci/`, `deploy/docker/`), each planning the whole tier for every tracked
file under it. A docs-only plan without the suite needs a security-posture decision, not a
narrower list, and an entry leaves the list only when no suite test reads it, which waits on the
publication-hygiene tests' real-tree runs moving to `tests/rails`. Narrowing another job is a
change of its own: its inputs, the reason, and a planted path in `tests/rails/test_ci_plan_tree.py`,
which also plants a path per override and finds the override's reason in its plan.

**In `ci.yml`,** the `changes` job runs `plan merge --repo <this repository> --outputs` over the
pull request's files (or, on a push to `main`, the push's), passed NUL-separated so a name with
a newline in it stays one name. The planner that runs is `main`'s, never the checkout's: the
step fetches `main`'s tip (on a push, the commit the push replaced), archives its `tools/ci`
into a folder of its own and runs it there over the checkout's catalogue and overrides, so a
pull request's catalogue is its input and never its judge. Main's areas file, which that archive
carries, is passed as `--trusted-areas`: its overrides are added to the checkout's, read against
main's own catalogue, so the overrides in force are the union of main's and the pull request's.
A pull request that drops or narrows the workflows', `tools/ci`'s or `deploy/docker`'s override
still runs the whole tier for them, one that drops a job-adding override still runs that job,
and one that removes the job itself runs the whole tier for that override's paths. A trusted
copy that cannot be read plans nothing, so everything runs. With no such commit, or none that
can be fetched, nothing plans. Main's catalogue is also the reference the checkout's is read
against, so a catalogue main refuses plans nothing either (see "Main's copy reads a pull
request's catalogue"); the step after the plan fails the job over it. The `ci:full` label passes
`--full`; the labels are the event's, so one added later counts from the next push. The step
publishes only `full` and a list of job ids exactly as the planner prints it; anything else, and no
answer, becomes `full=true`. The browser run's jobs and `apps-contract` run when `full` is not
`false` or the list names them, and survive a failed `changes` job (`!cancelled()`), so an answer
that is missing, malformed or never written runs them. `tests/rails/test_ci_plan_step_tree.py` runs
the step as written, against planted answers and the real planner. `browse-live` is a job of its own
that waits on nothing and runs on every pull request and every push, a tested tree's included: a
security job is never planned out, and its evidence is never reused. Every other job runs as before.

To add a rule, prefer a declared input of the job it affects; use an override only for a path
that is no job's input but changes a job's result, and give it a reason that says how.

## The verdict

`tools/ci/verdict.py` is the one function that turns a plan's job results into green or red. It
is green only when the plan is not empty, every planned job has a result, every result for it on
every attempt is `success`, and every result names the candidate's head and partner revisions.
A per-job record names only the partners its own job read (`apps-contract`'s names the apps
checkout, every other job's names none), so the verdict is given each planned key's partners
(`--key-partners`, which the plan reads from the job's catalogue entry: its `revision` inputs),
and a record must name exactly its key's, each one the candidate names, at the candidate's
revision. Given none, a record is held to the candidate's partners exactly. A `needs` result
carries the run's whole identity, which must name exactly the candidate's partners. Each of these
reads red, with a reason naming the job:

- a planned job with no result, or one that was cancelled, skipped or failed;
- a result word GitHub does not use;
- a result with no head, or for an older head, or for an older head of a partner repository, or
  for a partner the candidate does not name;
- a record that names no partner its job reads, or names one its job does not read;
- a red on an earlier attempt, even when a rerun went green;
- an empty plan;
- a job outside the plan that failed, was cancelled or gave a result word GitHub does not use,
  on any attempt. One outside the plan that was skipped or passed changes nothing.

It reads GitHub's `toJSON(needs)` and refuses any other shape: each job is named once, holding
exactly `result` and `outputs`. JSON keeps only the last of a repeated key, so a job's failure
followed by its success would otherwise read green. `needs` holds each job's latest attempt
only, so the command cannot see a red on an earlier attempt; the controller will read attempts
from the API. The head it stamps on a pull request's results is the pull request's head, never
`github.sha`, which is the test-merge commit.

`python -m tools.ci verdict`, or `python3 -I tools/ci/verdict.py` from a trusted job's checkout of
the default branch, exits 0 green, 1 red and 2 on input it cannot read. Any usage error is input
it cannot read: there is no `-h`, because a help page would exit 0 having judged nothing, and an
option given twice is refused rather than the last one winning. It appends its reasons, or why
it could not read its input, to the step summary.

`--records <folder>` adds the per-job records one run's test stage uploaded, one artifact per
folder as `download-artifact` lays them out, read with `from_job_records`, and `--run-id <id>`
(`github.run_id`) names that run: each is refused without the other. Each test-stage entry
uploads its record as `ci-record-<key slug>-<run id>-<attempt>` (`verdict.record_artifact`), so
a rerun never overwrites the first attempt's records, and each record must sit alone in the
folder that name gives its own key and attempt in this run: another run's artifact, a renamed
one, a second upload of a record, or two records in one folder is refused. With `--records` a
planned key is met only by its record, so one with no record reads `missing`, and a matrix
entry that never started is red even when `needs` shows the matrix job as green; a `needs` job
named as a planned key is refused, since it would stand in for that record. A record of another
head reads `stale head`; one naming another run reads `another run`, and a local one, which
names no run, `no run`.

`--attempt <n>` is the run's own attempt (`github.run_attempt`, 1 to 999999 in ASCII digits, 1
by default). The `needs` results are stamped with it. The records keep the attempt each names,
and one naming a later attempt than the run's is refused, so a rerun's record never stands in
an earlier attempt's reading. On a rerun, a planned key with no attempt-1 record reads
`missing on attempt 1`: every planned entry ran on attempt 1, so a key attempt 1 left no record
for (a runner that died, a cancelled entry) stays red, as a red on attempt 1 does, and a rerun
carries only what attempt 1 recorded. Also refused: a link anywhere in the folder, a file beside
the artifact folders, a file over 1 MB, and any record the reader would refuse.

**The failed tests the records name.** A record read with `from_job_records` carries its failed
tests: on a failure, each test `run-job`'s diagnostic rerun classified `flake` or `repeated
failure`, or left `not classified` (a rail, a security-marked test, a rerun that said nothing of
it), with the record's reason; on a pass, each `quarantined` failure an entry of
`tests/quarantine.toml` waived. The verdict lists every one, by job, attempt and test, after its
reasons, and reads them for nothing else: the first result is the result. So a job whose only
failure is a flake reads red as a `failure`, with the flake listed, and the same failure under a
valid quarantine entry is a pass that reads green, with the quarantined failure listed. A test's
id is the suite's text, so each one is written on its own line inside one code span: a backtick
reads as a quote, and a line break or any other character that is not printable as its escape.
GitHub refuses a step summary over 1 MiB whole, the reasons with it, so the step summary lists
the failed tests within 512 KiB and says how many more there are; the log lists every one.
`run <tier>` lists them with its verdict too. The merge controller's `merge-verdict` check run
reads `needs`, not a run's records, until #3910's controller reads them, so it lists none yet.

On a push to `main` only, `decide_main_push` takes one more input: the planned jobs
`tools/ci/superseded.py` proved were replaced by a newer push (see "A burst of merges" below).
Such a cancelled job reads `superseded` instead of `cancelled`, and is still red. `decide`, the
reading of every candidate, takes no such input.

The `results` job of `pr.yml` and of `merge.yml` calls it, main's copy, with `--records`,
`--run-id` and `--attempt` (see "The tier workflows"); the default branch's controller will, so a
candidate and its judge read the same results the same way.
`tests/test_ci_verdict.py` holds every red to a row, every refusal to a row naming its reason,
and a green plan to a control.

## The tier workflows

`pr.yml` (the change tier's fast feedback) and `merge.yml` (the merge tier's verdict) are the
candidate workflows of #3915, in one shape: plan, build, test, results. Each runs on
`workflow_dispatch` alone for now, beside `ci.yml`, which stays the check every pull request and
push to `main` runs, so no check a pull request reports moves. Dispatching one on a branch times
a run of its tier on that branch's head without doubling every pull request's cost. Neither
posts a check of its own: the pull request and push triggers come with the change that retires
`ci.yml`, and `merge-verdict` is the trusted controller's alone (#3910).

- **`tree-check`** (`merge.yml`) is the push stage's first question: did a green pull-request run
  of `merge.yml` test this tree. It runs the previous `main` commit's `tools/ci/tree_tested.py`,
  read from the API at `github.event.before`, over `merge.yml`'s own `pull_request` runs, as
  `ci.yml`'s `tree-check` asks it of `ci.yml`'s; anything but `tested=true|false` is `false`, and
  any event but a push to `main` answers `false` at once. Only `changes` reads the answer, as its
  plan step's `PUSH_PLAN`: a tested push plans `[tier.merge] every_push` alone (`plan --tested`:
  the security corpus, `apps-contract` and `browse-live`, which run on every push whatever the
  answer, the owner's rule of 2026-10-19), an untested one the whole tier (`--full`), and every
  other run what it changed. pr.yml's plan step is the same text, given no push plan.
- **`changes`** first reads the partner head the run tests against, the apps repository's `main` as
  one full object id (`git ls-remote`), and publishes it as `apps-partner`: every entry that reads
  the apps repository checks out that commit, and `results` names it as the candidate's partner. A
  head that is not one object id fails the job, so the verdict is red. Then it plans the workflow's
  tier (`TIER`: `change` or `merge`) over what the commit changed against `main`'s tip, with
  `main`'s planner, archived from `main`'s tip into a folder of its own as `ci.yml`'s `changes` runs
  it, and `plan --outputs --matrix`. Only the keys, each key's partners (`key-partners`, one list
  per key) and the matrices of the stage jobs the workflow runs (`STAGE_JOBS`) are published, each
  read back as JSON first. An answer that is not a plan the workflow can run publishes nothing and
  fails the job: no answer, a planner that fails, keys missing, empty or given twice, partners by
  key that are not one list per key, a stage job the workflow does not run, a stage job's matrix
  that is not a list of entries alone, an entry in a container (neither tier has one), a slug no
  artifact name can hold, or stage jobs that run other entries than the keys name. So does a `main`
  whose `tools/ci` cannot be fetched. The verdict then reads an empty plan and a failed job: red,
  never a skip. Both workflows plan with the same step text.
- **Main's catalogue check** is `merge.yml`'s `changes` job's last step (the job
  `merge_decision.CATALOGUE_CHECK` names), as `ci.yml`'s: main's `catalogue-check` over this
  run's catalogue. A refusal fails the job, which `results` reads in `needs`, so the merge tier's
  verdict is red. A run of `main` itself checks nothing: its catalogue is main's own.
- **`shard-plan`** (`merge.yml`) makes the run's one shard plan, as `ci.yml`'s does, for the
  suite's legs (an entry whose job reads `shard-history`). Each leg names `main-push` for its
  history (`--leg test-shard:main-push:junit-test-shard-:3`): main's push runs in either shape,
  `merge.yml`'s newest first and then `ci.yml`'s (`tools/ci/workflows.py`), so the plan is
  weighed by whichever shape has run, and `merge.yml` spells neither file.
- **The stage jobs** run the matrix: `build`, `test`, `test-2` and `test-3` in `merge.yml`, and
  `test` alone in `pr.yml`, whose tier's jobs wait for no other job's output. Each is skipped when
  the plan gives it no entry, and runs after the stage before it whatever that stage's result
  (`!cancelled()`). Every stage job has the same steps: the checkout (the whole history for an
  entry whose `history` is set), the partner checkout, at the head `changes` published, for an
  entry that reads `apps-partner`, the setup action with the entry's `python`, `node` and
  `programs`, the shard plan for one that reads `shard-history`, the build (`web-dist`) for one
  that waits on `web-build` and the browser legs' blob reports for one that waits on them; then
  one line,
  `python3 -m tools.ci run-job "$JOB" [--shard "$SHARD"] --out "$RUNNER_TEMP/run-job"`; then,
  whatever it answered, the entry's record under `ci-record-<slug>-<run id>-<attempt>`
  (`verdict.record_artifact`) and its reports, on a failure the `lint-feedback` run-job left (a
  `lint_feedback` entry's alone; see "How a trusted job reads a candidate"), and `web-build`'s
  dist and each browser leg's blob for the entries that wait on them. Each entry's cap is its job's `timeout_min`, its runner its
  job's `platform`, and one red entry cancels no other.
- **`results`** runs whatever ran before it (`always()`), after every other job, and judges the
  plan's keys against `toJSON(needs)` and the records the run's entries uploaded, with `main`'s
  `tools/ci/verdict.py`, read from the API and run under `python3 -I`, with the partner head
  `changes` published as the candidate's partner and its partners by key: a planned entry with no
  record, a cancelled, skipped or red one, a record of another head, partner head, run or later
  attempt, a record naming other partners than its job reads, and a failed job of the run each
  read red. So do a run with no published partner head and a `verdict.py`
  that cannot be read. The verdict is the job's last step and runs unless the run is cancelled
  (`!cancelled()`), so a run whose `changes` published no plan is judged, red, never skipped.

Each workflow's runs of one commit share one concurrency group (`pr-<sha>`, `merge-<sha>`) that
never cancels a run in flight: a later dispatch of the commit waits, and never waits on the other
tier's run.

Records are kept 30 days: the controller and the push replay read them at merge, after the run.
The issue gave no retention, so 30 is the default until the owner sets one. Reports are kept a
week and the build three days, as `ci.yml` keeps its own.

What the dispatch-only workflows do not do yet: `merge.yml` repeats the change tier's quick jobs,
since its verdict is the merge tier's whole plan; and its push stage (the tree check and the push
plan above) waits for its push trigger, which comes with the change that retires `ci.yml`, with
the suite's one shared group per shard on `main`.

The rows that hold them, in `tests/rails/test_ci_contract.py`, each with planted violations:

- "the-tier-workflows-run-only-their-closed-lists-of-lines": every job is a stage job or
  orchestration (`changes`, `shard-plan`, `results`); each orchestration step runs exactly the
  lines of one step the row lists for its job, in their order, each spelled exactly, its
  indentation too (a blank line or a comment aside), so a step that keeps its lines and moves
  one (publishing a plan before the check that refuses it) fails as an extra line does, and a
  listed step no job of either workflow runs fails too; a stage job runs its runner's identity
  and the run-job line and nothing else, its other steps use only the checkout, the setup action
  and the artifact actions, and every stage job's steps are the same.
- "the-tier-workflows-run-their-tiers-matrix-and-judge-its-records": the plan step plans the
  workflow's tier for its stage jobs; the stage jobs are exactly the parts the tier's whole plan
  splits into, each reading its own part after the stage before, with the matrix's runner and cap
  and `fail-fast: false`; each uploads its record always, under the name the verdict reads for every
  entry's key, run and attempt, kept 30 days; an entry that reads the shard plan waits for the run's
  `shard-plan`; `results` runs always, after every other job, downloads the records with no
  condition and ends with the verdict over them and `needs`, held back by nothing but a cancelled
  run; `changes` publishes the partner head, every stage job's partner checkout checks it out, and
  the verdict names it, read from that output, as the candidate's partner; `changes` publishes the
  partners by key and the verdict holds every record to them, read from that output; one
  concurrency group per workflow and commit, never cancelling, and no job's group of its own; no
  job is `merge-verdict`, by its id, its name or any key the tier's plan can give a stage job; and
  no expensive job (the suite's shards, the browser run, the package, the gateway image) is in
  both tiers' plans, so the expensive tier runs once per candidate.
- "every-push-runs-the-security-corpus-and-apps-contract-whatever-the-tree-check-says": merge.yml
  has its tree check, `changes` needs it and reads its answer only as the plan step's
  `PUSH_PLAN` (tested on a tested push, full on any other push, nothing on any other run), no
  other job reads it, and `[tier.merge] every_push` names the security corpus and
  `apps-contract`. Plants: a stage job or the verdict reading the answer, every push planned as
  tested, the answer read off a push, `changes` without the tree check, no tree check, and
  `every_push` without `apps-contract` or with no list.
- "main's catalogue check stands in front of merge.yml's verdict": the check is the last step of
  the `changes` job, after the plan, with no condition and no `continue-on-error`, and `results`
  reads the job.
- "pr.yml carries no push stage": no push trigger, no job or step condition, no workflow, job or
  step env, no job or step input (`with:`) and no other expression anywhere in the file that reads
  a push, `main`'s ref, the commit a push replaced or a push event's own fields, and no tree check.
- "the tier workflows take no trusted trigger and hold no write": no `pull_request_target`,
  `workflow_run` or `workflow_call`; `workflow_dispatch` alone until `ci.yml` is retired;
  declared permissions, every one `read` or `none`; no secret, in any spelling (`secrets.X`,
  `secrets['X']`, `toJSON(secrets)`, any case).

The supply-chain contract's action row reads every workflow, these included, so each action is
pinned by commit with its release. The Python row reads a stage job's `python-version` from the
plan's matrix as the Pythons the catalogue's jobs of its tier name, and the timeout row accepts
its `${{ matrix.timeout }}`, since the catalogue's loader holds every `timeout_min` to at least
`MIN_TIMEOUT_MIN`.
`tests/rails/test_ci_tier_workflow_steps_tree.py` runs the partner step, the plan step, the
catalogue check and the verdict step as written, against the real planner and planted answers.

**Both shapes, read from one place.** Until `ci.yml` is retired a candidate's checks run in two
shapes: `ci.yml`'s one run, and `pr.yml`'s and `merge.yml`'s pair. `tools/ci/workflows.py` names
the three, and a reader that takes its names from there (the telemetry collector, the fetch and
the rails floor do) reads both. A reader that spells `ci.yml` itself reads one, and nothing once
`ci.yml` goes. `tests/rails/test_the_candidate_workflows_are_named_in_one_place_tree.py` fails on
any file under `tools/`, `scripts/`, `src/` or `.github/` that names `ci.yml` in its code, and on a
`workflow_run` listener of `CI` that follows neither `PR` nor `Merge`, unless
`tests/rails/baselines/ci-yml-readers.sites.jsonl` lists it with its reason. That list only
shrinks: against the gate's base a line the base lacks fails, `added` and a reason or not. The
change that retires `ci.yml` lands with it empty.

## The pinned browser image

Every container-made baseline is made and compared in one environment (#3943): the official
Playwright image for the locked Playwright, named in `web/e2e/visual-image.json` by the digest of
its `linux/amd64` manifest, with the tag and the index digest it was read from beside it.
`tests/rails/test_pinned_playwright_image_tree.py` holds the pin's Playwright to the one the lock
resolves for `@playwright/test` (its `playwright`'s own `playwright-core`, not the older top-level
one another package brings), so a Playwright bump moves the lock and the pin in one change, and
fails any tracked file but the pin that names the image, by a tag or by a digest.

`web/e2e/run-pinned.mjs` runs one Playwright invocation against that image's browser. Only the
browser runs in the image, since the browser, the fonts and the renderer decide the pixels. The
test runner, the harness and the gateway it boots stay on the runner as in every browser leg, so
what the gateway sees (its network, tmux, docker) is the same as in every other run. On a
`linux/amd64` host it mounts the checkout and its own Node read-only at their own paths, so
nothing is installed. The image serves its browser with the locked playwright-core's `run-server`,
bound to 127.0.0.1 on the host's network and refused if it says otherwise, and the run connects
through Playwright's own `PW_TEST_CONNECT_WS_ENDPOINT`. None of the caller's variables enter the
image. Before the invocation it probes the image in a container with no network
(`web/e2e/visual-environment.mjs`: the image, its platform, the Playwright version, the browser
build, the WebGL renderer, and the fonts as a count and one sha256), and:

- a compare run fails when the probe differs from `web/e2e/visual-environment.json`, the manifest
  committed with the baselines, or when there is none, before any baseline is read, and passes
  `--update-snapshots=none`, so a missing baseline fails rather than being written;
- an update run (`--update`) writes the manifest from the probe and rewrites the suite's baselines.

Any other host is refused for now: automated contributors run it through CI alone.
`.github/workflows/visual-baselines.yml` ("Refresh visual baselines") is the update path. On
dispatch it sets the checkout up as a browser leg does, runs the UI matrix and the visual spec
each with `--update` in an invocation of its own, and uploads the manifest and the `-linux` sets
as the `visual-baselines` artifact. It commits nothing and holds no write token; a lane reads each
diff and commits the intended ones in a pull request that names the change in light and dark.

## A push to main, and the nightly breadth

### The tested tree

`ci.yml` runs on every push to `main`. Its first job, `tree-check`, asks whether a green
pull-request run already tested the tree the push lands. The answer is yes only when all of
these hold:

- exactly one pull request into `main` was merged as the pushed commit;
- that pull request's head has the same tree as the pushed commit;
- the head contains the previous `main` commit. A `pull_request` run tests
  `refs/pull/N/merge`, and that tree equals the head's only when the head contains its base;
- the first attempt of every `pull_request` run on that head concluded `success`. A green
  rerun of a red first attempt is not enough;
- those runs were this pull request's into `main`: the first was made after it was opened, and
  its base was never changed. A run names neither its pull request nor its base, and a base
  change starts no run, so a run made for another pull request on the same head, or before a
  retarget to `main`, may have tested another base and skipped `test-macos`.

Anything else, and any error, is no. Every job outside the always-run set (`tree-check`,
`changes`, `security`, `browse-live`, and `apps-contract` when the plan names it) waits
on the answer and runs unless it is exactly `tested=true`. So a push behind its base, a merge
queue's commit or a direct push runs the whole gate, `test-macos` included.

The check is `tools/ci/tree_tested.py`, and the copy that runs is the previous `main` commit's,
read from the API at `github.event.before`. It is standard-library Python, run on the image's
own `python3` with nothing checked out or installed. A pull request that changes the check is
judged by the copy it replaces, and the merge that adds the check runs the whole gate.
`docker-single-container.yml` asks the same question about its own runs before it builds.

### The gateway image

The catalogue's `image` entry builds the gateway image the release ships and boots it once per
candidate (#3917), and `docker-single-container.yml` runs the same two lines on a pull request:
a plain `docker build` of the `runtime` stage that keeps the image's content ID
(`--iidfile`), then the README smoke on that ID (`--image-id-file`), never a tag. Their inputs are
one list, every path the Dockerfile copies and the smoke reads, `src/personalclaw/`, `uv.lock`
and `deploy/docker/` among them, so a backend-only change boots the image it ships.

There is no layer cache. #3917 measured buildx with the Actions cache (scope `gateway-runtime`,
written by `main` alone): a warm build's job took 442 s with `load: true` and 318 s with the image
exported uncompressed, against the plain build's 163 s median, because reading the cached layers
back took 139 to 210 s, longer than building every layer, and writing them took 602 s a push. So no
workflow gives an image build a cache. `tests/rails/test_ci_image_tree.py` holds all of this.

Runs on `main` are grouped by commit and never cancelled, so no merge goes unchecked because
a later one replaced it. The suite's shards are the one exception (see "A burst of merges"
below): a commit whose shard was replaced is never read as checked. It reads superseded when
the API proves it, and unproven otherwise, red either way.
`ci.yml` runs on a push to `main` only: off `main` its one run is the
pull request's, and a newer push to the pull request's branch cancels the older run. A branch
with no pull request gets no `ci.yml` run, and GitHub starts no `pull_request` run while a pull
request has a merge conflict, so such a branch gets none until the conflict is resolved. It
cannot merge in that state either.

### A burst of merges

The suite is the exception. On `main`, `test-shard` and `test-macos` each join one group per
shard that every push's first attempt shares, with `cancel-in-progress: false`. A running shard
is never cancelled. While it runs, the next shard to join waits, and a later request's waiting
shard replaces the one before it. So a burst of merges usually runs the suite on its newest
commit, not once per commit. `tree-check`, the always-run jobs and every other job keep the
per-commit group.

The group replaces by order of joining, not by commit. An older push whose `tree-check` or
`shard-plan` runs slower can queue its shard after the newest commit's and evict it. Then the
newest commit's shard is the one cancelled. No later descendant replaced it, so its gate reads
`unproven` and fails closed, and that commit stays untested until someone re-runs it. A re-run
keys on its own commit, so it cannot be evicted again.

A commit whose shard was replaced is not verified on its own, and its gate (`test` for
`test-shard`, `test-macos-gate` for `test-macos`) stays red, with the reason
`superseded by <newer commit>` when the API proves it and `unproven` otherwise. A skipped check
would read as passing wherever a check is required, so the gate fails. The badge follows the
newest run. No release gate reads CI results yet: until #3911 lands, the
[release runbook](release-runbook.md#tagging) tags only a commit whose own main-push run was
green, and #3911 must enforce that.

The gate names that red only when the previous `main` commit's copy of
`tools/ci/superseded.py`, fetched as `tree-check`'s copy is but run under `python3 -I` (unlike
`tree-check`'s), proves it from the API:

- the run is a push to `main`;
- the shard was cancelled before any step ran;
- its check run carries GitHub's replacement annotation, naming exactly that shard's own group;
- a later push run of `ci.yml` on `main`, for a commit that descends from this one, has a job of
  the same name that was not skipped. The earliest such run is the one named;
- every cancelled shard of the run was replaced by one and the same commit.

Any other cancellation, or an answer the check cannot read, is `unproven`, and red as before.
On a pull request the word never exists. `verdict.decide_main_push` names it only on a push to
`main`; every candidate's reading, `verdict.decide`, takes no proof, so a pull request's
cancelled job is always `cancelled`. A superseded commit is never green.

The row `main-push-runs-and-is-never-cancelled` holds this: each suite job's group names its
job and shard, is shared by every first-attempt push to `main`, keys a re-run on its own commit,
and never cancels; no other job has a group of its own; and each gate (`test` and
`test-macos-gate`) runs the previous commit's check for its own job's group, names both reds and
never exits 0 on a superseded shard.

### Commit provenance

Every commit that reaches `main` is checked by `scripts/check_commit_provenance.py` (the rules
are in [CONTRIBUTING.md](../../CONTRIBUTING.md#developer-certificate-of-origin-dco)), in two
places:

- **`dco.yml`, on every pull request,** in `pr` mode: rules 1, 3 and 5, over the commits the
  API returns and the title in the event payload. It runs on `pull_request_target`, so it checks
  out only the base branch's script and `MAINTAINERS`, and puts no file or git object of the pull
  request on disk. The commits endpoint returns at most 250 commits, so a list short of the pull
  request's own count fails the check rather than passing half of it. It also answers for the
  pull request as it is when asked, not as the event saw it, so a list whose last commit is not
  the event's head (a push after the event, or a list cut short or reordered) fails it too.
- **`provenance.yml`, on every push to `main`,** in `main` mode: rules 1, 2, 3 and 5, over
  `before..sha`. It is a workflow of its own because each push checks only its own range, so a
  run that never finished would leave its commits unchecked for good. One job with no `needs:`
  cannot be dropped by `tree-check`'s answer or by another job's outcome, and at a few seconds on
  one runner, grouped by commit and never cancelled, it stays so whatever becomes of `ci.yml`'s
  main runs (#3915 plans one run per candidate).

`dco.yml` also runs when a pull request's title is edited, since rule 5 reads the title. Its job
token reaches one step, which only fetches the commits with `gh api`; the check that parses them
runs in the next step with no token. Each violation is also printed as an `::error::` annotation,
so a red run's summary names the commit.

The main push runs the **previous** main commit's copy of the script and of `MAINTAINERS`, read
with `git show "$BEFORE:…"` and run as `python3 -I` from the runner's temporary folder, as
`tree-check` runs the previous copy of its check. A push cannot edit the check, add its own
committer to `MAINTAINERS` or drop a module beside the script to pass itself; a change to either
file judges the pushes after the one that lands it. The one push with no previous copy is the one
that adds the check: `BEFORE`'s history never held the script, so that push runs its own copy and
says so in the summary. A `BEFORE` that once had the script and has lost it is refused, and so
is a `BEFORE` the checkout cannot resolve (all zeros, or an old tip a forced update orphaned):
a git failure is never read as "never had the check".

**What a squash merge must carry.** On `main` nothing can be amended, so the squash commit itself
has to pass. In GitHub's squash dialog, delete the `Co-authored-by` lines GitHub adds for the
other authors when the squash is yours: rule 3 refuses them on a maintainer's commit. Keep the
commit author's email equal to the email in its `Signed-off-by`: rule 1 reads the author GitHub
records, so a contributor whose GitHub commit email differs from their sign-off fails on `main`
(two of the four contributor squashes in history do).

**What it does not cover.** Rule 4, the pushed tree against the tree the merge controller
recorded for its pull request, is not checked: #3908 keeps it open until that record is posted.
It lives in the merge controller's check run (see "The merge verdict's record"). Until then
`GitHub <noreply@github.com>` commits a squash merge, a web-editor commit and a revert made from
a pull request's page alike, and rule 2 passes all three. A push to `main` that starts no run at
all (one made with a workflow's own token, say) is never checked either, because the next run
starts at its own `before`; rule 5 closes only the skip-marker case.

### A closed pull request's runs

A concurrency group cancels a run only when a newer run joins it, and closing a pull request
starts no run in the workflows that test it. `cancel-closed-pr-runs.yml` runs on
`pull_request` `closed`, merged or not. Each of its jobs joins one per-pull-request workflow's
group with `cancel-in-progress` and runs nothing but its Runner identity step: no checkout,
`permissions: {}`, and no `pull_request_target`. A job-level group and a workflow-level group are
one namespace across the repository's workflows (GitHub's "Control the concurrency of workflows
and jobs": group names "must be unique across workflows to avoid canceling in-progress jobs or
runs from other workflows"), so the job cancels that workflow's run.

Its keys are written for the close, not copied: when a pull request closes because it merged,
`github.ref` is the branch it merged into and `github.sha` its merge commit (GitHub's "Events
that trigger workflows", `pull_request`), so `ci.yml`'s own key would name the merge's `main`
group and its `cancel-in-progress` would read false. A workflow added later that runs on
`pull_request` needs a job here; the contract row below fails until it has one.

### `test-macos`

Pull requests into `main` run the suite on `macos-26` with Python 3.13, in three shards of whole
test files planned from macOS's own durations, two xdist workers each and no coverage. With
`test-shard` on Ubuntu and 3.12, every change that merges has run both operating systems and
both Pythons. The security corpus is the exception: no shard collects it, so a pull request
runs it once, in `security`, on Ubuntu and 3.12, and breadth runs it each night on macOS on
3.13. The three shards take three of the five macOS runners the account runs at once, and
breadth's `macos` leg runs one shard at a time (`max-parallel: 1`), so a pull request's run and a
night's breadth fit the five together; the contract row
"a-pull-requests-macos-jobs-and-breadths-macos-leg-fit-the-macos-runners" fails a count that does
not (ci.yml's macOS jobs and the merge tier's macOS shards, each with breadth's). Concurrent pull
requests still queue on macOS.

### The suite's shards

Every suite leg runs in shards of whole test files, planned once per run by
`python -m tools.ci shards plan` (`tools/ci/shards.py`): in `ci.yml` by the `shard-plan` job,
and in `breadth.yml` by `freshness`. The plan is longest-processing-time greedy over per-file
seconds, heaviest file first into the lightest shard; a file with no record weighs the median.
`shards check` fails the run when a collectable file is in no shard or in two, so nothing is
dropped or run twice. Each shard downloads the plan, writes its own list with `shards list`
(which fails, writing nothing, for an empty shard: pytest given no selection collects every
test), and runs `pytest tests --shard-list FILE`, which collects from `tests` as a whole-suite
run does but imports only the files the list names (`tests/shard_list.py`). A breadth shard
records its list's digest as one of its inputs, so a result is carried only onto the files it
measured. Modules stay whole, so a module-scoped fixture is set up in one shard.

The plan leaves out `tests/rails`, which the `rails` job runs, and each file in `shards.JOB_OWNED`,
which the catalogue job named beside it runs by name (in `ci.yml`'s shape and the tier workflows'
alike): `tests/test_website_installer.py`, in `static`; `tests/test_npm_advisory_floors.py`,
in `rails-checks`; and the security corpus (the ten `tests/security` modules,
`tests/test_supply_chain_scanner.py` and `tests/test_supply_chain_gates.py`), in `security`.
`shards check` fails a plan that lists one, and a rail
(`tests/rails/test_each_tree_check_runs_in_one_place.py`) fails when a listed job is no catalogue
job or its command stops running the file, so a file left out of the shards never runs nowhere.

`shards check` holds the plan to files on every run. Each night `breadth.yml` also holds the
shards to test ids, as a net under it. Every suite shard passes `--shard-ids FILE`, which writes
each id the run collected, as many times as the run holds it (under xdist the controller writes
the list its workers collected, which xdist runs only when every worker's is the same), and
uploads it as `shard-ids-<leg>-<i>`; the randomized-order leg's two orders write the one file,
which then holds the later order's. For each suite leg, a `planned-ids-<leg>` job on that
leg's own runner and Python (its `PY` and setup-uv's, which `uv run pytest` collects on)
collects the planned suite (`shards universe` writes every file the plan covers, with the same
exclusions) and uploads `planned-ids-<leg>`. So a platform or a Python never reads as a
mismatch. `shard-union` then runs `shards union` over the five legs whose red is the run's
(ubuntu, macos, arm, coverage-shard and random-order), which fails, naming each leg, on an id no
shard collected, an id the planned suite lacks, an id collected twice (by two shards or one),
an id the planned suite holds twice, an id file that is not text, and a shard or a planned
collection that uploaded no ids. It is red, never absent. The canary's shards are held to its
own collection by `shard-union-canary`, which, with `planned-ids-canary`, is
`continue-on-error` as the canary is: a red there is the next image's news, and `shard-union`
never reads the canary's ids. `planned-ids-macos` waits for the macOS suite, as
`security-macos` does, so it takes no macOS runner beside the suite's shards. The collections
run no test, so they are ordinary posture, and they run every night, since the unions read that
night's.

The per-file seconds come from #3948's telemetry record (see "CI telemetry" below): each file's
median first-attempt seconds over the leg's own reports in the three newest completed `main`
runs whose history the collector wrote, red ones included and cancelled ones skipped, on the
platform and worker count the leg's newest history recorded. It is the only source. Until the
first collected `main` run there is no record, and the plan weighs every file the same and says
so; that measured 1.29x on Linux and 1.33x on macOS on `main`'s run of d75e54a0, and lasts one
plan per leg. Only history the collector's own run uploaded is read: a `ci-telemetry.yml` run,
on `workflow_run`, on `main`, in this repository, whose history's first line names the CI run it
is read for. Any run, a fork's pull request included, can upload an artifact of the same name.
One run is not enough: a file's seconds swing with the runner it lands on (one file took
71-146 s over seven main runs), and replayed over those runs the median of three planned within
1.06x where one run planned up to 1.30x. Red runs count because their reports still time every
file they ran; reading green runs only froze the source for as long as `main` was red.

What no plan can balance is the runner itself. Over the same seven runs, all of one shard's
files together ran 0.66-1.52x their median on Linux and 0.70-1.95x on macOS, the whole
shard at once, as a slower host does. So each shard's log ends with "shard collection and
memory": each worker's collection seconds, its peak RSS at collection's end and over the run,
and the machine's CPUs, which shows a slow host for what it is.

Most of that is the host's CPU model. Over four main runs on Linux, a shard ran a median 1.09x
its plan on an EPYC 7763, 0.88x on an EPYC 9V74 and 0.71x on an EPYC 9V45, so two shards of one
run can finish about 1.5x apart on a plan balanced to 1.000x. Each report records its host's
model (`ci_cpu`), and the plan weighs its seconds by it: a model's factor is the median, over
the input runs' reports on that model, of what the report ran against what its files' plain
medians planned, and each report's seconds are divided by it before the median is taken. The
factors come from the three input runs only, never from the run being planned (`--run-id`), and
a report with no recorded model (one from before it was recorded, on macOS until #3923) weighs as it
ran. Replayed on one of those runs, judged with each shard's time divided by its host's factor,
this planned 1.10x where the plain medians planned 1.17x. In plain wall time no plan made in
advance can do better than the hosts the run draws, so the 1.15x bar is judged that way: each
shard's time divided by its host's factor, as the median over the newest five finished main
runs.
The durations only balance the shards: when neither source can be read, every file weighs the
same and the plan is still whole.

On `main`, `breadth.yml` keeps the plan. A breadth shard's list digest is one of its inputs, so
a plan made afresh each night, from durations that move a few percent, would hand every shard
other files, and the plan's digest would differ every night. Measured: two green `main`
runs a few hours apart planned 885 to 949 of each shard's files differently. So `freshness`
passes `--keep-from-main breadth.yml`, and the newest earlier `main` run's plan is reused for
every leg it still plans whole: the same test files, in the same number of shards. Any change
to the set of test files, or no readable earlier plan, plans afresh from the durations. The
plan is kept for a week, so the next night can read it. Off `main` nothing is reused, so no
earlier plan is read. `ci.yml` plans afresh on every run: nothing there is carried.

### `breadth.yml`

The rest of the platform breadth runs nightly (09:37 UTC), on dispatch, and on a rehearsal
tag's push, which is how a release measures its commit ([the release tags](#the-release-tags)):

- **`freshness`** runs first. On `main` it reads the newest `breadth-evidence-<sha256>` record
  of an earlier `main` run, refuses it unless its bytes hash to that digest, and follows a
  carried entry once, to the run that executed it. Every record a run holds is read (a rerun
  of failed jobs adds one per attempt, listed across every page and ordered by artifact id),
  and a measured red beats a later attempt's green on the same inputs. A key whose newest
  entry measured nothing (a shard that wrote no entry, a green whose issue could not be read)
  is looked for in older records, so a red behind it is still carried. A run with no record
  (cancelled, or its report wrote none) is skipped. A key whose carried origin cannot be read
  is left out, and its shard runs. When there are no earlier results, every shard runs. It
  also publishes the run's night: the date of the 09:37 UTC slot at or before the run's
  `created_at`, which every attempt of a run keeps.
- **The legs that may carry:** the rails on macOS 26 on 3.12, with breadth's one whole-tree
  `harness scan` (its outcome is the leg's with the rails'), and `collection-parses`: one
  whole-suite collection in one process with `tests/collection_parse_guard.py` loaded, red on any
  parse named as a file under the source index's roots, or of a whole `.py` file under them,
  while pytest collects, except by pytest's assertion rewrite and Hypothesis's own constants
  scan. It collects and runs no test, and no rail is security-marked. Each reads its own
  runner's image, architecture and toolchain, and on `main` it skips pytest and carries the
  earlier result, red or green, only when every input matches.
- **Legs that run every night:** the suite on Ubuntu 24.04 on 3.13, on macOS 26 on 3.12 (six
  shards, two at a time), on Ubuntu 24.04 on arm64 on 3.13, and under coverage; the security
  corpus on Ubuntu 24.04 on 3.12 and on macOS 26 on 3.13, a canary on the next Ubuntu image
  (advisory), the randomized-order leg, the installer smoke, and the dependency audit.
  `NON_REUSABLE` in `tools/ci/breadth_evidence.py` names them. The suite legs
  are here because the suite runs the security-marked modules, and a security result is never
  carried from an earlier night: each runs every night, which costs runner time and nothing
  else. The canary differs from the ubuntu-24.04 leg
  only in image and Python, so a red is the new image's: pytest keeps its own temporary
  folders, since `RUNNER_TEMP` sits under the runner's home, where the suite's home guard
  refuses a test's folders and paths print as `~/...`.
- **The union check** also runs every night: a `planned-ids-<leg>` collection for each suite leg,
  on that leg's runner and Python, and `shard-union`, which holds each gating leg's shards to it,
  each test id once, and `shard-union-canary`, the canary's, advisory as the canary is (see "The
  suite's shards").
- **`report`** runs last, whatever the legs concluded. It writes the record (canonical JSON)
  as the artifact `breadth-evidence-<sha256>`, kept 90 days, and puts the digest in the
  summary. It keeps one issue per red leg, titled `breadth: <leg> is red`, and reads only what
  the workflow's own bot wrote on it (its search names the bot as author, and a full or
  incomplete page is unreadable). Each red adds its inputs to the issue. A green on inputs that
  differ from every red's closes it. A green on any red's inputs is a flake: it is added to the
  issue, does not close it, and the record keeps the leg red. A rerun of the run produces one,
  whatever the clock says, because it keeps the run's night. So does a later night of the
  security corpus, the canary or the randomized-order leg while none of their recorded inputs
  has changed: they stay red until a change of input, or until the issue is closed by hand. The
  randomized-order leg's seeds are no input (its entries keep them beside their inputs), so a
  green on the next night's seeds keeps its issue open. The installer smoke and the
  audit record the night as an input, because their outside state moves daily, so for them the
  next scheduled night is a changed input. It holds `issues: write` and writes issues only on
  `main`.
- **A shard that writes no entry is red,** never absent. Its runner was lost, or it failed
  before it recorded anything. It ran on no inputs, so its red never hides the inputs an
  earlier red ran on, and no later run carries it: the next run looks past it to the shard's
  last measured result. A leg whose issue cannot be read has its greens recorded red, marked
  `unsettled`, which no later run carries; a red it measured stays a red and opens the issue.
  The record is still written. The issues step tries every leg, and fails at the end naming
  each one it could not write.

A record satisfies a release alone only when it holds every shard, none of them carried, and
all of them green.

### Off `main`, nothing is reused

On a tag or a release branch, no earlier result is read or carried, and no leg issue is read.
`previous` writes an empty map and reads no earlier run (the `freshness` job still asks the API
for its own run's `created_at`, for the night), every `decide` says run whatever map it is
handed, the one leg that may carry downloads the map only on `main`, and every leg executes on every
dispatch. `report` reads none of `main`'s leg issues off `main`, so an open issue there can
neither turn a tag's green into a flake nor, by failing to load, pin it red. The record a
release reads is what that dispatch measured.

"Main" is the full ref, `refs/heads/main`: every command is handed `$GITHUB_REF`, never the
short `$GITHUB_REF_NAME`, which is `main` for a tag called `main` too.

Why: the reuse on `main` rests on the leg issues, which hold every red's inputs and turn a
green on them into a flake. Off `main` no issue is written, so reuse there had to rebuild that
memory from earlier records. Each round of that (blind keys, a window over a tag's own runs
and `main`'s, a check of the never-reused legs against every red in it) opened a new way for a
red to come back green, or for a key to stay blind across dispatches. A release is rare and
the full breadth is affordable for it, so off `main` it is measured, not reconstructed.

Every entry and every record names the run attempt that wrote it (`attempt`, from
`GITHUB_RUN_ATTEMPT`). Three rules hold within one run:

- **A key is held to its job's latest attempt.** On any rerun, on `main` too, `report` reads the
  run's jobs as their latest attempts left them (`jobs?filter=latest`; each leg's job is named
  `breadth-leg <key>`). A rerun's entries are the newest of each name across attempts, so a
  shard that wrote none on its latest attempt would be stood in for by an older one. A key is
  red, marked `shard_job`, when its job did not succeed in its latest attempt, or ran in a later
  attempt than its entry names. When the jobs cannot be read, every key is red.
- **A red stays red on a rerun.** Off `main`, on attempt N > 1, `report` reads the run's earlier
  attempts' records. A key red on any of them is red on this one, whatever its inputs, marked
  `red_on_an_earlier_attempt`. Without a record of each of attempts 1 to N-1, by the attempt
  each names, what a missing attempt measured is not known, so every key is red, marked
  `earlier_attempt_unread`.
- **A release refuses a gap or any red.** `satisfies_release` takes every record of the run and
  the run's newest attempt, read from the run itself. It accepts the run only when there is a
  record of every attempt from 1 to the newest, the newest attempt's record satisfies a release
  alone, and no attempt recorded a red. An attempt that measured a red and wrote no record
  (cancelled, a failed upload, still running) is a gap, so the run is refused rather than
  accepted on an older attempt's green.

A re-dispatch is a new run, and so a new measurement: dispatch 1 red, then dispatch 2 green,
gives dispatch 2 an all-green record. Whether a release may proceed while an earlier dispatch
of the same commit was red is the release gate's question (#3911), not this workflow's.

### The rows that hold it

`tests/rails/test_ci_contract.py` holds each of these with planted violations:

- `ci.yml` runs on every push to `main` and on no other push (no `**` or other branch, no
  `branches-ignore`, no bare `push:`, no tag, no negated branch, no path filter) and never
  cancels a `main` run, at the workflow or the job level. The suite's shards are the one
  exception: a running shard is never cancelled, but a newer push's waiting shard replaces an
  older one's, which the gates name as superseded (see "A burst of merges");
- `test-macos` runs `test-shard`'s split on macOS under exactly the condition that runs it on
  pull requests into `main` and on an untested push: the same pytest words but the worker count
  and the report path, its own shard of its own leg's plan, the same job env and defaults, the same
  step `if:`, `working-directory`, env and shell, no `exclude`, and no `PYTEST_ADDOPTS` in a
  step's env or written by a step;
- every job outside the always-run set waits on `tree-check`;
- `tree-check` runs the previous commit's copy, read-only;
- no `macos-14` leg anywhere (any size or case, in `runs-on` as a string, a list or a
  `{group, labels}` mapping, an expression's quoted literal, or any value of the matrix it
  names), and no coverage on a push or pull request: per command, one that names `--cov` or
  `--cov=…` measures coverage, and nothing else does, because the addopts measure none;
- every pytest step that runs the suite names its worker count with `-n`: a step that names no
  path, or names only `tests`, as every shard does. A step that names its own files runs in one
  process, pytest's default;
- every suite shard runs its share of its run's one plan: one job plans every leg and checks the
  plan, and each shard waits on that job, downloads the plan, lists its own leg's shard of N (its
  matrix size, as the plan names it) and runs pytest over `tests` with that `--shard-list`;
- no step that runs the suite keeps pytest's temporary folders under the runner's home: no
  `--basetemp` and no `TMPDIR` under `RUNNER_TEMP`, the workspace, `$HOME` or `~`;
- no workflow command passes `--no-cov`, which would only undo a default that is not there;
- `freshness` first and `report` last;
- `NON_REUSABLE` matches the jobs, holds every leg that runs security-marked tests (the suite
  included), and no leg in it carries an earlier result;
- the evidence is named by its digest and kept 90 days;
- `report` writes only issues, and only on `main`;
- every breadth shard records what it ran: every job but `freshness`, `coverage` and `report`
  is a leg writing entries under one key no other job writes, its `decide --key` is that key,
  and the keys with their matrices' shard counts (every dimension, no `exclude`) are `SHARDS`;
  a managed Python is installed before `uv python find`; a crashed decision runs the shard;
  every `--tree` is the checkout's or freshness's with no fallback; the served installer is
  never hashed from a `curl … | sha256sum` pipe (continued lines joined), and an unreadable one
  exits red after its inputs are written; the installer smoke and the audit record freshness's
  night, read from the run's `created_at` and tied to the cron slot; and a failed coverage
  download is a failure;
- off `main` nothing is reused: `freshness`, every `decide` and `report` pass the full ref
  (`$GITHUB_REF`); every `decide`, `ran` and `report` pass the run attempt; every leg's job is
  named `breadth-leg <key>`; no never-reused leg downloads `freshness`'s map, and a tree-keyed
  shard downloads it only on `refs/heads/main`;
- `collection-parses` runs one pytest command, word for word:
  `--collect-only -qq -n0 -p collection_parse_guard tests` and its JUnit report, and no
  `PYTEST_` variable reaches it from an `env` or a `run`. Without the guard, or over part of
  the suite (a narrower path, an `--ignore=…`, a `-k`), it would be green on a parse it never
  saw;
- `docker-single-container.yml` checks the tree before it builds on `main`;
- every workflow that runs on `pull_request` has its run cancelled when the pull request closes:
  the row evaluates each workflow's group on a pull request's run and the canceller's on its
  close, merged and unmerged, from this repository and from a fork, and holds them equal with
  `cancel-in-progress`. It also fails a workflow with no group to join, a canceller job whose
  group a merge's push to `main` would share, and a canceller that runs anything, holds a
  permission or runs on any other event.

The rows check the edits a maintainer plausibly makes: a respelled flag, a new leg, a moved
condition, a fallback added for convenience. They do not evaluate expressions or shell, so a
deliberately adversarial rewrite (a coverage flag assembled from variables, a hash computed in
a helper script) can pass them; review is what holds those.

## CI telemetry

Every record of CI history, per run and per test, is written by one component in one versioned
schema: `tools/ci/telemetry.py` (`python -m tools.ci telemetry`), run by `ci-telemetry.yml` from
main's own code. Nothing else writes durations, first results, reruns or quarantines, and
the shard planner and the weekly report read it.

- **`collect`** runs when a CI, Merge or Breadth run completes. It counts only main's own runs: a
  CI or Merge run of a commit landing on `main`, and Breadth's scheduled or dispatched run on `main`, from this
  repository. It decides that from the run itself before it lists any artifact, and the job's
  `if:` holds the same rule, so a pull request's run starts no collector and its artifacts are
  never read. It writes `history-<run>-<attempt>.jsonl`: a first line naming the schema and its
  version, with the facts only the Actions API knows (queue delay, setup, time to the first
  failed job, verdict time, runner-minutes, cancelled work, artifact volume, schedule lag, one
  entry per job), then one line per job record the attempt uploaded, then one line per test case
  of that attempt's junit reports.
- **A first result is never replaced.** Each attempt is collected on its own: from that
  attempt's own start, end and conclusion, its own jobs, and only the reports uploaded between
  its start and its end. So attempt 1's file keeps the first result even when it is collected
  after a rerun, and a rerun's file sits beside it, never over it. Jobs and artifacts are listed
  page by page against the API's count; a listing that comes up short writes nothing.
- **Where each test ran.** Each pytest process records, on its first test case, its platform
  (OS, machine and Python) and its number of xdist workers (`tests/run_facts.py`), and every test
  line of that report carries them. The planner keeps one platform's and one `-n`'s seconds
  apart, so a leg that moves to another `-n` plans from the first run that recorded it. On Linux
  and macOS it also records the host's CPU model (`/proc/cpuinfo`'s, or the
  `machdep.cpu.brand_string` sysctl), and the run line's `cpus` maps each report to it (null
  where none was recorded), which the planner weighs seconds by.
- **Versions.** Version 3 added the run line's `cpus` and nothing else, and version 4 the job
  record lines and nothing else. The planner, the report and the parity check read versions 1 to
  4, so a history kept from before reads as one whose reports recorded no CPU model and whose run
  carried no job records, and they refuse anything newer.
- **Artifacts are data.** An archive over 8 MiB is never downloaded, a report is read in memory
  up to 8 MiB, a report that is not UTF-8 or declares a DOCTYPE anywhere is refused, and nothing
  is extracted or run. A report that cannot be read fails the collection and writes nothing. The
  planner reads a history line by line, each line within 1 MiB and the whole within 256 MiB.
- **One line bound.** Every reader (the weekly report and its fetch, the planner, the parity
  check) refuses a whole history with a line over 1 MiB, its line break included
  (`telemetry.MAX_LINE_BYTES`, which each of them reads), so the collector writes none: a line
  that would pass it fails the collection and writes nothing.
- **`slots`** runs daily on its own schedule, because a scheduled run that never starts fires no
  completion event. It writes one line per slot each scheduled workflow was due in the last
  eight days: `ran` (with its lag), `pending` (no run yet, and the next slot has not come), or
  `missed` (the next slot passed with no run for this one). A slot before the workflow existed
  was never due, and a disabled workflow's slots say `disabled`. Scheduled runs here start
  hours late (one workflow's last ten nights: 239-469 minutes), so a slot is called missed only
  once the next one has passed.
- **Kept 90 days.** One main run's history measured 49 MB as JSON lines and 2.7 MB compressed,
  about what its junit reports take. The junit reports stay at 7 days: the history holds every
  figure they give, so 90 days of both would double the storage for nothing. At the 59 main CI
  runs measured in one busy week and one Breadth night a day, 90 days is about 860 histories,
  roughly 2.3 GB compressed.
- **The diagnostic rerun:** `rerun_result` is read from the `rerun_result` property `run-job`
  writes on each failed case it reran: `flake` or `repeated failure`, null for a case it did not
  rerun. Any other value, or one on a case that did not fail first, fails the collection.
- **Each job's record** (schema version 4): each test-stage entry uploads its job's record, the
  one `run-job` writes, as `ci-record-<key slug>-<run>-<attempt>`, and each record the attempt
  uploaded is one `record` line after the run line, by key: the job's `result`, its `rerun` (each
  failed test with the diagnostic rerun's word for it, null when it was not classified) and its
  `quarantined` failures. It is the one place every runner's classification lands: Playwright
  classifies from its own report, and no junit report carries its word. A line of its own holds
  each record within the line bound however many jobs failed, where one line holding them all
  would pass it in a run with several thousand failed tests and lose the whole run to every
  reader. A record archive over 2 MiB is never downloaded; it must hold one `.json` file within a
  record's 1 MB, which `verdict.from_job_records` reads, in memory, and the record must be this
  run's and this attempt's, in the artifact its own key names. Anything else fails the collection
  and writes nothing, as an unreadable report does, since a history with fewer records would read
  as reruns that found less. So a job whose record passes 1 MB (several thousand failed tests:
  `run-job` does not yet hold its record within it, and `verdict --records` refuses the same
  record) leaves its run with no history. A run that uploaded no record (a `ci.yml` run today)
  has no record line.
- **The environment marker** (schema version 2): `environment_failure` is true when the case
  failed or errored with the exact words `ENVIRONMENT, NOT POLICY` in its failure's message or
  text, the suite's way of naming a red the environment caused rather than the code under test
  (the browse proof's page that stayed detached), and false for every other case. Version 2
  adds that field and changes nothing else: the report and the parity check read version 1 too,
  as histories without it, and so does the shard planner.
- **Not yet recorded:** a test line's `quarantined` is #3947's (a job's quarantined failures are
  on its record line), `cache_hits` is #3909's, and `worker` and `position` are #3949's. No API
  reports them, so they are null, never guessed.

`tools/ci/report.py` (`python -m tools.ci report --history <folder>`) reads only that schema
and fails (exit 2) on a file in another schema or a version it does not read, and on a folder
that does not exist; an empty folder is reported as no history. It prints the same table in a terminal
and to the job summary, and `--json` writes the figures.

It reads 90 days of history, about 860 files of about 49 MB each, without holding any of them:
first every file's run line, to find the window, then the test lines of the runs inside it, one
run at a time and one line at a time. A line over 1 MiB, or a file over 256 MiB, fails the
report; a file outside the window is read for its run line alone. Each run's outcomes are folded
into each test's figures before the next run is read, and a test's seconds are kept as each
distinct value with the number of runs that took it, so the percentiles are exact and what a
test takes grows with how many distinct seconds it records, never with its runs. The reader
that held every record took 1.1 GB for 4 main-sized runs.

The figures:

- **the window:** the 90 days up to the newest run. While less than 90 days exist, it states the
  exact window it read, and `window.mature` stays false, which keeps the 90-day replay and
  maturity checks off;
- **flake rates**, from first results: a first failure that a rerun then passed (a later
  attempt that passed, or a `rerun_result` of `flake`; a `repeated failure` is not one), over
  the test's runs, rated only from 10 runs up. Two records of one test in one attempt count once.
  A skip or an expected failure is not a run;
- **what the reruns found**, from each history's job record lines, of each run's lowest attempt
  as for first results: every test the diagnostic rerun classified, with how many job records
  found it a flake and how many a repeated failure, and the jobs that did, most flakes first,
  then most repeated failures, the first 20 in the issue and all of them in `report.json`. It
  counts every runner's word, Playwright's included, and says how many of the window's runs
  carried any records. A history of an older version carries none, and a record line in one, or
  one not in its shape, fails the report;
- **durations:** each test's p50 and p99, the 20 slowest, and the week-over-week trend;
- **the quarantine:** every entry of `tests/quarantine.toml` (`--quarantine` reads another list),
  with its issue, its assignee, its expiry and the days left, soonest first, and its reruns
  (`--reruns`, below): how many of its ten rounds it failed. On its date an entry has expired,
  as the suite reads it, and the report flags it. A missing list is an empty quarantine; an
  entry, or a reruns file, not in its shape fails the report;
- **browse-live's environment quarantine:** each of the newest 5 head commits of main that
  measured the browse proof, newest first, quarantined when a `junit-browse-live` line carries
  `environment_failure`. One head is one observation, so a rerun cannot inflate the rate: of a
  head's runs and their attempts the newest that was not cancelled counts, and when it measured
  no browse-live line, or only version-1 lines, which cannot say, the head is not observed and
  no older run stands in for it. 3 of 5 quarantined is a recurrence: flagged in the issue, and an
  error annotation in the log. These are the rules of the tracker workflow this row replaced,
  except that the tracker also counted pull-request heads, and the history holds main's pushes
  only. Fewer than 5 observed heads give no rate;
- **scheduled runs:** from the newest slots file, each workflow's slots that ran, with the median
  lag, and every missed or pending slot as a line of its own.

It reads the folder's files named `history-<run>-<attempt>.jsonl` (`history-\d+-\d+\.jsonl`) and
`slots-<time>.jsonl` (`slots-[A-Za-z0-9_.:-]+\.jsonl`), and the members so named at the top of
every archive in it, in place: the fetch below keeps each archive as it was downloaded, and a
member is unpacked in memory, line by line, within the same limits, never to disk. A member named
otherwise, under a folder, or whose name its archive holds twice is not read, and an archive that
is not one fails the report.
`--issue-repo` writes the weekly issue: it edits the one open issue titled "Weekly CI health"
that the workflow token's account (`github-actions[bot]`) opened, or opens it once. It looks in
the issues listing, not in search, which lags behind a new issue, so two reports in a row edit
one issue; two such issues (a race) edit the older and name the other in a warning. Anyone's
issue of that title is left alone. The body stays under GitHub's limit, cut with a line naming
the run whose summary and `report.json` hold the rest.

`tools/ci/parity.py` (`python -m tools.ci parity --history <history-run-attempt.jsonl>`) reads
one attempt's history, from the `telemetry-history-<run>-<attempt>` artifact, and compares the
test node ids `test-shard` ran (its `junit-test-shard-*` reports) with those `test-macos` ran
(`junit-test-macos-*`). A skipped case counts as run on its side, since it was collected there.
It names every id only one side ran and exits 1 when there is one, and exits 0 when the two sets
are equal. An id one side ran more than once is named but is no difference. It exits 2 on a file
it can't read: missing, empty, not JSON lines, another schema or a version it does not read, a
line over 1 MiB or a whole over 256 MiB, or no report of one side (a push whose tree was already
tested runs no `test-macos`). It reads only the file it is given, and no workflow runs it yet.

### The weekly report

`.github/workflows/flake-report.yml` runs Mondays at 08:13 UTC, a slot no other workflow takes,
and on dispatch, one run at a time and never cancelled:

- **`rerun`** runs every test the quarantine lists ten times (`python -m tools.ci
  quarantine-rerun`): one pytest process a round over every listed test, each with its JUnit
  report, within 25 minutes. A quarantined test is marked an expected failure, so its failure
  reports as one and counts as a failure; a round that ran nothing (pytest refused its arguments,
  or the suite refused the list) or ran out of time counts each test as not run, never as
  passed. It runs the repository's own tests, so it holds `contents: read` and no write token,
  and hands on only the JSON tally.
- **`report`** holds `contents: read` for its checkout, `actions: read` for the record, and
  `issues: write` for the one weekly issue, which it writes only on `main`. Its token reaches the
  fetch step and the report step alone. It runs whatever the reruns concluded, and says "no
  reruns" beside each entry when they did not run.
- **The fetch** (`tools/ci/fetch.py`, `python -m tools.ci fetch`) lists the collector's runs on
  main in the last 91 days and keeps those that are the collector's own and succeeded; of each
  run's artifacts it takes only the one that run uploaded, on main, in this repository,
  unexpired, named `telemetry-history-<run>-<attempt>` or `telemetry-slots-<run>`, within
  32 MiB as listed and as it arrives. These are the checks the shard planner makes
  (`telemetry.collector_run` and `telemetry.upload_refusal`, one copy of each). A history
  archive is kept only when it holds `history-<run>-<attempt>.jsonl` alone, whose run line names
  that run and attempt, a counted run (a `ci.yml` or `merge.yml` push to main, or a scheduled or
  dispatched Breadth run on main), and a run its own workflow's listing shows with that attempt. The fetch
  keeps the newest slots archive and every history in the window, one download at a time, in one
  stored archive, `window.zip`, beside `fetch-status.json`, how it was built. Every collector run
  and artifact it skips is in the status's refusals with the check that refused it.
- **Archives are read in place.** Neither the fetch nor the report unpacks anything to disk or
  runs anything an archive holds: `window.zip` holds the collector archives as they arrived, and
  each is read inside it. A member that is absolute, holds `..` or a folder, or is a link is
  refused and named in the log (`telemetry.member_refusal`), and so is a collector archive past
  32 MiB; a window of more than 2,000 members or 6 GiB, or holding a name twice (a member is
  opened by its name), is refused whole. The window being built stops at both caps, keeping the
  manifest's place, and never takes a name it holds.
- **The carried window.** Each history costs two calls of the job token's, which GitHub allows
  1,000 an hour for the whole repository, and 90 days are about 860 histories. So the report job
  uploads `window.zip` alone as `flake-report-window` (stored, since its archives are compressed
  already; kept 15 days, so one missed week is survived), with `fetched.json` in it naming the
  collector run each history came from. The next run reads the run that uploaded the artifact
  through the API, by the id the artifact names, and takes it only when that run is this
  workflow's own scheduled or dispatched run on main, and the artifact holds `window.zip` alone;
  otherwise the window refills from the record within the budget. Every carried history is
  checked again as a fetched one is: its collector run must still be listed as the collector's
  own, and its run line shown from its own workflow's listing. One that fails, that left the
  window, or whose `fetched.json` entry is not in its shape (no time with its zone, say) is
  dropped, and fetched again while its collector run is listed. Only the collector runs the
  window does not hold are fetched: about 70 a week.
- **A budget.** Before each history the fetch reads how many calls the token has left
  (`rate_limit`, which costs none) and stops at 200, so the other workflows keep theirs; a count
  it cannot read is a spent budget, and the status says so. It fetches newest first, warns how
  many it left, and the next run fetches the rest.
- **The record.** The issue states the exact window it read, how many of the 90 days and runs
  it holds, whether the carried window was kept or why it was refused, and how many histories
  were carried, dropped on the recheck, fetched, refused and left for the next run, with the
  first 20 refusals (each as code, since it can quote an archive's names).
- **No durations cache.** The shard planner reads the record itself (#4778), so this workflow
  refreshes nothing for it.

## The selection map

`tools/ci/selection_map.py` (`python -m tools.ci selection-map --data <coverage database>
--commit <sha> --out <file>`) is the first piece of selecting tests by coverage (#3946). It reads
a database that a suite run under `--cov=personalclaw --cov-context=test` wrote, and writes which
test files ran each source file. Nothing selects from it yet.

- **`map`:** each measured source file, repository-relative (`--root` makes an absolute one
  relative), to the sorted test files whose setup, call or teardown ran any line of it.
  pytest-cov names each test's context `<test id>|setup`, `|run` or `|teardown`; a test id
  whose case holds a `|` is read whole.
- **`hubs`:** the source files that more than 40% of the map's test files reach. A change to
  one runs the whole suite, since selection buys nothing for it.
- **`outside_tests`:** the source files whose lines ran only outside any test's context: at
  import, before the first test began, or in a child interpreter. No test file maps to them, so
  the selector is to cover them by the import closure of what a test launches, never by the map.
- **`commit`**, `built_at` and `test_files`, the number of test files the contexts name.
- **Read as data.** The database is opened read-only with `sqlite3`, in coverage's schema 7,
  which a test holds to the installed coverage's, so `tools/ci` stays standard library only.
  Only which file each context touched is read, from the line table and the branch table both.
  A file that is not a coverage database, another schema, and a database with no test context
  (a run without `--cov-context=test`) exit 2 and write nothing.

`[tool.coverage.run]` in `pyproject.toml` records what the map needs, in every coverage run,
breadth's coverage leg included:

- `branch = false`: lines, not branches, so the leg's percentage is line coverage;
- `relative_files = true`: paths relative to the checkout, so shards run on different runners
  combine into one map;
- `parallel = true`, and `patch = ["subprocess"]`: every child interpreter a test starts is
  measured too, since pytest-cov 7 no longer patches subprocesses itself. Coverage passes its
  configuration to the child in `COVERAGE_PROCESS_CONFIG`, the child writes its data file beside
  the run's, and pytest-cov combines it at the end. A child's lines carry no test's context, and
  a child started with an environment of its own that drops that variable is not measured.

`make selection-map` runs the suite under coverage with per-test contexts, with its data in
`selection-map/` under the git common dir (`git rev-parse --git-common-dir`), and writes
`selection-map/selection-map.json` there, the one place every worktree of the clone shares.
`SELECTION_MAP_ARGS` is pytest's (by default `-n auto --dist worksteal tests`) and
`SELECTION_MAP_COV` what to measure. The map is written from a red run too, and the exit is then
the suite's.

Not yet: the nightly map build in breadth's coverage leg, `tools/ci/select.py` reading the map,
shadow mode, and the recall study.

## The rails a change can break

`python -m tools.ci rails select --paths <path>...` (`tools/ci/rails_select.py`) prints the
rails a change of those paths can break, one per line (`--why` adds the reason for each). A lane
runs them beside its own tests rather than all of `tests/rails` (#3920).

- **The record.** A rails leg of a push to main arms `tests/rails_reads.py`: a JUnit report
  named `junit-rails-<leg>.xml`, with `GITHUB_EVENT_NAME` `push` and `GITHUB_REF`
  `refs/heads/main` (or `PERSONALCLAW_RAILS_READS=1`, for a record made by hand). A pull
  request's leg records nothing and pays none of its time. It writes `rails-reads-<leg>.json` beside the report, and the leg
  uploads it as the artifact `rails-reads-<leg>`. It charges each read to the rail module
  running. A read is a walk or file asked of the source index, a repository file opened or
  stat'd (a path not there included), a folder listed, a module looked for and not found, or a
  child started. A helper's cache and a module imported once answer only the first rail. So each
  read is also charged to the code that made it, and each import statement run is an edge:
  a rail reads what every module it imports read for any rail. A rail module with a failed test
  is `unrecorded`.
- **The harness.** The run's own setup is the `harness`: conftest, the plugin and guard modules
  it loads from `tests/`, the files they open, `pyproject.toml` and `uv.lock`. Its stats are not
  reads: pytest's collection asks `shard_list` about every file under `tests/`. A fixture the
  harness defines (a conftest fence, a guard's) runs as the harness, setup and teardown, so what
  it reads inside a rail's test is the harness's read, not the rail's. A package module the
  harness imports is no part of it: conftest's registry fences import some two hundred, and each
  selects only the rails that read it. An import-time break in one fails the change's own tests,
  which load the same harness.
- **Only main's record.** With no `--map`, the command reads the newest push run of main made in
  the last day that holds a record. It takes only the run's own artifacts, by name and size, and
  only a member recorded on the run's own commit. `--map <file>` reads record files instead.
- **What selects a rail:** a changed path it read; for a path added or removed since the record's
  commit, a listing of its folder (or the nearest folder that commit held); and, for every rail,
  a path the harness reads (its own files, never a package module).
- **What is always selected:** `tests/rails/test_ci_contract.py`, a rail that starts a child
  (nothing a child reads is recorded), a rail the record does not hold or left unrecorded, and
  every rail when there is no record or it is older than a day.

## Local runs

`python -m tools.ci run-job <id> [--shard i/N]` runs one catalogue job here, and
`python -m tools.ci run <tier> [--changed] [--fail-fast] [--no-reuse] [--admission-wait S]` runs
every job of a tier (`tools/ci/jobs.py`). `make check` is `run change --working-tree` and
`make gate` is `run merge`. A run reuses a job's recorded pass by the rules under "Verdict
reuse", and starts each job once the host has the memory it needs (seam 1, under "The seams").

- **CI's commands.** A job's toolchain installs run first, as `ci.yml` runs them, then each step,
  word for word. A step's environment is built from nothing: what a GitHub runner gives every
  step (`CI=true`, which the e2e jobs' Playwright config reads for its JSON report, retries and
  server; never `GITHUB_ACTIONS`, which says the run is on GitHub), then the workflow's
  (`UV_PYTHON_PREFERENCE`), then the job's, then the step's; of the caller's it gets only
  `PATH`. `{runner_temp}` is the job's own temporary folder, `{shard}` the shard asked for,
  `{base}` `--base` or the merge base with `origin/main`, and `{run_number}` the run's
  `GITHUB_RUN_NUMBER` on GitHub and `0` here, where no numbered run runs, whatever the caller
  set. The `chromium` install is the browser legs' three steps since #5265
  (`jobs.install_chromium`): Playwright's build without apt within 5 minutes, a check that
  Chromium starts within 2, and only when that check fails its system libraries from apt within
  5 more, named as the apt mirror when they do not finish, then the check again. A hung mirror
  holds the job for that bound, never its whole limit. Electron's setuid sandbox helper
  (`tools/ci/sandbox_helper.sh`) is made root's 4755 only on a GitHub-hosted Linux runner, which
  a workflow step reads from `GITHUB_ACTIONS` and `RUNNER_ENVIRONMENT` and a step under run-job
  never gets. So run-job reads the runner from its own process environment (`RUNNER_ENVIRONMENT`
  of `github-hosted` and an `ImageOS`, on Linux: `jobs.github_hosted_linux`), and gives the
  commands that run the helper, the `electron` install and a step that runs it, the marker
  `PCLAW_SANDBOX_HELPER_HOST=github-hosted`, and no other command. No catalogue entry may set
  it, and one forged in a local caller's shell never reaches a step.
- **Caches, managed Pythons and the project venv** go under `.local/run-job/cache`, never the
  caller's own: uv's cache and Pythons, the venv `uv sync` installs, npm's cache and the
  Playwright browsers. `npm ci` reinstalls the checkout's own `node_modules`, as it does on CI,
  and `uv venv` replaces a venv an earlier run left in the checkout (`UV_VENV_CLEAR`), where
  CI's checkout is new each run. On GitHub the caches are the setup action's own folders
  (`jobs._cache_env`), so the cache it restored is what the job reads and the cache it saves
  holds what the job wrote: setup-uv's `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_PYTHON`,
  and the npm cache setup-node restores in the runner's own home; and the project venv is uv's
  own, the checkout's `.venv`, as `ci.yml`'s jobs have it, where the exemplar smokes'
  `resolve_py.sh` finds it. setup-uv fails its post step
  on a cache folder nothing wrote to, which every uv job of `merge.yml` run 40053059413 did
  while run-job wrote to the work folder's.
- **The job's time** is its `timeout_min` less a minute, shared by every step, so the loader
  refuses a `timeout_min` under 2. A pytest step still running when it runs out prints
  `possible xdist hang` and lists its group's live processes before the group is stopped.
  Which test it hung on is in `reports/in-flight-<leg>.txt` beside the job's JUnit report
  (`tests/in_flight.py`): the xdist controller rewrites the tests started and not finished, oldest
  first, every 10 s, so after a hang the oldest is the test. It arms nothing in a worker, and a
  run that ends leaves it empty. merge.yml keeps it with the entry's `reports/`; ci.yml's suite
  legs upload it as `in-flight-<shard>`, on a cancel too.
- **Its reports.** Each JUnit report the job declares must exist and be newer than the job's
  start, or the job is red: a pytest that died before writing one is red, not green.
- **One record per job, or per shard.** `verdict.job_record` writes its key, result, head,
  partner revisions, run and attempt (on GitHub `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT`, and a
  value of either that is not a whole number from 1 in ASCII digits, or none, runs nothing;
  locally no run and attempt 1, so a run's verdict reads a local record red), the diagnostic
  rerun (below), which quarantined tests failed (read from the reports' `quarantine_issue`
  property), where a shard plan came from, a local worker count, and each step's argv, exit and
  seconds. The partner revisions are those of the partners the job's own external inputs name.
  `verdict.from_job_records` reads records back and refuses any other shape, NaN and Infinity
  among them. A checkout that was not clean at one head before and after the job leaves no
  record, since the head it would name is not the tree it ran; so does a partner checkout the
  job reads that was not at one head before and after it. The partner checkout (`.ci-apps`) and the e2e jobs' reports (`web/e2e-report.json`,
  `web/blob-report/`, `web/playwright-report/`) are git-ignored, so they leave the checkout clean.
- **What runs here.** A job for the other platform family, in a container, or needing a
  toolchain or a partner checkout (`.ci-apps`) this machine lacks is named and not run. A
  sharded job run here plans its shards with equal weights, since `shards plan` reads CI's
  telemetry through the API; its record says `plan: offline`, and on GitHub that path is never
  taken. `--workers N` replaces the catalogue's count, is written in the record, and is refused
  when `GITHUB_ACTIONS` is set.
- **`run`** goes in catalogue order, each job after the jobs its `after` names, every shard of
  each, and judges each job's records with `verdict.decide` against the head and the partner
  revisions that job reads. It keeps going after a red unless `--fail-fast` is given, and runs
  nothing more after an interrupted job. `--changed` runs the jobs `tools/ci/plan.py` plans for
  the diff from the base (see "Planning a run"). `run-job` always runs the job it is given.
- **Exit codes:** 0 every planned job ran here and passed; 1 a job ran and failed, git asked
  for a credential, or a job made the real home's `.personalclaw`; 2 a job could not run here,
  was interrupted or left no record, or the input could not be read: an unknown job or tier, a
  tier with no job (`weekly` and `release` have none yet), an option given twice or not spelled
  in full (no prefix stands in for an option), or a job env naming a variable the runner owns,
  which the loader refuses too. An account whose entry in the user database names no absolute
  home runs no job here either, and exits 2 before any step.
- **`--working-tree`** (both verbs) runs on the checkout as it stands, uncommitted changes
  included, so a check before a commit can pass. The steps, their environment, the time limit
  and the JUnit check are the same; no record is written, since the steps ran on no commit, and
  the exit is the steps' result: 0 when every planned job ran here and passed, 1 on a red, 2
  only for a job that cannot run here or an interrupted one. With `--changed`, the plan reads
  uncommitted changes and untracked files git does not ignore. It is refused on GitHub, where
  the checkout is the commit under test. A working-tree run is no evidence for a commit: `run`
  judges no record, and says so.
- **Off its platform, in a working-tree run only.** A job of another platform family (an
  `ubuntu-` job on a Mac) runs here when every toolchain program it needs is on `PATH`, so a
  check before a commit means something on either platform. Its exit follows its steps, and the
  summary names each job that ran off its platform with the platform CI runs it on, and says a
  local result is never CI's evidence. A missing toolchain program, a container or a missing
  partner checkout is still "cannot run here" (exit 2). The committed-tree mode, which records,
  still refuses a job of another platform family, so an off-platform result never becomes a
  record.
- **Not yet:** nothing reuses a recorded pass, so `run` runs every job; and nothing reads these
  records on GitHub.

Each step runs through `tools/ci/run.py`, which runs one command from an empty environment
(#3920):

- **A folder of its own.** Each job gets a new scratch folder, which only its owner can open, with
  its own home and temporary folder. A folder that already exists is refused, so no earlier job's
  home or record is reused.
- **An environment built from nothing.** The job sees only its `PATH`, the scratch `HOME` and
  `TMPDIR`, a fixed locale (`LANG` and `LC_ALL` are `C.UTF-8`), git's variables, and what the job
  declares. No token, ssh agent or `GIT_DIR` of the caller's reaches it. A declared variable that
  would replace one the runner sets, the locale among them, or any `GIT_CONFIG*` one, is refused.
- **The default PersonalClaw home.** Nothing sets `PERSONALCLAW_HOME`, as on a CI runner, so the
  job's PersonalClaw home is the default one, `~/.personalclaw` under its scratch `HOME`. A job
  may not declare `PERSONALCLAW_HOME` either, so no catalogue entry points a job at a home
  outside its scratch folder.
- **A git of its own.** git reads no system file, and its global file is the scratch one. An empty
  `credential.helper` on git's command line clears every helper a file names, so no helper of the
  caller's or of any file runs. The helper git does run first is a tripwire. It answers nothing,
  records only what git asked for (the action, protocol and host), never the rest of the request,
  and each request it records fails the run, even when a helper the job names on its own git
  command line answers after it. A job that sets git's command-line configuration of its own
  replaces the tripwire: inside a pytest job the suite's conftest does that, and there the suite's
  git guard covers git instead.
- **Exit codes.** 0 is OK. 1 is red: a non-zero exit, a timeout, a credential git asked for, or a
  real home's `.personalclaw` the job made; the last two stay red even when the run is
  interrupted. 2 is partial: a SIGINT or SIGTERM the runner gets from before the job starts until
  its group is gone, and the run is never 0. The runner takes those signals on the main thread
  only, so it refuses to start a job on any other.
- **Stopping a job.** A timeout or a signal sends SIGTERM to the job's whole process group, then
  SIGKILL once every process of the group has exited or 5 seconds have passed, whichever is
  first. A signal the runner gets meanwhile, other than the one that began the stop, cuts the 5
  seconds short. When a job ends on its own, whatever is left of its group gets SIGKILL, so no
  process of the group outlives the run.
- **The real home.** The runner and the suite's real-home and git guards take the real home from
  the account's entry in the user database, never from `$HOME`, so a job's scratch `HOME` can't
  retarget them: a test inside a job that writes the real `~/.personalclaw` by its path is still
  refused. Where `$HOME` names that same folder by another path (a link, another letter case, a
  firmlink), the guards keep `$HOME`'s spelling, and watch what they watched when they took it
  from `$HOME`. A run also fails when the real home's `.personalclaw` was absent as the job
  started and is there as it ends. The runner only asks whether that folder exists; it never
  reads, lists or changes the real home, which may be a live PersonalClaw's. An account with no
  entry in the user database, or whose entry names no absolute path, starts no job.
- **Not guarded yet:** what a job does under a real home's `.personalclaw` that was already there,
  or one it made and removed again; the rest of the real home, the model caches the suite's
  model-root guard forbids among them, since that guard still takes its home from `$HOME`; the
  container or worktree a job runs in; and scheduling. A local result never stands in for
  GitHub's, and no workflow reads what it writes.

## Verdict reuse

`tools/ci/verdict_store.py` holds the rules a local run reuses a job's result by (#3919), and
`python -m tools.ci run <tier>` (so `make gate`) follows them:

- **Before any job runs,** the run reads the checkout once and decides every planned job: a
  `tree` job with a recorded pass of its key, or a `ttl` job with one inside its TTL, is reused
  and not run; every other job runs. Each reused job prints
  `reused <id> from <sha>, <age>, <n> skipped`, and each job that runs `ran <id>: <why>` under its
  result. `<n>` counts the tests the recorded pass skipped, from its JUnit reports: a skipped case
  counts, an expected failure (`xfail`) and a quarantined case do not.
- **The receipt** is `reuse-receipt.json` in the run's folder (`.local/run-job/<run>/`, or
  `--out`): the run's `dirty` field, each planned job's decision, class, key, key inputs, reason,
  source and a TTL job's age, and, for each job that was not reused, whether its pass was recorded
  and why not (its class, a dirty run, red, not run here, a rerun, a quarantined failure, a store
  that cannot be written). When a key cannot be built at all (a checkout or file that cannot be
  read, a probe the fingerprint cannot name, a `{base}` that names no commit) reuse is off: the
  receipt also reads `"off": <why>`, still carries `dirty` and a `run` decision per job, every job
  runs, nothing is recorded, and the run says `reuse is off: <why>` once.
- **A reused job leaves no job record,** so the run's verdict counts it by its receipt
  (`verdict_store.judge_reused`): only a reused decision of a job the store may reuse, from a
  clean run, with a source, on this run's tree and keyed to its own inputs. One for another tree
  or another key reads `missing`. A receipt that reuses a security, live or `never` job, reuses in
  a dirty run or from no source, or names a job the plan does not or twice, is refused, and the
  verdict is red. A green run that reused a job says `### Verdict: green on reuse` and names each
  reused job, since it did not run on this candidate.
- **What is recorded:** each job of a reusable class that ran and passed, every shard of it, from
  the job records it left, with the tests it skipped. A red job, a `never` job, a job that did not
  run here and any job of a dirty run record nothing. A store that cannot be written records
  nothing and stops nothing: the run goes on, and the receipt says why.
- **The key's inputs** as a run reads them: the job as the catalogue was read, the digest of
  every job's entry, each install, and each step as this run runs it (a local `--workers` in its
  pytest worker count) with its folder; the placeholders stay unfilled: `{runner_temp}` is this
  run's own scratch folder, `{shard}` takes each of its values in every run of the job and the
  shard count is keyed, `{base}` is keyed as the full id of the commit it names, and
  `{run_number}`, another value every run, is named only by jobs that are never reused. Then the
  commit's tree, each partner's head, the digests of `uv.lock` and `package-lock.json`, each
  toolchain program and each program an install or step starts from the job's `PATH` or by an
  absolute path (its path and the sha256 of its bytes; nothing is started), the Python it asks
  for, the OS, the architecture, its container, its env, the worker count its steps get (`auto`
  read as this machine's CPU count), and the host's capabilities, read with the environment each
  step gets and a home that is none of the user's. A capability the steps read differently is
  keyed once per step, as `step <n>: <name>`.
- **To force a fresh run,** pass `--no-reuse`: every job runs, no recorded pass is read, and each
  clean pass is still recorded. `make check` (a working-tree run) and `run-job` neither read nor
  write the store.
- **What a gate can reuse:** of the merge tier's jobs only `rails`, `rails-checks`, `web-unit` and
  `web-build` are `tree`. Both suite jobs are security posture, and the others are `never` or
  live, so a second `make gate` on an unchanged tree still runs every one of those.

- **The key** is a sha256 over every input a job's result depends on: the job id, its entry's
  text, the catalogue's version, the exact argv, a digest of the selected tests or shards, the
  commit's tree, each partner repository's commit, each lock file's digest, the toolchain, the OS
  and architecture, the runner or container, the advisory policy, any other declared input, and
  the host capabilities. A change to any one is another key. The OS and architecture must be
  named, so no two platforms share a key; an empty runner means the bare host. An id or digest
  that is not exactly one (the wrong length, uppercase, a newline after it) is refused.
- **The class** is the entry's, read by `catalogue.parse_reuse`. A job whose `security` is not
  `ordinary`, or that is live (`live = true`, or reading a live feed), is read as `never`,
  whatever it declares. The catalogue has no canary marker, so a canary job is `never` only by
  its own entry's class.
- **The store** is `ci-verdicts/` in the clone's common git folder, shared by every worktree of
  the clone; no other clone's store is read. Its git runs with no `GIT_*` variable, so a
  `GIT_DIR`, index or configuration a hook exported neither moves the store nor changes what git
  reports. git still reads the user's own global and system configuration. A local entry never
  reaches GitHub.
- **A pass is recorded** only when it needed no rerun and its quarantined-failure list is present
  and empty, from a checkout that was clean when the run began and is still clean, on the same
  head and tree, when it is recorded, and only when the entry would be read back. Clean means
  `git status --porcelain --untracked-files=all` prints nothing, so an untracked file counts. An
  entry is written to a temporary file, flushed to disk, and then renamed. One that is torn,
  malformed, of another version, or whose inputs do not hash to its name is a miss, never an
  error: the job runs.
- **A dirty run** neither records nor replays: every job runs, and its receipt reads `dirty: true`.
  The receipt's `dirty` and the replay rule come from one reading of the checkout, taken when the
  run began. A job whose inputs name another tree than the checkout's runs.
- **The host's capabilities** (`tools/ci/capabilities.py`) are the key's `capabilities`: each
  tool the suite probes, as `absent` or its resolved path and the sha256 of its bytes; the
  browser the browse proofs would use, found as `tests/browse_chrome.py` finds it, a `~` in its
  roots being the job's home; and each skip switch (`PERSONALCLAW_TEST_CONTAINER_RUNTIME`,
  `PERSONALCLAW_REQUIRE_INSTALL_PROOF`, `PERSONALCLAW_REQUIRE_BROWSE_PROOF`,
  `PERSONALCLAW_REQUIRE_NPM_TREE`, `PERSONALCLAW_TEST_CHROME`) as `unset`, or `set:<value>`, so
  a switch set to the text `unset` is not one left unset. Nothing is started to read them, never
  `--version`. The tools come from the suite itself, never a hand-kept list: every
  `shutil.which` call under `tests/`, read by AST, whatever `PATH` it is given (one a test builds
  may still hold the host's folders), whose tool is a literal, a loop over literals or a constant
  of them, or a helper's parameter every call in its own module gives a literal; every candidate
  a test hands the container runtime's `require`, `available` or `skip_reason`; and the container
  runtime's `PROGRAMS`, which cover its own lookups. A probe the scan cannot name refuses the
  fingerprint, and so does `shutil.which` taken as a value, a helper another module names, and a
  loop over a name bound more than once or over a list or set, so no pass is keyed on a list
  read short; the rail `tests/rails/test_ci_capabilities_tree.py` holds this tree to it.

### A push to main's replay

`tools/ci/main_replay.py` holds what a push to `main` may replay from its pull request's run
(#3919), one job at a time, where `tools/ci/tree_tested.py` answers for the whole run. Nothing
calls it yet: `merge.yml`'s push stage will, once #3915's flip gives that workflow its push
trigger and #3910's controller App posts the merge verdict. `decide` replays nothing unless the
head's merge verdict counts, so calling it before then would run the whole merge tier on every
tested push.

`python -m tools.ci replay --plan <ids.json> --controller-revision <sha> --receipt <file>
[--image LABEL=IMAGE ...] [--toolchain NAME=VERSION ...]` is the push stage's call. It reads the
run's own environment (`GITHUB_EVENT_NAME`, `GITHUB_REF`, `GITHUB_REPOSITORY`, `GITHUB_SHA`, and
`BEFORE`, the commit the push replaced), with `GH_TOKEN` for the API. It takes the planned job ids
from `main`'s catalogue, and the image each runner label runs now as the push read it. It reads
the source run's record artifacts (`main_replay.source_records`):
- every page of the run's artifacts, against one count;
- of the `ci-record-` ones, each must name that run as its uploader;
- one named for a later attempt, and an expired one, is passed over, so its job runs;
- one named for attempt 1 must have been made while attempt 1 ran, within the archive bound, and
  hold the one record its own name names, of that run and attempt 1, read as the telemetry
  collector reads one (`telemetry.record_bytes`);
- anything else runs every job.
It asks `merge_verdict.verify` about the head. It prints one line per planned key, and writes
`replayed=` (the keys, as JSON) and `replays=` to `GITHUB_OUTPUT`, the lines to the step summary,
and the receipt to `--receipt`. It exits 0 with any answer, "every job runs" included, and 2 for
inputs it cannot take, before anything is read. The CI contract table's row "a pull request's
run never replays" holds every step that runs it, a local action's included: its own `if:` or
its job's must keep it off a `pull_request` and a `pull_request_target` run, whatever the
workflow's triggers are. A guard the row cannot evaluate fails it, never reads as false.

- **Nothing replays,** and every job runs, unless the run is a push to `main` naming the commit
  it replaced (a `pull_request` event replays nothing and reads nothing, so a pull request's own
  evidence never reaches its own verdict); `tree_tested`'s guards hold (one pull request merged
  as the commit, the pushed tree its head's tree, the head containing the previous `main`, the
  first run made after it opened, its base never changed), in a copy of its sequence that this
  module's own tests hold row for row; the `merge.yml` listing holds only runs of `merge.yml`
  and its count says it is whole; the head's first `merge.yml` `pull_request` run has a first
  attempt that completed, concluding `success` or `failure` (a cancelled or timed-out attempt
  is judged by nothing); the controller's merge verdict on the head counts; and every verdict
  file attempt 1 left reads as a job record. Any error reading the API, the verdict files or the
  merge verdict is a full run; inputs the caller gets wrong are refused before anything is read.
- **A job replays** only when its class is `tree` or `ttl` (a security, live or `never` job
  always runs); its job in attempt 1 concluded `success`; exactly one verdict file names it, of
  attempt 1, a pass on the head, reading no partner repository, with an empty quarantined-failure
  list and no rerun; a `ttl` job's attempt 1 started inside its TTL; and every other shard of it
  replays too, so one red shard runs every shard. Only attempt 1 is read, never a later attempt
  or a later run, so a red is never rerun into a green.
- **A later run is not read either way.** Unlike `tree_tested`, which needs every `pull_request`
  run on the head green on attempt 1, a later run's red does not stop the first run's green from
  replaying. What a later run found reaches the push only through the merge verdict, whose newest
  run on the head decides, so a later red verdict stops every replay.
- **The tree is not every input a pass stood on.** Each job record names what the job ran on
  (the catalogue's runner label; the image, `<ImageOS>/<ImageVersion>` on a GitHub-hosted runner
  and a container job's own image) and its toolchain's identities, as a key reads them
  (`job-record/3`; `jobs.runner_of`). A job replays only from a record of the source run itself
  (its `run_id`, so a record another run or a local run wrote is never read as this run's), run
  under the label `main`'s catalogue gives the job, on the image that label runs now (the
  caller's `images`, as the push read them; a container job's image is its catalogue's), never
  an empty one, with the Python `main`'s catalogue names and an identity for every word of its
  toolchain. A moved image, another toolchain, a label the push holds no image for, or a record
  that names neither (a `job-record/2` one, which every other reader still reads) runs the job.
- **A pass that read a partner repository never replays** on a push, whatever its class: the push
  has no partner head to hold the pull request's to. `apps-contract`, which reads the apps
  partner, is `never` today and runs on its class; were it made `tree`, its partner read would
  still keep it running.
- **The receipt** carries the trust receipt's fields (the pushed commit, its tree, the toolchain,
  and the sha256 of the plan, of attempt 1's job list and of each replayed verdict file), the
  source run, the images the push compared, and each job's decision, why, and the image a
  replayed pass ran on.

`tests/test_ci_main_replay.py` plants the API answers and the verdict files, each written by
`verdict.job_record`, and the quarantined one by `run-job` itself.

## Local tooling and evidence

A maintainer may run private tooling that makes local work faster. This section says, for each
place such tooling meets the repository, what it may decide, which evidence it reads and writes,
and where the repository's own commands and GitHub CI stay the authority (#4586). A contributor
needs none of it: the commands in [CONTRIBUTING.md](../../CONTRIBUTING.md) and GitHub CI produce
every verdict that counts.

Two rules hold for every seam:

- **A local result never stands in for GitHub's.** No local run, private or public, posts a
  check, writes a verdict GitHub reads, or turns a red green. A local receipt names no controller
  (see "Receipts"), so `trust.check_receipt` refuses it as GitHub evidence.
- **The fail-safe is the public path.** An input that is unknown, unreadable, missing, stale or
  edited selects the documented command and the full work, never a shortcut. The public tools
  already fail this way: a path the planner cannot classify plans the whole tier, and a job
  record that cannot be read is refused. A private tool that wraps them may only add work to
  that, never remove it.

### What each step validates

| Step | What runs | Counts as |
|---|---|---|
| Before a commit | `make check`: the change tier, on the working tree as it stands, no record | advice |
| Before opening or updating a pull request | `make gate`: the merge tier, on the committed tree, one record per job | advice |
| The pull request | `ci.yml`'s run on the pull request | the gate |
| A push to `main` | `ci.yml`'s main-push run: `tree-check`, then every job it does not skip | the check of what landed |
| A release | a tag on a `main` commit whose own main-push run is green and whose breadth, started by its rehearsal tag, is green ([release runbook](release-runbook.md#tagging)) | the release |

There is no batch step: each change gets its own pull request and its own verdict. Every change
is meant to reach `main` as the squash merge of an up-to-date pull request. `main` refuses a
direct push for everyone ([the ruleset on `main`](#the-ruleset-on-main)); a push that reaches it
some other way finds no pull request merged as that commit, so `tree-check` answers no and every
job runs. Until #3910's required verdict lands, a pull request can still merge with red checks:
the `merge-verdict` check is not posted yet, and its controller App comes with #3910. #3911's
release gate (`release-tag.yml`) refuses any other commit, and reads the breadth its rehearsal
tag started on it ([the release tags](#the-release-tags)).

### The seams

Each entry gives the seam's inputs, its outputs, what it does on failure, its trust boundary, the
identity it names (the tree, the external revisions, the toolchain), its receipt, and the public
path when the private tool is not there.

**1. Memory-aware admission of heavy and short jobs.** Private: it admits jobs across several
clones on one host, which the public runner leaves out.

- **Inputs:** each job's `workers` and `mem_gb` from the catalogue, whether the job is short or
  long, and the free memory of the host and of any container VM.
- **Outputs:** when each job starts. Never which jobs run, their commands or their results.
- **On failure:** a memory reading that is missing, unreadable, or older than the poll interval
  the tool declares, and a tool that crashes, admit nothing beyond what the run does on its own.
- **Trust boundary:** timing only. It never decides, reuses or reports a verdict.
- **Identity:** none of its own; the jobs it schedules record the tree and the partner revisions.
- **Receipt:** it writes none. The run's own job records are the evidence, and admission leaves
  them unchanged; its log of waits and peak memory is diagnostic only.
- **Without it:** the public runner admits by memory within one run (`tools/ci/admission.py`,
  #3920). Off GitHub, `make check` and `make gate` start each job only once the host's free
  memory (`MemAvailable` on Linux; the free, inactive and speculative pages on macOS) holds what
  it needs: its workers times the peak memory of its largest process the last time it ran in
  this clone, else the catalogue's `mem_gb`, else 1 GB a worker. A job just started, by this run
  or another clone's, has not grown to its peak, so the free reading does not show it yet: each
  admitted job enters its need in a ledger the machine shares (`admission-ledger.json` under
  `personalclaw-ci` in the user's cache folder), and admission counts each entry until its job
  ends, its run's process is gone, or it is 120 s old, checking and entering under one lock. A
  job that needs more than the host holds, or is still waiting after `--admission-wait` (30
  minutes), is not run, and the run is partial (exit 2). A memory reading it cannot take admits
  every job, a ledger it cannot use counts nothing, and the run says so. On GitHub every job
  starts at once. It reads no container VM's memory.

**2. Culprit-isolating batch validation.** Retired. It validated several branches together;
with no batch step there is nothing to publish.

**3. The local push gate and exact-pair verdict reuse.** Retired as private tooling: the check
before a push is the repository's own hook and `make gate`, and reuse is the public verdict
store.

- **The hook** (`.githooks/pre-push`, which runs `scripts/run_prepush.sh`, #3920) runs the
  change tier from the catalogue: `run change --pre-push --changed --working-tree --fail-fast
  --workers 2`, with the merge base with `origin/main` as `--base`. `--pre-push` leaves out each
  job whose entry says `prepush = false`, a test suite too slow for a push, whose gate is CI;
  today the hook runs `static` (lint, types, the asset licences and the installer's checks). It
  runs no check command of its own, so a job added to the change tier runs in it with no edit to
  the script. A red run refuses the push; a partial one lets it through, and says so. It refuses
  an outgoing ref that is not this worktree's `HEAD`, checks the working tree, and does not
  refuse a dirty one. Three contract rows hold it: the hook runs no check command (a planted
  `black` line fails); a change-tier job that runs a test suite (pytest over a folder or no path,
  vitest, `playwright test`, `npm run test*`, `make test*`) says `prepush = false`; and the
  render smoke, which the hook no longer runs, still runs in the merge tier (`e2e-shard`'s
  `smoke` project) or a release workflow.
- **Inputs of reuse:** the verdict store's key (see "Verdict reuse"): the job's entry, its exact
  steps, the selection, the commit's tree, each partner repository's commit (the apps partner
  among them), each lock file's digest, the toolchain, the OS and architecture, the container,
  and the host's capabilities. So a pass could be reused only for the exact core and partner
  pair it ran on.
- **Outputs:** a job reused or run, and why. Today nothing consults the store, so `make gate`
  runs every job. A `make gate` that reads the store, writes `reuse-receipt.json` and takes
  `--no-reuse` is #3919's local wiring, in review and not on `main`.
- **On failure:** a dirty checkout replays and records nothing; a key that cannot be built, or an
  entry that is torn, foreign or of another version, runs the job. A live or `security` job
  always runs, and its result is never recorded.
- **Trust boundary:** local only. The store lives in the clone's own git folder, and no entry
  reaches GitHub.
- **Identity:** the key above, which names the tree, the partner revisions and the toolchain.
- **Receipt:** the run's reuse receipt, #3919's: per job its decision, class, key, the key's
  inputs and its source, and the run's `dirty` field.
- **Without it:** `make gate` with every job run; GitHub CI on the pull request is the gate.
- **On GitHub,** a push to `main` decides with `tree-check`, all or nothing. A replay of each
  job its pull request's first run passed is #3919's push-side half, in review: it would read
  only attempt 1 of the head's first `merge.yml` run, run every shard of a job when one shard
  failed, and never replay a pass that read a partner repository, so `apps-contract` would
  always run. Nothing will call it until `merge.yml` writes a verdict file per job (#3915) and
  the controller App posts the merge verdict (#4584).

**4. One command contract for a branch, a pull request and a push.** Public: `make check` before
a commit, `make gate` before opening or updating a pull request, and GitHub CI on the pull
request as the gate (see "Local runs" and the table above). There is no batch command and no
command that pushes to `main`. Private guidance names the same two commands and copies no test
command of its own.

**5. Dependency stores shared across worktrees.** Private: it is machine setup outside the
repository.

- **Inputs:** the exact lock digests (`uv.lock`, `package-lock.json`) and the toolchain.
- **Outputs:** an environment identical to the one `uv sync --locked` and `npm ci` build from
  those locks.
- **On failure:** a store keyed by another lock digest, or one that is missing or unreadable,
  falls back to a cold `uv sync --locked` and `npm ci`. A store is added to and never edited, and
  carries a digest of its own content, checked before each use: a store edited under the right
  key fails that check and is rebuilt.
- **Trust boundary:** it never changes a verdict or a key; the verdict store's key already holds
  the lock digests and the toolchain.
- **Identity:** the lock digests and the toolchain above.
- **Receipt:** the run's reuse receipt (#3919), whose key inputs name the lock digests.
- **Without it:** the documented installs, `uv sync --locked --extra dev` and `npm ci`, which a
  catalogue job's toolchain runs anyway.

## The quarantine

The one way a known failure is waived is an entry of `tests/quarantine.toml` (#3947). The rules
live in `tests/quarantine.py`, which `tests/conftest.py` loads in every session:

- **At most 10 entries,** each an `[[entry]]` table with exactly `nodeid`, `issue` (the number
  of the issue that tracks the fix), `assignee` (the GitHub login of who owns it), `expires` (a
  date at most 30 days out, read in UTC) and `reason` (one line).
- **Never a rail or a `security`-marked test.** An entry for a rail (`tests/rails/`) or a
  security test stops the session. The marker is read from the source, on the module, a class or
  the test, and again on each collected item the list names, so a mark a hook adds is refused too.
- **A list that breaks a rule stops the session, and no test runs:** an eleventh entry, an
  entry expired or more than 30 days out, a missing issue, assignee or reason, a reason of more
  than one line, an unknown field or key, a test listed twice, or an id that names no test pytest
  collects (a renamed test takes its entry with it). The source is read before collection; what
  only collection shows (a mark a hook adds, a parametrized case that is gone) is refused then,
  under xdist too, before any test runs; a run of part of a module (`file::test`, `--lf`) judges
  none of its entries by what it left out. Either way the session ends with exit 4 and the
  reason. On an entry's `expires` date it has expired, and CI goes red with no change to the
  repository: renew it with a reason, or fix the test.
- **A quarantined test still runs.** It is marked `xfail(strict=False)` at collection, so its
  failure is recorded as a quarantined failure, in the terminal summary and as the junit
  property `quarantine_issue`, instead of a red, and a pass stays a pass despite
  `xfail_strict = true`. An open issue alone waives nothing.
- **Nothing else waives a failure.** `tests/waiver_guard.py` reads the list on its own, imports
  nothing from `tests/quarantine.py`, and fails every test pytest waived as an expected failure
  that the list does not name: a source `xfail` mark, strict or not, `pytest.xfail()` in a test
  or a fixture, or a quarantine bug that waives more than it lists. A known gap a test pins is
  asserted as it stands (the miss), so closing the gap reds it, as a strict `xfail` did.

The contract row `the-quarantine-is-capped-assigned-and-expiring` holds each rule the source
shows with a planted entry, and three controls that must pass: ten entries, an entry expiring in
exactly 30 days, and one case of a parametrized test. `tests/test_quarantine.py` holds the
session's half: what collection refuses, how a quarantined test ends, that an empty list changes
no test's outcome, and the guard. It is `security`-marked, so no entry may waive the tests that
prove the list's rules. The file's shape is a contract with the weekly CI health report (#3948),
which reads it for the quarantine's status: a field changes in both together.

**The diagnostic rerun.** When a pytest step of `run-job` exits 1 (tests ran and some failed)
and nothing else went wrong, its failed tests are read from the step's own JUnit report, as XML,
and rerun once, alone, in a fresh process (`-n 0` when the step ran workers; a step that names no
worker count ran in one process already, and its pytest may have no xdist), with the step's
per-test `--timeout` and inside the job's remaining time. It classifies only: the job stays red
whatever the rerun shows, since the first result is the result and only an entry here waives a
known failure.

- **Which test a case names:** pytest names a case from its own rootdir, which can be a
  package's (`packages/personalclaw-client-py` has a pytest config of its own), so a case's
  classname is looked up from each folder at or above the paths the step collects, and counts
  only as a `.py` file under one of those paths. A case that names no such file, or two, is not
  rerun, and its record says so. The rerun's own report is read only when every case in it is one
  the step's report failed, named alike.
- **Never rerun:** a rail (`tests/rails/`), and any `security`-marked test, which the rerun
  deselects (`-m "not security"`). A failure there is red at once. A rail starts no process; a
  security-marked test still starts one, which collects its module and deselects it (exit 5),
  so the test itself never runs again.
- **A security-posture job:** the job's result is never rerun or reused, and a security-marked
  test is never rerun; an ordinary failed test inside it gets one diagnostic rerun, recorded
  only. The suite jobs (`python-suite`, `python-suite-macos`) are such jobs, so their ordinary
  failures are classified while the job stays red.
- **Recorded three ways:** each failed test as `flake` (it passed alone) or `repeated failure`
  (it failed again), or null with the reason it was not rerun, in the job record's `rerun` field;
  as a `rerun_result` property on its case in the job's report, which the telemetry collector
  reads into the test line's `rerun_result`; and as a `::warning` line. The verdict lists each
  one from the record, and a flake's job still reads red (see "The verdict"); the collector
  carries the record's words into the history's record lines, which the weekly report counts.
- **The step's own outputs:** the files the step left beside its JUnit report (the real-home
  guard's `reports/real-home-guard.txt` among them) are put back after the rerun, and what the
  rerun wrote there moves to the job's scratch folder, so the job's reports describe the job.
- **Not rerun at all:** a passing job; a step that failed for any other reason (a usage error,
  the job's time running out, a signal, the credential tripwire, a program that is not pytest);
  and a step whose report is missing, older than the job or not XML. Their record's `rerun` is
  null. A test stopped by its own per-test `--timeout` is a failed test, and is rerun.

`tests/test_ci_jobs_rerun.py` plants a suite in a temporary repository and runs it through
`run-job`'s step path: a test that fails then passes, one that fails twice, one that times out
twice, and a security-marked one and a rail that fail, each job red; a package with its own
pytest root, whose failure is rerun as its own test and never as its namesake at the root; a step
stopped for its time, a signal or the tripwire, and a report it cannot trust, each with no rerun;
a rerun stopped at the end of the job's time, one the runner is interrupted in, and one whose
report cannot be read; and a session report beside the JUnit that the rerun leaves as the step
wrote it. The weekly report's flake rate counts the `flake` and not the `repeated failure` from
the report's property, and its "what the reruns found" counts each word from the job record. The
same file runs the results stage's verdict (`verdict --records`) over such a job's record,
uploaded as a GitHub runner's: an unquarantined flaky failure reads red with the flake listed,
and the same failure under a valid entry of the suite's own quarantine reads green with it listed
as quarantined.

**The web runners follow the same contract:** one retry, in a fresh process, with the same time
budget, and red on a first failure the retry passes.

- **Vitest:** under CI, `web/vitest.config.ts` adds a JUnit reporter to Vitest's own default
  reporters (the github-actions annotations on a CI runner stay), writing
  `reports/junit-web-unit.xml`, which `web-unit` declares; locally it sets no reporter, so
  Vitest's defaults stand. When `web-unit`'s `npm run test:web` step exits 1 and nothing else
  went wrong, `run-job` reads the failed tests from that report and reruns them once in a new
  process: `npx vitest run --maxWorkers=1` (and `VITEST_MAX_WORKERS=1`) in `web/`, the failed
  tests' files only, filtered to their full names, each whole (`--testNamePattern=^(?:…)$`, the
  names escaped), so a test that passed in the step, its title's namesake in another suite
  included, does not run again. The rerun has the config's own per-test time budget, inside the
  job's remaining time. A case that names no test file under `web/` is not rerun. The rerun's
  report is read only when every test it ran is one the step's report failed (Vitest reports a
  test of a named file that the filter leaves out as skipped). The classification is recorded in
  the same three places as pytest's, and the files beside the report are put back after the
  rerun. As for pytest, a step that fails for another reason (another exit, the job's time
  running out) and a report that is missing, older than the job or not XML get no rerun, and a
  step that exits 0 with such a report is red; a rerun that exits otherwise than 0 or 1, is
  stopped at the end of the job's time or is interrupted, or whose report is missing or
  unreadable, classifies nothing.
- **Playwright:** its own retry is the rerun, so `run-job` starts no second one. Under CI
  `web/playwright.config.ts` keeps `retries: 1` (a new worker process, the same test timeout) and
  sets `failOnFlakyTests`, so a test that passes only on its retry fails the run. When the `e2e`
  step exits 1, and nothing else went wrong, `run-job` reads its JSON report
  (`PLAYWRIGHT_JSON_OUTPUT_NAME`): a `flaky` test is a `flake`; an `unexpected` one whose retry
  failed or timed out too is a `repeated failure`; one that failed with no retry run, or whose
  retry ended otherwise (it was interrupted), is not classified. The record's `rerun` names the
  step's own argv and exit. A report that is missing, older than the job (an earlier run's, left
  when Playwright exits 1 before writing one, as on a config that throws at load), or not of the
  shape Playwright writes classifies nothing, and says so.

Three contract rows, one per runner, hold what the tree must not add, each rule with a planted
break, each listed option, plugin and key its own:

- `pytest-retries-once-in-a-fresh-process-on-the-same-budget`: no option in `addopts` (the
  root's, or a package's own under `packages/`) or a catalogue step that retries in the same
  process (`--reruns`, `--count`, `--lf`, `--sw` and the like), no retry plugin loaded with `-p`,
  and no retry plugin (`pytest-rerunfailures`, `flaky`, `pytest-repeat`, `pytest-retry`,
  `pytest-flakefinder`) in the dependencies of `pyproject.toml` or a package's own, or in
  `uv.lock`, since an installed one loads itself.
- `vitest-retries-once-in-a-fresh-process-on-the-same-budget`: no `--retry`, `--repeats`,
  `--testTimeout` or `--hookTimeout` in a catalogue step; no `retry` or `repeats` in the config,
  a project's included; the two npm scripts the step runs (the root's `test:web`, the web
  workspace's `test`) held to their text, so an option there cannot change the step's retries or
  budget unseen; and the JUnit report added under CI alone, to Vitest's own reporters, at
  `reports/junit-web-unit.xml`.
- `playwright-retries-once-in-a-fresh-process-on-the-same-budget`: no `--retries`,
  `--repeat-each`, `--timeout` or `--fail-on-flaky-tests` in the step; `retries` one under CI and
  set once (a project's own `retries` is refused); `failOnFlakyTests` set; no `repeatEach`; no
  test timeout of the config's own; and the step's JSON report named.

`tests/test_ci_jobs_web_rerun.py` runs both web runners through `run-job`'s step path with
stand-in `npm` and `npx` that play a planted suite, the rerun's name filter applied as Vitest
applies it: a Vitest test that fails then passes, one that fails twice and one that times out
twice, in one job, and a job whose only failure is a flake, each red; a passing test of the same
title, and a title of regular expression characters, never rerun with it; each step, report and
rerun fault above; the report folder put back; and for Playwright a flaky test, a repeated failure
and a timeout twice, a job whose only failure is a flake, a retry that did not fail, and a missing,
stale or malformed report. Neither real runner is started by these tests.

## State a test leaves behind

`tests/state_leak_guard.py` (#3949), loaded by `tests/conftest.py` in every process, names the
test that leaves process-global state changed, as itself, instead of the unrelated test on its
worker that turns red later.

- **What it reads.** A non-daemon thread the test started that is still alive after a short
  grace; the working directory; the handlers for `SIGINT`, `SIGTERM`, `SIGUSR1` and `SIGUSR2`; a
  child process the test started that is still running; a socket the test opened that is still
  listening (as the port guard tracks them); and each registry a `_restore_*` fence covers. The
  first five are read when the test's setup begins and again once its teardown has ended, so a
  fixture's own threads and ports, and a `monkeypatch` change, are not the test's. The directory
  and the handlers are given back once read. The environment stays `env_guard`'s.
- **Before the fences.** The fixture `_a_test_leaves_process_state_as_it_found_it` requests every
  fence, so its after-reading of the registries is taken before any fence restores one. Such a
  change is recorded as `fenced` and never fails while its fence stands; the record is what a
  fence's retirement is judged on.
- **Never failing:** a fenced change, and a thread named as a pool names its workers
  (`ThreadPoolExecutor`, asyncio's executor).
- **Report mode** is the default: every leak goes to `reports/state-leaks.json`, with the test's
  real xdist worker and its position in that worker's order, and the guard's own seconds. The
  terminal summary gives the count and the cost per test.
- **`--strict-state`** fails a leaking test in its teardown, with what it left, unless
  `tests/state_leak_allowlist.toml` names it with the issue that tracks its fix. At most 5
  entries, each exactly `nodeid` and `issue`; a list that breaks a rule stops the session.

Not read yet: files a test writes outside its temporary paths. Under xdist every worker writes
to one tree, so a per-test listing would charge one worker's writes to another worker's test;
the real-home guard already fails writes to the real home. The bisect tool comes later in
#3949.

### The randomized-order leg

`tests/random_order.py` adds `--random-order-seed N`: the modules, then the tests within each,
in the order `random.Random(N)` draws, and nothing else, so every xdist worker draws the same
order and a different seed a different one. A module stays whole. Each process writes the tests
it ran, in its order, with its real worker and position, to
`reports/random-order/order-<seed>-<worker>.jsonl`.

`breadth.yml`'s `random-order` leg runs it every night: four shards of the run's plan on
Ubuntu 24.04 and 3.13, each running its share in two orders, seeded `run number × 10 + 1` and
`+ 2` (`${{ github.run_number }}1` and `...2`), so a rerun of the run repeats the same orders.
Each order is its own step, so the second runs whatever the first did, and the shard is red
when either was. The catalogue's `random-order` entry holds its steps, token for token: it runs
the suite, so the security-marked modules, and is security posture. It is never reused
(`NON_REUSABLE`): a green is about tonight's orders alone. Both orders write the shard's ids
(`--shard-ids`), and `shard-union` holds them to `planned-ids-random-order`, the planned suite
collected on the leg's own runner and Python, so the leg runs every test the suite runs; the
file holds the later order's collection, since a seed only reorders what was collected.

Each entry keeps its seeds beside its inputs (`ran --seeds`), never among them: a red order is
the tree's, so a green on the next night's seeds is no changed input. A red stays open, as the
security corpus's does, until one of its inputs changes, and the leg's issue
(`breadth: random-order is red`) names the seeds of its red shards. Each shard uploads, whatever
its result, `random-order-<shard>`: every worker's order file for each seed, the state-leak
report for each seed (taken before any fence restored), and the shard's file list. A failing
worker's own sequence is its order file. Under `--dist worksteal` a worker runs a share of the
seed's order and then tests it steals from the tail of another's, so
`pytest -n 0 tests --shard-list <shard.txt> --random-order-seed <seed>`, which reruns the
seed's whole order in one process, runs no one worker's sequence.

## The release manifest

`tools/ci/release_manifest.py` (#3911) records every artifact a release built and smoked, in one
manifest, before anything publishes. The release's build writes and verifies it, in
`release-artifacts.yml`'s `manifest` job, on every release tag and every weekly rehearsal; the
release's approval and its publish phase will read it.

- **What it holds.** Each staged file's sha256 and size, by its path in the staged folder; each
  image's per-platform digests and its multi-platform index digest, the sha256 of the index bytes
  the registry stores; the full ref and the commit it was built from; and its own digest, the
  sha256 of every other field in canonical form (sorted keys, no spaces, UTF-8). The attestation
  manifests a BuildKit build adds beside each platform's are covered by the index digest, and are
  not platforms.
- **What built each artifact, as it ran** (#4585's step 7). One build per job that built an
  artifact: the runner image its set-up log reported (`Image` and `Version`) and the one the image
  itself names (`ImageOS` and `ImageVersion`), both kept since the two versions can differ on one
  runner (rehearsal run 40028736768's desktop-linux job); every action the job
  downloaded, nested ones included, with the SHA each resolved to, read from that same log rather
  than from the workflow text; the base images that ran, by digest (an image's from the SLSA
  provenance BuildKit attached to each platform, with the workflow containers the job pulled); the
  Python, uv, Node and npm versions it used (an image's build names none, its bases being its
  toolchain); and the artifacts it built. Each job writes its part as its last step
  (`tools/ci/build_identity.py record`); the manifest job finds that job in its own run by the
  runner's name and reads its set-up log through the API with `actions: read`
  (`build_identity.py collect`). The write refuses a build missing either runner image version, an
  action with no SHA, an image's build with no base digest, a file's build missing a tool, and an
  artifact no job or two jobs claim.
- **What it refuses to stage.** A symlink, a special file, a file or folder it cannot read, an
  empty folder, a hidden name, and a name with a space or a control character, each by name, never
  skipped: a release publishes what the folder holds. An index that is not a multi-platform index,
  that names a platform twice or none, or that spells a platform with anything but lowercase
  strings, is refused too.
- **Reading it back.** Every reader names the approved digest. A manifest whose bytes are not
  canonical, that repeats a key, that does not hash to its own digest, or that is not the approved
  one is refused.
- **`python -m tools.ci release-manifest write --staged <folder> --image NAME=INDEX_FILE ... --ref
  <full ref> --commit <sha> --builds <file> --out <file>`** writes it outside the folder it
  describes, and prints `digest=` to the step's outputs. Inside is decided by the folder's
  identity, not the spelling of the path, so neither a symlink into the folder nor a case variant
  of its name on a disk that ignores case puts the manifest in it.
- **`verify --manifest <file> --digest <approved> --staged <folder> --image NAME=INDEX_FILE ...`**
  names every missing, extra and changed file and image, and exits 1 on any.
- **`compare --manifest <file> --digest <approved> --select <glob> ... --published <file>`** holds
  an already-published target against the manifest before anything is written to it. The target
  is a JSON object of what it lists, each object's name (a file's basename, an image's name) to
  its digest (PyPI's bare hex is read as sha256). An object the selection names and the target
  lacks is created, an identical one is resumed, and a different one, or one the target holds that
  the selection does not name, fails with nothing to create or resume. A moving tag (`:latest`,
  `:X.Y`) names an earlier release on purpose, so the target lists only the digest of the tag that
  names this release.

Every verb exits 2 on input it cannot rely on (a file it cannot read, JSON it cannot parse, a path
it cannot write), with no `-h` and no option given twice, as `verdict` does, and runs as `python3
-I tools/ci/release_manifest.py` from a trusted job's checkout. `tests/test_ci_release_manifest.py`
holds every refusal to a row naming its reason, beside a control that passes.

## Supply chain

The actions, images and tools the workflows and Dockerfiles fetch are named exactly, so a run
tests the same inputs on a day nothing in the repository changed, and every job holds only the
token permissions it uses. The rules live in `tools/ci/supply_chain.py` (with the shell reader
in `tools/ci/shell_installs.py`), the policy they read in `tools/ci/supply-chain.toml`, and
`tests/rails/test_ci_supply_chain_contract.py` holds each rule as a row with planted violations.

### What the check reads, and what it does not

It reads every workflow (`.yml` and `.yaml`), every local action's `action.yml` (its steps
are judged as if they ran in the job that calls it), every Dockerfile or Containerfile in the
tree whatever its spelling, `.github/dependabot.yml`, `.nvmrc`, the root `package.json` and
`package-lock.json`, each image's npm manifest and lock under `deploy/docker/`, the policy, and
every shell script in the tree (`.sh` or `.bash` in any case, or a file with no suffix whose
first line opens a shell, such as a git hook). A workflow or action file that does not parse
fails (`workflow-yaml`), and that rule cannot be waived.

A step's `run:` is read, and so is each repository script it runs as `sh <path>`, under the
step's rules, and each script that script runs the same way, to a depth of four. `sh <path>` is
the one way to run a repository script, so every script a step runs is read: the path is a
repository path (`$GITHUB_WORKSPACE/` in front is the checkout's root, and `./` or `//` in it is
read in normal form), and a wrapper the reader already takes off (`sudo`, `env`, `timeout`, an
assignment, `uv run` and its options) runs the `sh <path>` behind it. Every other way of running
one fails, and none is skipped, in the step's text and in every script it reads: another shell or
a shell option (`bash x.sh`, `sh -e x.sh`, `bash -o pipefail x.sh`), `.` or `source`, a bare path
(`./x.sh`, `tools/ci/x`, `.githooks/pre-push`), a shell fed a script on its standard input (a
pipe, `< x.sh`, a here-string), an interpreter handed code that names one (`python3 -c`, a heredoc
`python3` reads), and a command that names one without running it the check knows of (`cp x.sh`;
`shellcheck`, `chmod`, `git`, `test` and the like only read it). So does a script the tree does
not hold, one named through a variable the check cannot read (`$ROOT/x.sh`), a file outside the
repository (`sh /tmp/x.sh`), a file a download in the same text wrote (`curl -o f …; sh f`), and
a script past the depth or one already being read. A shell that only reads its script (`sh -n`)
runs nothing in it, a heredoc a shell reads is read as its lines, and a system file sourced
(`. /etc/os-release`) is not the repository's.

It does not read, and review holds instead:

- **compose files.** The only images they name are the project's own GHCR images, which
  `clean-machine-walkthroughs.yml`'s `v3-container` pulls at `latest` on purpose: the
  published image is what that job tests.
- **`[build-system] requires`** in a `pyproject.toml`. The root pins its build backend exactly,
  and `tests/test_verify_wheel_contract.py` holds it equal to `uv.lock`;
  `packages/personalclaw-client-py` does not yet.
- **The pre-commit hook and Nix.** No workflow runs the hook. `flake.lock` locks nixpkgs by rev
  and hash, and `nix/pins.toml` pins each wheel by sha256.
- **What an exact pin brings with it.** An exact version names the tool, not its dependencies:
  without a lock or hashes the dependency tree resolves on the day. That is true of
  `pip-audit`'s tree, PyInstaller's hooks, and installs of this project itself (editable, as a
  uv tool, or the client package). Exact top-level pins are the boundary this check
  holds; transitive pins come from a lock (`uv sync --locked`, `npm ci`,
  `pip install --require-hashes -r`).
- **Whether a comment's digests are the image's.** The contract runs offline: it requires the
  comment above an image to name the tag the line pins and each platform's own digest, but
  does not ask a registry whether those digests are that tag's.
- **A download run in a later step.** A download piped into a shell or an interpreter, given
  as `<(curl …)` or as `sh -c "$(curl …)"`, fails; a file downloaded in one command and run in
  another (`curl -o x.sh … && sh x.sh`) is not traced.

### How to pin

- **An action:** a full commit SHA with its exact release as a comment,
  `uses: owner/repo@<40-character sha> # vX.Y.Z`. A bare `# v7` fails. A `docker://` action is
  an image and is pinned as one.
- **A workflow's container or image:** a tag with the digest of its linux/amd64 manifest, and a
  comment above it naming that platform and the index the manifest belongs to:
  `# ubuntu:26.04, the linux/amd64 manifest of index sha256:<index>`. Pinning the index digest
  itself fails, because an index is not any one platform's image.
  The line must hold the whole value: an image behind a trailing comment, a `${{ }}`
  expression or a flow mapping fails, and so does a `services:` image. setup-buildx's BuildKit
  image (`driver-opts: image=`) is judged the same way.
- **A Dockerfile base:** a tag with its index digest on the `FROM` line, and a comment above it
  that names the tag and gives each platform's own manifest digest
  (`#   linux/amd64 sha256:<digest>`), none of them the index. Write the base on the `FROM`
  line: a base named through an `ARG` fails, because Dependabot cannot move it, and a build
  that overrides a base `ARG` with `--build-arg` or `build-args` fails too. `COPY --from=` and
  `RUN --mount=from=` an image are judged as bases. Dockerfiles are read wherever they are in
  the tree, under any spelling (`Dockerfile-dev`, `app.dockerfile`).
- **An image a `run:` step fetches:** `docker run`, `pull` and `create`, and the `FROM` of a
  build fed on stdin, are judged as workflow images. The project's own GHCR images, and an
  image an earlier step of the job builds with `--tag`, are what those jobs test, so they pass.
- **An install a `run:` or `RUN` performs:** an exact version (`tool==X.Y.Z`, `pkg@X.Y.Z`), a
  lock (`npm ci`, `uv sync --locked`, `pip install --require-hashes -r`), or this project
  itself (a path in the checkout, a built wheel, or `personalclaw`). A URL, a VCS reference or
  a `$VAR` is never this project. A version through a variable counts only when the step's,
  job's or workflow's `env:`, or the Dockerfile's `ARG` default, gives it a literal exact value.
  `npm install` in a workflow, `npx` of a package neither pinned nor installed by an `npm ci`
  earlier in the job (from the checkout's root, for the root lock) or in the Dockerfile stage
  (of a lock that stage copied in from the build context before it), `uvx`, `pipx run` and
  `uv run --with` without a version, `uv pip sync` without hashes, and installers of other
  ecosystems (`go install`, `cargo`, `gem`, `pnpm`, `yarn`, `bun`) all fail. The command is
  found wherever the shell would run it: after `if`/`then`/`do`, inside `( )`, `{ }` and
  `$( )`, behind `sudo`, `env`, `timeout`, `xargs` and the like, in `sh -c "…"`, behind a
  Dockerfile `RUN`'s flags, in its exec form and in its heredocs. A `#` comment after a pin is
  not part of it.
- **An image's npm tools:** a `package.json` under `deploy/docker/` (today
  `deploy/docker/agent-clis/`, the gateway image's four agent CLIs) pins each tool exactly, with
  no range, under `dependencies` and no other field (`npm ci` installs optional and peer
  dependencies too). The `package-lock.json` beside it must have been generated from it: the
  same names at the same versions in the manifest, in the lock's root and in the lock's
  `node_modules` entries. Every package that lock holds, direct or deep in the tree, is fetched
  from the npm registry under a sha512 integrity. A Dockerfile stage copies the pair in from
  the build context and runs `npm ci` after the copy, in that stage. No install anywhere else
  (`npm install -g`, `npm i`, `npm add`, a pinned one included, in a workflow or a Dockerfile)
  fetches one of the tools that manifest names. To move a tool, edit its pin and run
  `npm install --package-lock-only` in that folder, or take Dependabot's PR, which does both.
- **A download a shell or an interpreter runs** fails: piped into `sh`, `bash`, `python` and the
  like (through `tee` or `sudo -E` too), or given as `<(curl …)` or `"$(curl …)"`. The one
  allowed is the public installer, `https://personalclaw.dev/install`, piped into `sh` by
  `tools/ci/install_smoke_served.sh` when `breadth.yml`'s install-smoke step that tests the
  served installer runs it itself, and nowhere else: not in that step's own text, not in another
  script it runs, not through another script, not from a copy of the script. The staged
  installer, `deploy/website/install.sh`, is the artifact the staged step tests: when that step
  runs `tools/ci/install_smoke_staged.sh` and that script runs it, its uv bootstrap from Astral's
  installer and its install of this project at a floor are its subject, and every other line of
  it is read as any script is; from any other step, through any other script, or as a copy, it
  is read as any script is. The bootstrap holds only while the file still names Astral's
  installer.
- **A setup action:** a literal exact version or the file that names one, never `check-latest`
  (anything but a literal `false` fails) and never an expression. setup-qemu names its binfmt
  image by digest; setup-buildx names an exact buildx release, and its BuildKit image by digest
  in `driver-opts`. A setup or install action the check does not know fails until its version
  inputs are added to `SETUP_INPUTS` (or `INSTALLS_BUNDLED`, for one whose pinned release
  bundles its tool) in `tools/ci/supply_chain.py`.
- **Node:** every Node that CI or an image runs is on a line `[node_end_of_life]` dates, before
  that date: `.nvmrc`, each setup-node version, and every `node:<tag>` image a workflow, an
  action or a Dockerfile names. A setup-node `node-version-file` other than `.nvmrc` fails, and
  so does an OS package's `nodejs`, whose line is the distribution's. The dates come from the
  Node.js release schedule. Add a line when the repository moves to it, and leave past lines
  in place.

Other OS packages (`apt-get`, `apk`, `brew`) are outside the install rule. They come with the
image the job runs in, which is pinned by digest, or with the runner image, which every job
logs in its Runner identity step.

### How a refresh arrives

Dependabot's version updates for Python, npm and actions wait a week after a release before
proposing it (`cooldown: default-days: 7`), so a broken or compromised release has time to be
noticed and pulled first. The wait never delays a security update. The `docker` block keeps
Dependabot's three-day default, because a base-image bump is how the images' OS security fixes
arrive, and images have no separate security-update path.

- **Actions:** Dependabot's `github-actions` ecosystem, weekly. Its PR moves the SHA and the
  comment together.
- **Python dependencies:** Dependabot's `pip` ecosystem, weekly, in one `python-deps` group. A
  PR that moves a `[project]` constraint leaves `uv.lock` stale and fails `uv sync --locked`, so
  re-lock it on the branch (`uv lock`, with the uv CI pins) or close it. Each upper bound
  `pyproject.toml` holds on purpose has an ignore at its own major, and
  `tests/test_dependency_bounds.py` holds the two lists equal. The `uv` ecosystem would rewrite
  the lock with the constraint, but Dependabot's updater runs a uv older than
  `[tool.uv] required-version`, which refuses it.
- **The build backend:** `[build-system] requires` pins setuptools exactly, equal to the
  setuptools `uv.lock` resolves (`tests/test_verify_wheel_contract.py`). Dependabot ignores it,
  since it would move the pin without the lock. Bump both in one commit: edit the pin, then
  `uv lock --upgrade-package setuptools` with the uv CI pins.
- **Dockerfile bases:** Dependabot's `docker` ecosystem for `deploy/docker/`, weekly. It moves
  the `FROM` line. In the same PR, refresh the tag and the platform digests in the comment
  above it by hand, since Dependabot does not read comments, and the contract fails a comment
  that no longer names the tag. Node has a group of its own and no major moves: a Node line
  change also moves `.nvmrc`, `release-image-tooling.yml`'s base (held equal to
  `Dockerfile.web`'s) and `[node_end_of_life]`, so it is a reviewed edit. The contract's plants
  read today's pins from the tree, so a refresh needs no test edit.
- **The gateway image's agent CLIs:** Dependabot's `npm` ecosystem for
  `deploy/docker/agent-clis/`, weekly, in one group, so the adapters and the CLIs they drive
  move together. Its PR moves the exact pins and the lock in one change, and
  `docker-single-container.yml` builds and smokes the image on it: the smoke resolves each of
  the lock's commands in a login shell, and the image build fails on a command whose link does
  not reach an executable. The contract fails a folder under `deploy/docker/` with an npm
  manifest that no Dependabot `npm` entry watches, an entry for one that waits less than seven
  days or moves its packages in anything but one `patterns: ["*"]` group, and a workflow whose
  `paths:` run it on a change of a Dockerfile but not on a change of a lock that Dockerfile
  installs.
- **Workflow containers, QEMU, BuildKit and buildx:** a reviewed edit, by hand. The release's
  QEMU and buildx steps (`release-artifacts.yml`'s `images` job) are also run by
  `release-image-tooling.yml`'s `pin-smoke` job on every pull request that changes
  `release.yml` or `release-artifacts.yml`: a two-platform build of a two-line image on the
  images' own node base, with no permissions, no other action and nothing pushed. The contract
  holds its pins and base equal to the release build's and `Dockerfile.web`'s, and requires it
  to build every platform the release builds. Its `layout-smoke` job runs the release's layout
  path on the same pins: the image built to an OCI layout with the release's attestations, each
  platform loaded from it and run, and the layout copied with the release's own `skopeo copy
  --all --preserve-digests`, to a second layout on the runner that must hold the same index. The
  contract holds it to the same pins, no permission, and copies only to a file on the runner. `image-pins.yml` asks each workflow container's
  registry every Monday, anonymously, what its tag names now (`python -m tools.ci image-pins`
  runs the same check locally). It names each pin whose tag has moved, with the new digest for
  the pin and the new index for its comment, and keeps one "Pinned images to refresh" issue
  open on `main` until the refresh merges. The refresh moves every copy of each old digest in
  the tree, not only the pin: the catalogue, a step that records the image and the rails'
  plants can hold one too (#5273 moved five files for two pins). A pin whose comment names no platform and index, an
  unknown registry, or a tag or platform that is gone fails it too. It holds `contents: read`
  and `issues: write`, and moves no pin itself.
- **Tools named by hand** (`pip-audit` in the audit job, `wheel` in the gateway image,
  `pyinstaller` in the desktop builds): nothing refreshes them yet. Moving one is a reviewed
  edit of its line. `packaging` in `release.yml`'s notes step is named at the version
  `uv.lock` holds, and a test holds the two equal: a Dependabot python-deps PR that moves
  packaging in `uv.lock` is red until that line follows.
- **The pin check:** `action-pins.yml` asks the GitHub API, once per release, which commit
  each pinned tag names, following an annotated tag to its commit. It reads the workflows and
  the local actions. It fails on a SHA that is not its tag's commit, a tag that is gone, and a
  pin with no release comment. It runs weekly, for a tag that moves upstream, where an answer
  the API could not give fails too; and on every pull request that changes a workflow, a local
  action or the check, where such an answer is a warning, so an API blip cannot turn the pull
  request red. It holds `contents: read`, and uses its token for reads only.

### Least privilege

- No `write-all`, and every job declares its permissions or inherits declared ones. A job that
  calls another repository's workflow declares them too; a call to a local workflow is exempt,
  because that workflow's own jobs are read here.
- A write is granted on the job that uses it, never at the top of a workflow, where it would
  reach every job added later.
- Every write a job holds, and every checkout that keeps its credentials, is an
  `[[audited_write]]` in the policy: the workflow, the job, the permission, the steps that use
  it and why. A write that is not listed fails, and so does an entry that matches nothing. A
  credential entry names each checkout that keeps its credentials by a `name:` no other checkout
  in the job shares. A local action's checkout may never keep them, since it runs in every job
  that calls it. Every checkout sets `persist-credentials: false` today, so none is listed.
- No workflow-level, job-level or job-container `env:` hands the job token or a secret to every
  step, in any spelling (`secrets.X`, `secrets['X']`, `toJSON(secrets)`, any case); set it on
  the step that calls the API. An `env:` that is an expression fails, since its variables cannot
  be read.

The audited writes today:

| Workflow | Job | Permission | Used by |
|---|---|---|---|
| `dco.yml` | `dco` | `pull-requests: write` | the commit-provenance remediation comment |
| `pr-feedback.yml` | `lint-help` | `pull-requests: write` | the lint remediation comment |
| `release.yml` | `pypi`, `pypi-client` | `id-token: write` | Trusted Publishing to PyPI |
| `release.yml` | `images` | `packages: write` | pushing the two images to GHCR |
| `release.yml` | `notes` | `contents: write` | creating the GitHub Release |
| `release.yml` | `attest` | `id-token: write`, `attestations: write` | build-provenance attestations |
| `breadth.yml` | `report` | `issues: write` | one issue per red nightly leg |
| `flake-report.yml` | `report` | `issues: write` | the one "Weekly CI health" issue |
| `settings-report.yml` | `report` | `issues: write` | the one "Repository settings drift" issue |

### The dependency audit

`tools/ci/audit.py` audits the graphs core ships, read from its locks and never from a fresh
resolution, so a lock pinned to an older vulnerable release is reported even when a fresh
resolution would be clean:

- **Python:** `uv export --locked` once per shipped graph, with the extras its artifact
  installs (`PYTHON_GRAPHS`: the wheel, the gateway image's `uv export` in
  `deploy/docker/Dockerfile.backend`, the desktop bundle's `uv sync` in `release.yml`),
  audited by `pip-audit --no-deps --disable-pip`. `tests/test_ci_audit.py` reads each graph's
  extras from the file that installs them.
- **npm:** `npm audit --package-lock-only` on the root `package-lock.json`, every workspace
  and build tool included, since build tools run in CI and shape the artifacts, and on each
  image's own lock under `deploy/docker/` (today the gateway image's agent CLIs), from its
  folder with `--prefix`, as a graph of its own: a waiver names that lock as its `file`.
  `tests/rails/test_ci_audit_tree.py` holds every lock under `deploy/docker/` to be one.

It fails on a tool that did not run (no report, or one that cannot be read), on a high or
critical finding no live waiver covers, and on a waiver that is expired, more than 30 days
out, missing a field, or matching no finding. pip-audit reports no severity, so a Python
finding gates as a high one would. Every other finding is listed in the job summary. It runs
as `ci.yml`'s `audit` job, gated on `tree-check` like every job a tested tree skips, and as
breadth's nightly `audit` leg, whose red opens the leg's issue. A new advisory on a dependency
no pull request touched therefore stops merging until it is fixed or waived. The release half,
refusing an unwaived critical, is #3911's.

Waivers are `[[waiver]]` entries in `tools/ci/audit-baseline.toml`, in the form of the
exceptions below. Not audited here: the apps' and the website's locks, which their own
repositories audit, and the images' OS packages, which need a scanner of their own.

### Dependency review

`.github/workflows/dependency-review.yml` runs on `pull_request_target`, so GitHub takes it from
`main` and a pull request cannot edit what reviews it. It holds `contents: read` and no
checkout: `actions/dependency-review-action` reads the pull request's base and head commits from
the event and asks the dependency-graph API to compare them, and it fails on a dependency the
pull request adds, or moves to, with a known vulnerability. It is not a required check; with
CodeQL's default setup, which lives in the repository settings out of a candidate's reach, it
becomes an input of the one required `merge-verdict` (#3910). The apps and the website run the
same workflow.

### Nothing throws an exit status away unseen

The contract row `nothing-throws-an-exit-status-away-unseen` (`tests/ci_masking.py`) finds
every `|| true`, `|| :` and `continue-on-error` in a workflow, a Dockerfile or a script under
`scripts/`, and requires each to be named in its list with a reason. An entry that matches
nothing fails, and a tolerated pytest step must have its outcome read back by a later step of
its job. It reads the spellings the tree uses; `set +e`, `|| echo` and the like are review's
to catch.

### Exceptions

An `[[exception]]` waives exactly one violation of one rule in one file, while a pin cannot hold
yet. It names `file`, `reference` and `scope` exactly as the row prints the violation (for a
workflow step, `<job> <step>: <problem>`; a repeat of the same problem in a file gets ` (2)`),
with an `owner`, a `reason` that says what ends it, and an `expires` date at most 30 days after
the day it is written. Every field but `expires` is a non-empty string. Only the pinning rules
may be waived (`action-sha`, `image-digest`, `run-install`, `setup-floating`, `node-eol`); a
write is audited, never waived, and a file that does not parse is never waived.

An expired exception, one more than 30 days out, one that matches nothing, one that matches
more than one violation, and one missing a field all fail. To renew one, set a new expiry and
say in the reason what has changed. To end one, fix the reference and delete the entry: an
exception left behind after its violation is gone fails as matching nothing.

The day an exception expires, or a Node line reaches its end of life, CI goes red with no
change to the repository. That is the policy: a dated exception is a deadline, and a Node past
its end of life gets no security fixes. Renew or fix before the date.

There are no exceptions today. The contract's exception rows plant their own exception beside a
planted install, so they hold whether or not the policy has one.

## The settings report

`.github/workflows/settings-report.yml` runs `python -m tools.ci settings-report` every Monday at
07:41 UTC, and on a dispatch. It reads the live settings of core, the apps and the website and
compares them with `.github/settings-expected.json` and with each repository's own
`.github/rulesets/*.json` on its default branch. It sends GETs only and writes no setting. Any
difference fails the run, and on `main` it opens or edits the one open issue titled "Repository
settings drift" that the workflow's account opened, one line per difference, and closes it once
a run finds none.

What it reads, for each repository:

| Setting | What it compares | Read with |
|---|---|---|
| each ruleset | target, enforcement, conditions and rules, against the file of the same name; a ruleset with no file, or a file with no ruleset, is drift | the job token |
| bypass use | the week's rule suites whose result is `bypass`; each one fails unless `allowed bypass` names its actor and ref prefix (none does) | `SETTINGS_READ_TOKEN` |
| `classic protection` | the default branch's classic protection, or `null` for none | `SETTINGS_READ_TOKEN` |
| `merge methods` | merge commits, squash and rebase | `SETTINGS_READ_TOKEN`, through GraphQL: the repository answer shows them only to a caller that may push |
| `security` | secret scanning, push protection, Dependabot security updates | `SETTINGS_READ_TOKEN` |
| `dependabot alerts` | on or off | `SETTINGS_READ_TOKEN` |
| `dependency graph` | on (its SBOM answers) or off; dependency review fails on every pull request without it | the job token |
| `codeql default setup` | its state, query suite and languages | `SETTINGS_READ_TOKEN` |
| `actions` | whether Actions run, the allowed actions, `sha_pinning_required` | `SETTINGS_READ_TOKEN` |
| `workflow token` | the default token's permissions, whether Actions may approve pull requests | `SETTINGS_READ_TOKEN` |
| `fork approval` | which outside contributors' runs wait for approval | `SETTINGS_READ_TOKEN` |
| `environments` | every environment: reviewers, self-review, wait timer, and the branches and tags it deploys from | the job token |

GitHub returns a ruleset's bypass list only to a caller with write access to that ruleset, so
the report compares a ruleset's `bypass_actors` only when an answer carries them, and otherwise
checks what a bypass list allows by its use: any bypass in the week fails. The list itself is
read back with the maintainer's own login when a ruleset is applied.

**The credential.** No workflow token can hold administration read. `SETTINGS_READ_TOKEN` is a
fine-grained token the maintainer mints for the three repositories with Administration and
Metadata read and nothing else, and keeps as a secret of the `settings-report` environment, which
only `main` may deploy to, so a branch's copy of the workflow cannot reach it (a repository
secret would reach every branch's runs). The report holds that environment's deployment policy
to `main` alone, and `tests/rails/test_ci_settings_report_tree.py` holds the workflow to its one
job, its schedule and dispatch, and the issue on `main`. A setting the report cannot read,
because the token is missing, expired or short of a permission, fails as `not read`, never as a
pass. Renew the token before it expires.

**Changing a setting.** Change the setting and its value in `.github/settings-expected.json` (or
the ruleset file) together, in a pull request that says why. A setting changed by hand alone is
drift the next Monday.

`tests/test_ci_settings_report.py` holds each row on planted API answers: a secret scanning,
push protection, CodeQL or SHA-pinning setting turned off, a dependency graph turned off, a widened workflow token, a changed
fork policy, a merge method turned back on, an environment opened to every ref or stripped of
its reviewer, an added environment, classic protection put back, a removed required check, a
required check bound to another App, an added bypass actor, a ruleset with no file, a deleted
ruleset, a bypass used in the week, and an answer the API refused, each fails; the unchanged
answers pass.
