"""The one home of the per-leg verdict vocabulary (shared contracts §14.3).

`release-verify.yml`'s legs and the walkthroughs' V1 wheel leg each end in ONE verdict about
one released artifact — `passed`, `failed` or `incomplete` — with its reason. The vocabulary
lives here, in one executable, for the same reason `scripts/smoke_backend_bundle.sh` is one
script rather than two inline copies: two copies of a vocabulary drift, and the drift is
invisible until someone reads two logs side by side and finds they disagree about what a word
means.

WHY `incomplete` EXISTS. A release can be unverifiable for a reason that is no one's failure
today: v0.2.0 attaches no AppImage or deb and carries no `SHA256SUMS` (#4323), so the desktop
legs cannot check files that were never published. `failed` would blame the release for a gap
in the pipeline; `passed` would be exactly the lie this workflow exists to make impossible.
`incomplete`, with the reason named, is the honest third answer — and the report job treats
it as "did not verify" exactly like `failed`.

WHY THE RECORDING IS SHARED AND THE JUDGEMENT IS NOT. Each leg's step decides what its
evidence adds up to, because only the leg knows what it measured; `write_verdict` only
records the decision in the one file shape the report job reads back (`verdict.json`) and in
the same job-summary line. A leg that hand-rolls its JSON is a leg whose verdict the report
cannot find.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

#: The vocabulary. A fourth word needs a reader, not just a writer: the report job, the
#: tests and the issue body all switch on these three.
VERDICTS = ("passed", "failed", "incomplete")


def write_verdict(
    leg: str,
    verdict: str,
    reason: str,
    *,
    out_dir: str = ".",
    release: str = "",
) -> pathlib.Path:
    """Write one leg's ``verdict.json`` and append its line to the job summary.

    Returns the path written. Raises ``ValueError`` on a word outside :data:`VERDICTS` — a
    typo'd verdict must fail loudly here, not publish a fourth category nothing reads.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict {verdict!r} is not one of {VERDICTS}")
    payload: dict[str, str] = {"leg": leg, "verdict": verdict, "reason": reason}
    if release:
        payload["release"] = release
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "verdict.json"
    # sort_keys: two legs' files diff cleanly, and the report job's merge is order-free.
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- **{leg}**: {verdict} — {reason}\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--leg", required=True, help="the leg this verdict belongs to")
    parser.add_argument("--verdict", required=True, choices=VERDICTS)
    parser.add_argument("--reason", required=True, help="one sentence a reader can act on")
    parser.add_argument(
        "--release", default="", help="the release tag or version, when one is known"
    )
    parser.add_argument("--out-dir", default=".", help="directory to write verdict.json into")
    args = parser.parse_args(argv)
    path = write_verdict(
        args.leg, args.verdict, args.reason, out_dir=args.out_dir, release=args.release
    )
    print(f"verdict written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
