"""Test fixtures. Every test runs against a throwaway database.

The road graph is loaded once for the whole session -- it is 4974 nodes and
takes ~4 s, and nothing in the tests mutates it.
"""
import os
import tempfile

import pytest

from app import config


@pytest.fixture(scope="session", autouse=True)
def _test_db():
    """Point the store at a temp file so tests never touch app/data/reports.db."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sih-test-")
    os.close(fd)
    config.DB = path
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def clean_db():
    from app import store
    store.reset()
    yield


@pytest.fixture(scope="session")
def network():
    from app.network import net
    return net()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
