"""Same real-transaction-wrapped pattern as tests/conftest.py's
`matching_db_session` (see its own docstring for the savepoint-rollback
mechanics) -- no TRUNCATE needed here, `chat_sessions`/`chat_messages` are
new tables with no pre-existing fixture data to protect.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import DATABASE_URL


@pytest.fixture
def chatbot_db_session():
    try:
        engine = create_engine(DATABASE_URL)
        connection = engine.connect()
    except Exception as exc:
        pytest.skip(f"No reachable database for DB-touching tests: {exc}")

    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()
