"""Secret / credential lint for template exports.

Pure and data-driven on purpose: this exact rule set is deliberately
duplicated in the api.tesserae.ink repo (``tesserae_api/template_lint.py``),
which re-checks submissions server-side; this copy runs at export time so
secrets never leave the machine. Keep the two files diff-identical when
changing rules.

``lint_strings(items)`` takes ``(where, value)`` pairs and returns
``{"errors": [...], "warnings": [...]}`` entries of ``{where, match, rule}``.
Errors block a submission; warnings are surfaced to the reviewer.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Hard errors: these substrings mean a credential is present.
_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authorization-header", re.compile(r"(?i)\bauthorization\b\s*[:=]")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google-key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}")),
    ("aws-access-key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# Warnings: likely-but-not-certain credential material.
_WARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "url-query-secret",
        re.compile(
            r"(?i)[?&](?:api_?key|apikey|token|key|secret|password|pass|auth)=[^&\s\"']{6,}"
        ),
    ),
    ("jwt-like", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("basic-auth-url", re.compile(r"(?i)\bhttps?://[^/\s:@\"']+:[^/\s:@\"']+@")),
)

# Entropy scan: long uniform base64/hex-ish strings that look like keys.
_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=_-]{20,}\b")
_ENTROPY_THRESHOLD = 4.0
_ENTROPY_MIN_LEN = 20

# Candidates that are common false positives: pure hex colour runs, plain
# words, decimal numbers, and data URI payload heads are skipped cheaply.
_BORING = re.compile(r"^(?:[0-9]+|[a-z]+|[A-Z]+|[A-Za-z]+)$")


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def lint_strings(items: list[tuple[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Scan ``(where, value)`` pairs for credential material.

    ``where`` is a human-readable locator ("el e_a1b2 js", "input city
    default") echoed back so findings are actionable. Matched text is
    truncated in the report so the report itself never carries a full secret.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for where, value in items:
        if not value:
            continue
        text = str(value)
        for rule, pattern in _ERROR_PATTERNS:
            m = pattern.search(text)
            if m:
                errors.append({"where": where, "match": m.group(0)[:24] + "…", "rule": rule})
        for rule, pattern in _WARNING_PATTERNS:
            m = pattern.search(text)
            if m:
                warnings.append({"where": where, "match": m.group(0)[:24] + "…", "rule": rule})
        if text.startswith("data:image/"):
            continue  # inlined image payloads are high-entropy by nature
        for candidate in _ENTROPY_CANDIDATE.findall(text):
            if len(candidate) < _ENTROPY_MIN_LEN or _BORING.match(candidate):
                continue
            if _shannon_entropy(candidate) > _ENTROPY_THRESHOLD:
                warnings.append(
                    {"where": where, "match": candidate[:12] + "…", "rule": "high-entropy-string"}
                )
                break  # one entropy warning per value is enough signal
    return {"errors": errors, "warnings": warnings}
