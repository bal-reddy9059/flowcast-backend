import os
from pathlib import Path
import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

# Ensure the app uses a local SQLite test database during pytest runs.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_flowcast.db")

from app.database import Base, get_db, engine as app_engine
from app.main import app

TEST_DB_PATH = Path("./test_flowcast.db")

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=app_engine)


def override_get_db(test_db_session):
    try:
        yield test_db_session
    finally:
        pass


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Create and destroy the temporary test database for the test session."""
    Base.metadata.create_all(bind=app_engine)
    yield
    app_engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture(scope="function")
def db_session():
    """Provide a fresh database session with rollback for each test."""
    connection = app_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
async def async_client(db_session):
    """Async HTTP client using the FastAPI app and a test DB session."""
    app.dependency_overrides[get_db] = lambda: override_get_db(db_session)
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
