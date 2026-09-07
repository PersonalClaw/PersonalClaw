# Scanner Testing — the Published Adversarial Corpus

The [threat model](threat-model.md) claims that community content is gated at one
chokepoint before it can reach the live skills tree. This document is the evidence
for that claim, and it is deliberately public: the corpus advertises exactly how the
gate is attacked, which is worth more than the obscurity it costs. A control whose
tests nobody can read is a control nobody can audit.

Two things are being tested, and they are not the same thing:

1. **The scanner refuses what it claims to refuse** — five attack classes, each with
   at least one test that fails if the corresponding control stops firing.
2. **The corpus itself is load-bearing** — weaken one control and a corpus test must
   turn red. A suite that stays green against a broken scanner is worse than no
   suite, because it certifies nothing while looking like it does.

Everything below is reproducible from a clean clone with no credentials, no network,
and no vendor SDK.

## How to run it

```bash
pip install -e ".[dev]"
python -m pytest -n 0 --no-cov tests/security/
```

`-n 0` is not required — the corpus is parallel-safe — but it keeps the
integrity-race output readable. To run one class:

```bash
python -m pytest -n 0 --no-cov tests/security/ -k invisible-char
```

The corpus also runs unattended: `.github/workflows/full.yml` carries a
`security-corpus` job that executes `tests/security/` plus the scanner unit tests on
every nightly `schedule:` run (07:00 UTC), on pushes to `main`, and on
`workflow_dispatch`. It is a separate job so a corpus regression is legible on its
own rather than buried in the full-suite matrix.

## What the corpus is made of

Cases live in `tests/security/corpus/<class>/<case>.json` — one directory per attack
class, one JSON file per case. The harness is
`tests/security/test_scanner_adversarial.py`.

A case is **inert data**, never a program. It describes a payload
(`files[].path` + `files[].contents`, or `variants[]` of script text) and names, in
`expect`, the refusal being asserted. The harness materializes the payload into a
`tmp_path` staging tree and feeds it to the real gate. Nothing under `corpus/` is
executable, and `TestCorpusIsComplete::test_corpus_payloads_are_inert` enforces that
— a non-JSON artifact or an executable bit reds CI.

`expect` is a key into the harness's `HANDLERS` table. A case whose `expect` has no
handler is a fixture nobody asserts on, and
`test_every_case_is_wired_to_an_assertion` reds. This is the corpus's vacuity floor:
an emptied class directory, an orphaned fixture, or a class that quietly stopped
being exercised fails loudly instead of collecting zero tests and reading like a
pass.

## The six attack classes

| Class | The attack | The control that must refuse it |
|---|---|---|
| `archive` | Zip-slip and absolute-path entries, traversal hidden mid-path (`assets/../../..`), and a case-collision pair (`setup.sh` / `SETUP.SH`) whose surviving twin differs by filesystem | `marketplace.py::_stage_files` and `marketplace.py::install_skill_files` both reject `..` and leading `/`; the case-collision pair must reach `DANGEROUS` on a case-sensitive *and* a case-insensitive filesystem |
| `integrity-race` | A source that serves clean bytes to the scan and malicious bytes to a re-fetch; a concurrent writer that rewrites the quarantine directory the moment the scan is done with it; an in-process writer that mutates the payload between scan and commit; bytes edited on disk after a clean install | `marketplace.py::install_scanned` commits the scanned in-memory payload and never re-fetches; `install_skill_files`'s per-file scan catches the in-process mutation; `marketplace.py::verify_skill_integrity` catches the post-install edit against the `_write_lock` baseline |
| `verdict-evasion` | Whitespace and flag-order variants of `rm -rf /`; `base64 -d \| sh`; the read-credentials-then-egress pipeline spread over three lines; **trust-tier laundering** (the same malware declaring `builtin`); `force=True` against the non-overridable floor | `supply_chain.py::_scan_script` patterns, the proximity heuristic in the same function, and `supply_chain.py::SkillScanner._aggregate` — which must never downgrade `DANGEROUS` for any tier |
| `invisible-char` | A bidi override in manifest prose and in a script; a zero-width space inside `rm` that takes the pattern rules out of play | `supply_chain.py::SkillScanner._scan_invisible` — bidi is `DANGEROUS`, zero-width is `WARNING`, and a community install must not proceed on the warning unconfirmed |
| `degenerate-manifest` | A dangerous script padded past the scanner's per-file read cap; frontmatter missing, unclosed, or carrying a name outside the allowed charset; a payload with no `SKILL.md` at all | The oversized blob is skipped by `SkillScanner.scan` (by `supply_chain.py::_MAX_FILE_BYTES`, deliberately — the walk does not read unbounded files) and refused by the uncapped commit-side gate in `install_skill_files`; `marketplace.py::_validate_skill_md` rejects the degenerate manifests |
| `baseline-tamper` | The packaged command denylist itself: a self-consistent rewrite of `baseline_denylist.json` (patterns *and* `sha256` both changed), a digest that disagrees with its patterns, an honest digest over zero patterns, an in-process `.clear()` of the live list, and the no-trusted-source-left state where the live list, the import-time snapshot and the file are all unverifiable at once | `security.py::_read_packaged_baseline` raises on a bad digest or an empty list; `baseline_denied_command_patterns()` heals the live list from the verified snapshot and, when nothing verifies, enforces the *union* rather than shrinking; `verify_baseline_denylist()` compares the file against the fingerprint captured at import and refuses to adopt a divergent one — every branch writing `baseline_denylist_reasserted` or `baseline_denylist_tamper_attempt` to the SEL |

The oversize row is the one worth reading twice. Two controls disagree about the same
bytes: the quarantine walk skips the blob, the commit-side scan refuses it. The
corpus pins **both halves**, so removing the second one — the only control that
actually stops it — reds immediately instead of leaving a size-gated bypass.

`baseline-tamper` is the odd one out and deliberately so: the other five attack an
incoming *artifact*, this one attacks the *denylist that judges it*. Added by **SH-7**
alongside the mode-independence matrix in `tests/security/test_mode_independence.py`,
which proves no approval mode — `default`, `auto`, `yolo`, `acceptEdits` — and no trust
simulator can let a baseline-matched command run. That matrix is worthless if the
baseline can be quietly shortened underneath it, so the two ship together.

Every `baseline-tamper` case asserts the same **triple**, and all three legs matter:

1. **Detected** — `verify_baseline_denylist()` reports `file_verified: false`, or the
   real `_read_packaged_baseline()` raises.
2. **Audited** — the SEL carries exactly one `baseline_denylist_tamper_attempt` (or
   `baseline_denylist_reasserted` for the self-heal case) with the expected
   `metadata.reason`. A tamper that raises but is never logged is invisible.
3. **Still enforcing** — the commands the case names in `variants[]` are *still* refused
   by `denied_command_reason()`, and a benign control is *still* allowed. Without the
   benign half, a build that refused everything would pass leg 3.

The class also drives the tamper through the **real** `_read_packaged_baseline` by
rooting `security.resources` at a temp copy of the data file, rather than substituting a
reader that re-implements the parse. The installed
`src/personalclaw/baseline_denylist.json` is never written to — the copy lives under
`tmp_path`, and `TestBaselineTamperClass::test_the_tamper_never_touches_the_installed_data_file`
reds if a case ever leaks into the checkout.

## The scanned-bytes == installed-bytes invariant

The sharpest case is not a pattern; it is a swap. If content can change between the
moment it is scanned and the moment it is installed, the verdict describes bytes that
no longer exist.

`install_scanned` closes this by construction: it fetches once, stages to a
quarantine directory, scans that directory, and then commits **the same in-memory
payload it staged**. It never re-reads the staging tree and never re-fetches.

The corpus proves it rather than restating it. The harness wraps
`supply_chain.py::scan_dir` (which `install_scanned` resolves at call time), takes a
per-file sha256 digest of the staging tree *as the scanner sees it*, and compares that
map to a digest of the installed tree afterwards. The assertion is **map equality**,
not "the install succeeded". Alongside it, the fetch counter must read exactly `1`.

The swap is performed from a second thread, joined so the test stays deterministic; a
swap thread that hangs or throws fails the test rather than silently doing nothing.
The three windows an attacker could aim at:

- **Re-fetch.** The hostile source serves malware to fetch #2. The install never makes
  fetch #2, so there is nothing to serve.
- **Quarantine rewrite.** The attacker rewrites the staging tree right after the scan
  reads it. The commit does not read the staging tree, so the installed digest still
  matches the scanned digest.
- **In-memory payload mutation.** The attacker mutates the payload list itself between
  scan and commit — the only window left, and one that requires already executing code
  in the process. The commit-side per-file scan refuses it, so nothing lands.

## Proving the corpus is load-bearing

`TestCorpusRedsOnAWeakenedScanner` weakens one control per class **in process, via
`monkeypatch`**, and asserts the matching corpus rail now fails. The shipped scanner
is never modified, so the demonstration is permanent and reproducible rather than a
branch someone has to remember to delete.

Each row below is also the recipe for reproducing the same result by hand — edit the
file, run the class, watch it red, then restore.

| Weaken this | And this reds |
|---|---|
| Drop `destructive_root` from `supply_chain.py::_DANGEROUS_SCRIPT` | `verdict-evasion/destructive-root-variants` |
| Empty `supply_chain.py::_INVISIBLE_CHARS` | `invisible-char/bidi-override-in-manifest` |
| Make `marketplace.py::_validate_skill_md` return no errors | `degenerate-manifest/missing-frontmatter` |
| Make `marketplace.py::_stage_files` *skip* an unsafe path instead of raising | `archive/zip-slip-parent-escape` |
| Have the commit write anything other than the scanned bytes | `integrity-race/refetch-swap` |
| Rebind `security.py::_BASELINE_SHA256` to the tampered file's own digest | `baseline-tamper/self-consistent-file-rewrite` |
| Make `security.py::_note_baseline_tamper` always return `False` | `baseline-tamper/snapshot-and-file-both-rebound` |

The last two are chosen for a reason. The archive weakness is the plausible one — a
gate that quietly drops the bad entry and installs the rest, which looks like
robustness and is a bypass. And the integrity mutation is **not malicious**: it
appends a comment. Byte equality catches a post-scan substitution even when the
substituted bytes would pass the scan, which a verdict-only assertion never could.

The two `baseline-tamper` rows split detection from audit on purpose. Rebinding the
fingerprint is the exact bug the class exists to catch — a module that re-derives its
fingerprint from the file it is verifying will happily adopt a self-consistent rewrite.
Suppressing `_note_baseline_tamper` leaves detection *and* enforcement intact and only
removes the audit trail; that is still a failure, so the rail must red on it rather than
only on a shrink.

## Execution-reachability scoping of the `DANGEROUS` band

The `DANGEROUS` rules match a string *shape* and are blind to whether anything executes
that string. A bundle whose test fixture holds a real attack string — the string that
proves its input validation refuses the attack — was therefore judged identically to a
bundle that runs it. `DANGEROUS` is terminal and non-consentable, so the fixture
permanently blocked the install: two first-party bundles could not be installed at all,
while a bundle that never tested its validation shipped clean. The rule as written taught
authors to delete their adversarial tests or obfuscate the strings until the scanner
stopped recognising them.

`supply_chain.py` closes that with a second pass that asks a narrower question: **can
this matched literal be executed at all, anywhere in this bundle?** Only on a positive
answer of *no* is the finding re-scored — to `WARNING`, never lower, never dropped, with
rule, path, surface and evidence intact, so the user is still told and still consents.

The predicate is stated on the AST and **never on a filename**. That is the whole basis
of it: "skip test files" would create a place to park a payload, whereas "nothing here
can execute this" cannot — parking a payload requires something that executes it.
Renaming a payload `test_evil.py` therefore buys an attacker nothing, and the attack
table in `tests/security/test_scanner_reachability.py` asserts exactly that.

Five conjunctive clauses, default-deny. Fail any one — or fail to *evaluate* one — and
the finding stays `DANGEROUS`:

| Clause | What must hold | What it closes |
|---|---|---|
| **L1** literal, not code | The file parses as Python and *every* match of this rule lies wholly inside a `str` constant | A shell script (its text is its program); a payload in a comment; a second, live occurrence of the same shape that the scanner did not report |
| **L2** no execution sink | The file contains no `os.system`/`popen`/`exec*`/`spawn*`/`fork`, no `eval`/`exec`/`compile`/`__import__`, no `subprocess` call whose `shell` is other than a literal `False`, and every spawn site's argv[0] is a literal non-shell, non-interpreter, non-path program | The literal being handed to an interpreter — **including after being concatenated into a larger string**, which is why the clause is stated at module scope rather than on the literal's argument position |
| **L3** no export | No other module imports it, no sibling names its file or stem in a string, no script or config mentions it, `app.json` does not declare it | Another file lifting the literal out. Prose is excluded on purpose: a README saying "run pytest test_provider.py" is documentation |
| **L4** the graph is trustworthy | No `eval`/`exec`/`compile`/`__import__`/`importlib`/`runpy`/`pickle`/`marshal`/`ctypes`, no computed `getattr`, no `import *`, no `sys.path` mutation, and no Python or loader file the walk could not read — anywhere in the bundle | L3 is a static claim; a bundle that can rewrite its own import graph, or a file nobody could read, makes it worthless |
| **L5** importing it is a no-op | The module's top level calls nothing outside a small pure allowlist | The payload firing on import — unreachable is not never-imported |

A file that does not parse is reported as its **own** outcome
(`Reachability.UNPARSEABLE`), distinct from both `REACHABLE` and `UNREACHABLE`, and
treated exactly like `REACHABLE`. "We could not tell" must never render as "safe".
`scan_text` gets no scoping at all: a bare blob has no bundle around it, so
non-reachability cannot be proved and the default-deny answer stands.

Re-measure any checkout of bundles with:

```bash
PYTHONPATH=src:. python tools/measure_scanner_scope.py /path/to/PersonalClawApps
```

Every re-scored finding prints the clauses that granted it, so a downgrade nobody can
check is not possible.

### Proving each clause is load-bearing

`TestRedsOnAWeakenedCheck` in `tests/security/test_scanner_reachability.py` neuters one
clause at a time, in process via `monkeypatch`, and asserts the matching rail fails.
Each row is also the recipe for reproducing it by hand.

| Weaken this | And this reds |
|---|---|
| Replace `supply_chain.py::_scope_by_reachability` with the identity | the two blocked bundle shapes become uninstallable again |
| Widen L1's `str_spans` to the whole file | `payload in a comment, which is not rescued` |
| Clear L2's `_FileFacts.sinks` | `payload built at runtime and then executed`, and `payload passed straight to os.system` |
| Stub L3's `_BundleReach._referenced_elsewhere` to `None` | `inert payload module that the provider imports` |
| Clear L4's `_FileFacts.dynamic` | `inert payload module in a bundle that imports importlib` |
| Clear L5's `_FileFacts.top_level_calls` | `inert-looking payload module that runs code on import` |
| Report a parse failure as a parsed file with a whole-file span | the unparseable-file rail |
| Force `_BundleReach.decide` to answer `UNREACHABLE` | every `verdict-evasion` corpus case |

The L2 rows are the ones worth reading twice, and they are split for a reason. The
simple shape — the literal sitting in the sink's own argument list — is not the shape
that decides the design. The deciding shape is a literal that is *never* an argument to
anything: it is concatenated into a command at runtime and that result is executed. A
check that asked "is this literal passed to a sink?" clears that file. Asking instead
"can this file hand *any* string to an interpreter?" does not, which is why the clause
is stated at module scope and why both shapes are pinned separately.

## Residual risks

The corpus pins what holds. These are the gaps it also pins, honestly, so they are
auditable rather than invisible. They are accepted, not unnoticed.

- **An unreferenced module of pure inert data becomes consentable rather than refused.**
  A bundle can ship a file containing nothing but a literal attack string, referenced by
  nothing, and get `WARNING` where it previously got `DANGEROUS`. That is a real
  reduction in the floor for that one shape, and every route out of the file is closed by
  a clause and tested — but "nothing in *this* bundle can reach it" is not "nothing can
  ever reach it". The counterweight is measured, not asserted: the `DANGEROUS` band is a
  literal matcher, so the same attacker already gets **`clean`** from
  `P = "rm -" + "rf / "` and only **`warning`** from a live `os.system("rm -rf /")`. The
  capability given up is refusing the attacker who wrote the payload plainly *and* left
  it unreachable *and* did not obfuscate it — and even they get the finding, the rule id,
  the evidence, and a required click. Scoping does **not** address the false-negative
  side, and should not be read as doing so.

- **Zero-width splitting degrades `DANGEROUS` to `WARNING`.** A zero-width space
  inside `rm` defeats the destructive-root regex; the invisible-codepoint rule still
  fires, so a community install refuses without an explicit override. But a warning is
  overridable by design, so `force=True` installs it — and on a source the owner has
  declared `official` or `trusted`, `_aggregate` lowers that warning to `low` and the
  install proceeds. The corpus asserts the refusal *and* the forced install, and
  checks that `.pclaw-lock.json` records `verdict: warning`, so the override leaves a
  trace. Trust tiers are owner-declared provenance; a control that ignored them would
  make the tier meaningless.
- **A warning-band in-process payload swap.** The commit-side scan refuses only
  `DANGEROUS`, so an adversary already executing code inside the process could swap in
  warning-band bytes and break byte equality. This sits below the boundary — an
  attacker who can mutate a live Python list has no need of the skill installer. See
  the [limitations](limitations.md) note on an already-compromised host.
- **Static inspection is not containment.** The scanner reads text; it does not run
  anything in a sandbox. A payload that is dangerous only at runtime, or through a
  dependency it fetches later, is outside what any pattern gate can see.
- **Binary entries are not scanned.** A payload entry carrying `data` (bytes) has no
  scannable text; its provenance is the sha256 in `.pclaw-lock.json` and nothing more.

## Adding a case

1. Drop a JSON file in `tests/security/corpus/<class>/`. Required keys: `id` (prefixed
   with the class), `class`, `summary`, `expect`, and either `files` or `variants`.
2. Point `expect` at an existing handler, or write a new one and register it in
   `HANDLERS`. An unregistered `expect` reds — deliberately.
3. Run `python -m pytest -n 0 --no-cov tests/security/`.
4. If the new case reveals a real bypass, it is a finding: fix the control in the same
   change, or record it under **Residual risks** above with the reasoning. A corpus
   case is never weakened to make the suite green.
