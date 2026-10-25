"""The source-tree checks split from
``tests/test_website_installer.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import website_installer_shared
from ci_catalogue_steps import read, text
from website_installer_shared import (
    _ROOT,
    REQUIRE_ENV,
    SERVED_URL,
    _live_leg,
    code_only,
    fetch_guards,
    header_claims,
    jobs,
)

installer_text = website_installer_shared.installer_text


_PR = _ROOT / ".github" / "workflows" / "pr.yml"


_BREADTH = _ROOT / ".github" / "workflows" / "breadth.yml"


#: Job ids long enough in these workflows that their absence means the parser broke rather
#: than that CI changed. The vacuity floor for :func:`jobs`.
LONG_STANDING_PR_JOBS = frozenset({"changes", "test", "results"})


LONG_STANDING_BREADTH_JOBS = frozenset({"install-smoke", "audit", "security", "coverage"})


class TestHeaderClaimsAreTrue:
    """Every workflow and job the installer's header advertises must actually exist.

    The header used to promise a weekly ``full.yml`` container smoke that had never been
    written. A comment claiming coverage is worse than no comment: it answers the question
    a reader would otherwise go and check. These cases make the claim executable.
    """

    def test_every_workflow_the_header_names_exists(self, installer_text: str) -> None:
        for wf, _job in sorted(header_claims(installer_text)):
            assert (
                _ROOT / ".github" / "workflows" / wf
            ).is_file(), f"the header claims coverage in {wf}, which does not exist"

    def test_every_job_the_header_names_exists(self, installer_text: str) -> None:
        for wf, job in sorted(header_claims(installer_text)):
            text = (_ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
            present = jobs(text)
            assert job in present, (
                f"the header claims {wf} job `{job}` covers the installer, but {wf} has "
                f"no such job (it has: {sorted(present)}). This is exactly the defect the "
                "file was carrying: an advertised job that was never written."
            )


class TestLiveInstallerLegIsWired:
    """The network half — and the reason a network failure here cannot read as a pass."""

    @pytest.fixture(scope="class")
    @classmethod
    def leg(cls) -> tuple[str, str]:
        return _live_leg(_BREADTH.read_text(encoding="utf-8"))

    def test_breadth_yml_has_a_live_installer_leg(self, leg: tuple[str, str]) -> None:
        jid, _body = leg
        assert jid, "unreachable — _live_leg asserts this"

    def test_it_exercises_the_staged_file_too(self, leg: tuple[str, str]) -> None:
        """Served-only would not catch a broken edit until after it was hand-applied.

        Running both separates "our next installer is broken" (staged red) from "the site
        or PyPI regressed" (served red) — two different pages for two different people.

        Asserted as EXECUTION, not as a mention. A step that hashes or diffs the staged path
        names it too — a byte comparison step once did both — so a substring check passed
        with the staged smoke leg deleted outright: measured, the leg was reduced to
        served-only and the case stayed green. Hashing a file is not running it.
        """
        _jid, body = leg
        assert re.search(
            r"(?:^|\s)(?:/bin/)?(?:sh|dash|bash)\s+\S*deploy/website/install\.sh",
            code_only(body),
        ), (
            "nothing in the live leg EXECUTES deploy/website/install.sh (hashing or diffing "
            "it does not count). A staged edit that breaks the installer would go green "
            "until someone copied it to the website repo."
        )

    def test_it_pipes_the_served_bytes_into_a_shell(self, leg: tuple[str, str]) -> None:
        """The served leg must run the documented pipeline, not just download the file.

        ``curl … | sh`` is itself part of what is being asserted — no TTY on stdin, and a
        partial transfer must install nothing. Fetching to a file and running that file
        would exercise a path no user takes, and would quietly drop the pipe semantics.
        """
        _jid, body = leg
        assert re.search(
            rf"curl[^\n]*{re.escape(SERVED_URL)}[^\n]*\|\s*(?:/bin/)?sh\b",
            code_only(body).replace("\\\n", " "),
        ), (
            "the served bytes are never piped into a shell, so the leg does not exercise "
            "the one-liner users are actually given."
        )

    def test_every_fetch_failure_exits_nonzero_and_says_unproven(
        self, leg: tuple[str, str]
    ) -> None:
        """The ninth absent-vs-declared-false is what this case is here to prevent.

        A curl that cannot reach the host proves nothing. If that path exits zero — or
        merely warns — the job reports success for having tested nothing, which is how a
        missing tmux got read as a passing test class in this repo last month.

        Asserted per GUARD, not per job: the first version of this case checked the whole
        job body for the word "unproven", and a mutant that reworded the message inside a
        guard still passed because an annotation title elsewhere in the job carried the word.
        ``exit`` is the load-bearing half — the vocabulary only makes the red legible.
        """
        _jid, body = leg
        guards = fetch_guards(code_only(body))
        served = [g for g in guards if SERVED_URL in g]
        assert len(served) == 1, (
            f"expected exactly one guarded fetch of {SERVED_URL} (the served smoke); found "
            f"{len(served)} among {len(guards)} guard(s). An unguarded curl cannot report "
            "`unproven` at all."
        )
        for guard in guards:
            assert re.search(r"^\s*exit\s+[1-9]", guard, flags=re.MULTILINE), (
                "a fetch-failure branch does not exit nonzero, so an unreachable host would "
                f"be reported as a passing check:\n{guard}"
            )
            assert "unproven" in guard, (
                "a fetch-failure branch reds without saying it proved nothing. The red must "
                f"be legible as `unproven`, not as a generic failure:\n{guard}"
            )

    def test_the_leg_is_not_continue_on_error(self, leg: tuple[str, str]) -> None:
        """One line would make every red above invisible, and this workflow already uses it.

        ``breadth.yml``'s ``audit`` job is deliberately ``continue-on-error: true`` (report-only
        supply-chain scan). Copying that line onto this job — a plausible edit the first time
        a flaky network annoys someone — turns the whole installer rail into decoration that
        reports success while failing, which is the defect this file exists to end.
        """
        _jid, body = leg
        assert "continue-on-error" not in code_only(body), (
            "the live installer leg is marked continue-on-error, so its failures do not fail "
            "the workflow. A rail that cannot go red is a comment. If network flakiness is "
            "the problem, retry the fetch — do not silence the verdict."
        )

    def test_it_redirects_uv_tool_dirs(self, leg: tuple[str, str]) -> None:
        """The smoke must install into a scratch dir, not over whatever the runner has."""
        _jid, body = leg
        code = code_only(body)
        assert "UV_TOOL_DIR" in code and "UV_TOOL_BIN_DIR" in code, (
            "the live leg does not redirect uv's tool dirs, so the smoke writes into the "
            "runner's real tool directories."
        )

    def test_both_smoke_legs_assert_the_installed_binary_runs(self, leg: tuple[str, str]) -> None:
        """Installing without running proves the wheel downloaded, not that it works.

        Counted, not merely found: with one occurrence the assertion is satisfied by whichever
        leg happens to have it, and the other leg can install into its own tool dir and never
        check that anything came out. Two legs, two verdicts.
        """
        _jid, body = leg
        found = code_only(body).count("personalclaw --version")
        assert found >= 2, (
            f"`personalclaw --version` appears {found} time(s) in the live leg; both the "
            "staged and served smokes must run the installed binary, or one of them proves "
            "only that a download happened."
        )

    def test_current_checkout_runs_setup_with_system_timezone_paths_removed(
        self, leg: tuple[str, str]
    ) -> None:
        """The release's first real command must run before the release reaches PyPI."""
        _jid, body = leg
        code = code_only(body)
        assert re.search(r"^\s*uv\s+tool\s+install\b.*\s\.\s*$", code, re.MULTILINE)
        assert 'PYTHONTZPATH=""' in code
        assert 'TZ="America/Los_Angeles"' in code
        assert re.search(r"\bpersonalclaw\s+setup\b", code)
        assert '"timezone": "America/Los_Angeles"' in code


class TestLintJobChecksTheInstaller:
    """The every-PR half of the linter claim, which cannot skip. Read from the catalogue's
    ``static`` job, which the tier workflows run (``tests/ci_catalogue_steps.py``)."""

    @pytest.fixture(scope="class")
    @classmethod
    def lint_body(cls) -> str:
        return text(read().job("static"))

    def test_lint_runs_shellcheck_on_the_installer(self, lint_body: str) -> None:
        """The INVOCATION, not the word.

        The step installs its tools with ``for tool in shellcheck dash``, so "shellcheck
        appears in this job" is true even with every actual check deleted: measured, removing
        both ``shellcheck --version`` and ``shellcheck -s sh …`` left this case green because
        the apt loop still named the tool. Naming a linter is not running it.
        """
        assert re.search(
            r"shellcheck\s+(?:-\S+\s+)*-s\s+sh\s+\S*deploy/website/install\.sh",
            code_only(lint_body),
        ), (
            "the static job never invokes `shellcheck -s sh deploy/website/install.sh`, "
            "so the header's shellcheck-clean claim rests on nothing again."
        )

    def test_lint_runs_both_posix_syntax_checks(self, lint_body: str) -> None:
        """``dash -n`` and ``sh -n`` are not interchangeable, and neither replaces shellcheck.

        Measured while mutating this rail: ``[[ -t 0 ]]`` — a bashism that breaks the script
        under a real dash — passes ``dash -n`` cleanly, because dash parses ``[[`` as an
        ordinary command name and only fails at RUNTIME. shellcheck is the only member of the
        set that rejects it at check time. All three are named here so that dropping one is a
        red rather than a silent narrowing of what "POSIX-clean" means.
        """
        code = code_only(lint_body)
        for tool in ("sh -n", "dash -n"):
            assert re.search(
                rf"{re.escape(tool)}\s+\S*deploy/website/install\.sh", code
            ), f"the static job does not run `{tool}` on the installer"

    def test_lint_sets_the_require_proof_lever(self, lint_body: str) -> None:
        """Without the lever, a runner image that drops shellcheck turns red into skip."""
        assert REQUIRE_ENV in code_only(lint_body), (
            f"the static job does not set {REQUIRE_ENV}, so a missing shellcheck or "
            "dash would silently skip the cases that prove the installer lints."
        )


class TestParsersAreNotVacuous:
    """Every assertion above is only worth its ability to fail. Drive it and watch."""

    def test_jobs_finds_the_real_workflows(self) -> None:
        pr = set(jobs(_PR.read_text(encoding="utf-8")))
        full = set(jobs(_BREADTH.read_text(encoding="utf-8")))
        assert LONG_STANDING_PR_JOBS <= pr, f"parser lost pr.yml jobs: {LONG_STANDING_PR_JOBS - pr}"
        assert (
            LONG_STANDING_BREADTH_JOBS <= full
        ), f"parser lost breadth.yml jobs: {LONG_STANDING_BREADTH_JOBS - full}"

    def test_fetch_guards_finds_the_real_ones(self) -> None:
        """The live leg must really contain the guards the assertions iterate over."""
        _jid, body = _live_leg(_BREADTH.read_text(encoding="utf-8"))
        guards = fetch_guards(body)
        served = [g for g in guards if SERVED_URL in g]
        assert len(served) == 1, (
            f"parser found {len(served)} guarded fetch(es) of {SERVED_URL} in the live leg "
            f"({len(guards)} guard(s) in all)"
        )
        assert all("curl" in g for g in guards)
