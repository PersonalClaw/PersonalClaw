# Validator fixtures

Three apps and four candidate registry documents. Between them they pin the three
listing outcomes the policy promises, plus the schema's refusals.

## `apps/`

Each directory is a complete, minimal app: `app.json`, `provider.py`, `README.md`,
`LICENSE`. They differ in exactly one thing — what a script in the tree contains —
so the scanner verdict is the only variable.

| Fixture | The one difference | Scanner verdict | Outcome |
|---|---|---|---|
| `clean-app` | nothing | `clean` | **listed** |
| `warning-app` | `scripts/setup.sh` runs `curl` | `warning` | **listed**, verdict displayed |
| `dangerous-app` | `scripts/install.sh` runs `rm -rf /` | `dangerous` | **blocked**, rule recorded |

`dangerous-app/scripts/install.sh` carries a guard that exits before its destructive
line and is not marked executable. The validator never runs any of it — the scanner
is a static text scan.

## `registries/`

Candidate `app-registry.json` documents. `repo` is `{{REPO_BASE}}/<fixture>`, a
**placeholder**: there is no way to commit a machine-independent local repo URL.
Tests substitute it with the `file://` URL of a throwaway git repository built from
the matching `apps/` directory, then run the validator with `--allow-file-repos`.

That substitution is the only difference between the offline test path and the
production path. `file://` and `https://` are the same code — `git ls-remote` for
liveness, `git clone --depth 1` for the fetch — so an offline run exercises the real
fetcher rather than a stub of it. Every test that substitutes the placeholder asserts
the placeholder was actually present first, so a renamed token fails loudly instead
of validating a URL nobody meant.

`https://` is the ONLY scheme the validator accepts by default; `file://` requires
`--allow-file-repos`, which the CI workflow never passes. A listing PR therefore
cannot point the fetcher at the runner's own filesystem.
