"""The GHCR push proof runs release.yml's own login and push, to a scratch package only (#3911).

``.github/workflows/release-push-proof.yml`` proves the release's image push against GHCR before
a tag needs it, because a tag's ``release.yml`` cannot be fixed after it is cut. It is a proof
only if it runs the release's step, and safe only if it can reach nothing the release publishes:

- it runs only on a dispatch, its one job only on main of this repository, with ``contents:
  read``, ``actions: read`` and ``packages: write``, no environment and no secret but the job's
  own token, for the login;
- its login is ``release.yml``'s ``images`` login, and its push step's ``run`` is that job's push
  step's, character for character;
- the tag it pushes is ``ghcr.io/personalclaw/release-rehearsal-web``'s, never a release image;
- it pushes a release rehearsal's web layout and index, from the run the dispatch names, once a
  step has read that run and found it a green rehearsal on main;
- a step after the push, run whatever came before, deletes that scratch package, and a last one,
  run whatever came before too, reads it back and must get a 404.

Each rule is planted to fail.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import source_index
import yaml
from rails_repo import REPO

WORKFLOWS = REPO / ".github" / "workflows"
PROOF = WORKFLOWS / "release-push-proof.yml"
RELEASE = WORKFLOWS / "release.yml"

PUSH = "Push the layout, every digest kept"
CHECK = "The rehearsal run is a release rehearsal on main"
DELETE = "Delete the scratch package"
GONE = "The scratch package is gone"
SCRATCH = "ghcr.io/personalclaw/release-rehearsal-web"
PERMISSIONS = {"contents": "read", "actions": "read", "packages": "write"}
RELEASE_IMAGES = re.compile(r"ghcr\.io/personalclaw/personalclaw-")


def _load(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(text)
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


def _named(steps: list[dict], name: str) -> int | None:
    return next((i for i, s in enumerate(steps) if s.get("name") == name), None)


def _login(steps: list[dict]) -> dict | None:
    return next(
        (s for s in steps if str(s.get("uses", "")).startswith("docker/login-action@")), None
    )


def proof_problems(release_text: str, proof_text: str) -> list[str]:
    doc = _load(proof_text)
    out: list[str] = []
    if set(doc.get("on") or {}) != {"workflow_dispatch"}:
        out.append(f"runs on {sorted(doc.get('on') or {})}, not a dispatch alone")
    if doc.get("permissions") != {"contents": "read"}:
        out.append(f"the workflow holds {doc.get('permissions')!r}")
    jobs = doc.get("jobs") or {}
    if set(jobs) != {"push"}:
        return [*out, f"has jobs {sorted(jobs)}, not the one push job"]
    job = jobs["push"]
    condition = " ".join(str(job.get("if") or "").split())
    for needed in (
        "github.ref == 'refs/heads/main'",
        "github.repository == 'PersonalClaw/PersonalClaw'",
    ):
        if needed not in condition:
            out.append(f"the job runs without {needed}")
    if job.get("permissions") != PERMISSIONS:
        out.append(f"the job holds {job.get('permissions')!r}")
    if "environment" in job:
        out.append(f"the job names environment {job['environment']!r}")
    steps = list(job.get("steps") or [])
    release = list(_load(release_text)["jobs"]["images"]["steps"])
    login, released_login = _login(steps), _login(release)
    if login is None or login != released_login:
        out.append("its login is not release.yml's images login")
    for step in steps:
        if step is not login and "secrets." in json.dumps(step):
            out.append(f"step {step.get('name') or step.get('uses')!r} reads a secret")
    push, released_push = _named(steps, PUSH), _named(release, PUSH)
    if push is None or released_push is None:
        return [*out, "it has no push step of release.yml's to run"]
    if steps[push].get("run") != release[released_push].get("run"):
        out.append("its push step's run is not release.yml's")
    env = steps[push].get("env") or {}
    tags = str(env.get("TAGS", ""))
    if not tags.startswith(f"{SCRATCH}:") or RELEASE_IMAGES.search(tags) or "\n" in tags.strip():
        out.append(f"it pushes {tags!r}, not the scratch package alone")
    if "personalclaw-web" not in str(env.get("LAYOUT")) or "personalclaw-web" not in str(
        env.get("INDEX")
    ):
        out.append("it pushes something other than the rehearsal's web layout")
    downloads = [
        s.get("with") or {}
        for s in steps[:push]
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    ]
    if sorted(str(d.get("name")) for d in downloads) != [
        "image-index-web",
        "image-layout-web",
    ] or any(d.get("run-id") != "${{ inputs.run-id }}" for d in downloads):
        out.append("it does not download the named run's web layout and index")
    check = _named(steps, CHECK)
    first_download = (
        next(
            i
            for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("actions/download-artifact@")
        )
        if downloads
        else None
    )
    if check is None or first_download is None or check > first_download:
        out.append("nothing reads the named run as a green rehearsal on main before its download")
    elif "release-rehearsal.yml" not in str(steps[check].get("run")):
        out.append("its run check does not hold the run to release-rehearsal.yml")
    delete = _named(steps, DELETE)
    if delete is None or delete < push:
        return [*out, "nothing deletes the scratch package after the push"]
    if str(steps[delete].get("if")) not in ("${{ always() }}", "always()"):
        out.append("the delete does not run whatever came before")
    package = str((steps[delete].get("env") or {}).get("PACKAGE"))
    if f"ghcr.io/personalclaw/{package}" != SCRATCH or "-X DELETE" not in str(
        steps[delete].get("run")
    ):
        out.append(f"the delete removes {package!r}, not the scratch package")
    gone = _named(steps, GONE)
    if gone is None or gone != len(steps) - 1 or gone < delete:
        out.append("no last step reads the scratch package back as gone")
    else:
        run = str(steps[gone].get("run"))
        if str(steps[gone].get("if")) not in ("${{ always() }}", "always()"):
            out.append("the read-back does not run whatever came before")
        if "HTTP 404" not in run or "-X DELETE" in run or "exit 1" not in run:
            out.append("the read-back does not require a 404")
        if (steps[gone].get("env") or {}).get("PACKAGE") != package:
            out.append("the read-back reads another package than the one deleted")
    for step in steps:
        if "${{ inputs." in str(step.get("run", "")):
            out.append(f"step {step.get('name')!r} puts an input into its command")
    return out


def _texts() -> tuple[str, str]:
    files = set(source_index.workflow_files())
    for path in (RELEASE, PROOF):
        assert path in files, f"{path.name} is not under .github/workflows"
    return source_index.text(RELEASE), source_index.text(PROOF)


def test_the_proof_runs_release_ymls_push_to_a_scratch_package_and_deletes_it() -> None:
    release, proof = _texts()
    assert proof_problems(release, proof) == []


@pytest.mark.parametrize(
    ("old", "new", "said"),
    [
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n", "not a dispatch alone"),
        ("github.ref == 'refs/heads/main' && ", "", "without github.ref"),
        ("      packages: write\n", "      packages: write\n      contents: write\n", "holds"),
        (
            "ghcr.io/personalclaw/release-rehearsal-web:proof-",
            "ghcr.io/personalclaw/personalclaw-web:proof-",
            "not the scratch package alone",
        ),
        (
            'skopeo copy --all --preserve-digests "oci',
            'skopeo copy --all "oci',
            "is not release.yml's",
        ),
        ("          username: ${{ github.actor }}\n", "", "not release.yml's images login"),
        (
            "deleted by hand.\n        if: ${{ always() }}\n",
            "deleted by hand.\n",
            "the delete does not run whatever came before",
        ),
        (
            "deleted by hand.\n        if: ${{ always() }}\n        env:\n"
            "          GH_TOKEN: ${{ github.token }}\n          PACKAGE: release-rehearsal-web\n",
            "deleted by hand.\n        if: ${{ always() }}\n        env:\n"
            "          GH_TOKEN: ${{ github.token }}\n          PACKAGE: personalclaw-web\n",
            "removes",
        ),
        (
            "          name: image-layout-web\n",
            "          name: image-layout-gateway\n",
            "download",
        ),
        (
            "      - name: The rehearsal run is a release rehearsal on main\n",
            "      - name: a check of something else\n",
            "nothing reads the named run",
        ),
        (
            '"repos/$GITHUB_REPOSITORY/actions/runs/$RUN_ID"',
            '"repos/$GITHUB_REPOSITORY/actions/runs/${{ inputs.run-id }}"',
            "puts an input into its command",
        ),
        (
            '          if ! grep -q "HTTP 404" "$RUNNER_TEMP/gone.err"; then\n',
            '          if ! grep -q "HTTP" "$RUNNER_TEMP/gone.err"; then\n',
            "does not require a 404",
        ),
        (
            "      - name: The scratch package is gone\n        # The read-back",
            "      - name: a last note\n        # The read-back",
            "no last step reads the scratch package back",
        ),
        (
            "          GH_TOKEN: ${{ github.token }}\n          RUN_ID",
            "          GH_TOKEN: ${{ secrets.PAT }}\n          RUN_ID",
            "reads a secret",
        ),
    ],
)
def test_a_planted_edit_fails(old: str, new: str, said: str) -> None:
    release, proof = _texts()
    assert proof.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    found = proof_problems(release, proof.replace(old, new))
    assert any(said in p for p in found), found


def test_a_release_push_that_moves_alone_fails_the_proof() -> None:
    """The proof is held to release.yml's step, so a release change without it fails here."""
    release, proof = _texts()
    planted = release.replace('"docker://$ref"', '"docker://$ref" --retry-times 3', 1)
    assert planted != release
    assert "its push step's run is not release.yml's" in proof_problems(planted, proof)
