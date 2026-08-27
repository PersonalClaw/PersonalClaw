# Delisting policy

A listing can be removed. This is what removal means, what triggers it, and how long
it takes.

## What delisting does, and what it cannot do

Removing a row from `app-registry.json` stops **new** discovery: the app disappears from
the registry source, from the Store listing that reads it, and from the registry pages
on personalclaw.dev.

It does **not** uninstall anything. Anyone who already installed the app still has it,
and PersonalClaw will not reach out and remove it. A delisting is a statement about
what this list advertises, not a kill switch, and nobody should plan otherwise.

## Grounds

**Immediate — removed as soon as it is confirmed, with no notice period, because the
harm is ongoing:**

| Ground | How it is found |
|---|---|
| The scanner verdict has become `dangerous` | scheduled re-validation, or any PR that touches the row |
| Credible report of malware, credential theft, impersonation of another project, or spam | a report to the maintainers |

**On 14 days' notice — an issue is opened that @-mentions the `maintainer`, and the row
is removed if it is still true when the notice expires:**

| Ground | How it is found |
|---|---|
| The repository is gone, private, or unreachable across two consecutive weekly re-validations | scheduled re-validation |
| `app.json` no longer parses or no longer validates | scheduled re-validation |
| The license file or the manifest's `license` has been removed | scheduled re-validation |
| The row no longer matches the manifest's `types` or `permissions_declared` | scheduled re-validation |

**On request:** the `maintainer` asks for removal. No notice period and no
justification required.

The 14-day cases are all *fixable*. The notice exists so that a maintainer who is on
holiday does not lose their listing over a CI hiccup, and the two-consecutive-runs rule
on unreachability exists so one bad afternoon at a git host is not a delisting.

## Process

1. Scheduled re-validation runs weekly over every row (`revalidate-listings.yml`). It
   is the same script CI runs on a PR, over the whole file rather than the changed
   rows.
2. A failure opens or updates one tracking issue that names the row, the check that
   failed, and the verbatim reason. That issue is the notice.
3. On expiry — or immediately, for the immediate grounds — a maintainer opens a PR that
   deletes the row. The commit message names the ground and links the issue. **Git
   history is the delisting audit log**; there is no separate ledger to keep in sync.
4. The tracking issue is closed with a link to that commit.

## Re-listing

A delisted app may be listed again by opening a normal listing PR once the problem is
fixed. Delisting is not a blocklist, and there is no penalty period.

The one exception is removal for malware, credential theft, or impersonation. Those
re-listings are a maintainer judgement call, not a CI outcome, and CI passing again is
not by itself sufficient.

## Reporting a listing

Open an issue, or use the security contact in PersonalClaw core's `SECURITY.md` if the
report should not be public. A report needs the row's `name` and what you observed —
ideally a commit or file path. Reports about an app's *quality* are not delisting
grounds; this registry never claimed the app was good.
