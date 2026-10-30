"""Deterministic non-final disclosure drafting from existing incident evidence.

This module never determines materiality or files a report. Its input values
may be entirely unverified. The textual layout deliberately marks all user-
submitted material as reported rather than established fact.
"""

LOG_EXCERPT_LIMIT = 2000


def _one_line(value: str | None) -> str:
    """Avoid letting a user-supplied title/source inject apparent headings."""
    return " ".join((value or "").split()) or "Not supplied"


def _quoted(value: str | None) -> str:
    """Prefix untrusted multi-line text so it cannot impersonate a section."""
    if not value:
        return "  | Not supplied"
    return "\n".join("  | " + line for line in value.splitlines()) or "  | Not supplied"


def build_draft(
    *, incident_id: str, title: str, log: str, occurred_at: str,
    created_at: str, source: str | None, review: dict,
) -> str:
    """Create advisory draft text without making unsupported factual claims."""
    excerpt = log[:LOG_EXCERPT_LIMIT]
    cutoff = len(log) > LOG_EXCERPT_LIMIT
    log_note = (f"First {LOG_EXCERPT_LIMIT} characters only; remainder omitted."
                if cutoff else "Full submitted log text (unverified).")

    return "\n".join([
        "DRAFT — FORM 8-K ITEM 1.05 SUPPORT — NOT AN SEC FILING",
        "STATUS: NON-FINAL. FOR AUTHORIZED LEGAL REVIEW ONLY.",
        "This template uses submitted, unverified data. It is not a finding of", 
        "legal materiality, an approved disclosure, or a substitute for counsel.",
        "",
        "Incident reference", 
        f"  Internal incident ID: {incident_id}",
        f"  Reported title: {_one_line(title)}",
        f"  Submitted source: {_one_line(source)}",
        "",
        "1. Nature of incident (unverified report)",
        f"  Log excerpt: {log_note}",
        _quoted(excerpt),
        "  The event description above is reported evidence, not a verified", 
        "  characterization of any breach, attacker, affected asset, or outcome.",
        "",
        "2. Scope and systems affected",
        "  Not independently established. Confirm affected systems, data,", 
        "  geographic reach, and ongoing operational consequences.",
        "",
        "3. Timing and discovery",
        f"  Occurrence time (as submitted): {occurred_at}",
        f"  Intake time (recorded by system): {created_at}",
        "  Discovery date, materiality determination date, and resolution dates", 
        "  are NOT established by these timestamps; verify independently.",
        "",
        "4. Materiality review and potential impact",
        "  Latest recorded human decision: MATERIAL (user-submitted, not authenticated).",
        f"  Claimed reviewer: {_one_line(review.get('reviewer'))}",
        f"  Recorded review time: {_one_line(review.get('reviewedAt'))}",
        "  Submitted reviewer rationale (unverified):",
        _quoted(review.get("rationale")),
        "  Financial, operational, reputational and other material effects have", 
        "  not been verified or quantified by this API. Legal counsel must confirm", 
        "  applicable materiality determination and disclosure wording.",
        "",
        "5. Outstanding verification and approval",
        "  Confirm the nature, scope, timing and impact with trusted evidence;", 
        "  identify unknown facts and any later amendments required.",
        "  Obtain authorized legal approval prior to any real SEC filing.",
        "  THIS SYSTEM DOES NOT FILE OR FINALIZE SEC DISCLOSURES.",
    ])
