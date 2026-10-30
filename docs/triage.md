# Phase 4 deterministic incident triage (REQ-03, AC-03)

**Purpose.** The assessment endpoint screens a stored plain-text incident log for indicators that could warrant human materiality review. It **does not** make a legal SEC materiality decision, identify a verified breach, submit a disclosure, or bypass the human review endpoint.

## Rules and thresholds

The `triage.py` function `assess_log(log)` reports `potentiallyMaterial` (boolean), `explanation` (non-empty string), and `indicators` (array of strings). It returns these same keys used by the Phase 2 OpenAPI contract; `main.py` persists the result and moves an incident from `RECEIVED` to `POTENTIALLY_MATERIAL` if flagged, or `UNDER_REVIEW` otherwise.

Each distinct **positive** rule contributes its weight once per incident:

| Indicator | Weight | Example |
| --- | ---: | --- |
| `ransomware` | 3 | Possible ransomware affecting servers |
| `data_breach` | 3 | Possible data breach |
| `data_exfiltration` | 3 | Evidence of exfiltration |
| `data_exposure` | 3 | Customer data exposed |
| `service_outage` | 2 | Critical service outage |
| `unauthorized_access` | 2 | Unauthorized access attempts |
| `credential_compromise` | 2 | Compromised credentials |
| `sensitive_data` | 1 | Customer/personal/sensitive data referenced |
| `failed_login_burst` | 1 | Repeated failed logins or 12 attempts |
| `privileged_target` | 1 | An admin/root target **in a failed-login burst** |

A score **at least 2** flags a record for human review. The threshold is an **engineering heuristic defined for this coursework**, not an SEC standard or a statistical calibration. The explanation includes the score, threshold and reasons. Non-flagged incidents **still require human review** if the organization deems it appropriate.

The implementation checks simple nearby negations (`no`, `without`, `not detected`, etc.) to reduce obvious false positives such as `No evidence of data breach`. It looks across the entire submitted incident for log-in frequency/count and account context because these can appear on separate lines. These rules are deterministic; there is no AI/LLM call, no extra service, and no new dependency.

## Validation and limits

Run `python -m pytest -q`. `test_triage.py` includes positive, negative, negation and boundary fixtures, and a three-run repeatability assertion. `test_api.py` adds end-to-end checks for persisted assessments and both state outcomes, repeated requests returning HTTP 409, and invalid operations not mutating stored data.

**Known limitations:** Regex-based interpretation can miss synonyms, imperfect logging, subtle negation, chronology, and complex causal statements. Co-occurring signals may refer to different events in one submitted log. No empirical precision/recall or human-validated materiality accuracy is claimed; the pod must agree on shared fixture labels for AC-03. The project's Phase 2 evaluation plan mentioned a temperature-zero model; this proposed deterministic implementation does **not** use an LLM and should not be described as AI-powered without a separate design decision.

For privacy and safety, use **synthetic test data only** with the public test-reset endpoint. All final legal materiality decisions remain with authorized human reviewers.
