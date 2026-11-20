"""Identity reserved for one incident per imported Team 102 report."""

TEAM102_SOURCE_INDEX_NAME = "uq_incidents_team102_source"
TEAM102_SOURCE_PREDICATE = (
    "substr(source, 1, 15) = 'team102:report:' OR "
    "substr(source, 1, 23) = 'team102-fixture:report:'"
)


def is_team102_source(source):
    return isinstance(source, str) and source.startswith((
        "team102:report:", "team102-fixture:report:",
    ))
