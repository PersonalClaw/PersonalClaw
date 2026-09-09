# Atom audit protocol

The ordered procedure for establishing the **accurate** state of every atom in
`docs/roadmap/atomic/dag.json`, one plan at a time. The order is the owner's and is not
negotiable: understand, then observe the code, then validate as a user, then fix or file,
then record.

**The governing rule: observe the actual state, never believe documented proof.** An
execution log, a plan's task table and a `done` status are all *claims*. The proof is the
code on disk, a passing test, and the product's behaviour when a real person drives it.

## Picking the next plan

Read `docs/roadmap/atoms/verdicts.json`, then take the first plan code in `dag.json`
`plans[]` order that still has atoms with no verdict. Work its atoms in id order.

## Per atom, in this order

1. **Intent.** From the atom's `title` / `scope` / `done_when`, state what it means for the
   end user — not what it means for the codebase.

2. **Observe the code.** Grep and read for the symbols, files, tests and config its
   `done_when` names, and run its own tests:

   ```
   .venv/bin/python -m pytest <paths> -q --timeout=300 -p no:randomly --no-cov
   ```

   A path that pre-existed the work is TRIVIAL evidence, never strong — see
   `tools/audit_landed_atoms.py` on why a container earns no credit. A symbol, a make
   target or an env var had to be typed by the work; a directory did not.

3. **Validate as a user.** If the atom has any user-facing surface, drive it in a real
   browser against the running gateway (`http://127.0.0.1:10011`; a second fresh home runs
   on `10012`). Use only real interactions through `playwright-cli` — `goto`, `snapshot`,
   `find`, `click`, `fill`, `press`, `select`, `drag`. **No `curl`, no `fetch`, no scripted
   API calls, no `eval`.** A feature that works at the endpoint and not in the UI is not
   done.

   If the gateway is not answering:

   ```
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:10011/
   PERSONALCLAW_HOME="$PWD/.validate-home" PERSONALCLAW_AUTH_MODE=none \
     nohup .venv/bin/personalclaw gateway --no-open --port 10011 > /tmp/gwv.log 2>&1 &
   ```

   (That `curl` is a liveness probe, not feature validation — the distinction is the point.)

4. **Fix or file.** A gap fixable in roughly ten minutes: fix it on a branch off `main`
   (`feature-` / `bugfix-` / `improvement-<slug>`), one concern per branch, `git commit -s`
   **with hooks** (never `--no-verify`), push, and open a PR filling
   `.github/PULL_REQUEST_TEMPLATE.md`. Falsify every new test by breaking the fix and
   watching it red before restoring. Anything larger: open a GitHub issue carrying the
   expected behaviour, the observed behaviour, the reproduction as real UI steps, and the
   intended solution.

5. **Record.** Append a verdict to `docs/roadmap/atoms/verdicts.json`
   (`confirmed` / `partial` / `contradicted` / `unverifiable`) naming the evidence you
   **observed**, what you **drove** in the UI, and any PR or issue. Then:

   ```
   python3 tools/gen_atom_records.py
   .venv/bin/python -m pytest tests/test_atom_records.py -q --timeout=300 -p no:randomly --no-cov
   ```

   If the code contradicts `dag.json`'s status, say so explicitly in the verdict. `dag.json`
   is owner-maintained: **propose the flip, do not make it.**

## Discipline — each of these cost a real mistake

- **Read for a deliberate design decision before calling anything a defect.** Two
  "confirmed defects" in `OU-1` were both intentional and documented in the code: the
  4-step `STEPS` vs 5-step UI gap (the import step is deliberately not a stored resume
  point, because the importer is idempotent and keeps its own ledger), and the
  name-loss-on-reload (`user_name` **is** the `onboarded` route guard, so committing it
  mid-flow would release the guard). When a finding collapses, withdraw it plainly.
- **Never chain many shell commands in one call**, and never let one traverse `$HOME`
  broadly. A path reaching `~/.kirocrew` or `~/.aws` is refused by policy — it has fired
  three times in this campaign. Keep each call short and scoped to the repo.
- **zsh does not word-split unquoted parameters.** Pass literal paths; a variable holding
  several becomes one argument and the command reports a path that does not exist.
- Run from the repo root, and `cd` there in each call — the shell's directory does not
  reliably persist.
- **Never commit** `.validate-home/`, `.vh2/`, or any dev home. Never push to `main`. Never
  merge a PR unless the owner said to.
- `docs/roadmap/atoms/*.md` is **generated**. Never hand-edit one; the fact belongs in
  `dag.json` or `verdicts.json`. A stray `.md` added to that directory is treated as
  orphaned and deleted on the next run.

## Per cycle

Commit the verdicts plus regenerated records on `audit-verdicts-<plancode>`, push, and open
one PR per plan. Report in at most six lines: plan code, atoms audited, verdicts, anything
contradicted, and any PR or issue opened. A cycle with nothing new to say should say only
that.

Stop when every atom carries a verdict.
