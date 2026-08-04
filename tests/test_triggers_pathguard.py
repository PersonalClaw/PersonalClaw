"""PathGuard: the `paths` capability, compared as paths rather than strings (decision 7 — S118).

🔴 THE DEFECT. `paths` has been a fail-closed member of `CAPABILITY_KEYS` since S69 and is
rendered as a fence in the UI — but `capability_allows` compared it with `_matches_entry`, which
is prefix matching built for tool names like `mcp__github__*`. Measured against the real function
before a line was written, with the allowlist `["/Users/me/notes/*"]`:

    ALLOW  /Users/me/notes/../../.ssh/id_rsa        # 🔴 traversal, permitted
    ALLOW  /Users/me/notes/../.aws/credentials      # 🔴 traversal, permitted

So a trigger fenced to a notes directory could reach an SSH key, and the ledger would record the
fire as permitted. The fence was not weak — it was measuring the wrong thing.

Every path case here is built on REAL files and REAL symlinks under `tmp_path`. A test asserting
against string literals would be testing the same wrong model the bug came from.
"""

from __future__ import annotations

import os

import pytest

from personalclaw.triggers.pathguard import (
    canonicalize,
    is_within,
    path_allowed,
    unsafe_entries,
)
from personalclaw.triggers.screen import capability_allows


@pytest.fixture
def tree(tmp_path):
    """A real directory tree with the three shapes that matter: an allowed scope, an outside
    secret, and a prefix-sibling whose name starts with the scope's name."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "today.md").write_text("hi")
    (tmp_path / "secrets.txt").write_text("s")
    sibling = tmp_path / "notesEVIL"
    sibling.mkdir()
    (sibling / "p.txt").write_text("p")
    return tmp_path


# ── canonicalize ──


def test_it_resolves_dotdot(tree):
    assert canonicalize(str(tree / "notes" / ".." / "secrets.txt")) == str(
        (tree / "secrets.txt").resolve()
    )


def test_it_expands_a_tilde():
    assert canonicalize("~").startswith(os.path.expanduser("~")[:4])


def test_an_empty_path_is_empty_not_the_cwd():
    """ "" must not canonicalize to the process's working directory — that would silently turn a
    missing path into a real, and possibly allowlisted, location."""
    assert canonicalize("") == ""
    assert canonicalize(None) == ""  # type: ignore[arg-type]


# ── is_within ──


def test_a_file_inside_the_root_is_within(tree):
    assert is_within(str(tree / "notes" / "today.md"), str(tree / "notes")) is True


def test_the_root_itself_is_within_the_root(tree):
    assert is_within(str(tree / "notes"), str(tree / "notes")) is True


def test_a_PREFIX_SIBLING_is_NOT_within(tree):
    """🔴 The classic `startswith` bug: `/x/notes` "contains" `/x/notesEVIL`. It survives review
    because the code looks obviously correct, which is why `commonpath` decides instead."""
    assert is_within(str(tree / "notesEVIL" / "p.txt"), str(tree / "notes")) is False


def test_a_TRAVERSAL_out_of_the_root_is_not_within(tree):
    assert is_within(str(tree / "notes" / ".." / "secrets.txt"), str(tree / "notes")) is False


def test_a_SYMLINK_pointing_out_of_the_root_is_not_within(tree):
    """The half canonicalizing the candidate alone would miss if it did not follow links: a file
    that LIVES inside the scope but IS the outside secret."""
    link = tree / "notes" / "escape"
    link.symlink_to(tree / "secrets.txt")
    assert is_within(str(link), str(tree / "notes")) is False


def test_a_SYMLINKED_ROOT_is_matched_at_its_TARGET(tree):
    """The allowlist side. A watched directory that is itself a link must compare at its target,
    or the fence means something different from what it names."""
    real = tree / "real-notes"
    real.mkdir()
    (real / "a.md").write_text("a")
    link_root = tree / "linked-notes"
    link_root.symlink_to(real)
    assert is_within(str(real / "a.md"), str(link_root)) is True


# ── the fence, through the REAL capability_allows ──


def test_a_file_in_scope_is_ALLOWED(tree):
    caps = {"paths": [f"{tree / 'notes'}/*"]}
    assert capability_allows(caps, key="paths", value=str(tree / "notes" / "today.md")).allowed


def test_a_file_out_of_scope_is_DENIED(tree):
    caps = {"paths": [f"{tree / 'notes'}/*"]}
    assert not capability_allows(caps, key="paths", value=str(tree / "secrets.txt")).allowed


def test_THE_TRAVERSAL_IS_NOW_REFUSED(tree):
    """🔴 THE DEFECT, pinned. This exact assertion failed before this session: it ALLOWED."""
    caps = {"paths": [f"{tree / 'notes'}/*"]}
    escape = str(tree / "notes" / ".." / "secrets.txt")
    decision = capability_allows(caps, key="paths", value=escape)
    assert decision.allowed is False
    assert "outside" in decision.reason


def test_the_refusal_names_BOTH_the_resolved_and_the_written_path(tree):
    """An author who wrote `notes/../secrets.txt` needs to see what it RESOLVED to; otherwise the
    refusal reads as a false positive against the text they typed."""
    caps = {"paths": [f"{tree / 'notes'}/*"]}
    escape = str(tree / "notes" / ".." / "secrets.txt")
    reason = capability_allows(caps, key="paths", value=escape).reason
    assert "secrets.txt" in reason
    assert ".." in reason, "the reason must echo what the author actually wrote"


def test_a_prefix_sibling_is_refused_through_the_fence(tree):
    caps = {"paths": [str(tree / "notes")]}
    assert not capability_allows(caps, key="paths", value=str(tree / "notesEVIL")).allowed


# ── the refusal discipline, mirrored from capability_allows ──


def test_no_paths_block_denies():
    assert path_allowed(None, "/tmp/x")[0] is False
    assert path_allowed([], "/tmp/x")[0] is False


def test_a_STRING_allowlist_is_refused_not_coerced():
    """`{"paths": "/Users/me/notes"}` LOOKS like a grant. Coercing it would make a malformed fence
    work, which teaches people to write it that way."""
    allowed, reason = path_allowed("/Users/me/notes", "/Users/me/notes/a.md")
    assert allowed is False
    assert "must be a list" in reason


def test_a_non_string_entry_is_skipped_not_crashed(tree):
    caps = {"paths": [None, 42, f"{tree / 'notes'}/*"]}
    assert capability_allows(caps, key="paths", value=str(tree / "notes" / "today.md")).allowed


def test_an_unresolvable_candidate_FAILS_CLOSED():
    """Fail-closed here, opposite to the kill switch, and deliberately: a stuck-open path fence
    hands out filesystem access nobody granted, while a stuck-closed kill switch merely halts work.
    When in doubt about REACH, refuse."""
    assert path_allowed(["/tmp"], "")[0] is False
    assert path_allowed(["/tmp"], "\x00bad")[0] is False


# ── bypass_immune ──


def test_a_SENSITIVE_path_is_refused_even_when_ALLOWLISTED():
    """🔴 decision 7 reserves checks "no allowlist may silence". An entry naming `~/.ssh` is far
    likelier to be a mistake — or an edit nobody intended — than a real grant."""
    ssh = os.path.expanduser("~/.ssh")
    allowed, reason = path_allowed([ssh], os.path.join(ssh, "id_rsa"))
    assert allowed is False
    assert "sensitive" in reason


def test_the_sensitive_check_runs_BEFORE_the_allowlist():
    """Ordering is the control. Checked after a match, a matching entry would short-circuit it and
    the immunity would be decorative."""
    ssh = os.path.expanduser("~/.ssh")
    reason = path_allowed(["/", ssh], os.path.join(ssh, "id_rsa"))[1]
    assert "sensitive" in reason, "a broad entry must not pre-empt the immunity check"


# ── the other capability keys are untouched ──


def test_the_TOOLS_key_still_uses_prefix_globs():
    """PathGuard is routed by key. A change that applied path semantics to `tools` would break
    every `mcp__github__*` fence in existence."""
    assert capability_allows({"tools": ["mcp__github__*"]}, key="tools", value="mcp__github__x")


def test_the_PROVIDERS_key_still_matches_exactly():
    assert capability_allows({"providers": ["bash"]}, key="providers", value="bash").allowed
    assert not capability_allows({"providers": ["bash"]}, key="providers", value="bashx").allowed


# ── the doctor's unbounded-fence finding ──


def test_a_bare_star_is_reported_as_bounding_NOTHING():
    """A fence the user believes in that grants everything is this program's recurring failure."""
    assert [e for e, _ in unsafe_entries(["*"])] == ["*"]
    assert [e for e, _ in unsafe_entries(["/*"])] == ["/*"]


def test_a_RELATIVE_entry_is_reported():
    """It resolves against the GATEWAY's cwd, so it means different things depending on how the
    gateway was started — indistinguishable from a broken fence when it eventually denies."""
    found = unsafe_entries(["notes/*"])
    assert found and "relative" in found[0][1]


def test_an_absolute_scoped_entry_is_NOT_reported():
    """The fix for a finding must never trip the finding."""
    assert unsafe_entries(["/Users/me/notes/*"]) == []


def test_the_doctor_reports_an_unbounded_path_fence(tmp_path):
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:file:notes", "capabilities": {"paths": ["*"]}}]
    findings = diagnose(rows, known_workflows=None).findings
    finding = next(f for f in findings if f.code == "unbounded_path_fence")
    assert "bounds nothing" in finding.detail
    assert finding.fix


def test_the_doctor_is_SILENT_for_a_real_scope():
    from personalclaw.triggers.calendar import diagnose

    rows = [{"id": "schedule:file:notes", "capabilities": {"paths": ["/Users/me/notes/*"]}}]
    findings = diagnose(rows, known_workflows=None).findings
    assert not [f for f in findings if f.code == "unbounded_path_fence"]
