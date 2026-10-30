"""AC-03: deterministic positive, negative, boundary and repeatability fixtures."""

import pytest

from triage import assess_log


@pytest.mark.parametrize(("log", "expected", "indicators"), [
    ("Possible ransomware affecting production servers", True, ["ransomware"]),
    ("Possible data breach affecting customers", True, ["data_breach"]),
    ("Evidence of exfiltration of records", True, ["data_exfiltration"]),
    ("Customer data exposed by attack", True, ["data_exposure", "sensitive_data"]),
    ("No customer data exposed", False, []),
    ("Critical service outage lasting two hours", True, ["service_outage"]),
    ("Unauthorized access attempts against VPN", True, ["unauthorized_access"]),
    ("Compromised credentials were identified", True, ["credential_compromise"]),
    ("2026-09-20 host42 sshd: Failed password for admin from 203.0.113.5; 12 attempts in 90s", True, ["failed_login_burst", "privileged_target"]),
    ("Failed password for admin from 203.0.113.5, 12 attempts in 90s", True, ["failed_login_burst", "privileged_target"]),
    ("Repeated failed SSH logins to a normal account", False, ["failed_login_burst"]),
    ("Customer data is stored securely on this host", False, ["sensitive_data"]),
    ("No evidence of data breach", False, []),
    ("Ransomware was not detected", False, []),
    ("No ransomware, no unauthorized access and no system outage", False, []),
    ("No ransomware, but unauthorized access detected", True, ["unauthorized_access"]),
    ("No evidence of data breach; exfiltration confirmed", True, ["data_exfiltration"]),
    ("Ordinary access log: GET /index.html 200", False, []),
    ("customer data may be affected by unauthorized access", True, ["unauthorized_access", "sensitive_data"]),
])
def test_fixture_classification(log, expected, indicators):
    result = assess_log(log)
    assert result["potentiallyMaterial"] is expected
    assert result["indicators"] == indicators
    assert result["explanation"]
    assert set(result) == {"potentiallyMaterial", "explanation", "indicators"}


def test_repeatability_three_runs():
    log = "Repeated failed logins for admin; 12 attempts in 90s; possible data breach"
    outcomes = [assess_log(log) for _ in range(3)]
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_no_legal_materiality_claim():
    result = assess_log("Ransomware on servers")
    assert "human materiality review" in result["explanation"].lower()
