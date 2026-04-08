import sys

import pytest
from fastapi.testclient import TestClient

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app
from goal_ops_console.services import build_services

sys.dont_write_bytecode = True


@pytest.fixture
def services():
    return build_services(Settings(database_url=":memory:"))


@pytest.fixture
def app():
    return create_app(Settings(database_url=":memory:"))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
