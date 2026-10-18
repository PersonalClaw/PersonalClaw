"""The commit-provenance check holds every commit a pull request brings, and every commit that
lands on ``main``, to the project's commit policy.

Each case plants one commit in a scratch repository (no remote, no system or global git
configuration, no credential helper) and runs ``scripts/check_commit_provenance.py`` the way
CI runs it: as a program, with ``--mode pr`` over a pull request's commits as the commits API
returns them, or ``--mode main`` over a pushed range.

The rules:
  1. every commit carries ``Signed-off-by: <author name> <author email>`` as a line of its own,
     Dependabot's own commits (by its one exact address) skipped;
  2. (main only) every committer is an identity ``MAINTAINERS`` lists;
  3. no commit a maintainer authored (by name or by email) carries a co-author, a tool or
     session trailer, a generated-with footer or a session link;
  5. no commit message, and no pull request title, carries a marker that starts no run.

The workflow cases below hold the two places the check runs: ``dco.yml`` on
``pull_request_target``, and ``provenance.yml`` on every push to main, which runs the PREVIOUS
main commit's copy of the check. The last ones run that workflow's own shell in a scratch
repository, so what is pinned is what it does, not how it is spelled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from rails_repo import REPO

_REPO_ROOT = REPO
_SCRIPT = _REPO_ROOT / "scripts" / "check_commit_provenance.py"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

MAINTAINER = ("Ada Maintainer", "ada@example.com")
MERGER = ("Merge Bot", "merge@example.com")
CONTRIBUTOR = ("Cy Contributor", "cy@example.org")
OTHER = ("Dee Other", "dee@example.net")
#: Dependabot's author identity, exactly as GitHub writes it on the bot's commits.
DEPENDABOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")

_MAINTAINERS_FILE = (
    "# Identities allowed to commit on main.\n"
    "Ada Maintainer <ada@example.com>  the maintainer's own commits\n"
    "Merge Bot <merge@example.com>  the squash merge of a pull request\n"
)

_GIT_ENV_KEYS = ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT")


def _signoff(who: tuple[str, str]) -> str:
    return f"Signed-off-by: {who[0]} <{who[1]}>"


def _scratch_env(root: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


class Repo:
    """A scratch repository that commits with exactly the identities a case names."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.env = _scratch_env(root)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.maintainers = root.parent / f"{root.name}-MAINTAINERS"
        self.maintainers.write_text(_MAINTAINERS_FILE, encoding="utf-8")
        self.base = self.commit("base\n\n" + _signoff(MAINTAINER))

    def git(self, *args: str, stdin: bytes | None = None) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            input=stdin,
            capture_output=True,
            check=True,
        )
        return done.stdout.decode("utf-8", "replace").strip()

    def commit(
        self,
        message: str,
        *,
        author: tuple[str, str] = MAINTAINER,
        committer: tuple[str, str] | None = None,
    ) -> str:
        committer = committer or author
        env = {
            **self.env,
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": committer[0],
            "GIT_COMMITTER_EMAIL": committer[1],
        }
        message_file = self.root.parent / f"{self.root.name}-message.txt"
        message_file.write_text(message + "\n", encoding="utf-8")
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", str(message_file)],
            cwd=self.root,
            env=env,
            check=True,
        )
        return self.git("rev-parse", "HEAD")

    def raw_commit(self, body: bytes) -> str:
        """A commit object written byte for byte, on top of HEAD: what `git commit` refuses."""
        tree = self.git("rev-parse", "HEAD^{tree}")
        head = self.git("rev-parse", "HEAD")
        who = f"{MAINTAINER[0]} <{MAINTAINER[1]}> 1700000000 +0000"
        header = f"tree {tree}\nparent {head}\nauthor {who}\ncommitter {who}\n\n".encode()
        sha = self.git(
            "hash-object", "-t", "commit", "-w", "--literally", "--stdin", stdin=header + body
        )
        self.git("update-ref", "HEAD", sha)
        return sha

    def check_main(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run("--mode", "main", "--range", f"{self.base}..HEAD", *extra)

    def check_pr(
        self, *extra: str, title: str = "feat: a change", count: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        """The pull request's commits as the commits API returns them, one page per commit, with
        the event payload and the pull request's own commit count."""
        shas = self.git("rev-list", "--reverse", f"{self.base}..HEAD").split()
        return self.check_api(
            [self._api_commit(sha) for sha in shas], *extra, title=title, count=count
        )

    def check_api(
        self,
        items: list,
        *extra: str,
        title: str = "feat: a change",
        count: int | None = None,
        head: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """The given API items, with the event's head the last item's id unless one is named."""
        pages = "".join(json.dumps([item]) + "\n" for item in items)
        commits = self.root.parent / f"{self.root.name}-commits.json"
        commits.write_text(pages, encoding="utf-8")
        event = self.root.parent / f"{self.root.name}-event.json"
        event.write_text(json.dumps({"pull_request": {"title": title}}), encoding="utf-8")
        if head is None:
            last = items[-1] if items else None
            named = isinstance(last, dict) and isinstance(last.get("sha"), str)
            head = last["sha"] if named else "a" * 40
        return self.run(
            "--mode",
            "pr",
            "--commits-json",
            str(commits),
            "--expected-count",
            str(len(items) if count is None else count),
            "--expected-head",
            head,
            "--event",
            str(event),
            *extra,
        )

    def _api_commit(self, sha: str) -> dict:
        fields = self.git("log", "-1", "--format=%an%x00%ae%x00%cn%x00%ce", sha).split("\0")
        message = self.git("cat-file", "commit", sha).split("\n\n", 1)[1]
        return _api_item(sha, (fields[0], fields[1]), (fields[2], fields[3]), message)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), "--maintainers", str(self.maintainers), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )


def _api_item(sha: str, author: tuple[str, str], committer: tuple[str, str], message: str) -> dict:
    return {
        "sha": sha,
        "commit": {
            "author": {"name": author[0], "email": author[1]},
            "committer": {"name": committer[0], "email": committer[1]},
            "message": message.rstrip("\n"),
        },
    }


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


def _rules(done: subprocess.CompletedProcess[str]) -> list[int]:
    """The rule number of every violation line the check printed."""
    found = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "rule":
            found.append(int(parts[2]))
    return found


# ── A clean range ────────────────────────────────────────────────────────────────────────────


def test_a_clean_range_passes_in_both_modes(repo: Repo) -> None:
    repo.commit("feat: a change\n\nBody.\n\n" + _signoff(MAINTAINER))
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 0, done.stdout + done.stderr
        assert _rules(done) == []


# ── Rule 1: a sign-off that matches the author ───────────────────────────────────────────────


def test_an_unsigned_commit_fails_rule_1_in_both_modes(repo: Repo) -> None:
    sha = repo.commit("feat: unsigned")
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1
        assert _rules(done) == [1]
        assert done.stdout.startswith(sha)


@pytest.mark.parametrize(
    "signed_as",
    [("Ada Other", "ada@example.com"), ("Ada Maintainer", "someone@example.com")],
    ids=["another-name", "another-email"],
)
def test_a_signoff_that_is_not_the_author_fails_rule_1(
    repo: Repo, signed_as: tuple[str, str]
) -> None:
    repo.commit("feat: signed by someone else\n\n" + _signoff(signed_as))
    done = repo.check_main()
    assert done.returncode == 1 and _rules(done) == [1]
    assert "Ada Maintainer <ada@example.com>" in done.stdout


def test_a_signoff_in_the_middle_of_a_line_is_not_one(repo: Repo) -> None:
    repo.commit(f"feat: a change\n\nAs required: {_signoff(MAINTAINER)} was meant here.")
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1 and _rules(done) == [1], done.stdout


def test_a_dependabot_commit_without_a_signoff_is_skipped(repo: Repo) -> None:
    repo.commit("build(deps): bump a thing", author=DEPENDABOT, committer=MAINTAINER)
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 0, done.stdout


@pytest.mark.parametrize(
    "author",
    [
        ("Robot Builder", "robot@example.org"),
        ("dependabot[bot]", "bot@example.org"),
        ("dependabot[bot]", "Dependabot@users.noreply.github.com"),
        ("dependabot[bot]", "1+dependabot[bot]@users.noreply.github.com"),
        ("dependabot[bot]", "49699333+DEPENDABOT[bot]@users.noreply.github.com"),
    ],
    ids=["bot-in-the-name", "its-name-another-email", "a-lookalike", "another-id", "another-case"],
)
def test_the_dependabot_skip_is_its_one_exact_address(repo: Repo, author: tuple[str, str]) -> None:
    """The skip reads the author's email and nothing else, and only Dependabot's exact address
    passes. Any author can set that address, so the skip is as narrow as it can be made: no
    name, no other id, no other case, no lookalike."""
    repo.commit("build(deps): bump a thing", author=author, committer=MAINTAINER)
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1 and _rules(done) == [1], done.stdout


# ── Rule 2: a committer MAINTAINERS lists (main only) ────────────────────────────────────────


def test_a_committer_not_in_maintainers_fails_rule_2_on_main_only(repo: Repo) -> None:
    repo.commit("feat: a contributor's own\n\n" + _signoff(CONTRIBUTOR), author=CONTRIBUTOR)
    main = repo.check_main()
    assert main.returncode == 1 and _rules(main) == [2]
    assert "Cy Contributor <cy@example.org>" in main.stdout
    pr = repo.check_pr()
    assert pr.returncode == 0, pr.stdout


@pytest.mark.parametrize(
    "committer",
    [("Someone Else", "ada@example.com"), ("Ada Maintainer", "ada@example.org")],
    ids=["listed-email-another-name", "listed-name-another-email"],
)
def test_rule_2_matches_the_whole_listed_identity(repo: Repo, committer: tuple[str, str]) -> None:
    repo.commit("feat: a change\n\n" + _signoff(MAINTAINER), committer=committer)
    done = repo.check_main()
    assert done.returncode == 1 and _rules(done) == [2], done.stdout


# ── Rule 3: no co-author or tool-session marks on a maintainer's commit ─────────────────────


@pytest.mark.parametrize(
    "mark",
    [
        "Co-authored-by: Dee Other <dee@example.net>",
        "co-authored-by: Dee Other <dee@example.net>",
        "Session-Id: 0f3c9a",
        "Agent-Session: 0f3c9a",
        "X-Session-Url: https://tool.example.com/x",
        "Generated-by: some-tool 1.2",
        "Assisted-by: some-tool",
        "🤖 Generated with some-tool",
        "Generated by some-tool",
        "See https://tool.example.com/sessions/0f3c9a for the run.",
        "https://tool.example.com/code/session_01AbCdEf",
        "https://tool.example.com/share/0f3c9a",
        "https://tool.example.com/chat/0f3c9a",
        "https://chat.example.com/c/0f3c9a",
        "https://tool.example.com/c/conversation/0f3c9a",
    ],
)
def test_a_maintainer_commit_carrying_a_tool_or_co_author_mark_fails_rule_3(
    repo: Repo, mark: str
) -> None:
    repo.commit(f"feat: a change\n\n{mark}\n{_signoff(MAINTAINER)}")
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1, done.stdout
        assert _rules(done) == [3], done.stdout


@pytest.mark.parametrize(
    "line",
    [
        "The policy refuses a Co-authored-by: line on our commits.",
        "Possession: the store keeps the session it opened.",
        "Sessions are listed in https://example.com/docs/sessions.md now.",
        "Chat history lives in https://example.com/guide/chat-history.",
    ],
    ids=["a-trailer-quoted-mid-line", "possession", "a-page-about-sessions", "a-chat-page"],
)
def test_body_prose_that_only_names_these_words_is_not_a_mark(repo: Repo, line: str) -> None:
    repo.commit(f"docs: explain\n\n{line}\n\n" + _signoff(MAINTAINER))
    done = repo.check_main()
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize(
    "author",
    [("ADA MAINTAINER", "ada@elsewhere.example.org"), ("Ada M.", "ADA@EXAMPLE.COM")],
    ids=["listed-name-any-case", "listed-email-any-case"],
)
def test_rule_3_knows_a_maintainer_by_name_or_email(repo: Repo, author: tuple[str, str]) -> None:
    """Rule 3 is about the person, so a maintainer committing under another address, or the
    listed address under another spelling of the name, is still one."""
    repo.commit(
        f"feat: a change\n\nCo-authored-by: Dee Other <dee@example.net>\n{_signoff(author)}",
        author=author,
        committer=MAINTAINER,
    )
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1 and _rules(done) == [3], done.stdout


def test_a_contributors_own_co_author_passes(repo: Repo) -> None:
    repo.commit(
        "feat: pairing\n\nCo-authored-by: Dee Other <dee@example.net>\n" + _signoff(CONTRIBUTOR),
        author=CONTRIBUTOR,
        committer=MAINTAINER,
    )
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 0, done.stdout


# ── Rule 5: no marker that starts no run ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "marker",
    ["[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]", "[SKIP CI]"],
)
def test_a_skip_marker_in_any_commit_message_fails_rule_5(repo: Repo, marker: str) -> None:
    """GitHub starts no run for a push whose head commit carries one, and the next push checks
    only its own range, so a marker anywhere in what lands is refused."""
    repo.commit(f"feat: a change {marker}\n\n" + _signoff(MAINTAINER))
    repo.commit("feat: the next one\n\n" + _signoff(MAINTAINER))
    for done in (repo.check_main(), repo.check_pr()):
        assert done.returncode == 1 and _rules(done) == [5], done.stdout


def test_a_skip_checks_trailer_fails_rule_5(repo: Repo) -> None:
    repo.commit(f"feat: a change\n\nskip-checks: true\n{_signoff(MAINTAINER)}")
    assert _rules(repo.check_main()) == [5]


def test_a_skip_marker_in_the_pull_request_title_fails_rule_5(repo: Repo) -> None:
    """A squash merge's message starts with the title, so the title is checked too."""
    repo.commit("feat: a change\n\n" + _signoff(MAINTAINER))
    done = repo.check_pr(title="feat: a change [ci skip]")
    assert done.returncode == 1 and _rules(done) == [5], done.stdout
    assert done.stdout.startswith("pull-request rule 5 ")


# ── The two ways GitHub lands a pull request ─────────────────────────────────────────────────


def test_a_rebase_merge_keeps_the_message_and_the_maintainer_commits_it(repo: Repo) -> None:
    """GitHub's rebase merge re-commits each commit with the merging maintainer as committer
    and the message unchanged, so rules 1 and 3 see the pull request's own commit."""
    repo.commit(
        "feat: a contributor's change\n\n" + _signoff(CONTRIBUTOR),
        author=CONTRIBUTOR,
        committer=MAINTAINER,
    )
    assert repo.check_main().returncode == 0


def test_a_squash_merge_is_committed_by_the_merge_identity(repo: Repo) -> None:
    """GitHub's squash merge writes one commit authored by the pull request's author and
    committed by GitHub, with a message built from the commits and a co-author line for each
    other author. That passes: the author signed off, GitHub is listed, and the co-author is
    on a contributor's commit."""
    repo.commit(
        "feat: a contributor's change (#7)\n\n"
        f"{_signoff(CONTRIBUTOR)}\n{_signoff(MAINTAINER)}\n"
        "Co-authored-by: Dee Other <dee@example.net>",
        author=CONTRIBUTOR,
        committer=MERGER,
    )
    assert repo.check_main().returncode == 0


def test_a_squash_message_without_the_authors_signoff_fails_rule_1(repo: Repo) -> None:
    repo.commit(
        "feat: a contributor's change (#7)\n\n" + _signoff(MAINTAINER),
        author=CONTRIBUTOR,
        committer=MERGER,
    )
    done = repo.check_main()
    assert done.returncode == 1 and _rules(done) == [1]


def test_a_maintainer_squash_that_gained_a_co_author_fails_rule_3(repo: Repo) -> None:
    """A maintainer's pull request that carries another author's commit gains a co-author line
    when it is squashed, and rule 3 refuses that on the push to main: trim the line at merge."""
    repo.commit(
        f"feat: a change (#8)\n\n{_signoff(MAINTAINER)}\n"
        "Co-authored-by: Dee Other <dee@example.net>",
        author=MAINTAINER,
        committer=MERGER,
    )
    done = repo.check_main()
    assert done.returncode == 1 and _rules(done) == [3]


# ── Output: one line per violation, and no line a commit can start ──────────────────────────


def test_every_violation_gets_its_own_line_and_the_report_file_holds_them(
    repo: Repo, tmp_path: Path
) -> None:
    repo.commit("feat: two problems\n\nSession-Id: 1", author=MAINTAINER, committer=OTHER)
    report = tmp_path / "report.txt"
    done = repo.check_main("--report", str(report))
    assert done.returncode == 1
    assert sorted(_rules(done)) == [1, 2, 3]
    assert report.read_text(encoding="utf-8").splitlines() == done.stdout.splitlines()


def test_every_violation_is_also_an_annotation_on_its_own_line(repo: Repo) -> None:
    """A red run's summary names the commit: each violation is printed again as `::error::`,
    and a name planted with a newline still makes one annotation, not a second command."""
    sha = "d" * 40
    name = "Cy\n::warning::planted"
    done = repo.check_api([_api_item(sha, (name, "cy@example.org"), MAINTAINER, "feat: x")])
    assert done.returncode == 1
    annotations = [line for line in done.stderr.splitlines() if line.startswith("::")]
    assert annotations == [f"::error::{line}" for line in done.stdout.splitlines()], done.stderr


def test_a_clean_run_writes_an_empty_report(repo: Repo, tmp_path: Path) -> None:
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    report = tmp_path / "report.txt"
    assert repo.check_main("--report", str(report)).returncode == 0
    assert report.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "name",
    ["Cy\r::error::planted", "Cy\n::warning::planted", "Cy\u2028::error::planted", "Cy\x1b[2Jx"],
    ids=["carriage-return", "newline", "line-separator", "escape"],
)
def test_no_field_a_commit_supplies_can_start_a_line(repo: Repo, name: str) -> None:
    """The runner reads a line that starts `::` as a workflow command, so every field a commit
    supplies is printed with its control and line-break characters collapsed."""
    sha = "a" * 40
    item = _api_item(sha, (name, "cy@example.org"), (name, "cy@example.org"), "feat: x")
    done = repo.check_api([item])
    assert done.returncode == 1 and _rules(done) == [1]
    assert all(line.startswith(f"{sha} rule ") for line in done.stdout.splitlines()), done.stdout
    assert not any(ch in done.stdout for ch in "\r\x1b\u2028")
    assert "\n::" not in done.stdout and not done.stdout.startswith("::")


def test_a_record_separator_in_a_message_is_read_as_text(repo: Repo) -> None:
    repo.commit("feat: a change\x1e with a separator\n\n" + _signoff(MAINTAINER))
    done = repo.check_main()
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "record",
    [b"a" * 40 + b"\x00Ada Maintainer", b"b" * 40 + b"\x00A\x00a@x\x00A\x00a@x\x00message"],
    ids=["short-of-its-fields", "another-commits-record"],
)
def test_a_record_git_could_not_format_whole_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch, record: bytes
) -> None:
    """git log formats one commit's six fields, the commit's own id first. A record short of
    its fields, or naming another commit, is refused rather than read as a commit. Once the NUL
    refusal runs first, no commit git writes reaches this, so git is stood in for here."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_commit_provenance", _SCRIPT)
    assert spec and spec.loader
    provenance = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up by name while it is built, so the module is registered
    # first, and only for this test.
    monkeypatch.setitem(sys.modules, spec.name, provenance)
    spec.loader.exec_module(provenance)
    sha = "a" * 40
    replies = {"rev-list": sha.encode(), "cat-file": b"tree t\n\nmsg\n", "log": record}

    def git(*args: str) -> bytes:
        return replies.get(args[0], b"")

    monkeypatch.setattr(provenance, "_git", git)
    with pytest.raises(provenance.Unusable, match="not 6"):
        provenance.commits_in_range(f"{'c' * 40}..{sha}")


def test_a_commit_whose_object_holds_a_nul_cannot_be_checked(repo: Repo) -> None:
    """git's formatted message stops at a NUL, so a co-author after one would be read past.
    Such a commit is refused outright."""
    repo.raw_commit(
        f"feat: a change\n\n{_signoff(MAINTAINER)}\n".encode()
        + b"\x00Co-authored-by: Dee Other <dee@example.net>\n"
    )
    done = repo.check_main()
    assert done.returncode == 2, done.stdout + done.stderr
    assert "NUL" in done.stderr


# ── Failing closed: exit 2, and no report ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "contents",
    ["Ada Maintainer <ada@example.com>\n", "no identity here  a reason\n", ""],
    ids=["no-reason", "no-identity", "empty"],
)
def test_an_unreadable_maintainers_file_fails_closed(repo: Repo, contents: str) -> None:
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    repo.maintainers.write_text(contents, encoding="utf-8")
    done = repo.check_main()
    assert done.returncode == 2, done.stdout + done.stderr


def test_a_maintainers_line_without_its_reason_fails_closed_among_good_ones(repo: Repo) -> None:
    """Every identity says why it may commit on main. One line without a reason, beside lines
    that have one, is refused by number, and the commit it would admit is never judged."""
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    repo.maintainers.write_text(
        _MAINTAINERS_FILE + "Cy Contributor <cy@example.org>\n", encoding="utf-8"
    )
    done = repo.check_main()
    assert done.returncode == 2, done.stdout + done.stderr
    # The message is printed with whitespace runs collapsed, as every line the check prints is.
    assert "MAINTAINERS:4: expected `Name <email> reason`" in done.stderr, done.stderr


def test_a_missing_maintainers_file_fails_closed(repo: Repo) -> None:
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    repo.maintainers.unlink()
    assert repo.check_main().returncode == 2


def test_the_maintainers_file_is_always_named(repo: Repo) -> None:
    """There is no default: the workflow that runs the check names the copy it trusts."""
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--mode", "main", "--range", f"{repo.base}..HEAD"],
        cwd=repo.root,
        env=repo.env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2


def test_a_check_that_cannot_run_writes_no_report_and_removes_a_stale_one(
    repo: Repo, tmp_path: Path
) -> None:
    repo.commit("feat: unsigned")
    report = tmp_path / "report.txt"
    report.write_text("stale line from an earlier run\n", encoding="utf-8")
    repo.maintainers.write_text("", encoding="utf-8")
    done = repo.check_main("--report", str(report))
    assert done.returncode == 2
    assert not report.exists()


@pytest.mark.parametrize(
    "bad_range",
    ["0000000000000000000000000000000000000000..HEAD", "nonexistent..HEAD", "HEAD..HEAD"],
    ids=["branch-creation", "unknown-commit", "empty"],
)
def test_a_range_with_nothing_to_check_fails_closed(repo: Repo, bad_range: str) -> None:
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    done = repo.run("--mode", "main", "--range", bad_range)
    assert done.returncode == 2, done.stdout + done.stderr
    if bad_range.startswith("0000"):
        assert "all zeros" in done.stderr, done.stderr


@pytest.mark.parametrize(
    "text",
    ["", "[]\n", "{not json\n", '[{"sha": "abc"}]\n', '{"sha": "x"}\n'],
    ids=["empty", "no-commits", "not-json", "no-fields", "not-a-page"],
)
def test_an_empty_or_malformed_commit_list_fails_closed(repo: Repo, text: str) -> None:
    commits = repo.root.parent / "commits.json"
    commits.write_text(text, encoding="utf-8")
    event = repo.root.parent / "event.json"
    event.write_text('{"pull_request": {"title": "t"}}', encoding="utf-8")
    done = repo.run(
        "--mode",
        "pr",
        "--commits-json",
        str(commits),
        "--expected-count",
        "1",
        "--expected-head",
        "a" * 40,
        "--event",
        str(event),
    )
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "--expected-head" not in done.stderr, done.stderr


@pytest.mark.parametrize(
    "bad",
    [
        {"sha": "b" * 40},
        {"sha": "not-a-sha", "commit": _api_item("x", MAINTAINER, MAINTAINER, "m")["commit"]},
        {
            "sha": "b" * 40,
            "commit": {**_api_item("x", MAINTAINER, MAINTAINER, "m")["commit"], "message": 7},
        },
    ],
    ids=["no-commit", "not-a-sha", "a-message-that-is-not-text"],
)
def test_good_commits_beside_a_malformed_one_fail_closed(repo: Repo, bad: dict) -> None:
    good = _api_item("a" * 40, MAINTAINER, MAINTAINER, "feat: x\n\n" + _signoff(MAINTAINER))
    done = repo.check_api([good, bad, good])
    assert done.returncode == 2, (done.stdout, done.stderr)


@pytest.mark.parametrize("count", [2, 251], ids=["one-short", "past-the-api-limit"])
def test_a_commit_list_short_of_the_pull_requests_count_fails_closed(
    repo: Repo, count: int
) -> None:
    """The commits endpoint returns at most 250 and pagination then stops without an error, so
    a list shorter than the pull request's own count is refused rather than half-checked."""
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    done = repo.check_pr(count=count)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "at most 250" in done.stderr


def _signed(sha: str, subject: str) -> dict:
    return _api_item(sha, MAINTAINER, MAINTAINER, f"{subject}\n\n" + _signoff(MAINTAINER))


@pytest.mark.parametrize(
    ("order", "head"),
    [
        (["a", "b"], "c" * 40),  # truncated: the head's own commit never arrived
        (["b", "a"], "b" * 40),  # reordered: the head is in the list, but not last
        (["a", "b"], "b" * 39),  # not a full commit id
    ],
    ids=["truncated", "reordered", "head-not-a-sha"],
)
def test_a_commit_list_that_does_not_end_at_the_events_head_fails_closed(
    repo: Repo, order: list[str], head: str
) -> None:
    """The commits API answers for the pull request as it is when asked, so a list from after a
    later push, or one cut short or reordered, is refused rather than checked as this head's."""
    report = repo.root.parent / "report.txt"
    items = [_signed(c * 40, f"feat: {c}") for c in order]
    done = repo.check_api(items, "--report", str(report), head=head)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "head" in done.stderr, done.stderr
    assert not report.exists()


@pytest.mark.parametrize("head", ["", "b" * 39, "B" * 40], ids=["empty", "short", "upper-case"])
def test_a_head_that_is_not_a_commit_id_is_named_as_such(repo: Repo, head: str) -> None:
    """A head that is no commit id is the workflow's mistake (an empty or mistyped input), not
    a pull request that moved, so the refusal says which."""
    done = repo.check_api([_signed("b" * 40, "feat: b")], head=head)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "--expected-head must be a full commit id" in done.stderr, done.stderr


def test_a_commit_list_ending_at_the_events_head_is_checked(repo: Repo) -> None:
    """The control for the head check: the same well-signed commits, ending at the head the
    event names, pass."""
    items = [_signed("a" * 40, "feat: a"), _signed("b" * 40, "feat: b")]
    done = repo.check_api(items, head="b" * 40)
    assert done.returncode == 0, (done.stdout, done.stderr)


def test_pr_mode_without_the_events_head_is_refused(repo: Repo) -> None:
    commits = repo.root.parent / "commits.json"
    commits.write_text(json.dumps([_signed("a" * 40, "feat: a")]) + "\n", encoding="utf-8")
    event = repo.root.parent / "event.json"
    event.write_text('{"pull_request": {"title": "t"}}', encoding="utf-8")
    done = repo.run(
        "--mode",
        "pr",
        "--commits-json",
        str(commits),
        "--expected-count",
        "1",
        "--event",
        str(event),
    )
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "--expected-head" in done.stderr, done.stderr


def test_main_mode_refuses_the_events_head(repo: Repo) -> None:
    repo.commit("feat: fine\n\n" + _signoff(MAINTAINER))
    done = repo.check_main("--expected-head", "a" * 40)
    assert done.returncode == 2, (done.stdout, done.stderr)


# ── Real commits on main ────────────────────────────────────────────────────────────────────


def test_the_repositorys_maintainers_file_lists_the_squash_merge_identity() -> None:
    lines = [
        line
        for line in (_REPO_ROOT / "MAINTAINERS").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert any(line.startswith("GitHub <noreply@github.com> ") for line in lines), lines
    assert all(len(line.split(">", 1)[1].strip()) > 0 for line in lines), lines


# ── dco.yml: the base branch's check, over the API's data, nothing of the candidate run ─────


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text("utf-8"))


def _dco_steps() -> list[dict]:
    return _workflow("dco.yml")["jobs"]["dco"]["steps"]


def _step(steps: list[dict], name: str) -> dict:
    return next(s for s in steps if s.get("name") == name)


# dco.yml's trust shape (trigger, base-only checkout, token in the fetch step alone, no
# expression in code, the count and the event handed over, the growing fence) is the CI contract
# row `dco-checks-a-pull-request-with-the-base-branchs-check`, each property with its own plant.
# What is left here is behaviour: the comment step's own script, run.


def _run_the_comment_script(
    tmp_path: Path, report: str | None, comments: list[dict] | None = None
) -> list[dict]:
    """The comment step's own script, run under node with GitHub's client stubbed, against a
    report file (or none) and the comments already on the pull request. Returns what it would
    have created or updated, each marked with which."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the comment step's script cannot run here")
    script = _step(_dco_steps(), "Explain how to fix DCO")["with"]["script"]
    path = tmp_path / "provenance.txt"
    if report is not None:
        path.write_text(report, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(
        "const posted = [];\n"
        f"const existing = {json.dumps(comments or [])};\n"
        "const github = { paginate: async () => existing, rest: { issues: {\n"
        "  listComments: () => {},\n"
        "  createComment: async (a) => posted.push({...a, made: 'created'}),\n"
        "  updateComment: async (a) => posted.push({...a, made: 'updated'}) } } };\n"
        "const context = { repo: { owner: 'o', repo: 'r' },\n"
        "  payload: { pull_request: { number: 7, base: { ref: 'main' } } } };\n"
        "(async () => {\n" + script + "\n})().then(() => console.log(JSON.stringify(posted)));\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [node, str(harness)],
        env={"PATH": os.environ["PATH"], "REPORT": str(path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_comment_fences_the_report_with_a_fence_it_cannot_close(tmp_path: Path) -> None:
    sha = "c" * 40
    report = f"{sha} rule 1 no `Signed-off-by: A <a@example.org>` line ````` [x](https://x)\n"
    [posted] = _run_the_comment_script(tmp_path, report)
    body = posted["body"]
    assert body.startswith("<!-- dco-help -->")
    assert "\n``````text\n" + report.strip() + "\n``````\n" in body, body
    assert "Rule 1:" in body and "Rule 3:" not in body


def test_the_comment_updates_only_the_bots_own_marked_comment(tmp_path: Path) -> None:
    """A person's comment that happens to hold the marker is left alone: the bot posts its own,
    and on a later run updates only that one."""
    report = f"{'c' * 40} rule 1 no sign-off\n"
    person = {"id": 1, "body": "quoting <!-- dco-help --> here", "user": {"type": "User"}}
    [posted] = _run_the_comment_script(tmp_path, report, [person])
    assert posted["made"] == "created", posted
    bot = {"id": 2, "body": "<!-- dco-help --> old", "user": {"type": "Bot"}}
    [posted] = _run_the_comment_script(tmp_path, report, [person, bot])
    assert posted["made"] == "updated" and posted["comment_id"] == 2, posted


@pytest.mark.parametrize("report", [None, ""], ids=["no-report", "an-empty-report"])
def test_the_comment_posts_nothing_without_a_report(tmp_path: Path, report: str | None) -> None:
    assert _run_the_comment_script(tmp_path, report) == []


# ── provenance.yml: every push to main, with the previous main commit's check ───────────────


# provenance.yml's shape is the CI contract row
# `provenance-checks-every-main-push-with-mains-previous-check`. What is left here is behaviour:
# the workflow's own shell, run against a scratch main.


def _provenance_run() -> tuple[str, dict]:
    [job] = _workflow("provenance.yml")["jobs"].values()
    step = next(s for s in job["steps"] if "check_commit_provenance" in str(s.get("run", "")))
    return str(step["run"]), dict(step.get("env") or {})


class Pushes:
    """A scratch main whose commits carry stand-in checks, to run provenance.yml's own shell."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.env = _scratch_env(root)
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", MAINTAINER[0]), ("user.email", MAINTAINER[1])):
            self.git("config", key, value)
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def commit(self, files: dict[str, str | None], message: str = "c") -> str:
        for rel, text in files.items():
            path = self.root / rel
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def push(self, before: str, after: str, tmp: Path) -> subprocess.CompletedProcess[str]:
        run, _env = _provenance_run()
        (tmp / "runner").mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", "-c", run],
            cwd=self.root,
            env={
                **self.env,
                "BEFORE": before,
                "GITHUB_SHA": after,
                "RUNNER_TEMP": str(tmp / "runner"),
                "GITHUB_STEP_SUMMARY": str(tmp / "summary.md"),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )


def _stand_in(label: str, code: int) -> str:
    """A check that says which copy ran, how, and with which MAINTAINERS, then exits *code*."""
    return (
        "import sys\n"
        f"print('ran:{label}', 'isolated' if sys.flags.isolated else 'not-isolated')\n"
        "listed = open(sys.argv[sys.argv.index('--maintainers') + 1]).read().strip()\n"
        "print('maintainers:' + listed)\n"
        "print('args:' + ' '.join(sys.argv[3:]))\n"
        f"sys.exit({code})\n"
    )


_CHECK = "scripts/check_commit_provenance.py"


def test_a_push_is_judged_by_the_previous_commits_check_and_maintainers(tmp_path: Path) -> None:
    """The pushed commit edits the check to pass itself and adds its committer to MAINTAINERS.
    Neither is what runs: the previous commit's copies run, isolated (`python3 -I`)."""
    pushes = Pushes(tmp_path / "main")
    before = pushes.commit({_CHECK: _stand_in("before", 0), "MAINTAINERS": "before-list"})
    after = pushes.commit({_CHECK: _stand_in("pushed", 0), "MAINTAINERS": "pushed-list"})
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ran:before isolated" in done.stdout
    assert "maintainers:before-list" in done.stdout
    assert f"args:--mode main --range {before}..{after}" in done.stdout
    assert "pushed" not in done.stdout


def test_the_previous_commits_verdict_is_the_jobs(tmp_path: Path) -> None:
    pushes = Pushes(tmp_path / "main")
    before = pushes.commit({_CHECK: _stand_in("before", 1), "MAINTAINERS": "m"})
    after = pushes.commit({_CHECK: _stand_in("pushed", 0)})
    assert pushes.push(before, after, tmp_path).returncode == 1


def test_the_push_that_adds_the_check_is_judged_by_its_own_copy(tmp_path: Path) -> None:
    """The one push with no previous copy is the one that lands the check: the previous main
    commit's history never held it. The summary says so."""
    pushes = Pushes(tmp_path / "main")
    before = pushes.commit({"README": "x"})
    after = pushes.commit({_CHECK: _stand_in("pushed", 0), "MAINTAINERS": "pushed-list"})
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ran:pushed isolated" in done.stdout
    assert "never had the check" in (tmp_path / "summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "before",
    ["0000000000000000000000000000000000000000", "e" * 40],
    ids=["all-zeros", "a-commit-the-checkout-does-not-hold"],
)
def test_a_previous_commit_that_does_not_resolve_refuses_the_push(
    tmp_path: Path, before: str
) -> None:
    """A BEFORE the checkout cannot resolve (a branch re-created, or an old tip a forced update
    orphaned) is not one that never had the check: the job refuses it before choosing a copy."""
    pushes = Pushes(tmp_path / "main")
    pushes.commit({"README": "x"})
    after = pushes.commit({_CHECK: _stand_in("pushed", 0), "MAINTAINERS": "pushed-list"})
    done = pushes.push(before, after, tmp_path)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "ran:" not in done.stdout


def test_a_previous_commit_that_lost_the_check_refuses_the_push(tmp_path: Path) -> None:
    """Deleting the check must not open a push that judges itself with its own copy."""
    pushes = Pushes(tmp_path / "main")
    pushes.commit({_CHECK: _stand_in("old", 0), "MAINTAINERS": "m"})
    before = pushes.commit({_CHECK: None})
    after = pushes.commit({_CHECK: _stand_in("pushed", 0)})
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ran:" not in done.stdout
