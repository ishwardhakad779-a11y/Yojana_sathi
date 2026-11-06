import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("yojana_sathi.db")

DATABASE_URL = os.getenv("DATABASE_URL")
USE_PG = bool(DATABASE_URL)
SQLITE_PATH = Path(
    os.getenv("SQLITE_PATH", Path(__file__).parent.parent / "data" / "yojana.db")
)

_pg_pool = None
if USE_PG:
    try:
        from psycopg_pool import ConnectionPool
        # Connection pool for PostgreSQL on Render
        _pg_pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=10,
            timeout=10,
            open=False,
        )
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL pool: {e}. Falling back to SQLite.")
        USE_PG = False


def enabled() -> bool:
    """Check if database is enabled (always True)."""
    return True


@contextmanager
def _conn():
    """
    Context manager for database connections with automatic pooling.

    Uses PostgreSQL connection pool when DATABASE_URL is set,
    otherwise falls back to SQLite.

    Yields:
        Database connection object
    """
    if USE_PG and _pg_pool is not None:
        with _pg_pool.connection() as c:
            yield c
            c.commit()
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(SQLITE_PATH)
        try:
            yield c
            c.commit()
        finally:
            c.close()


def _run(c, query: str, params=()):
    """
    Execute SQL query with database-agnostic placeholder conversion.

    Args:
        c: Database connection
        query: SQL query string
        params: Query parameters tuple

    Returns:
        Cursor object after execution
    """
    # Convert ? placeholder to %s for Postgres
    if USE_PG:
        query = query.replace("?", "%s")
    return c.execute(query, params)


def init_db():
    global USE_PG
    if USE_PG and _pg_pool is not None:
        try:
            _pg_pool.open()
            logger.info("PostgreSQL connection pool opened successfully.")
        except Exception as e:
            logger.error(f"Could not open PostgreSQL pool: {e}. Reverting to SQLite.")
            USE_PG = False

    with _conn() as c:
        if USE_PG:
            id_col = "BIGSERIAL PRIMARY KEY"
            now = "now()"
        else:
            id_col = "INTEGER PRIMARY KEY AUTOINCREMENT"
            now = "CURRENT_TIMESTAMP"

        _run(
            c,
            f"""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                profile    TEXT NOT NULL DEFAULT '{{}}',
                intent     TEXT NOT NULL DEFAULT 'info',
                topic      TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT {now}
            )
            """,
        )
        _run(
            c,
            f"""
            CREATE TABLE IF NOT EXISTS messages (
                id         {id_col},
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                sources    TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL DEFAULT {now}
            )
            """,
        )
        _run(
            c,
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id)",
        )
    logger.info(f"Database ready: {'Postgres' if USE_PG else f'SQLite ({SQLITE_PATH})'}")


def close_db():
    if _pg_pool is not None:
        try:
            _pg_pool.close()
            logger.info("PostgreSQL connection pool closed.")
        except Exception as e:
            logger.warning(f"Error closing PostgreSQL pool: {e}")


def check_health() -> bool:
    try:
        with _conn() as c:
            _run(c, "SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


def get_session(session_id: str) -> dict:
    with _conn() as c:
        row = _run(
            c,
            "SELECT profile, intent, topic FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return {}
    return {"profile": json.loads(row[0] or "{}"), "intent": row[1], "topic": row[2]}


def save_session(session_id: str, profile: dict, intent: str, topic: str):
    with _conn() as c:
        _run(
            c,
            """
            INSERT INTO sessions (session_id, profile, intent, topic)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (session_id) DO UPDATE SET
                profile = excluded.profile,
                intent = excluded.intent,
                topic = excluded.topic
            """,
            (session_id, json.dumps(profile, ensure_ascii=False), intent, topic),
        )


def save_message(session_id: str, role: str, content: str, sources=None):
    with _conn() as c:
        _run(
            c,
            "INSERT INTO messages (session_id, role, content, sources) VALUES (?, ?, ?, ?)",
            (session_id, role, content, json.dumps(sources or [], ensure_ascii=False)),
        )


def get_history(session_id: str) -> list:
    with _conn() as c:
        rows = _run(
            c,
            "SELECT role, content, sources FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [
        {"role": r[0], "content": r[1], "sources": json.loads(r[2] or "[]")}
        for r in rows
    ]
