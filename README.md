# pr-images

Images for pull request descriptions, and nothing else: captures of PersonalClaw's own UI that a pull request's body links to, so the links keep working after the pull request's branch is deleted.

- This branch has no code, never has a pull request, and is never merged.
- Each folder belongs to one change and is named for it.
- Every image is taken against fixture data, from the e2e harness's own home: no host paths, account names, planning ids or anything personal on screen.
- Each image is listed in `ASSET_LICENSES.json` beside this file, project-owned and under the repository's licence.
- A body links an image by this branch's commit, `https://github.com/PersonalClaw/PersonalClaw/raw/<commit>/<folder>/<file>.png`, so a later commit here never changes what an old body shows.
