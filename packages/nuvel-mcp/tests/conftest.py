import pytest

import _pg


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "file_store: inspects the OAuth JSON file itself; skipped when the OAuth "
        "suites run on Postgres (NUVEL_MCP_TEST_OAUTH_STORE=postgres)")


def pytest_collection_modifyitems(config, items):
    if not _pg.OAUTH_ON_POSTGRES:
        return
    skip = pytest.mark.skip(reason="inspects the OAuth JSON file; the store is Postgres")
    for item in items:
        if "file_store" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def pg_db():
    """A migrated database in its own schema, dropped after the test."""
    if not _pg.DSN:
        pytest.skip("NUVEL_MCP_TEST_DATABASE_URL is not set")
    return _pg.open_db()


@pytest.fixture(autouse=True)
def _drop_test_schemas():
    # Every schema (and pool) a test opened is dropped when it ends, so a long
    # run never piles up connections.
    yield
    _pg.drop_all()
