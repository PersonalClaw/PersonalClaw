"""A comment or a docstring is not behaviour, so the install scanner's warning rules skip it.

The warning rules read a Python file's raw text. ``reads_sensitive_path`` skipped a line that
starts with ``#`` and nothing else, and the other warning rules skipped nothing, so a docstring
explaining that a read of ``~/.aws/credentials`` is refused reached the install-consent card as
"This code reads a file where credentials and keys are kept." Two of a first-party channel app's
three findings were exactly that.

What is commentary is decided by Python's own tokeniser and the AST — a ``COMMENT`` token, or a
module, class or function docstring — never by how a line looks. A match in a string the code
holds is not commentary and still counts, a file that does not parse keeps every finding, and a
finding that also matches code shows the code as its evidence rather than the comment.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.supply_chain import Verdict, scan_dir

_MANIFEST = '{"name": "demo-app", "version": "0.1.0", "provider": {"implementation": "provider:x"}}'


def _scan(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "staged"
    for rel, content in {"app.json": _MANIFEST, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return scan_dir(root)


def _hits(report, rule: str):
    return [f for f in report.findings if f.rule == rule]


def test_a_credential_path_named_in_a_docstring_is_not_a_read(tmp_path):
    report = _scan(
        tmp_path,
        {
            "provider.py": (
                "def agent_names():\n"
                '    """Read each agent file through the safe reader, so a link into a sensitive\n'
                "    path (e.g. ``~/.aws/credentials``) is refused.\n"
                '    """\n'
                "    return []\n"
            ),
        },
    )
    assert _hits(report, "reads_sensitive_path") == []
    assert report.verdict is Verdict.CLEAN


def test_a_credential_path_named_in_an_inline_comment_is_not_a_read(tmp_path):
    report = _scan(
        tmp_path, {"provider.py": "owner = load_owner()  # kept in ~/.personalclaw/.env\n"}
    )
    assert _hits(report, "reads_sensitive_path") == []


def test_a_command_named_only_in_commentary_is_not_reported(tmp_path):
    report = _scan(
        tmp_path,
        {
            "provider.py": (
                '"""Fetch a feed; the README shows the same request made with wget."""\n'
                "# by hand it is: curl https://example.com/feed.json\n"
                "def fetch():\n"
                "    return None\n"
            ),
        },
    )
    assert _hits(report, "curl_network") == []
    assert report.verdict is Verdict.CLEAN


def test_a_real_read_beside_a_docstring_is_reported_with_the_read_as_evidence(tmp_path):
    report = _scan(
        tmp_path,
        {
            "provider.py": (
                '"""Mentions ~/.aws/credentials only to say it is never read."""\n'
                "import os\n"
                'creds = open(os.path.expanduser("~/.aws/credentials")).read()\n'
            ),
        },
    )
    (hit,) = _hits(report, "reads_sensitive_path")
    assert hit.evidence.startswith("L3: ") and "open(" in hit.evidence, hit.evidence
    assert report.verdict is Verdict.WARNING


def test_a_comment_line_is_never_the_evidence_for_a_read_below_it(tmp_path):
    report = _scan(
        tmp_path,
        {"scripts/backup.sh": "# never cat ~/.ssh/id_rsa here\ncat ~/.ssh/id_rsa > /tmp/k\n"},
    )
    (hit,) = _hits(report, "reads_sensitive_path")
    assert hit.evidence.startswith("L2: ") and "cat ~/.ssh/id_rsa" in hit.evidence, hit.evidence


def test_a_command_in_a_string_the_code_holds_still_counts(tmp_path):
    report = _scan(tmp_path, {"provider.py": 'CMD = "curl https://example.com/feed.json"\n'})
    assert len(_hits(report, "curl_network")) == 1
    assert report.verdict is Verdict.WARNING


def test_a_file_that_does_not_parse_keeps_its_findings(tmp_path):
    report = _scan(tmp_path, {"provider.py": "def broken(:\n    # curl https://example.com\n"})
    assert len(_hits(report, "curl_network")) == 1


def test_a_carriage_return_does_not_hide_code_behind_a_comment(tmp_path):
    """Python ends a line at a bare carriage return, so the read after it is code."""
    source = "# note\rimport os\n" + 'creds = open(os.path.expanduser("~/.aws/credentials"))\n'
    report = _scan(tmp_path, {"provider.py": source})
    assert len(_hits(report, "reads_sensitive_path")) == 1
