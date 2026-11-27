import logging
from types import TracebackType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from support_ops.api.main import create_app
from support_ops.db.session import get_engine

LEAKY_DRIVER_MESSAGE = 'connection to server at "secret-host.example.test" failed'


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeConnection:
    def __init__(self, pgvector_version: str | None) -> None:
        self._pgvector_version = pgvector_version

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def execute(self, statement: Any) -> _FakeResult:
        if "pg_extension" in str(statement):
            return _FakeResult(self._pgvector_version)
        return _FakeResult(1)


class _FakeEngine:
    def __init__(self, pgvector_version: str | None = "0.8.0", fail: bool = False) -> None:
        self._pgvector_version = pgvector_version
        self._fail = fail

    def connect(self) -> _FakeConnection:
        if self._fail:
            raise OperationalError("SELECT 1", {}, Exception(LEAKY_DRIVER_MESSAGE))
        return _FakeConnection(self._pgvector_version)


def _client(engine: _FakeEngine) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


def test_health_ok_when_database_and_pgvector_available() -> None:
    response = _client(_FakeEngine(pgvector_version="0.8.0")).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"app": "ok", "database": "ok", "pgvector": "ok"},
        "pgvector_version": "0.8.0",
    }


def test_health_unavailable_when_pgvector_missing() -> None:
    response = _client(_FakeEngine(pgvector_version=None)).get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"] == {"app": "ok", "database": "ok", "pgvector": "missing"}


def test_health_unavailable_without_leaking_driver_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        response = _client(_FakeEngine(fail=True)).get("/health")

    assert response.status_code == 503
    assert response.json()["checks"] == {"app": "ok", "database": "error", "pgvector": "unknown"}
    assert "secret-host" not in response.text
    assert "secret-host" not in caplog.text
    assert "OperationalError" in caplog.text
