import pytest
from team102_convert import ConversionError, convert_report

OK = {"report_id": 17, "body": {
    "summary": "Suspicious export", "raw_lines": ["a", "b"],
    "timeline": [{"timestamp": "2026-01-02T00:00:00Z"}, {"timestamp": "2026-01-01T10:00:00+00:00"}]}}


def test_maps_fields():
    assert convert_report(OK) == {"title": "Suspicious export", "log": "a\nb",
        "occurredAt": "2026-01-01T10:00:00Z", "source": "team102:report:17"}


def test_long_summary_truncated_and_log_falls_back():
    r = {"report_id": 1, "body": {"summary": "x" * 300, "timeline": OK["body"]["timeline"]}}
    p = convert_report(r)
    assert len(p["title"]) == 200 and p["log"] == "x" * 300


@pytest.mark.parametrize("bad", [
    {}, {"report_id": 1, "body": []},
    {"report_id": 1, "body": {"summary": "s"}},                                 # no time
    {"report_id": 1, "body": {"timeline": OK["body"]["timeline"]}},             # no log
])
def test_rejects(bad):
    with pytest.raises(ConversionError):
        convert_report(bad)


def test_naive_time_treated_as_utc():
    r = {"report_id": 1, "body": {"summary": "s", "timeline": [{"ts": "2026-01-01T00:00:00"}]}}
    assert convert_report(r)["occurredAt"] == "2026-01-01T00:00:00Z"


def test_actual_team102_raw_line_objects_preserve_original_text_and_order():
    # Mirrors sec450/reports.py:build_report in Team 102's shared repository.
    report = {"report_id": 18, "body": {
        "summary": "Suspicious authentication",
        "raw_lines": [
            {"raw_id": 11, "batch_id": 3, "source": "ssh_auth", "text": "first raw line"},
            {"raw_id": 12, "batch_id": 3, "source": "ssh_auth", "text": "second raw line"},
        ],
        "timeline": [{"ts": "2026-11-10T14:00:00Z"}],
    }}
    result = convert_report(report)
    assert result["title"] == "Suspicious authentication"
    assert result["log"] == "first raw line\nsecond raw line"
    assert result["source"] == "team102:report:18"


def test_mixed_legacy_strings_and_report_objects_keep_text():
    report = {"report_id": 19, "body": {
        "summary": "Synthetic legacy input",
        "raw_lines": ["plain text", {"text": "structured text"}],
        "timeline": [{"ts": "2026-11-10T14:00:00Z"}],
    }}
    assert convert_report(report)["log"] == "plain text\nstructured text"


@pytest.mark.parametrize("bad_lines", [
    "not a list",
    {"text": "not a list"},
    [{"raw_id": 12}],
    [{"text": 27}],
    [None],
])
def test_malformed_raw_line_entries_are_rejected_not_silently_discarded(bad_lines):
    report = {"report_id": 20, "body": {
        "summary": "Must not silently lose source evidence",
        "raw_lines": bad_lines,
        "timeline": [{"ts": "2026-11-10T14:00:00Z"}],
    }}
    with pytest.raises(ConversionError, match="raw_lines"):
        convert_report(report)
