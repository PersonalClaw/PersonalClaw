#!/usr/bin/env python3
"""Publication-hygiene check over the tracked tree — is every published path fit to publish?

This repository is PUBLIC. `git ls-files` is, exactly, the list of things the world gets,
and `git rm` removes a path from the tree but never from history. So the only cheap moment
to refuse an unfit path is BEFORE it lands, and the only thing that can refuse it on every
PR is a rail.

The rules live in ``publication-hygiene-baseline.json`` (hand-authored policy, each rule
carrying its own rationale); this module only MEASURES the tree against them. That split is
deliberate and is the one thing not to "simplify":

⚠️  THERE IS NO REGENERATE MODE, ON PURPOSE. The sibling rails in this repo
    (``config-baseline``, ``inert-surface``, ``docs-lint``, the three structural ratchets)
    are generated censuses of populations that legitimately move, so each ships a generator
    and a shrink-only ratchet. A publication denylist does not move: a match is a defect, and
    a "regenerate the baseline" verb would exist only to bless one. The escape hatch is the
    reviewed ``allowed`` list, which costs a rationale a stranger reading the public repo
    would accept.

⚠️  AND THIS RAIL SHIPS AT ZERO, which is the opposite of the structural ratchets' "ship at
    the measured population, never at zero" ruling — deliberately, because the situation is
    the inverse of the one that ruling warns about. That ruling protects against a never-run
    gate given teeth at zero reding a whole tree of pre-existing decay at once. Here the
    decay was REMOVED in the same change that added the rail (``temp-screenshots/`` deleted,
    ``scratch/`` renamed, two ``/Users/<maintainer>`` leaks scrubbed), so zero IS the measured
    population. Any nonzero result is a genuine regression, not a backlog.

Four rule families: denied PATH shapes, a ceiling on tracked BINARY size, absolute paths into
a real HOME directory, and INTERNAL REFERENCES — the name, host, identifier or phrase of a
system that is not public. The last one ships at zero for the same reason as the others: the
references were rewritten in the change that added it.

⚠️  THE INTERNAL-REFERENCE DENYLIST IS DIGESTS, NEVER PLAINTEXT. A plaintext list would
    publish, in this public repository, the very names it exists to keep out of it — the same
    self-defeat the home-path rule avoids by being an allowlist. So the checker folds the
    tree into candidates (words, two-word phrases, host suffixes, digit-shaped codes, id
    shapes), digests each distinct one with a salt, and compares digests. One CONTROL entry
    per kind, whose plaintext the test suite assembles at runtime, is what proves each kind
    fires against the shipped policy.

Run it directly for the report::

    python3 scripts/check_publication_hygiene.py              # exit 0 iff the tree is clean
    python3 scripts/check_publication_hygiene.py --scan PATH...
        # the internal-reference rule over files OUTSIDE the tree — an unpacked wheel, a PR
        # body or release note saved to a file, anything that is about to be published
    python3 scripts/check_publication_hygiene.py --digest KIND TEXT
        # the policy line(s) that deny TEXT (KIND: word, phrase, host, code or id)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read cap per file for the content rules. The longest real-home path we need to see is a
#: few hundred bytes; reading whole multi-megabyte sources to find one would make the rail
#: slow enough that someone switches it off. The largest tracked text file is well under it.
_CONTENT_READ_CAP = 2_000_000


def baseline_path() -> Path:
    """The committed policy file. Named to match the sibling ``*-baseline.json`` rails."""
    return REPO_ROOT / "publication-hygiene-baseline.json"


def load_baseline() -> dict:
    """Parse the committed policy. Raises rather than defaulting: a rail that silently
    falls back to an empty rule set when its policy file is unreadable is a rail that
    reports PASS for the rest of the repository's life."""
    return json.loads(baseline_path().read_text(encoding="utf-8"))


def tracked_files(root: Path | None = None) -> list[str]:
    """Every tracked path, as a repo-relative POSIX string — i.e. exactly what a clone gets.

    ``-z`` because a published repo may legitimately contain a path with a space, and
    line-splitting ``git ls-files`` would silently drop the tail of one.
    """
    root = root or REPO_ROOT
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _allowed_pairs(baseline: dict) -> set[tuple[str, str]]:
    """``(path, rule)`` pairs the policy deliberately exempts. Keyed on BOTH so an exemption
    granted for one rule cannot silently cover a different defect in the same file."""
    return {(e["path"], e["rule"]) for e in baseline.get("allowed", [])}


def path_rule_violations(paths: list[str], baseline: dict) -> list[str]:
    """Every tracked path matching a denied path pattern, minus the reviewed exemptions."""
    allowed = _allowed_pairs(baseline)
    found = []
    for rule in baseline["path_rules"]:
        pattern = re.compile(rule["pattern"])
        for path in paths:
            if pattern.search(path) and (path, rule["name"]) not in allowed:
                found.append(f"{rule['name']}: {path}")
    return sorted(found)


def _is_binary(blob: bytes) -> bool:
    """A NUL byte in the first 8 KB — the same heuristic ``git diff`` uses to decide a file
    has no textual diff. Good enough, and it is the definition the size rule wants: the
    exemption exists for files a reader reads (source, docs, lockfiles), not for images."""
    return b"\0" in blob[:8192]


def _text_blobs(paths: list[str], root: Path) -> Iterator[tuple[str, str]]:
    """``(path, text)`` for every tracked TEXT file, read up to the cap, decoded leniently.

    The one reader both content rules share. Binary files are skipped by the NUL heuristic:
    their bytes are compressed or structured, and a word "found" in a PNG's pixels would be
    noise that teaches everyone to ignore the rail.
    """
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing: no content to read.
        if not full.is_file():
            continue
        blob = full.read_bytes()[:_CONTENT_READ_CAP]
        if not _is_binary(blob):
            yield path, blob.decode("utf-8", "replace")


def oversized_binaries(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Tracked BINARY files over the ceiling. Text is exempt — see the rule's rationale."""
    root = root or REPO_ROOT
    rule = baseline["binary_size_rule"]
    cap = rule["max_bytes"]
    found = []
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing; it is not a blob
        # that weighs on a clone, so it is not this rule's business.
        if not full.is_file():
            continue
        size = full.stat().st_size
        if size <= cap:
            continue
        if _is_binary(full.read_bytes()[:8192]):
            found.append(f"{rule['name']}: {path} is {size} bytes (ceiling {cap})")
    return sorted(found)


def real_home_paths(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Absolute paths into a home directory whose owner name is not a declared placeholder.

    Reports one line per (file, name) rather than per occurrence: the fix is always "stop
    naming that person", and forty lines for one file buries the other files.
    """
    root = root or REPO_ROOT
    rule = baseline["real_home_path_rule"]
    pattern = re.compile(rule["pattern"])
    placeholders = set(rule["placeholder_home_names"])
    found = set()
    for path, text in _text_blobs(paths, root):
        for name in pattern.findall(text):
            if name not in placeholders:
                found.add(f"{rule['name']}: {path} names home directory '{name}'")
    return sorted(found)


# ── internal references ──────────────────────────────────────────────────────────────────

#: The candidate kinds, and so the keys of the policy's ``denied`` map. Each compound kind
#: (phrase, host, code, id) has a ``-head`` twin: the digest of one WORD every match of it
#: must contain. A file none of whose words is a head never runs that kind's pattern, which
#: keeps a whole-tree scan to one tokenising pass (measured: the four full-text patterns
#: were most of the cost, and nearly every file carries no head at all).
INTERNAL_REFERENCE_KINDS = (
    "word",
    "phrase-head",
    "phrase",
    "host-head",
    "host",
    "code-head",
    "code",
    "id-head",
    "id",
)

_DIGITS = "0123456789"
#: A word: a maximal run of ASCII letters and digits in the case-folded text, so
#: ``snake_case``, ``kebab-case`` and ``dotted.names`` fold into the words a reader sees.
_WORD = re.compile(r"[a-z0-9]+")
#: The same run, case preserved — so a mixed-case run can also be split into its CamelCase
#: parts (``FooService`` is read as ``fooservice``, ``foo`` and ``service``).
_RUN = re.compile(r"[A-Za-z0-9]+")
_CAMEL_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
#: A dotted, host-like run. Every suffix of two or more labels is a candidate, so a denied
#: host also denies each of its subdomains. Anchored at a label start: unanchored, the engine
#: retries from every character inside every word, which made the scan quadratic per word.
#: A dot may precede the run, so a wildcard family (``*.example.org``) is still read.
_HOST = re.compile(r"(?<![a-z0-9-])[a-z0-9-]+(?:\.[a-z0-9-]+)+")
#: A catalogue code: letters, an optional hyphen, one to three digits. Not followed by
#: ``.<digit>``, so a version string (``pkg-1.6.0``) is never read as a code. Digits fold to
#: ``9`` (count-preserving), so one entry covers a whole numbered family.
_CODE = re.compile(r"(?<![a-z0-9])([a-z]{2,6})(-?)([0-9]{1,3})(?![a-z0-9]|\.[0-9])")
#: A document-store id: a three-letter prefix, ``_``, then 14 mixed-case base62 characters
#: (shape ``m14``) or 8 lowercase-and-digit characters (shape ``l8``). Only PREFIX and SHAPE
#: are digested, so one entry covers every id the store issues. Matched case-sensitively.
_ID = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>[a-z]{3})_(?:"
    r"(?P<m14>(?=[a-z0-9]*[A-Z])(?=[A-Za-z]*[0-9])(?=[A-Z0-9]*[a-z])[A-Za-z0-9]{14})"
    r"|(?P<l8>(?=[a-z]*[0-9])(?=[0-9]*[a-z])[a-z0-9]{8})"
    r")(?![A-Za-z0-9])"
)
#: How far apart two words may sit and still read as one phrase: a space or a hyphen, or a
#: line break with a comment marker and indentation between them in a wrapped comment.
_PHRASE_GAP = 12


def internal_reference_digest(kind: str, candidate: str, salt: str) -> str:
    """The denylist entry for one canonical candidate.

    Salted, so the list cannot be reversed by looking each digest up in a public table of
    word hashes. It is not a secret — a short word can be brute-forced — it only keeps the
    names out of the published bytes, which is the whole job.
    """
    return hashlib.sha256(f"{salt}\0{kind}\0{candidate}".encode("utf-8")).hexdigest()


def _words(text: str) -> set[str]:
    """Every word of *text*, case-folded: each whole run, plus the CamelCase parts of a
    mixed-case one. Only a run with an uppercase letter after its first is split, so the
    split costs nothing on ordinary prose."""
    runs = set(_RUN.findall(text))
    words = {run.lower() for run in runs}
    for run in runs:
        tail = run[1:]
        if tail != tail.lower() and not run.isupper():
            words.update(part.lower() for part in _CAMEL_PART.findall(run))
    return words


def _code_shape(letters: str, hyphen: str, digits: str) -> str:
    return f"{letters}{hyphen}{'9' * len(digits)}"


def _id_shape(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}_{'m14' if match.group('m14') else 'l8'}"


class InternalReferenceMatcher:
    """Finds denylisted references in text while knowing the denylist only as digests.

    A whole-tree scan digests each DISTINCT candidate once, and a file's candidates are
    checked with set arithmetic against the ones already found denied — so the per-file cost
    is the tokenising, not a Python call per word (measured: that call was most of the time).
    """

    def __init__(self, rule: dict) -> None:
        self._salt = rule["digest_salt"]
        denied = rule["denied"]
        self._denied = {kind: frozenset(denied.get(kind, ())) for kind in INTERNAL_REFERENCE_KINDS}
        self._seen: dict[str, set[str]] = {kind: set() for kind in INTERNAL_REFERENCE_KINDS}
        self._hits: dict[str, set[str]] = {kind: set() for kind in INTERNAL_REFERENCE_KINDS}

    def _denied_among(self, kind: str, candidates: set[str]) -> set[str]:
        """The subset of *candidates* the denylist refuses, digesting only never-seen ones."""
        seen = self._seen[kind]
        new = candidates - seen
        if new:
            denied, salt = self._denied[kind], self._salt
            self._hits[kind].update(
                c for c in new if internal_reference_digest(kind, c, salt) in denied
            )
            seen |= new
        return candidates & self._hits[kind]

    def references(self, text: str) -> list[tuple[str, str]]:
        """Every ``(kind, surface)`` denied reference in *text*, sorted, once each."""
        low = text.lower()
        words = _words(text)
        found = {("word", w) for w in self._denied_among("word", words)}
        heads = self._denied_among("phrase-head", words)
        if heads:
            pairs = _phrase_pattern(sorted(heads)).finditer(low)
            phrases = {f"{m.group(1)} {m.group(2)}" for m in pairs}
            found.update(("phrase", p) for p in self._denied_among("phrase", phrases))
        if self._denied_among("host-head", words):
            for run in set(_HOST.findall(low)):
                labels = run.split(".")
                suffixes = {".".join(labels[start:]) for start in range(len(labels) - 1)}
                denied = self._denied_among("host", suffixes)
                if denied:
                    found.add(("host", max(denied, key=len)))
        letters = {w.rstrip(_DIGITS) for w in words if w[-1] in _DIGITS}
        if self._denied_among("code-head", words | letters):
            codes = {(_code_shape(*m), "".join(m)) for m in set(_CODE.findall(low))}
            denied = self._denied_among("code", {shape for shape, _ in codes})
            found.update(("code", surface) for shape, surface in codes if shape in denied)
        if self._denied_among("id-head", words):
            ids = {(_id_shape(m), m.group(0)) for m in _ID.finditer(text)}
            denied = self._denied_among("id", {shape for shape, _ in ids})
            found.update(("id", surface) for shape, surface in ids if shape in denied)
        return sorted(found)

    def located(self, text: str) -> list[tuple[int, str, str]]:
        """``(line, kind, surface)`` for every denied reference in *text*.

        A clean text (the overwhelming case) costs one pass. Only a text that carries a
        reference is read again, line by line, to say WHERE. A phrase is the one kind that can
        straddle a line break (a wrapped comment), so it is placed on its first word's line.
        """
        hits = self.references(text)
        if not hits:
            return []
        located = {
            (number, kind, surface)
            for number, line in enumerate(text.splitlines(), 1)
            for kind, surface in self.references(line)
        }
        placed = {(kind, surface) for _, kind, surface in located}
        low = text.lower()
        for kind, surface in hits:
            if kind == "phrase" and (kind, surface) not in placed:
                for match in _wrapped_phrase(surface).finditer(low):
                    located.add((low.count("\n", 0, match.start()) + 1, kind, surface))
        return sorted(located)


def _phrase_pattern(heads: list[str]) -> re.Pattern[str]:
    """A head word followed, within the phrase gap, by the next word (captured by lookahead
    so that word can itself start the next phrase)."""
    alternation = "|".join(map(re.escape, heads))
    return re.compile(rf"(?<![a-z0-9])({alternation})(?=[^a-z0-9]{{1,{_PHRASE_GAP}}}([a-z0-9]+))")


def _wrapped_phrase(phrase: str) -> re.Pattern[str]:
    """Where a two-word phrase sits when a line break falls between its words."""
    head, tail = (re.escape(part) for part in phrase.split(" ", 1))
    return re.compile(rf"(?<![a-z0-9]){head}(?=[^a-z0-9]{{1,{_PHRASE_GAP}}}{tail}(?![a-z0-9]))")


def internal_references(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Tracked paths and text that name a non-public system — see the rule's rationale.

    One line per (file, line, reference): unlike a home path, each occurrence is its own
    sentence to rewrite, so the line is the useful unit. There is no exemption mechanism for
    this rule — a match is always a defect, fixed by stating the principle instead.
    """
    root = root or REPO_ROOT
    rule = baseline["internal_reference_rule"]
    matcher = InternalReferenceMatcher(rule)
    found = []
    for path in paths:
        for kind, surface in matcher.references(path):
            found.append(f"{rule['name']}: {path} — its PATH names a denied {kind} ({surface!r})")
    for path, text in _text_blobs(paths, root):
        for line, kind, surface in matcher.located(text):
            found.append(f"{rule['name']}: {path}:{line} names a denied {kind} ({surface!r})")
    return sorted(found)


def scan_files(targets: list[Path]) -> list[tuple[Path, str]]:
    """``(file, shown path)`` for every file under *targets*: a directory is walked and each
    file is shown relative to it; a file stands for itself."""
    files = []
    for target in targets:
        if target.is_dir():
            files += [(p, p.relative_to(target).as_posix()) for p in sorted(target.rglob("*"))]
        else:
            files.append((target, target.name))
    return [(file, shown) for file, shown in files if file.is_file()]


def scan_paths(targets: list[Path], baseline: dict | None = None) -> list[str]:
    """The internal-reference rule over files that are NOT the tracked tree: an unpacked
    wheel, a PR body or release note saved to a file. Every file is read WHOLE (a minified
    bundle can outgrow the tree's read cap); binaries are skipped, as in the tree."""
    baseline = baseline or load_baseline()
    rule = baseline["internal_reference_rule"]
    matcher = InternalReferenceMatcher(rule)
    found = []
    for file, shown in scan_files(targets):
        for kind, surface in matcher.references(shown):
            found.append(f"{rule['name']}: {file} — its PATH names a denied {kind} ({surface!r})")
        blob = file.read_bytes()
        if _is_binary(blob):
            continue
        for line, kind, surface in matcher.located(blob.decode("utf-8", "replace")):
            found.append(f"{rule['name']}: {file}:{line} names a denied {kind} ({surface!r})")
    return sorted(found)


def denylist_entries(kind: str, text: str) -> list[tuple[str, str]]:
    """The canonical ``(kind, candidate)`` pairs that deny *text*: the entry itself and, for
    a compound kind, the head word it is only ever checked behind.

    *kind* is ``word``, ``phrase`` (two words), ``host`` (denies its subdomains too),
    ``code`` (``ab-12``: denies every code of that letters-and-digit-count shape) or ``id``
    (a real id: denies every id of that prefix and shape).
    """
    low = text.strip().lower()
    words = _WORD.findall(low)
    if kind == "word":
        if words != [low]:
            raise ValueError(f"a word is one run of letters and digits, not {text!r}")
        return [("word", low)]
    if kind == "phrase":
        if len(words) != 2:
            raise ValueError(f"a phrase is exactly two words, not {text!r}")
        return [("phrase", " ".join(words)), ("phrase-head", words[0])]
    if kind == "host":
        labels = low.split(".")
        if not _HOST.fullmatch(low) or len(labels) < 2 or not _WORD.findall(labels[-2]):
            raise ValueError(f"not a dotted host name: {text!r}")
        return [("host", low), ("host-head", _WORD.findall(labels[-2])[0])]
    if kind == "code":
        code = _CODE.fullmatch(low)
        if not code:
            raise ValueError(f"a code is letters, an optional hyphen and 1-3 digits: {text!r}")
        return [("code", _code_shape(*code.groups())), ("code-head", code.group(1))]
    if kind == "id":
        ident = _ID.fullmatch(text.strip())
        if not ident:
            raise ValueError(f"an id is abc_ + 14 mixed-case or 8 lowercase base62: {text!r}")
        return [("id", _id_shape(ident)), ("id-head", ident.group("prefix"))]
    raise ValueError(f"unknown kind {kind!r} (word, phrase, host, code or id)")


def denylist_lines(kind: str, text: str, salt: str) -> list[str]:
    """``KIND DIGEST`` lines to add to the policy's ``denied`` map so that *text* is refused."""
    return [f"{k} {internal_reference_digest(k, c, salt)}" for k, c in denylist_entries(kind, text)]


def violations(root: Path | None = None) -> list[str]:
    """Every publication-hygiene defect in the tracked tree, sorted and grouped by rule
    family. Empty list == the tree is fit to publish."""
    baseline = load_baseline()
    paths = tracked_files(root)
    return [
        *path_rule_violations(paths, baseline),
        *oversized_binaries(paths, baseline, root),
        *real_home_paths(paths, baseline, root),
        *internal_references(paths, baseline, root),
    ]


def _report(found: list[str], clean: str, baseline: dict) -> int:
    if not found:
        print(clean)
        return 0
    print(f"publication-hygiene: FAIL ({len(found)} finding(s))")
    for line in found:
        print(f"  - {line}")
    rule = baseline["internal_reference_rule"]
    if any(line.startswith(f"{rule['name']}:") for line in found):
        print("\n" + rule["how_to_fix"])
    if any(not line.startswith(f"{rule['name']}:") for line in found):
        print("\n" + baseline["how_to_fix_a_red"])
    return 1


def main(argv: list[str] | None = None) -> int:
    """Print the report; exit ``0`` iff nothing checked is unfit to publish."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", nargs="+", type=Path, metavar="PATH")
    mode.add_argument("--digest", nargs=2, metavar=("KIND", "TEXT"))
    args = parser.parse_args(argv)
    baseline = load_baseline()
    if args.digest:
        kind, text = args.digest
        try:
            lines = denylist_lines(kind, text, baseline["internal_reference_rule"]["digest_salt"])
        except ValueError as exc:
            parser.error(str(exc))
        print("\n".join(lines))
        return 0
    if args.scan:
        found = scan_paths(args.scan, baseline)
        clean = f"publication-hygiene: PASS ({len(scan_files(args.scan))} files scanned, 0 found)"
        return _report(found, clean, baseline)
    found = violations()
    clean = f"publication-hygiene: PASS ({len(tracked_files())} tracked paths, 0 unfit)"
    return _report(found, clean, baseline)


if __name__ == "__main__":
    sys.exit(main())
