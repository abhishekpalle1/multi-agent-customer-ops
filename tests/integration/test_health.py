from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from support_ops.api.main import create_app
from support_ops.db.session import get_engine


@pytest.fixture
def client(test_engine: Engine) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: test_engine
    with TestClient(app) as test_client:
        yield test_client


def test_health_confirms_postgres_and_pgvector(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"app": "ok", "database": "ok", "pgvector": "ok"}
    assert body["pgvector_version"]
    # The DATABASE_URL engine must never be created by the test run.
    assert get_engine.cache_info().currsize == 0


def test_test_database_is_migrated_to_head(
    test_engine: Engine, alembic_head_revision: str | None
) -> None:
    with test_engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        has_vector = connection.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ).scalar_one()

    assert alembic_head_revision is not None
    assert current == alembic_head_revision
    assert has_vector is True
