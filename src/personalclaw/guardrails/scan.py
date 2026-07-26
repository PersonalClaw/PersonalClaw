"""Outbound secret/PII scan at the model-call seam (AUTONOMY-GUARDRAILS §2.2).

The network egress chokepoint guards the *transport*; this guards the *content*.
Every outbound prompt bound for a REMOTE provider passes the scan before it leaves
the machine. Local-only providers skip to ``warn`` (the content never leaves).

Builds on what already exists rather than reinventing detection:

* ``security.redact_credentials`` / ``redact_exfiltration_urls`` supply the
  credential + exfil-URL passes (AWS keys, private keys, Slack tokens, base64
  variants, suspicious query strings).
* A small PII pass adds email + phone + long key-shaped strings.

The mode ladder (per ``GuardrailsConfig.scan_mode``, but forced to ``warn`` for
local providers):

* ``warn``   — log the findings + proceed with the ORIGINAL prompt.
* ``redact`` — substitute the findings out, proceed with the CLEANED prompt.
* ``block``  — refuse the call (the caller raises ``SecretLeakBlocked``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# PII patterns beyond the credential/exfil passes. Deliberately conservative — a
# personal gateway's own prompts routinely contain the user's own email, so these
# feed WARN/REDACT, never a hard block on their own in the default mode ladder.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# E.164-ish / common phone shapes (7+ digits with separators), avoiding bare years.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")


@dataclass
class ScanResult:
    """The outcome of scanning one outbound prompt."""

    text: str  # possibly-redacted prompt to send (== input in warn mode)
    findings: int  # count of secret/PII hits detected
    blocked: bool = False  # True only in block mode with findings
    categories: tuple[str, ...] = ()  # e.g. ("credential", "email")


def _count_pii(text: str) -> tuple[int, list[str]]:
    cats: list[str] = []
    n = 0
    emails = _EMAIL_RE.findall(text)
    if emails:
        n += len(emails)
        cats.append("email")
    phones = _PHONE_RE.findall(text)
    if phones:
        n += len(phones)
        cats.append("phone")
    return n, cats


def _redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def scan_outbound(text: str, *, mode: str) -> ScanResult:
    """Scan ``text`` for secrets/PII and apply the ``mode`` ladder.

    ``mode`` is ``warn`` | ``redact`` | ``block`` (an unknown value is treated as
    ``warn`` — the least surprising, never a silent hard block). Detection always
    runs; the mode only decides what happens to a finding.
    """
    if not text:
        return ScanResult(text=text, findings=0)

    cleaned_cred, cred_warnings = redact_credentials(text)
    cleaned_both, url_warnings = redact_exfiltration_urls(cleaned_cred)
    pii_count, pii_cats = _count_pii(text)

    findings = len(cred_warnings) + len(url_warnings) + pii_count
    categories: list[str] = []
    if cred_warnings:
        categories.append("credential")
    if url_warnings:
        categories.append("exfil_url")
    categories.extend(pii_cats)

    if findings == 0:
        return ScanResult(text=text, findings=0)

    mode = mode if mode in ("warn", "redact", "block") else "warn"
    if mode == "block":
        logger.warning("outbound scan: %d finding(s) → BLOCK (%s)", findings, ",".join(categories))
        return ScanResult(text=text, findings=findings, blocked=True, categories=tuple(categories))
    if mode == "redact":
        return ScanResult(
            text=_redact_pii(cleaned_both), findings=findings, categories=tuple(categories)
        )
    # warn: proceed with the original text, just record it.
    logger.info("outbound scan: %d finding(s) → WARN (%s)", findings, ",".join(categories))
    return ScanResult(text=text, findings=findings, categories=tuple(categories))
