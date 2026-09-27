"""No value in the test code has a real token's format, and a provider's key shape stays where a
test needs it.

This repository is public, and secret scanners read public code: a placeholder written in a
provider's token format is reported as a leaked credential, and a real-looking one teaches the
next contributor that pasting a key into a test is normal. So test code follows two rules.

1. **No test value matches a published token format**: an AWS key id in that alphabet, a GitHub
   token of its real length, a fine-grained PAT, a Slack token with its number groups, a Stripe
   key, a signed JWT, a whole private-key block. The AWS documentation's example values (ending
   ``EXAMPLE``) are the one exception: public, fake by construction, and known to every scanner.
   A redaction test that needs a PEM header or a JWT writes it split (``"-----" "BEGIN …"``): the
   value under test is the same, the source is not a key.
2. **A provider's key prefix appears only where the shape is what is tested.** A value such as a
   ``sk-ant-…``, ``ghp_…`` or ``xoxb-…`` placeholder stays only in the files below, each of which
   exercises code that recognises a credential by its value's shape (a redactor, a masker, a
   secret lint, the capture store); their tests fail when the value loses its shape (measured
   literal by literal). Everywhere else a test uses a neutral fake (``fake-anthropic-test``),
   because nothing on that path reads the shape.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Published formats (rule 1). Written with character classes so this file matches none of them.
_REAL_FORMATS = (
    re.compile(r"\b(?:A3T[A-Z0-9]|A[K]IA|A[S]IA|A[B]IA|A[C]CA)[A-Z2-7]{16}\b"),
    re.compile(r"\bg[h][pousr]_[0-9a-zA-Z]{36}"),
    re.compile(r"g[i]thub_pat_\w{82}"),
    re.compile(r"x[o]xb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*"),
    re.compile(r"x[o]x[pe](?:-[0-9]{10,13}){3}-[a-zA-Z0-9-]{28,34}"),
    re.compile(r"(?i)x[a]pp-\d-[A-Z0-9]+-\d+-[a-z0-9]+"),
    re.compile(r"s[k]-ant-(?:api03|admin01)-[a-zA-Z0-9_\-]{93}AA"),
    re.compile(r"s[k]-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{58,74}T3BlbkFJ"),
    re.compile(r"s[k]-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20}"),
    re.compile(r"h[f]_[a-zA-Z]{34}"),
    re.compile(r"(?:s[k]|r[k])_(?:test|live|prod)_[a-zA-Z0-9]{10,99}"),
    re.compile(r"A[I]za[0-9A-Za-z\-_]{35}"),
    re.compile(r"g[s]k_[a-zA-Z0-9]{52}"),
    re.compile(
        r"-{5}B[E]GIN[ A-Z0-9_-]{0,100}PRIVATE KEY( BLOCK)?-{5}[\s\S-]{64,}?KEY( BLOCK)?-{5}"
    ),
    re.compile(
        r"\be[y][a-zA-Z0-9]{17,}\.e[y][a-zA-Z0-9/\\_-]{17,}\.(?:[a-zA-Z0-9/\\_-]{10,}={0,2})?"
    ),
)

#: Provider key prefixes (rule 2), however short the value after them.
_PREFIXED = re.compile(
    r"s[k]-ant-|(?<![A-Za-z0-9_])s[k]-[A-Za-z0-9_\-]{2,}"
    r"|(?<![A-Za-z0-9])(?:A[K]IA|A[S]IA)[0-9A-Z]{4,}"
    r"|(?<![A-Za-z0-9])g[h][pousr]_[A-Za-z0-9]{2,}|g[i]thub_pat_"
    r"|(?<![A-Za-z0-9])x[o]x[abeoprs]-[A-Za-z0-9\-]{2,}|(?<![A-Za-z0-9])x[a]pp-[0-9A-Za-z\-]{2,}"
    r"|(?<![A-Za-z0-9_])h[f]_[A-Za-z0-9]{6,}"
)

#: The AWS documentation's example values: exempt from both rules.
_DOCUMENTED_EXAMPLE = re.compile(r"EXAMPLE")

#: Rule 2's files: each exercises code that recognises a credential by its value's shape, and its
#: tests fail when the value loses that shape.
_SHAPE_TESTS = frozenset(
    {
        "tests/fixtures/agent_tool_homes/noor/.claude/.claude.json",
        "tests/fixtures/agent_tool_homes/noor/.codex/config.toml",
        "tests/smoke_sandbox.sh",
        "tests/test_a_masked_value_never_overwrites_the_real_one.py",
        "tests/test_approval_path_tool_input_shapes.py",
        "tests/test_approval_read_only_supply.py",
        "tests/test_channel_approvals_show_what_will_run.py",
        "tests/test_channels_are_handed_masked_text.py",
        "tests/test_claude_code_import_reads_what_claude_code_writes.py",
        "tests/test_codex_import_reads_what_codex_writes.py",
        "tests/test_computer_use_dispatch.py",
        "tests/test_computer_use_live_view.py",
        "tests/test_computer_use_policy.py",
        "tests/test_diagnostics_log_redaction.py",
        "tests/test_download_filename_header.py",
        "tests/test_capture_import.py",
        "tests/test_capture_staging.py",
        "tests/test_capture_store.py",
        "tests/test_evals_harvest.py",
        "tests/test_evals_study_arms.py",
        "tests/test_imports_share_one_secret_policy.py",
        "tests/test_inbound_mcp.py",
        "tests/test_learning_template_gate.py",
        "tests/test_ledger_golden.py",
        "tests/test_legibility_always_on.py",
        "tests/test_local_model_hf_token.py",
        "tests/test_mcp_import_list_shows_no_secret_and_reads_claude_config_dir.py",
        "tests/test_no_read_shows_what_its_twin_masks.py",
        "tests/test_onboarding_import_api.py",
        "tests/test_onboarding_import.py",
        "tests/test_onboarding_model_check.py",
        "tests/test_ledger_rails.py",
        "tests/test_run_deliverable.py",
        "tests/test_provider_boundary_residue.py",
        "tests/test_redaction_cost.py",
        "tests/test_redaction_mask_never_persists.py",
        "tests/test_rooms_store.py",
        "tests/test_run_ledger_redaction.py",
        "tests/test_security_audit_api.py",
        "tests/test_security.py",
        "tests/test_session_share.py",
        "tests/test_session_starters.py",
        "tests/test_trigger_list_projection_parity.py",
        "tests/test_triggers_delivery.py",
        "tests/test_triggers_facade_store.py",
        "tests/test_triggers_history.py",
        "tests/test_triggers_secrets.py",
        "tests/test_what_a_model_is_handed_is_masked.py",
        "tests/test_workflows_confirmation.py",
        "tests/test_workflows_project_archive.py",
        "tests/test_workflows_scope.py",
    }
)

_TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".ts",
    ".tsx",
    ".js",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
}
_SKIP_PARTS = {"node_modules", ".venv", "__pycache__", "dist", "build"}


def _test_files() -> list[Path]:
    """Test code: everything under ``tests/``, and the web's own test and e2e files."""
    found: list[Path] = []
    for base, pick in (
        (ROOT / "tests", lambda p: True),
        (ROOT / "web" / "src", lambda p: ".test." in p.name or ".spec." in p.name),
        (ROOT / "web" / "e2e", lambda p: True),
    ):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if (
                path.is_file()
                and path.suffix in _TEXT_SUFFIXES
                and not _SKIP_PARTS & set(path.relative_to(ROOT).parts)
                and pick(path)
                and path.resolve() != Path(__file__).resolve()
            ):
                found.append(path)
    return found


def _real_formats(text: str) -> list[str]:
    return [
        m.group(0)[:24]
        for rx in _REAL_FORMATS
        for m in rx.finditer(text)
        if not _DOCUMENTED_EXAMPLE.search(m.group(0))
    ]


def _prefixed(text: str) -> list[str]:
    return [
        m.group(0)[:24]
        for m in _PREFIXED.finditer(text)
        if not _DOCUMENTED_EXAMPLE.search(text[m.start() : m.end() + 16])
    ]


def _census() -> dict[str, tuple[list[str], list[str]]]:
    out: dict[str, tuple[list[str], list[str]]] = {}
    for path in _test_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        real, prefixed = _real_formats(text), _prefixed(text)
        if real or prefixed:
            out[path.relative_to(ROOT).as_posix()] = (real, prefixed)
    return out


def test_no_test_value_has_a_published_token_format():
    real = {path: hits for path, (hits, _) in _census().items() if hits}
    assert real == {}, (
        "A test value has a real token's format (a secret scanner reports it as a leaked "
        "credential). Use a neutral fake, or split a PEM header or JWT the test needs: "
        f"{real}"
    )


def test_a_provider_key_prefix_appears_only_where_the_shape_is_tested():
    prefixed = {path for path, (_, hits) in _census().items() if hits}
    new = sorted(prefixed - _SHAPE_TESTS)
    assert not new, (
        "These test files carry a provider-key-shaped placeholder: use a neutral fake "
        "(`fake-anthropic-test`), or, when the test is about recognising a key by its shape, add "
        f"the file to _SHAPE_TESTS in {Path(__file__).name}: {new}"
    )
    stale = sorted(_SHAPE_TESTS - prefixed)
    assert not stale, f"no key-shaped value left in these; remove them from _SHAPE_TESTS: {stale}"


def test_the_detectors_see_what_they_are_for():
    """Positive control. Assembled at run time, so this file holds none of them."""
    real = [
        "A" + "KIA" + "WWWWWWWWWWWWWWWW",
        "gh" + "p_" + "a1" * 18,
        "sk" + "_live_" + "9f8e7d6c5b4a39",
        "-----" + "BEGIN PRIVATE KEY-----\n" + "M" * 70 + "\n-----" + "END PRIVATE KEY-----",
        "ey" + "J" + "a" * 18 + ".ey" + "J" + "b" * 18 + "." + "c" * 12,
    ]
    for sample in real:
        assert _real_formats(sample), sample
    assert not _real_formats("A" + "KIA" + "IOSFODNN7EXAMPLE")
    assert not _real_formats("-----" + "BEGIN RSA PRIVATE KEY-----\nMIIE")  # a header alone
    for sample in ("s" + "k-ant-test", "g" + "hp_fixture", "x" + "oxb-saved", "h" + "f_explicit"):
        assert _prefixed(f'"{sample}"'), sample
    assert not _prefixed('"fake-anthropic-test" "task-queue" "mask-this"')


def test_the_census_reads_the_test_code():
    """Vacuity floor: the walk reaches the tests, and the web's own tests."""
    files = _test_files()
    assert len(files) > 2000, len(files)
    assert any(p.suffix == ".tsx" for p in files)
