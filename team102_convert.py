"""DRAFT: Team 102 report envelope -> CreateIncident payload. Pure; no I/O."""

from datetime import datetime, timezone

from request_models import CreateIncident

# API doc: timeline entries have a time field; events call it `ts`, the timeline blurb says "time". Accept both.
TIME_KEYS = ("timestamp", "time", "ts", "occurred_at", "occurredAt")


class ConversionError(ValueError):
    pass


def _entry_time(entry):
    if not isinstance(entry, dict):
        return None
    for key in TIME_KEYS:
        value = entry.get(key)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # API doc: all times UTC
    return None


def convert_report(report: dict, fallback_occurred_at: datetime | None = None) -> dict:
    """Return a validated CreateIncident dict. fallback_occurred_at is opt-in (e.g. fetched_at)."""
    rid, body = report.get("report_id"), report.get("body")
    if not isinstance(rid, int) or not isinstance(body, dict):
        raise ConversionError("not a Team 102 report envelope")

    summary = (body.get("summary") or "").strip()
    lines = [x for x in body.get("raw_lines") or [] if isinstance(x, str)]
    log = "\n".join(lines) or summary
    times = [t for t in map(_entry_time, body.get("timeline") or []) if t]
    when = min(times) if times else fallback_occurred_at
    if when is None:
        raise ConversionError("no timezone-aware time in timeline")

    payload = {
        "title": (summary or f"Team 102 report {rid}")[:200],
        "log": log,
        "occurredAt": when.isoformat().replace("+00:00", "Z"),
        "source": f"team102:report:{rid}",
    }
    try:
        CreateIncident(**payload)
    except ValueError as exc:
        raise ConversionError(str(exc)) from None
    return payload
