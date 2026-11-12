"""The credential mask covers a whole private key block, header to END line.

The mask took a key's header words and left its base64 body and its END line in the text, and a
PKCS#8 or encrypted key's header did not match at all; a decoded body holds no pattern, so the
base64 pass could not take it either. Each form the mask reads is a case here: its lines broken,
escaped as a JSON string writes them (once or twice), run onto one line, indented, marked as a diff,
a quote or a comment marks them, written as string literals in code, with a legacy key's headers,
an OpenPGP key's armor, and cut short with no END line. The inverses keep a block masked across its
lines.

No key is written in this file. Each block is assembled when the test runs, its header split and its
body the base64 of counted bytes, so the source holds no key a scanner would report.
"""

from __future__ import annotations

import base64
import json

import pytest

from personalclaw.security import (
    MaskConflict,
    keep_masked_lines,
    keep_masked_spans,
    masked_edit,
    redact_credentials,
    redact_exfiltration_urls,
    redact_for_display,
    restore_masked_spans,
)

pytestmark = pytest.mark.security

DASHES = "-" * 5
MASK = "[REDACTED: private key]"
LABELS = [
    "RSA PRIVATE KEY",
    "DSA PRIVATE KEY",
    "EC PRIVATE KEY",
    "OPENSSH PRIVATE KEY",
    "PRIVATE KEY",
    "ENCRYPTED PRIVATE KEY",
]
#: A legacy encrypted key's headers as OpenSSL writes them, and the blank line after them.
LEGACY = (
    "Proc-Type: 4,ENCRYPTED",
    "DEK-Info: AES-128-CBC,00112233445566778899AABBCCDDEEFF",
    "",
)


def _body(seed: int = 7, size: int = 290) -> list[str]:
    """Base64 lines of 64, as a PEM body is wrapped; 290 bytes end in a short line padded with =."""
    raw = bytes((seed * 31 + i * 17) % 256 for i in range(size))
    text = base64.b64encode(raw).decode()
    return [text[i : i + 64] for i in range(0, len(text), 64)]


def _block(
    label: str,
    *,
    sep: str = "\n",
    end: bool = True,
    headers: tuple[str, ...] = (),
    lines: list[str] | None = None,
) -> str:
    lines = [f"{DASHES}BEGIN {label}{DASHES}", *headers, *(_body() if lines is None else lines)]
    if end:
        lines.append(f"{DASHES}END {label}{DASHES}")
    return sep.join(lines)


def _gone(masked: str, body: list[str] | None = None) -> None:
    """Nothing of the block is left: no body line, no header or END line."""
    for line in _body() if body is None else body:
        assert line not in masked, line
    assert "BEGIN" not in masked and "END " not in masked, masked


def _marked(mark: str, text: str) -> str:
    return "\n".join(mark + line for line in text.split("\n"))


@pytest.mark.parametrize("label", LABELS)
def test_a_multi_line_block_of_each_kind_is_masked_whole(label: str) -> None:
    text = f"the deploy key is below.\n{_block(label)}\nkeep it out of the repo"
    masked, warnings = redact_credentials(text)
    assert masked == f"the deploy key is below.\n{MASK}\nkeep it out of the repo"
    assert any(label in w for w in warnings), warnings


@pytest.mark.parametrize("brk", ["\r\n", "\r"], ids=["windows", "a lone CR"])
def test_a_block_with_other_line_breaks_is_masked_whole(brk: str) -> None:
    masked, _ = redact_credentials(f"a{brk}{_block('PRIVATE KEY', sep=brk)}{brk}b")
    assert masked == f"a{brk}{MASK}{brk}b"


@pytest.mark.parametrize(
    "brk", ["\n", "\r\n", "\r", "\n\t"], ids=["LF", "CRLF", "a lone CR", "a tab indent"]
)
def test_a_block_a_json_string_escapes_is_masked_whole_and_the_document_parses(brk: str) -> None:
    doc = json.dumps({"key": _block("PRIVATE KEY", sep=brk) + brk, "note": "rotated"})
    masked, _ = redact_credentials(doc)
    _gone(masked)
    assert json.loads(masked) == {"key": f"{MASK}{brk}", "note": "rotated"}


def test_a_key_file_inside_a_tools_json_input_is_masked_whole() -> None:
    """A service account's key file, written by a tool: its JSON is a string in the tool's JSON, so
    each line break is escaped twice."""
    key_file = json.dumps({"type": "service_account", "private_key": _block("PRIVATE KEY") + "\n"})
    masked, _ = redact_credentials(json.dumps({"path": "sa.json", "content": key_file}))
    _gone(masked)
    assert json.loads(json.loads(masked)["content"])["private_key"] == f"{MASK}\n"


def test_a_json_writer_that_escapes_slashes_is_masked_whole() -> None:
    doc = json.dumps({"key": _block("PRIVATE KEY")}).replace("/", "\\/")
    masked, _ = redact_credentials(doc)
    assert json.loads(masked) == {"key": MASK}


@pytest.mark.parametrize("sep", [" ", "\t", ""], ids=["spaces", "tabs", "nothing"])
def test_a_block_run_onto_one_line_is_masked_whole(sep: str) -> None:
    text = f"KEY={_block('RSA PRIVATE KEY', sep=sep)} next"
    masked, _ = redact_credentials(text)
    assert masked == f"KEY={MASK} next"


@pytest.mark.parametrize("indent", ["    ", "\t"], ids=["spaces", "a tab"])
def test_an_indented_block_under_a_yaml_key_is_masked_whole(indent: str) -> None:
    block = _block("EC PRIVATE KEY", sep="\n" + indent)
    masked, _ = redact_credentials(f"tls:\n  key: |\n{indent}{block}\n  cert: none\n")
    assert masked == f"tls:\n  key: |\n{indent}{MASK}\n  cert: none\n"


@pytest.mark.parametrize(
    "headers",
    [
        LEGACY,
        ("Proc-Type: 4, ENCRYPTED", *LEGACY[1:]),
        (*LEGACY[:2], "Comment: ada's laptop key", ""),
    ],
    ids=["as OpenSSL writes them", "a value with a space", "a comment"],
)
@pytest.mark.parametrize("sep", ["\n", " "], ids=["lines", "one line"])
def test_a_legacy_encrypted_key_with_its_headers_is_masked_whole(
    headers: tuple[str, ...], sep: str
) -> None:
    """Run onto one line, a header's value stops at the next header or the body, so the words after
    the END line stay."""
    text = f"x{sep}{_block('RSA PRIVATE KEY', sep=sep, headers=headers)}{sep}y"
    masked, _ = redact_credentials(text)
    assert masked == f"x{sep}{MASK}{sep}y"


def test_a_legacy_key_run_onto_one_line_in_a_json_string_still_parses() -> None:
    doc = json.dumps({"key": _block("RSA PRIVATE KEY", sep=" ", headers=LEGACY), "note": "rotated"})
    masked, _ = redact_credentials(doc)
    assert json.loads(masked) == {"key": MASK, "note": "rotated"}


@pytest.mark.parametrize(
    "after",
    [
        "\n  cert: abc\n  port: 443\nother: 1\n",
        " block: unexpected EOF at byte 12",
        "\nError: file truncated at 3 bytes\nnext line",
    ],
    ids=["yaml lines", "words on its line", "a capitalised name"],
)
def test_a_header_line_with_no_key_after_it_keeps_the_text_that_follows(after: str) -> None:
    """A header counts as a key's only where a body line follows it."""
    masked, _ = redact_credentials(f"key: {DASHES}BEGIN PRIVATE KEY{DASHES}{after}")
    assert masked == f"key: {MASK}{after}"


@pytest.mark.parametrize(
    "mark",
    ["-", "+", " ", "# ", "> ", "> > "],
    ids=["diff removed", "diff added", "diff context", "comment", "quote", "a quote of a quote"],
)
def test_a_block_whose_lines_carry_a_mark_is_masked_whole(mark: str) -> None:
    masked, _ = redact_credentials(f"before\n{_marked(mark, _block('RSA PRIVATE KEY'))}\nafter")
    _gone(masked)
    assert masked == f"before\n{mark}{MASK}\nafter"


def test_a_diff_that_replaces_a_key_masks_both_bodies() -> None:
    old, new = _body(7), _body(9)
    lines = [
        f" {DASHES}BEGIN RSA PRIVATE KEY{DASHES}",
        *("-" + line for line in old),
        *("+" + line for line in new),
        f" {DASHES}END RSA PRIVATE KEY{DASHES}",
        " port = 22",
    ]
    masked, _ = redact_credentials("\n".join(lines))
    _gone(masked, old + new)
    assert masked == f" {MASK}\n port = 22"


@pytest.mark.parametrize(
    ("opening", "joint", "closing", "quote"),
    [
        ("KEY = (\n    ", '\\n"\n    "', '\\n"\n)', '"'),
        ("String key = ", '\\n" +\n    "', '\\n";', '"'),
        ("const key = ", "\\n' +\n  '", "';", "'"),
    ],
    ids=["python", "java", "javascript"],
)
def test_a_key_written_as_string_literals_in_code_is_masked_whole(
    opening: str, joint: str, closing: str, quote: str
) -> None:
    text = opening + quote + _block("PRIVATE KEY").replace("\n", joint) + closing
    masked, _ = redact_credentials(text)
    _gone(masked)
    assert masked == opening + quote + MASK + closing


BEGIN_LINE = f"{DASHES}BEGIN PRIVATE KEY{DASHES}"
END_LINE = f"{DASHES}END PRIVATE KEY{DASHES}"


@pytest.mark.parametrize(
    "code",
    [
        f'HEADER = "{BEGIN_LINE}"\nFOOTER = "{END_LINE}"',
        f'PEM = ("{BEGIN_LINE}", "{END_LINE}")',
        f'pem = "{BEGIN_LINE}" + "\\n" + body + "{END_LINE}"',
    ],
    ids=["two constants", "a tuple", "built around a variable"],
)
def test_code_that_names_the_header_and_end_lines_keeps_the_code_between(code: str) -> None:
    """A header named in code is a key named in text: its words go, and the END line and the code
    between them stay."""
    masked, _ = redact_credentials(code)
    assert masked == code.replace(BEGIN_LINE, MASK)


@pytest.mark.parametrize(
    "lines",
    [_body(), _body()[:3] + [_body()[3][:20]]],
    ids=["whole lines", "cut mid-line"],
)
def test_a_block_cut_short_is_masked_through_its_last_body_line(lines: list[str]) -> None:
    text = f"cut:\n{_block('PRIVATE KEY', end=False, lines=lines)}\n... and the run failed"
    masked, _ = redact_credentials(text)
    _gone(masked, lines)
    assert masked == f"cut:\n{MASK}\n... and the run failed"


@pytest.mark.parametrize("words", ["Thanks, please rotate it", "Done", "---"])
def test_a_cut_block_leaves_the_words_after_it(words: str) -> None:
    lines = [f"{DASHES}BEGIN OPENSSH PRIVATE KEY{DASHES}", *_body()[:3], words]
    masked, _ = redact_credentials("\n".join(lines))
    assert masked == f"{MASK}\n{words}"


def test_a_short_unpadded_last_line_before_the_end_line_is_masked() -> None:
    body = _body(size=294)
    assert len(body[-1]) == 8 and not body[-1].endswith("=")
    masked, _ = redact_credentials(f"a\n{_block('RSA PRIVATE KEY', lines=body)}\nb")
    assert masked == f"a\n{MASK}\nb"


@pytest.mark.parametrize("size", [300, 291, 294], ids=["full last line", "padded", "unpadded"])
@pytest.mark.parametrize("checksum", [True, False], ids=["checksum", "no checksum"])
def test_an_openpgp_private_key_block_is_masked_whole(size: int, checksum: bool) -> None:
    """Its armor headers come before a blank line, and its checksum line before the END line."""
    armor = ("Version: GnuPG v2", "Comment: GPGTools - https://gpgtools.org", "")
    lines = _body(3, size) + (["=AbCd"] if checksum else [])
    block = _block("PGP PRIVATE KEY BLOCK", headers=armor, lines=lines)
    masked, warnings = redact_credentials(f"a\n{block}\nb")
    assert masked == f"a\n{MASK}\nb"
    assert any("PGP PRIVATE KEY BLOCK" in w for w in warnings), warnings


@pytest.mark.parametrize("label", ["PRIVATE KEY", "ENCRYPTED PRIVATE KEY", "PGP PRIVATE KEY BLOCK"])
def test_a_base64_chunk_that_holds_a_key_is_masked(label: str) -> None:
    """A Kubernetes secret's data is the key base64-encoded: decoded, its header is what shows."""
    encoded = base64.b64encode(_block(label).encode()).decode()
    masked, _ = redact_credentials(f"tls.key: {encoded}")
    assert masked == "tls.key: [REDACTED: encoded credential]"


@pytest.mark.parametrize(
    "header",
    [
        "BEGIN%20PRIVATE%20KEY",
        "BEGIN%20ENCRYPTED%20PRIVATE%20KEY",
        "BEGIN%20RSA%20PRIVATE%20KEY",
        "BEGIN+PGP+PRIVATE+KEY+BLOCK",
    ],
)
def test_a_url_whose_query_carries_a_key_header_is_held(header: str) -> None:
    url = f"https://collector.example/c?k={DASHES}{header}{DASHES}"
    masked, warnings = redact_exfiltration_urls(f"see {url} now")
    assert url not in masked and warnings, masked


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        (
            "Server=db;Uid=sa;Pwd=Hunter2Hunter;Database=app",
            "Server=db;Uid=sa;[REDACTED: credential];Database=app",
        ),
        ("pwd=Hunter2Hunter next", "[REDACTED: credential] next"),
        ("SERVER=db;UID=sa;PWD=Hunter2Hunter;", "SERVER=db;UID=sa;[REDACTED: credential];"),
        ("MYSQL_PWD=Hunter2Hunter mysql -u root", "MYSQL_[REDACTED: credential] mysql -u root"),
        ("userpwd=Hunter2Hunter", "user[REDACTED: credential]"),
    ],
    ids=["a connection string", "lower case", "ODBC", "a name that ends in it", "one word"],
)
def test_a_pwd_assignment_is_masked(text: str, masked: str) -> None:
    assert redact_credentials(text)[0] == masked


@pytest.mark.parametrize("name", ["aws_secret_access_key", "SessionToken", "AccessKeyId"])
def test_a_block_right_after_a_cloud_key_name_is_one_mask(name: str) -> None:
    """The block's mask is not read again as the name's value, which would split it."""
    masked = redact_for_display(f"{name}={_block('PRIVATE KEY')}")
    assert masked == f"{name}={MASK}"


@pytest.mark.parametrize(
    "text",
    [
        "PWD=/home/ada/projects/site",
        "OLDPWD=/home/ada/projects/site",
        "PWD=~/projects/site",
        "PWD=${HOME}/projects/site",
        "pwd=/home/ada/projects/site",
        "cur_pwd=/home/ada/site",
        "pwd = os.getcwd()",
        "pwd: Hunter2Hunter",
        "Server=db;Pwd=[REDACTED: credential];Database=app",
    ],
    ids=[
        "a folder",
        "the folder before",
        "a folder at home",
        "a folder from a variable",
        "lower case, a folder",
        "a name that ends in it, a folder",
        "code",
        "a colon",
        "a mask",
    ],
)
def test_a_pwd_that_holds_a_folder_or_is_not_written_against_its_equals_stays(text: str) -> None:
    assert redact_for_display(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Paste your private key into the field below; it starts with a BEGIN line.",
        "ssh-ed25519 " + base64.b64encode(bytes(range(51))).decode() + " ada@laptop",
        "\n".join(
            [f"{DASHES}BEGIN PUBLIC KEY{DASHES}", *_body(), f"{DASHES}END PUBLIC KEY{DASHES}"]
        ),
        "\n".join(
            [f"{DASHES}BEGIN CERTIFICATE{DASHES}", *_body(), f"{DASHES}END CERTIFICATE{DASHES}"]
        ),
        _block("PGP PUBLIC KEY BLOCK", headers=("",), lines=[*_body(3, 300), "=AbCd"]),
    ],
    ids=["prose", "ssh public key", "public key", "certificate", "openpgp public key"],
)
def test_prose_and_public_material_stay_as_they_are(text: str) -> None:
    assert redact_for_display(text) == text


def test_a_second_pass_changes_nothing() -> None:
    once = redact_for_display(f"a\n{_block('PRIVATE KEY')}\nb")
    assert redact_for_display(once) == once


@pytest.mark.parametrize("after", ["\nnext line\n", "\n", ""], ids=["text", "a line break", "end"])
def test_the_masked_view_restores_the_whole_block(after: str) -> None:
    """The literal after a block can be a bare line break, which also occurs inside the block."""
    stored = f"config:\n{_block('PRIVATE KEY')}{after}"
    edited = redact_for_display(stored).replace("config:", "settings:")
    assert restore_masked_spans(edited, stored) == stored.replace("config:", "settings:")
    assert keep_masked_spans(edited, stored) == stored.replace("config:", "settings:")


def test_a_whole_file_write_keeps_the_block_on_its_unchanged_line() -> None:
    stored = f"# deploy\n{_block('OPENSSH PRIVATE KEY')}\n"
    shown = redact_for_display(stored)
    written = keep_masked_lines(shown.replace("# deploy", "# deploy key"), stored)
    assert written == stored.replace("# deploy", "# deploy key")
    with pytest.raises(MaskConflict):
        keep_masked_lines(shown.replace(MASK, f"{MASK} # moved"), stored)


def test_an_edit_beside_the_block_lands_with_the_key_kept() -> None:
    stored = f"port: 22\n{_block('EC PRIVATE KEY')}\n"
    text, count = masked_edit(stored, "port: 22", "port: 2222")
    assert count == 1 and text == stored.replace("port: 22", "port: 2222")


def test_dropping_the_second_of_two_blocks_keeps_the_first_whole() -> None:
    first, second = _block("RSA PRIVATE KEY"), _block("EC PRIVATE KEY", lines=_body(5))
    stored = f"a\n{first}\n{second}\nb\n"
    assert keep_masked_spans(f"a\n{MASK}\nb\n", stored) == f"a\n{first}\nb\n"


def test_an_edit_of_a_header_with_no_key_after_it_keeps_the_lines_after_it() -> None:
    stored = f"tls:\n  key: {DASHES}BEGIN PRIVATE KEY{DASHES}\n  cert: abc\n  port: 443\n"
    text, count = masked_edit(stored, f"  key: {MASK}\n", "  key: none\n")
    assert count == 1 and text == "tls:\n  key: none\n  cert: abc\n  port: 443\n"
