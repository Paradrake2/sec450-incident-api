"""Provisional source parsing preserves opaque evidence without guessing facts."""

import json

import pytest

from team102_contract import SourceApiError, canonical_report, parse_report, report_url


def test_minimal_report_keeps_unknown_fields_and_advisory_content():
    document = {
        "report_id": 17,
        "producer_extension": {"opaque": "retained"},
        "body": {
            "raw_lines": ["original\nline"], "summary": "Synthetic report",
            "ai_assessment": {"status": "unavailable", "reason": "fixture"},
        },
    }
    raw = json.dumps(document).encode()
    assert parse_report(raw, 17) == document


@pytest.mark.parametrize("raw", [
    b'{"report_id":17,"body":{},"report_id":18}',
    b'{"report_id":17,"body":{"x":1,"x":2}}',
    b'{"report_id":true,"body":{}}',
    b'{"report_id":"17","body":{}}',
    b'{"report_id":18,"body":{}}',
    b'{"report_id":17,"body":[]}',
    b'{"report_id":17}',
    b'[{"report_id":17,"body":{}}]',
    b'{"report_id":17,"body":{"secret":NaN}}',
    b'{"report_id":17,"body":{"secret":1e9999}}',
    b'{"report_id":17,"body":{"secret":"\\ud800"}}',
    b'{"report_id":17,"body":{"secret":"\xff"}}',
    b'{"report_id":17,"body":{"private fixture":',
])
def test_ambiguous_or_unsupported_responses_are_rejected_without_payload_leaks(raw):
    with pytest.raises(SourceApiError) as failure:
        parse_report(raw, 17)
    assert failure.value.kind == "invalid_response"
    assert "secret" not in str(failure.value)
    assert "private fixture" not in str(failure.value)


def test_canonical_content_ignores_json_layout_but_preserves_actual_changes():
    first = parse_report(b'{"report_id":17, "body":{"summary":"A", "extra":1}}', 17)
    reordered = parse_report(b'{ "body": {"extra":1,"summary":"A"},"report_id":17 }', 17)
    changed = parse_report(b'{"report_id":17,"body":{"summary":"B","extra":1}}', 17)
    assert canonical_report(first) == canonical_report(reordered)
    assert canonical_report(first) != canonical_report(changed)


@pytest.mark.parametrize("report_id", [True, -1, "17", 10 ** 64])
def test_report_ids_cannot_be_coerced_or_used_as_paths(report_id):
    with pytest.raises(ValueError):
        report_url(report_id)
