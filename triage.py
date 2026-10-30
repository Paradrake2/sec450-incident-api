"""Deterministic, advisory triage of submitted *plain-text* incident logs.

This is an engineering screening heuristic, NOT an SEC materiality determination.
The numerical threshold is a testable project policy, not a regulatory threshold.
Only a human reviewer may set MATERIAL_CONFIRMED or NOT_MATERIAL.

Version 1: avoid external services and nondeterministic output so podmates can
exercise the same inputs against independent implementations.
"""

import re
from dataclasses import dataclass

SCREENING_THRESHOLD = 2


@dataclass(frozen=True)
class Signal:
    code: str
    description: str
    weight: int
    pattern: re.Pattern[str]


# Specific reported security events receive higher weights than general context.
SIGNALS = (
    Signal("ransomware", "reported ransomware activity", 3, re.compile(r"\bransomware\b", re.I)),
    Signal("data_breach", "reported data breach", 3, re.compile(r"\b(?:data breach|breach of (?:customer|personal|sensitive) data)\b", re.I)),
    Signal("data_exfiltration", "reported data exfiltration or leakage", 3, re.compile(r"\b(?:exfiltrat\w*|data leak(?:age)?)\b", re.I)),
    Signal("data_exposure", "reported exposure of sensitive data", 3, re.compile(r"\b(?:(?:customer|personal|sensitive) data (?:was )?(?:exposed|leaked|disclosed)|(?:exposure|leak) of (?:customer|personal|sensitive) data|(?:customer|personal|sensitive) data exposure)\b", re.I)),
    Signal("service_outage", "reported service or system outage", 2, re.compile(r"\b(?:system outage|service outage|production outage|critical (?:service|system) (?:is )?down)\b", re.I)),
    Signal("unauthorized_access", "reported or suspected unauthorized access", 2, re.compile(r"\bunauthori[sz]ed access\b", re.I)),
    Signal("credential_compromise", "reported compromise of credentials", 2, re.compile(r"\b(?:compromised credentials|stolen credentials|credentials? compromised)\b", re.I)),
    Signal("sensitive_data", "reference to potentially sensitive information", 1, re.compile(r"\b(?:customer data|personal data|sensitive data|pii|financial records|medical records)\b", re.I)),
)

# Simple bounded negation works for synthetic log fixtures; it is not NLP.
_NEGATED_BEFORE = re.compile(
    r"(?:\b(?:no|not|without|never|neither)\b|\bruled out\b|\bnegative for\b)"
    r"(?:\W+\w+){0,5}\W*$", re.I
)
_NEGATED_AFTER = re.compile(
    r"^\W*(?:(?:was|were|is|are)\W+)?(?:not detected|not found|ruled out|absent)\b", re.I
)
_FAILED_LOGIN = re.compile(r"\b(?:failed\s+(?:ssh\s+)?(?:password|logins?|login attempts?|authentication)|login failures?)\b", re.I)
_BURST = re.compile(r"\b(?:repeated|multiple|numerous|burst|brute[ -]?force|dozens)\b|\b(?:1\d|[2-9]\d+)\s+(?:(?:failed|login)\s+)*attempts?\b", re.I)
_PRIVILEGED = re.compile(r"\b(?:admin(?:istrator)?|root|privileged|service account)\b", re.I)


def _clauses(log: str) -> list[str]:
    # Do not split on periods: logs commonly contain dotted IP addresses.
    return [part for part in re.split(r"[\n;]+", log) if part.strip()]


def _has_positive_match(pattern: re.Pattern[str], clauses: list[str]) -> bool:
    for clause in clauses:
        for match in pattern.finditer(clause):
            prefix = clause[max(0, match.start() - 75):match.start()]
            # A contrast clause resets the scope of a preceding negation:
            # "No ransomware, but unauthorized access was detected".
            prefix = re.split(r"\b(?:but|however|although|yet)\b", prefix, flags=re.I)[-1]
            if _NEGATED_BEFORE.search(prefix):
                continue
            if _NEGATED_AFTER.search(clause[match.end():match.end() + 40]):
                continue
            return True
    return False


def assess_log(log: str) -> dict:
    """Return the existing shared-API Assessment shape; no database mutations."""
    clauses = _clauses(log)
    findings: list[tuple[str, str, int]] = []

    for signal in SIGNALS:
        if _has_positive_match(signal.pattern, clauses):
            findings.append((signal.code, signal.description, signal.weight))

    # A burst alone is not proof of access. Privileged-account targeting adds
    # context; paired findings cross the advisory review threshold.
    # Supporting count and account metadata may be on another line of the same
    # submitted incident. Check across the incident after excluding negated events.
    if _has_positive_match(_FAILED_LOGIN, clauses) and _BURST.search(log):
        findings.append(("failed_login_burst", "repeated failed-login activity", 1))
        if _PRIVILEGED.search(log):
            findings.append(("privileged_target", "administrative or privileged account targeted", 1))

    score = sum(weight for _, _, weight in findings)
    potentially_material = score >= SCREENING_THRESHOLD
    if findings:
        descriptions = "; ".join(description for _, description, _ in findings)
        explanation = (
            f"Advisory screening score {score} (review threshold {SCREENING_THRESHOLD}). "
            f"Observed indicators: {descriptions}. "
            + ("Human materiality review recommended." if potentially_material
               else "Below the automated flag threshold; human review is still required.")
        )
    else:
        explanation = "No configured positive incident indicators found; human review is still required."

    return {
        "potentiallyMaterial": potentially_material,
        "explanation": explanation,
        "indicators": [code for code, _, _ in findings],
    }
