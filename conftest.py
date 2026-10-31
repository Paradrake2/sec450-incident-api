"""Choose isolated test storage before application modules are collected."""

import os
import sys
from contextlib import ExitStack

import pytest
from sqlalchemy.exc import SQLAlchemyError

from testing_database import disposable_engine


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    stack = ExitStack()
    config._sec450_store_stack = stack
    config._sec450_previous_database_url = os.environ.get("DATABASE_URL")
    try:
        engine = stack.enter_context(disposable_engine())
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    except SQLAlchemyError:
        raise pytest.UsageError(
            "Could not prepare the disposable test store. Check that local "
            "PostgreSQL is running and TEST_DATABASE_URL selects a test database."
        ) from None
    os.environ["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)


def pytest_unconfigure(config):
    stack = getattr(config, "_sec450_store_stack", None)
    if stack is None:
        return
    try:
        # main creates its own engine from the selected URL. Release it before
        # deleting SQLite files or dropping the PostgreSQL session schema.
        module = sys.modules.get("main")
        if module is not None:
            module.engine.dispose()
        stack.close()
    finally:
        previous = config._sec450_previous_database_url
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
