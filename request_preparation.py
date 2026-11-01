"""Prepare incident writes in existence-before-field-validation order.

FastAPI decodes the JSON envelope before resolving dependencies. This seam
orders incident lookup before validation of fields in an otherwise decoded
body; it does not replace the framework's malformed-JSON handling.
"""

from fastapi import Body, Depends, Path
from pydantic import BaseModel


def build_preparation(get_session, lookup, review_model, link_model):
    """Return review/related dependencies sharing the route's selected session.

    ``lookup(session, incident_id)`` must return the incident or raise the
    application's not-found exception. Route handlers receive ``(row, data)``
    and can also depend on the same ``get_session`` callable: FastAPI caches
    that request's dependency result, rather than opening a second session.

    Types are attached to actual body parameters, so generated OpenAPI retains
    the original request model and path alias without manual schema patches.
    The factory does not choose a store or inspect application global state.
    """
    if not callable(get_session) or not callable(lookup):
        raise TypeError("Request preparation needs session and lookup callables")
    for model in (review_model, link_model):
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("Request preparation needs Pydantic body models")

    def require_existing(
        incident_id: str = Path(alias="incidentId"),
        session=Depends(get_session),
    ):
        return lookup(session, incident_id)

    def prepare_review(
        row=Depends(require_existing),
        data: review_model = Body(...),
    ):
        return row, data

    def prepare_related(
        row=Depends(require_existing),
        data: link_model = Body(...),
    ):
        return row, data

    return prepare_review, prepare_related
