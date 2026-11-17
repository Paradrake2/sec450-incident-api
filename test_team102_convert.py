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
