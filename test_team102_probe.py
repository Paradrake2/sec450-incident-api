"""Read-only schema discovery uses synthetic responses and never a real API key."""

from datetime import datetime, timezone
import json

import pytest

from team102_contract import SourceApiError, SourceRead
from team102_probe import inspect_response, probe_one


def incident(**changes):
    document = {
        "id": "synthetic-incident-id", "title": "PRIVATE INCIDENT TITLE",
        "log": "PRIVATE RAW EVIDENCE", "occurredAt": "2026-10-31T13:00:00Z",
        "createdAt": "2026-10-31T13:01:00+00:00", "state": "RECEIVED",
        "relatedIncidentIds": [],
    }
    document.update(changes)
    return document


def raw(document):
    return json.dumps(document).encode("utf-8")


def source_read(document):
    return SourceRead(
        raw_content=raw(document), fetched_at=datetime.now(timezone.utc),
        source_url="https://seclog.redinbo.win/v1/reports/synthetic-id",
    )


class FakeClient:
    def __init__(self, detail, listing=None):
        self.detail = detail
        self.listing = listing
        self.calls = []

    def read_reports_sample(self):
        self.calls.append(("list",))
        return source_read(self.listing)

    def read_report(self, report_id):
        self.calls.append(("detail", report_id))
        return source_read(self.detail)

    def read_logs_sample(self):
        self.calls.append(("logs",))
        return source_read(self.listing)


def test_direct_incident_response_matches_without_printing_content():
    result = inspect_response(raw(incident()))
    assert result["classification"] == "incident_response"
    assert result["directIncidentResponse"] is True
    assert result["incidentMatches"] == ["$"]
    assert result["guideMatches"] == []
    assert result["schemaSource"] == "contract_schemas.IncidentResponse"
    assert result["scope"] == "one_sample_only"
    serialized = json.dumps(result)
    assert "PRIVATE" not in serialized
    assert "synthetic-incident-id" not in serialized


def test_nullable_and_nested_incident_fields_are_validated():
    result = inspect_response(raw(incident(
        source=None,
        assessment={"potentiallyMaterial": True, "explanation": "PRIVATE ANALYSIS", "indicators": ["PRIVATE FLAG"]},
        review={"decision": "MATERIAL", "reviewer": "PRIVATE REVIEWER", "reviewedAt": "2026-10-31T14:00:00Z"},
    )))
    assert result["directIncidentResponse"] is True
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("wrapper", ["report", "incident", "data", "body"])
def test_incident_in_known_envelope_is_distinguished_from_direct_match(wrapper):
    result = inspect_response(raw({wrapper: incident()}))
    assert result["classification"] == "incident_in_envelope"
    assert result["directIncidentResponse"] is False
    assert len(result["incidentMatches"]) == 1
    assert result["incidentMatches"][0] != "$"


def test_guide_shape_is_only_a_provisional_guide_match():
    result = inspect_response(raw({
        "report_id": 17, "body": {"summary": "PRIVATE SOURCE SUMMARY"},
    }))
    assert result["classification"] == "guide_report"
    assert result["directIncidentResponse"] is False
    assert result["incidentMatches"] == []
    assert result["guideMatches"] == ["$"]
    assert "PRIVATE" not in json.dumps(result)


def test_both_guide_and_nested_incident_match_is_explicitly_ambiguous():
    result = inspect_response(raw({"report_id": 17, "body": incident()}))
    assert result["classification"] == "ambiguous"
    assert result["incidentMatches"] and result["guideMatches"]
    assert result["directIncidentResponse"] is False


@pytest.mark.parametrize("document", [
    {"report_id": True, "body": {}}, {"report_id": -1, "body": {}},
    {"report_id": "17", "body": {}}, {"report_id": 17, "body": []},
    {"report_id": 10**64, "body": {}},
])
def test_guide_match_does_not_claim_compatibility_for_invalid_identity_or_body(document):
    result = inspect_response(raw(document))
    assert result["classification"] == "unsupported"
    assert result["guideMatches"] == []


@pytest.mark.parametrize("changes", [
    {"id": 17}, {"title": 17}, {"state": "PRIVATE INVALID STATE"},
    {"relatedIncidentIds": [17]}, {"occurredAt": "PRIVATE INVALID DATE"},
    {"occurredAt": "2026-02-30T13:00:00Z"},
    {"createdAt": "2026-10-31T13:00:00"},
    {"assessment": {"potentiallyMaterial": "true", "explanation": "PRIVATE"}},
    {"assessment": {"potentiallyMaterial": True, "explanation": ""}},
    {"review": {"decision": "MATERIAL", "reviewer": "PRIVATE", "reviewedAt": "bad"}},
])
def test_schema_validation_does_not_coerce_types_or_ignore_date_formats(changes):
    result = inspect_response(raw(incident(**changes)))
    assert result["directIncidentResponse"] is False
    assert result["classification"] == "unsupported"
    assert result["candidates"][0]["incidentResponse"]["violations"]
    assert "PRIVATE" not in json.dumps(result)


def test_missing_required_fields_are_reported_by_known_name_without_values():
    result = inspect_response(raw({"title": "PRIVATE TITLE"}))
    check = result["candidates"][0]["incidentResponse"]
    assert check["conforms"] is False
    assert "id" in check["missingRequired"]
    assert "log" in check["missingRequired"]
    assert "PRIVATE" not in json.dumps(result)


def test_unknown_keys_are_counted_and_not_disclosed_or_traversed():
    document = incident(**{"PRIVATE KEY MAY CONTAIN DATA": {"report": incident()}})
    result = inspect_response(raw(document))
    assert result["directIncidentResponse"] is True  # Our published schema allows extras.
    assert result["candidates"][0]["unknownFieldCount"] == 1
    assert "PRIVATE" not in json.dumps(result)
    assert len(result["incidentMatches"]) == 1


def test_arrays_sample_the_first_record_only():
    result = inspect_response(raw({"reports": [incident(), {"PRIVATE KEY": "PRIVATE VALUE"}]}))
    assert result["classification"] == "incident_in_envelope"
    assert len(result["incidentMatches"]) == 1
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("document", [[], {"reports": []}, {"incidents": []}])
def test_empty_collection_is_no_sample_not_schema_mismatch(document):
    result = inspect_response(raw(document))
    assert result["classification"] == "no_sample"
    assert result["incidentMatches"] == []


def test_empty_extra_collection_does_not_hide_invalid_incident_schema():
    result = inspect_response(raw(incident(occurredAt="PRIVATE INVALID DATE", reports=[])))
    assert result["classification"] == "unsupported"
    assert "PRIVATE" not in json.dumps(result)


def test_candidate_traversal_is_bounded():
    document = incident()
    for _ in range(20):
        document = {"data": document, "report": {"PRIVATE KEY": "PRIVATE VALUE"}}
    result = inspect_response(raw(document))
    assert result["classification"] == "unsupported"
    assert len(result["candidates"]) <= 12
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("payload", [
    b'{"id":1,"id":2}', b'{"report_id":NaN}', b'"\\ud800"',
    b'{"report_id":1e999}', b'not json', b'\xff',
])
def test_ambiguous_or_invalid_json_is_rejected_with_sanitized_error(payload):
    with pytest.raises(SourceApiError) as error:
        inspect_response(payload)
    assert error.value.kind == "invalid_response"
    assert "Traceback" not in error.value.message
    decoded = payload.decode("utf-8", errors="ignore")
    assert not decoded or decoded not in error.value.message


def test_explicit_id_reads_detail_only_and_does_not_expose_the_id():
    client = FakeClient(incident())
    result = probe_one(client, "private-opaque-id")
    assert client.calls == [("detail", "private-opaque-id")]
    assert result["requestCount"] == 1
    assert result["sampleSource"] == "detail"
    assert result["classification"] == "incident_response"
    assert "private-opaque-id" not in json.dumps(result)


@pytest.mark.parametrize("identity", [{"report_id": 17}, {"id": "opaque-uuid-like-id"}])
def test_default_discovery_reads_one_detail_instead_of_assuming_list_shape(identity):
    client = FakeClient(incident(), {"reports": [identity]})
    result = probe_one(client)
    assert client.calls == [("list",), ("detail", str(next(iter(identity.values()))))]
    assert result["requestCount"] == 2
    assert result["sampleSource"] == "detail"
    assert result["directIncidentResponse"] is True


def test_default_discovery_empty_list_does_not_request_detail():
    client = FakeClient(incident(), {"reports": []})
    result = probe_one(client)
    assert client.calls == [("list",)]
    assert result["requestCount"] == 1
    assert result["sampleSource"] == "list"
    assert result["classification"] == "no_sample"


@pytest.mark.parametrize("listing", [
    {"UNKNOWN PRIVATE WRAPPER": [{"id": "PRIVATE ID"}]},
    {"reports": [{"id": "https://private.example/report"}]},
    {"reports": [{"report_id": "../private"}]},
    {"reports": [{"id": True}]},
])
def test_default_discovery_requires_safe_id_before_detail_request(listing):
    client = FakeClient(incident(), listing)
    with pytest.raises(SourceApiError) as failure:
        probe_one(client)
    assert client.calls == [("list",)]
    assert failure.value.kind == "invalid_response"
    assert "PRIVATE" not in failure.value.message
    assert "private" not in failure.value.message


def test_cli_missing_key_exits_before_creating_client(monkeypatch, capsys):
    import team102_probe

    monkeypatch.delenv("SEC450_KEY", raising=False)
    monkeypatch.setattr(team102_probe, "dotenv_values", lambda path: {})
    monkeypatch.setattr(team102_probe, "Team102Client", lambda *args, **kwargs: pytest.fail("No source call without a key"))
    assert team102_probe.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "configuration"
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("detail, expected_exit", [
    (incident(), 0), ({"report_id": 17, "body": {}}, 2),
    ({"PRIVATE KEY": "PRIVATE VALUE"}, 2),
])
def test_cli_prints_only_redacted_schema_summary(monkeypatch, capsys, detail, expected_exit):
    import team102_probe

    class ContextClient(FakeClient):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    client = ContextClient(detail)
    monkeypatch.setenv("SEC450_KEY", "PRIVATE API KEY")
    monkeypatch.setattr(team102_probe, "dotenv_values", lambda path: {})
    monkeypatch.setattr(team102_probe, "Team102Client", lambda key: client)
    assert team102_probe.main(["--report-id", "17"]) == expected_exit
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["scope"] == "one_sample_only"
    assert "PRIVATE" not in captured.out


def test_cli_empty_discovery_returns_distinct_no_sample_exit(monkeypatch, capsys):
    import team102_probe

    class ContextClient(FakeClient):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    client = ContextClient(incident(), {"reports": []})
    monkeypatch.setenv("SEC450_KEY", "PRIVATE API KEY")
    monkeypatch.setattr(team102_probe, "dotenv_values", lambda path: {})
    monkeypatch.setattr(team102_probe, "Team102Client", lambda key: client)
    assert team102_probe.main([]) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["classification"] == "no_sample"
    assert "PRIVATE" not in captured.out
    assert client.calls == [("list",)]


def log_entry():
    return {
        "ts": "2026-10-31T14:00:00Z", "source": "PRIVATE SOURCE",
        "host": "PRIVATE HOST", "client_ip": "PRIVATE ADDRESS",
        "username": "PRIVATE USER", "action": "PRIVATE ACTION",
        "target": "PRIVATE TARGET", "outcome": "PRIVATE OUTCOME",
        "status_code": 200, "bytes_sent": 1234, "clock_anomaly": False,
        "PRIVATE FIELD NAME": "PRIVATE EXTRA VALUE",
    }


@pytest.mark.parametrize("nullable_fields", [False, True])
def test_log_sample_reports_known_types_without_disclosing_event_or_unknown_keys(nullable_fields):
    entry = log_entry()
    if nullable_fields:
        entry.update(client_ip=None, username=None)
    result = inspect_response(raw({
        "query": {"PRIVATE QUERY FIELD": "PRIVATE QUERY VALUE"},
        "count": 1, "collection_gaps": [], "logs": [entry],
    }))
    assert result["classification"] == "unsupported"
    assert result["directIncidentResponse"] is False
    assert result["incidentMatches"] == []
    assert result["guideMatches"] == []
    event = next(candidate for candidate in result["candidates"] if candidate["path"].endswith(".logs[0]"))
    assert event["knownFields"] == {
        "ts": "string", "source": "string", "host": "string",
        "client_ip": "null" if nullable_fields else "string",
        "username": "null" if nullable_fields else "string",
        "action": "string", "target": "string", "outcome": "string",
        "status_code": "integer", "bytes_sent": "integer", "clock_anomaly": "boolean",
    }
    assert event["unknownFieldCount"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_empty_log_collection_is_no_sample():
    result = inspect_response(raw({"query": {}, "count": 0, "collection_gaps": [], "logs": []}))
    assert result["classification"] == "no_sample"
    assert result["incidentMatches"] == []


def test_log_probe_reads_once_without_any_report_request():
    from team102_probe import probe_logs

    client = FakeClient(None, {"logs": [log_entry()]})
    result = probe_logs(client)
    assert client.calls == [("logs",)]
    assert result["requestCount"] == 1
    assert result["sampleSource"] == "logs"
    assert result["classification"] == "unsupported"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("records, expected_exit", [
    ([log_entry()], 2), ([], 3), ([incident()], 0),
])
def test_cli_logs_mode_reads_once_and_prints_only_schema_results(monkeypatch, capsys, records, expected_exit):
    import team102_probe

    class ContextClient(FakeClient):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    client = ContextClient(None, {"logs": records})
    monkeypatch.setenv("SEC450_KEY", "PRIVATE API KEY")
    monkeypatch.setattr(team102_probe, "dotenv_values", lambda path: {})
    monkeypatch.setattr(team102_probe, "Team102Client", lambda key: client)
    assert team102_probe.main(["--resource", "logs"]) == expected_exit
    captured = capsys.readouterr()
    assert captured.err == ""
    summary = json.loads(captured.out)
    assert summary["sampleSource"] == "logs"
    assert summary["requestCount"] == 1
    assert "PRIVATE" not in captured.out
    assert client.calls == [("logs",)]


def test_cli_rejects_report_id_in_logs_mode_before_creating_client(monkeypatch, capsys):
    import team102_probe

    monkeypatch.setenv("SEC450_KEY", "PRIVATE API KEY")
    monkeypatch.setattr(team102_probe, "dotenv_values", lambda path: {})
    monkeypatch.setattr(team102_probe, "Team102Client", lambda *args, **kwargs: pytest.fail("Invalid option combination must not access source"))
    assert team102_probe.main(["--resource", "logs", "--report-id", "PRIVATE-ID"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "configuration"
    assert "PRIVATE" not in captured.err
    assert "Traceback" not in captured.err

